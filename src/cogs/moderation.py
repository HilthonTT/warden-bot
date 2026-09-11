"""Moderation: kick, ban, timeout, warnings, purge, and the case log.

Every command runs hierarchy and self-protection checks, tries to DM the
target *before* acting (afterwards the bot may no longer share a server with
them), records a numbered case, and mirrors the result to the mod log.

Permissions:
  /kick, /warn, /warnings, /delwarn, /case, /cases -> Kick Members
  /ban, /tempban, /unban, /clearwarnings           -> Ban Members
  /timeout, /untimeout                             -> Moderate Members
  /purge                                           -> Manage Messages
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import tasks

from core.checks import guild_permissions, hierarchy_error
from core.cog import WardenCog
from core.constants import (
    AUDIT_REASON_MAX,
    EMBED_FIELD_VALUE_MAX,
    EMBED_TOTAL_MAX,
    truncate,
)
from core.duration import (
    DISCORD_MAX_TIMEOUT,
    DurationError,
    format_duration,
    parse_duration,
)
from core.embeds import action_embed, add_field, base_embed, info_embed
from core.pagination import Paginator
from core.responses import fail, reply, try_dm
from data.models import Case, CaseAction

if TYPE_CHECKING:
    from core.bot import WardenBot

log = logging.getLogger(__name__)

NO_REASON = "No reason provided"
SECONDS_PER_DAY = 86_400
CASES_PER_PAGE = 8
#: Discord rejects an embed whose *total* text exceeds 6000 characters, so a
#: page is closed on whichever limit is hit first. Eight cases with long
#: reasons used to overflow it and fail the whole command with a 400.
PAGE_CHAR_BUDGET = EMBED_TOTAL_MAX - 128
PURGE_MAX = 100
SWEEP_INTERVAL_SECONDS = 60

ACTION_COLORS: dict[CaseAction, discord.Color] = {
    CaseAction.WARN: discord.Color.gold(),
    CaseAction.KICK: discord.Color.orange(),
    CaseAction.BAN: discord.Color.red(),
    CaseAction.TEMPBAN: discord.Color.dark_red(),
    CaseAction.UNBAN: discord.Color.green(),
    CaseAction.TIMEOUT: discord.Color.dark_gold(),
    CaseAction.UNTIMEOUT: discord.Color.teal(),
}


def audit_reason(moderator: discord.abc.User, reason: str) -> str:
    """Format an audit-log reason within Discord's length cap."""
    return truncate(f"By {moderator} — {reason}", AUDIT_REASON_MAX)


class Moderation(WardenCog):
    """Moderation commands."""

    async def cog_load(self) -> None:
        self.sweep_expired_bans.start()

    async def cog_unload(self) -> None:
        self.sweep_expired_bans.cancel()

    async def record(
        self,
        guild: discord.Guild,
        action: CaseAction,
        target: discord.abc.User,
        moderator: discord.abc.User,
        reason: str,
        *,
        expires_at: int | None = None,
        extra: dict[str, str] | None = None,
    ) -> tuple[Case, discord.Embed]:
        """Write the case and build its report embed."""
        case, _ = await self.db.add_case(
            guild.id, action, target.id, moderator.id, reason, expires_at=expires_at
        )
        embed = action_embed(
            action.label,
            ACTION_COLORS.get(action, discord.Color.blurple()),
            target,
            moderator,
            reason,
            extra={"Case": f"#{case.number}", **(extra or {})},
        )
        return case, embed

    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(user="The member to kick.", reason="Why you're kicking them.")
    @guild_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if (err := hierarchy_error(interaction, user)) is not None:
            await fail(interaction, err)
            return

        reason = reason or NO_REASON
        await interaction.response.defer(thinking=True)

        await try_dm(user, f"You have been kicked from **{guild.name}**.\nReason: {reason}")
        try:
            await user.kick(reason=audit_reason(interaction.user, reason))
        except discord.Forbidden:
            await fail(interaction, "I lack permission to kick that user.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return

        _, embed = await self.record(guild, CaseAction.KICK, user, interaction.user, reason)
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(name="ban", description="Ban a user from the server.")
    @app_commands.describe(
        user="The user to ban (member or external user ID).",
        reason="Why you're banning them.",
        delete_message_days="Days of recent messages to delete (0-7).",
    )
    @guild_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: str | None = None,
        delete_message_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        await self._ban(interaction, user, reason, delete_message_days, duration=None)

    @app_commands.command(name="tempban", description="Ban a user and lift it automatically later.")
    @app_commands.describe(
        user="The user to ban.",
        duration="How long the ban lasts, e.g. 12h, 7d, 2w.",
        reason="Why you're banning them.",
        delete_message_days="Days of recent messages to delete (0-7).",
    )
    @guild_permissions(ban_members=True)
    async def tempban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        duration: str,
        reason: str | None = None,
        delete_message_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        try:
            delta = parse_duration(duration)
        except DurationError as exc:
            await fail(interaction, str(exc))
            return
        await self._ban(interaction, user, reason, delete_message_days, duration=delta)

    async def _ban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: str | None,
        delete_message_days: int,
        *,
        duration: timedelta | None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        target_member = guild.get_member(user.id)
        if (
            target_member is not None
            and (err := hierarchy_error(interaction, target_member)) is not None
        ):
            await fail(interaction, err)
            return

        reason = reason or NO_REASON
        await interaction.response.defer(thinking=True)

        expires_at: int | None = None
        extra: dict[str, str] = {"Message cleanup": f"{delete_message_days}d"}
        notice = f"You have been banned from **{guild.name}**.\nReason: {reason}"
        if duration is not None:
            expires_at = int(time.time() + duration.total_seconds())
            extra["Expires"] = f"<t:{expires_at}:R>"
            notice = (
                f"You have been banned from **{guild.name}** for "
                f"{format_duration(duration)}.\nReason: {reason}"
            )

        if target_member is not None:
            await try_dm(target_member, notice)

        try:
            await guild.ban(
                user,
                reason=audit_reason(interaction.user, reason),
                delete_message_seconds=delete_message_days * SECONDS_PER_DAY,
            )
        except discord.Forbidden:
            await fail(interaction, "I lack permission to ban that user.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return

        action = CaseAction.TEMPBAN if duration is not None else CaseAction.BAN
        _, embed = await self.record(
            guild,
            action,
            user,
            interaction.user,
            reason,
            expires_at=expires_at,
            extra=extra,
        )
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(name="unban", description="Unban a user by ID.")
    @app_commands.describe(
        user_id="The numeric ID of the banned user.",
        reason="Why you're lifting the ban.",
    )
    @guild_permissions(ban_members=True)
    @app_commands.checks.cooldown(2, 5.0, key=lambda i: i.user.id)
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        try:
            target_id = int(user_id.strip())
        except ValueError:
            await fail(interaction, "User ID must be a number.")
            return

        await interaction.response.defer(thinking=True)
        try:
            user = await self.bot.fetch_user(target_id)
        except discord.NotFound:
            await fail(interaction, "No Discord account with that ID.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return

        reason = reason or NO_REASON
        try:
            await guild.unban(user, reason=audit_reason(interaction.user, reason))
        except discord.NotFound:
            await fail(interaction, "That user is not banned.")
            return
        except discord.Forbidden:
            await fail(interaction, "I lack permission to unban that user.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return

        await self._retire_tempbans(guild.id, user.id)
        _, embed = await self.record(guild, CaseAction.UNBAN, user, interaction.user, reason)
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(
        name="timeout", description="Time a member out so they can't talk or react."
    )
    @app_commands.describe(
        user="The member to time out.",
        duration="How long, e.g. 10m, 2h30m, 7d. Discord's maximum is 28 days.",
        reason="Why you're timing them out.",
    )
    @guild_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: str,
        reason: str | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if (err := hierarchy_error(interaction, user)) is not None:
            await fail(interaction, err)
            return

        try:
            delta = parse_duration(duration)
        except DurationError as exc:
            await fail(interaction, str(exc))
            return

        if delta > DISCORD_MAX_TIMEOUT:
            await fail(
                interaction,
                f"Discord caps timeouts at 28 days — you asked for "
                f"{format_duration(delta)}. Use `/tempban` for longer.",
            )
            return

        reason = reason or NO_REASON
        await interaction.response.defer(thinking=True)

        until = discord.utils.utcnow() + delta
        spelled = format_duration(delta)
        await try_dm(
            user,
            f"You have been timed out in **{guild.name}** for {spelled}.\nReason: {reason}",
        )
        try:
            await user.timeout(until, reason=audit_reason(interaction.user, reason))
        except discord.Forbidden:
            await fail(interaction, "I lack permission to time that member out.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return

        _, embed = await self.record(
            guild,
            CaseAction.TIMEOUT,
            user,
            interaction.user,
            reason,
            expires_at=int(until.timestamp()),
            extra={"Duration": spelled, "Expires": f"<t:{int(until.timestamp())}:R>"},
        )
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(name="untimeout", description="Lift a member's timeout early.")
    @app_commands.describe(user="The member to release.", reason="Why.")
    @guild_permissions(moderate_members=True)
    async def untimeout(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if not user.is_timed_out():
            await fail(interaction, f"{user.mention} isn't timed out.")
            return

        reason = reason or NO_REASON
        await interaction.response.defer(thinking=True)
        try:
            await user.timeout(None, reason=audit_reason(interaction.user, reason))
        except discord.Forbidden:
            await fail(interaction, "I lack permission to edit that member.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return

        await try_dm(user, f"Your timeout in **{guild.name}** has been lifted.")
        _, embed = await self.record(guild, CaseAction.UNTIMEOUT, user, interaction.user, reason)
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(name="purge", description="Bulk-delete recent messages in this channel.")
    @app_commands.describe(
        amount="How many messages to scan and delete (1-100).",
        user="Only delete messages from this member.",
        contains="Only delete messages containing this text.",
    )
    @guild_permissions(manage_messages=True)
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, PURGE_MAX],
        user: discord.Member | None = None,
        contains: str | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel | discord.Thread):
            await fail(interaction, "Run this in a server text channel.")
            return

        needle = (contains or "").lower()

        def matches(message: discord.Message) -> bool:
            if user is not None and message.author.id != user.id:
                return False
            return not needle or needle in message.content.lower()

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            deleted = await channel.purge(
                limit=amount, check=matches, reason=audit_reason(interaction.user, "purge")
            )
        except discord.Forbidden:
            await fail(interaction, "I need **Manage Messages** in this channel.")
            return
        except discord.HTTPException as exc:
            await fail(
                interaction,
                f"Discord error: {exc}. Messages older than 14 days can't be bulk-deleted.",
            )
            return

        filters = []
        if user is not None:
            filters.append(f"from {user.mention}")
        if contains:
            filters.append(f"containing `{truncate(contains, 60)}`")
        suffix = f" {' and '.join(filters)}" if filters else ""
        await reply(
            interaction, f"🧹 Deleted **{len(deleted)}** message(s){suffix}.", ephemeral=True
        )

        embed = base_embed("🧹 Messages purged", color=discord.Color.blurple())
        add_field(embed, "Moderator", interaction.user.mention, inline=True)
        add_field(embed, "Channel", channel.mention, inline=True)
        add_field(embed, "Deleted", str(len(deleted)), inline=True)
        if filters:
            add_field(embed, "Filters", " and ".join(filters))
        await self.modlog.send(guild, embed)

    @app_commands.command(name="warn", description="Warn a member.")
    @app_commands.describe(user="The member to warn.", reason="Why you're warning them.")
    @guild_permissions(kick_members=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if (err := hierarchy_error(interaction, user)) is not None:
            await fail(interaction, err)
            return

        await interaction.response.defer(thinking=True)

        cfg = await self.config.get(guild.id)
        expires_at = (
            int(time.time()) + cfg.warn_expiry_days * SECONDS_PER_DAY
            if cfg.warn_expiry_days > 0
            else None
        )
        case, count = await self.db.add_case(
            guild.id,
            CaseAction.WARN,
            user.id,
            interaction.user.id,
            reason,
            expires_at=expires_at,
        )

        expiry_note = f"\nThis warning expires <t:{expires_at}:R>." if expires_at else ""
        await try_dm(
            user,
            f"⚠️ You have been warned in **{guild.name}**.\n"
            f"Reason: {reason}\nActive warnings: **{count}**{expiry_note}",
        )

        extra = {"Case": f"#{case.number}", "Active warnings": str(count)}
        if expires_at is not None:
            extra["Expires"] = f"<t:{expires_at}:R>"
        embed = action_embed(
            CaseAction.WARN.label,
            ACTION_COLORS[CaseAction.WARN],
            user,
            interaction.user,
            reason,
            extra=extra,
        )
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)
        await self.escalation.apply(guild, user, count, cfg)

    @app_commands.command(name="warnings", description="List a user's active warnings.")
    @app_commands.describe(user="The user to query.")
    @guild_permissions(kick_members=True)
    async def warnings_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await self._show_cases(
            interaction, user, action=CaseAction.WARN, title=f"Warnings for {user}"
        )

    @app_commands.command(name="cases", description="List every case for a user.")
    @app_commands.describe(user="The user to query.")
    @guild_permissions(kick_members=True)
    async def cases_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await self._show_cases(interaction, user, action=None, title=f"Cases for {user}")

    async def _show_cases(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        *,
        action: CaseAction | None,
        title: str,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        active_only = action is CaseAction.WARN
        records = await self.db.get_cases(
            guild.id, user_id=user.id, action=action, active_only=active_only
        )
        if active_only:
            records = [case for case in records if not case.is_expired]

        if not records:
            noun = "active warnings" if active_only else "cases"
            await reply(interaction, f"{user.mention} has no {noun}.", ephemeral=True)
            return

        pages = self._case_pages(guild, user, records, title)
        view = Paginator(pages, owner_id=interaction.user.id)
        await reply(
            interaction,
            embed=view.current,
            view=None if view.single_page else view,
            ephemeral=True,
        )
        if not view.single_page:
            view.message = await interaction.original_response()

    def _case_pages(
        self,
        guild: discord.Guild,
        user: discord.Member,
        records: list[Case],
        title: str,
    ) -> list[discord.Embed]:
        """Lay cases out over as many embeds as their length needs."""

        def fresh() -> discord.Embed:
            embed = base_embed(title, color=discord.Color.gold())
            embed.set_thumbnail(url=user.display_avatar.url)
            return embed

        pages: list[discord.Embed] = []
        page = fresh()
        for case in records:
            name = self._case_heading(case)
            value = truncate(self._case_body(guild, case), EMBED_FIELD_VALUE_MAX)
            filled = len(page.fields) >= CASES_PER_PAGE
            overflows = len(page) + len(name) + len(value) > PAGE_CHAR_BUDGET
            if page.fields and (filled or overflows):
                pages.append(page)
                page = fresh()
            add_field(page, name, value)
        if page.fields:
            pages.append(page)
        return pages

    @staticmethod
    def _case_heading(case: Case) -> str:
        suffix = "" if case.active else " • retired"
        if case.active and case.is_expired:
            suffix = " • expired"
        return f"{case.action.emoji} Case #{case.number} — {case.action.label}{suffix}"

    @staticmethod
    def _case_body(guild: discord.Guild, case: Case) -> str:
        moderator = guild.get_member(case.moderator_id)
        byline = moderator.mention if moderator else f"`{case.moderator_id}`"
        lines = [f"By {byline} • <t:{case.created_at}:R>", case.reason]
        if case.expires_at is not None:
            lines.append(f"Expires <t:{case.expires_at}:R>")
        return "\n".join(lines)

    @app_commands.command(name="case", description="Show one case by number.")
    @app_commands.describe(number="The case number shown by /cases.")
    @guild_permissions(kick_members=True)
    async def case_cmd(self, interaction: discord.Interaction, number: int) -> None:
        guild = interaction.guild
        assert guild is not None

        case = await self.db.get_case(guild.id, number)
        if case is None:
            await fail(interaction, f"No case `#{number}` in this server.")
            return

        target = self.bot.get_user(case.user_id)
        embed = info_embed(self._case_heading(case))
        add_field(
            embed,
            "User",
            f"{target.mention if target else ''} `{case.user_id}`".strip(),
            inline=True,
        )
        add_field(embed, "Details", self._case_body(guild, case))
        await reply(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="clearwarnings", description="Clear all active warnings for a user.")
    @app_commands.describe(user="The user whose warnings will be cleared.")
    @guild_permissions(ban_members=True)
    async def clearwarnings(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        cleared = await self.db.clear_warnings(guild.id, user.id)
        if cleared == 0:
            await reply(interaction, f"{user.mention} had no active warnings.", ephemeral=True)
            return

        embed = action_embed(
            "Warnings cleared",
            discord.Color.green(),
            user,
            interaction.user,
            f"Cleared {cleared} warning(s).",
        )
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(name="delwarn", description="Retire a single warning by case number.")
    @app_commands.describe(number="The case number shown by /warnings.")
    @guild_permissions(kick_members=True)
    async def delwarn(self, interaction: discord.Interaction, number: int) -> None:
        guild = interaction.guild
        assert guild is not None

        case = await self.db.get_case(guild.id, number)
        if case is None:
            await fail(interaction, f"No case `#{number}` in this server.")
            return
        if case.action is not CaseAction.WARN:
            await fail(
                interaction,
                f"Case `#{number}` is a {case.action.label.lower()}, not a warning.",
            )
            return
        if await self.db.deactivate_case(guild.id, number) is None:
            await fail(interaction, f"Case `#{number}` was already retired.")
            return

        await reply(interaction, f"✅ Retired warning case `#{number}`.", ephemeral=True)

    async def _retire_tempbans(self, guild_id: int, user_id: int) -> None:
        """Stop the sweeper chasing a ban a moderator already lifted."""
        for case in await self.db.get_cases(
            guild_id, user_id=user_id, action=CaseAction.TEMPBAN, active_only=True
        ):
            await self.db.deactivate_case(guild_id, case.number)

    @tasks.loop(seconds=SWEEP_INTERVAL_SECONDS)
    async def sweep_expired_bans(self) -> None:
        """Lift temporary bans whose duration has elapsed.

        Discord has no native expiring ban, so the bot has to do it. Cases stay
        active until the unban succeeds, which means a restart mid-expiry
        retries rather than leaving someone banned forever.
        """
        for case in await self.db.due_cases(CaseAction.TEMPBAN):
            guild = self.bot.get_guild(case.guild_id)
            if guild is None:
                continue

            user = discord.Object(id=case.user_id)
            try:
                await guild.unban(user, reason=f"Temporary ban from case #{case.number} expired")
            except discord.NotFound:
                log.info("Tempban case #%s was already lifted", case.number)
            except discord.Forbidden:
                log.warning("Cannot lift tempban #%s — missing Ban Members", case.number)
                continue
            except discord.HTTPException:
                log.warning("Failed to lift tempban #%s", case.number, exc_info=True)
                continue

            await self.db.deactivate_case(case.guild_id, case.number)

            target = self.bot.get_user(case.user_id)
            embed = base_embed(
                f"{CaseAction.UNBAN.emoji} Temporary ban expired",
                color=ACTION_COLORS[CaseAction.UNBAN],
                description=(
                    f"**User:** {target.mention if target else ''} `{case.user_id}`\n"
                    f"**Original case:** #{case.number}\n"
                    f"**Reason given:** {case.reason}"
                ),
            )
            await self.modlog.send(guild, embed)

    @sweep_expired_bans.before_loop
    async def before_sweep(self) -> None:
        """Hold the sweeper until the gateway is up.

        A client that was never started (a test harness, or a failed login)
        raises instead of waiting; there is nothing to sweep in that case, so
        the loop retires rather than dying with an unretrieved exception.
        """
        try:
            await self.bot.wait_until_ready()
        except RuntimeError:
            log.debug("Bot never started; not sweeping expired bans")
            self.sweep_expired_bans.cancel()


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(Moderation(bot))
