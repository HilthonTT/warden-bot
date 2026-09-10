"""Moderation: kick, ban, unban, warn, and warning history.

Every command runs hierarchy and self-protection checks, tries to DM the
target *before* acting (afterwards the bot may no longer share a server with
them), and mirrors the result to the configured mod-log channel.

Permissions:
  /kick, /warn, /warnings, /delwarn  -> Kick Members
  /ban, /unban, /clearwarnings       -> Ban Members
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from core.checks import guild_permissions, hierarchy_error
from core.cog import WardenCog
from core.constants import AUDIT_REASON_MAX, EMBED_FIELD_VALUE_MAX, truncate
from core.embeds import action_embed, add_field, base_embed
from core.responses import fail, reply, try_dm

if TYPE_CHECKING:
    from core.bot import WardenBot

log = logging.getLogger(__name__)

NO_REASON = "No reason provided"
WARNINGS_PAGE_SIZE = 25
SECONDS_PER_DAY = 86_400


def audit_reason(moderator: discord.abc.User, reason: str) -> str:
    """Format an audit-log reason within Discord's length cap."""
    return truncate(f"By {moderator} — {reason}", AUDIT_REASON_MAX)


class Moderation(WardenCog):
    """Moderation commands."""

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

        embed = action_embed(
            "Kicked",
            discord.Color.orange(),
            user,
            interaction.user,
            reason,
        )
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

        if target_member is not None:
            await try_dm(
                target_member,
                f"You have been banned from **{guild.name}**.\nReason: {reason}",
            )

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

        embed = action_embed(
            "Banned",
            discord.Color.red(),
            user,
            interaction.user,
            reason,
            extra={"Message cleanup": f"{delete_message_days}d"},
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

        try:
            await guild.unban(user, reason=audit_reason(interaction.user, reason or "—"))
        except discord.NotFound:
            await fail(interaction, "That user is not banned.")
            return
        except discord.Forbidden:
            await fail(interaction, "I lack permission to unban that user.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return

        embed = action_embed(
            "Unbanned",
            discord.Color.green(),
            user,
            interaction.user,
            reason or "—",
        )
        await reply(interaction, embed=embed)
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
        warning_id, count = await self.db.add_warning(
            guild.id,
            user.id,
            interaction.user.id,
            reason,
        )

        await try_dm(
            user,
            f"⚠️ You have been warned in **{guild.name}**.\n"
            f"Reason: {reason}\nTotal warnings: **{count}**",
        )

        embed = action_embed(
            "Warned",
            discord.Color.gold(),
            user,
            interaction.user,
            reason,
            extra={"Warning #": str(warning_id), "Total": str(count)},
        )
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)
        await self.escalation.apply(guild, user, count, cfg)

    @app_commands.command(name="warnings", description="List a user's warnings.")
    @app_commands.describe(user="The user to query.")
    @guild_permissions(kick_members=True)
    async def warnings_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        total = await self.db.count_warnings(guild.id, user.id)
        if total == 0:
            await reply(interaction, f"{user.mention} has no warnings.", ephemeral=True)
            return

        warns = await self.db.get_warnings(guild.id, user.id, limit=WARNINGS_PAGE_SIZE)
        embed = base_embed(f"Warnings for {user}", color=discord.Color.gold())
        embed.set_thumbnail(url=user.display_avatar.url)
        for record in warns:
            moderator = guild.get_member(record.moderator_id)
            byline = moderator.mention if moderator else f"`{record.moderator_id}`"
            add_field(
                embed,
                f"#{record.id} • <t:{record.created_at}:R>",
                truncate(f"By {byline}\n{record.reason}", EMBED_FIELD_VALUE_MAX),
            )
        embed.set_footer(
            text=(
                f"Showing {len(warns)} of {total} total"
                if total > len(warns)
                else f"{total} warning(s) total"
            ),
        )
        await reply(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="clearwarnings",
        description="Clear all warnings for a user.",
    )
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
            await reply(interaction, f"{user.mention} had no warnings.", ephemeral=True)
            return

        embed = action_embed(
            "Warnings Cleared",
            discord.Color.green(),
            user,
            interaction.user,
            f"Cleared {cleared} warning(s).",
        )
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(name="delwarn", description="Delete a single warning by ID.")
    @app_commands.describe(warning_id="The numeric ID shown by /warnings.")
    @guild_permissions(kick_members=True)
    async def delwarn(
        self,
        interaction: discord.Interaction,
        warning_id: int,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if await self.db.remove_warning(guild.id, warning_id):
            await reply(interaction, f"✅ Removed warning #{warning_id}.", ephemeral=True)
        else:
            await fail(interaction, f"No warning with ID `{warning_id}` in this server.")


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(Moderation(bot))
