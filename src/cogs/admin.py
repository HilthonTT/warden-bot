"""Per-guild configuration commands (all require Manage Server).

/set_modlog          channel for moderation embeds and ticket transcripts
/set_eventlog        channel for message and member event logging
/set_staff_role      role exempt from automod, and granted ticket access
/set_honeypot        arm a channel as a spam-bot honeypot
/clear_honeypot      disarm the honeypot
/set_warn_thresholds tune auto-kick / auto-ban warning thresholds
/set_warn_expiry     how long a warning counts toward those thresholds
/antispam            rate limits for flooding, repetition, and mass mentions
/gatekeeper          minimum account age and raid-alert thresholds
/config              show the current configuration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from core.checks import guild_permissions
from core.cog import WardenCog
from core.embeds import add_field, info_embed, success_embed
from core.responses import fail, reply

if TYPE_CHECKING:
    from core.bot import WardenBot

HONEYPOT_ADVICE = (
    "For best results deny **View Channel** for @everyone, keep the channel out "
    "of search, and never link it. Spam bots that enumerate every readable "
    "channel through the API will still find it."
)

OFF = "*not set*"
DISABLED = "disabled"


class Admin(WardenCog):
    """Server configuration."""

    async def _require_writable(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> bool:
        """Reject a log channel the bot can't post in, rather than dropping reports."""
        guild = interaction.guild
        assert guild is not None
        me = guild.me
        if me is not None and not channel.permissions_for(me).send_messages:
            await fail(interaction, f"I can't send messages in {channel.mention}.")
            return False
        return True

    @app_commands.command(
        name="set_modlog",
        description="Set the channel where moderation actions are logged.",
    )
    @app_commands.describe(channel="The channel to use as the mod log.")
    @guild_permissions(manage_guild=True)
    async def set_modlog(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        assert interaction.guild is not None
        if not await self._require_writable(interaction, channel):
            return
        await self.config.update(interaction.guild.id, mod_log_channel_id=channel.id)
        await reply(interaction, f"✅ Mod log set to {channel.mention}", ephemeral=True)

    @app_commands.command(
        name="set_eventlog",
        description="Set the channel for message edits, deletions, joins and leaves.",
    )
    @app_commands.describe(channel="The channel to use as the event log.")
    @guild_permissions(manage_guild=True)
    async def set_eventlog(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        assert interaction.guild is not None
        if not await self._require_writable(interaction, channel):
            return
        await self.config.update(interaction.guild.id, event_log_channel_id=channel.id)
        await reply(
            interaction,
            f"✅ Event log set to {channel.mention}. Message edits and deletions, "
            f"joins and leaves will be recorded there.",
            ephemeral=True,
        )

    @app_commands.command(
        name="set_staff_role",
        description="Set the role exempt from automod and granted ticket access.",
    )
    @app_commands.describe(role="The staff role.")
    @guild_permissions(manage_guild=True)
    async def set_staff_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        assert interaction.guild is not None
        await self.config.update(interaction.guild.id, staff_role_id=role.id)
        await reply(
            interaction,
            f"✅ **@{role.name}** is now the staff role: exempt from the language "
            f"filter, the spam filter and honeypot bans, and granted access to "
            f"new tickets.",
            ephemeral=True,
        )

    @app_commands.command(
        name="set_honeypot",
        description="Arm a channel as a honeypot — anyone who posts there is auto-banned.",
    )
    @app_commands.describe(channel="The honeypot channel.")
    @guild_permissions(manage_guild=True)
    async def set_honeypot(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        assert interaction.guild is not None
        await self.config.update(interaction.guild.id, honeypot_channel_id=channel.id)
        await reply(
            interaction,
            embed=success_embed(
                f"🍯 Honeypot armed on #{channel.name}",
                f"{HONEYPOT_ADVICE}\n\nModerators and the configured staff role are exempt.",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="clear_honeypot",
        description="Disarm the honeypot for this server.",
    )
    @guild_permissions(manage_guild=True)
    async def clear_honeypot(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.config.update(interaction.guild.id, honeypot_channel_id=None)
        await reply(interaction, "✅ Honeypot disarmed.", ephemeral=True)

    @app_commands.command(
        name="set_warn_thresholds",
        description="Set the auto-kick and auto-ban warning thresholds.",
    )
    @app_commands.describe(
        kick_threshold="Number of active warnings that triggers an auto-kick.",
        ban_threshold="Number of active warnings that triggers an auto-ban.",
    )
    @guild_permissions(manage_guild=True)
    async def set_warn_thresholds(
        self,
        interaction: discord.Interaction,
        kick_threshold: app_commands.Range[int, 1, 100],
        ban_threshold: app_commands.Range[int, 1, 100],
    ) -> None:
        if ban_threshold < kick_threshold:
            await fail(interaction, "Ban threshold must be ≥ kick threshold.")
            return

        assert interaction.guild is not None
        await self.config.update(
            interaction.guild.id,
            warn_kick_threshold=kick_threshold,
            warn_ban_threshold=ban_threshold,
        )
        await reply(
            interaction,
            f"✅ Auto-kick at **{kick_threshold}** warning(s); auto-ban at **{ban_threshold}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="set_warn_expiry",
        description="How many days a warning counts toward the thresholds.",
    )
    @app_commands.describe(days="Days before a warning stops counting. 0 means never.")
    @guild_permissions(manage_guild=True)
    async def set_warn_expiry(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 0, 3650],
    ) -> None:
        assert interaction.guild is not None
        await self.config.update(interaction.guild.id, warn_expiry_days=days)
        if days == 0:
            await reply(
                interaction,
                "✅ Warnings never expire. Every warning a member has ever "
                "received counts toward auto-kick and auto-ban.",
                ephemeral=True,
            )
            return
        await reply(
            interaction,
            f"✅ Warnings stop counting after **{days}** day(s). Existing warnings "
            f"keep the expiry they were given when they were issued.",
            ephemeral=True,
        )

    @app_commands.command(
        name="antispam",
        description="Configure the rate-based spam filter.",
    )
    @app_commands.describe(
        enabled="Turn the spam filter on or off.",
        messages="Messages within the window that count as flooding.",
        seconds="Length of the flood window, in seconds.",
        mentions="Mentions in one message that count as mass-mentioning.",
        block_invites="Delete and warn on Discord invite links.",
    )
    @guild_permissions(manage_guild=True)
    async def antispam(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        messages: app_commands.Range[int, 2, 50] | None = None,
        seconds: app_commands.Range[int, 1, 60] | None = None,
        mentions: app_commands.Range[int, 1, 50] | None = None,
        block_invites: bool | None = None,
    ) -> None:
        assert interaction.guild is not None
        fields: dict[str, object] = {"antispam_enabled": enabled}
        if messages is not None:
            fields["antispam_message_limit"] = messages
        if seconds is not None:
            fields["antispam_window_seconds"] = seconds
        if mentions is not None:
            fields["antispam_mention_limit"] = mentions
        if block_invites is not None:
            fields["invite_filter_enabled"] = block_invites

        cfg = await self.config.update(interaction.guild.id, **fields)
        await reply(
            interaction,
            embed=success_embed(
                f"Spam filter {'enabled' if cfg.antispam_enabled else 'disabled'}",
                f"**Flood:** {cfg.antispam_message_limit} messages in "
                f"{cfg.antispam_window_seconds}s\n"
                f"**Mass mentions:** more than {cfg.antispam_mention_limit} per message\n"
                f"**Invite links:** "
                f"{'blocked' if cfg.invite_filter_enabled else 'allowed'}\n\n"
                f"A hit deletes the message and warns the member, so it counts "
                f"toward auto-kick and auto-ban.",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="gatekeeper",
        description="Set the minimum account age and raid-alert threshold.",
    )
    @app_commands.describe(
        min_account_age_hours="Kick accounts younger than this on join. 0 disables it.",
        raid_joins="Joins within the window that trigger a raid alert. 0 disables it.",
        raid_seconds="Length of the join window, in seconds.",
    )
    @guild_permissions(manage_guild=True)
    async def gatekeeper(
        self,
        interaction: discord.Interaction,
        min_account_age_hours: app_commands.Range[int, 0, 8760] | None = None,
        raid_joins: app_commands.Range[int, 0, 100] | None = None,
        raid_seconds: app_commands.Range[int, 5, 3600] | None = None,
    ) -> None:
        assert interaction.guild is not None
        fields: dict[str, object] = {}
        if min_account_age_hours is not None:
            fields["min_account_age_hours"] = min_account_age_hours
        if raid_joins is not None:
            fields["raid_join_threshold"] = raid_joins
        if raid_seconds is not None:
            fields["raid_join_window_seconds"] = raid_seconds

        if not fields:
            await fail(interaction, "Give at least one setting to change.")
            return

        cfg = await self.config.update(interaction.guild.id, **fields)
        age = f"{cfg.min_account_age_hours}h" if cfg.min_account_age_hours > 0 else DISABLED
        raid = (
            f"{cfg.raid_join_threshold} joins / {cfg.raid_join_window_seconds}s"
            if cfg.raid_join_threshold > 0
            else DISABLED
        )
        await reply(
            interaction,
            embed=success_embed(
                "Gatekeeper updated",
                f"**Minimum account age:** {age}\n**Raid alert:** {raid}\n\n"
                f"Account-age kicks are skipped for anyone with a moderation "
                f"permission or the staff role.",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="config",
        description="Show the current bot configuration for this server.",
    )
    @guild_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        cfg = await self.config.get(guild.id)
        custom_words = len(await self.words.custom_words(guild.id))

        def channel(channel_id: int | None) -> str:
            if channel_id is None:
                return OFF
            found = guild.get_channel(channel_id)
            return found.mention if found else f"`{channel_id}` *(deleted)*"

        def role(role_id: int | None) -> str:
            if role_id is None:
                return OFF
            found = guild.get_role(role_id)
            return found.mention if found else f"`{role_id}` *(deleted)*"

        embed = info_embed(f"Configuration — {guild.name}")
        add_field(embed, "Mod log", channel(cfg.mod_log_channel_id), inline=True)
        add_field(embed, "Event log", channel(cfg.event_log_channel_id), inline=True)
        add_field(embed, "Staff role", role(cfg.staff_role_id), inline=True)
        add_field(embed, "Honeypot", channel(cfg.honeypot_channel_id), inline=True)
        add_field(embed, "Ticket category", channel(cfg.ticket_category_id), inline=True)
        add_field(
            embed,
            "Warnings",
            f"kick at {cfg.warn_kick_threshold}, ban at {cfg.warn_ban_threshold}\n"
            + (f"expire after {cfg.warn_expiry_days}d" if cfg.warn_expiry_days else "never expire"),
            inline=True,
        )
        add_field(
            embed,
            "Language filter",
            f"{'enabled' if cfg.automod_enabled else DISABLED}\n{custom_words} custom word(s)",
            inline=True,
        )
        add_field(
            embed,
            "Spam filter",
            DISABLED
            if not cfg.antispam_enabled
            else (
                f"{cfg.antispam_message_limit}/{cfg.antispam_window_seconds}s, "
                f"{cfg.antispam_mention_limit} mentions\n"
                f"invites {'blocked' if cfg.invite_filter_enabled else 'allowed'}"
            ),
            inline=True,
        )
        add_field(
            embed,
            "Gatekeeper",
            f"min age: "
            f"{f'{cfg.min_account_age_hours}h' if cfg.min_account_age_hours else DISABLED}\n"
            f"raid alert: "
            + (
                f"{cfg.raid_join_threshold}/{cfg.raid_join_window_seconds}s"
                if cfg.raid_join_threshold
                else DISABLED
            ),
            inline=True,
        )
        embed.set_footer(text="Ticket category and staff role are also set by /ticket_config.")
        await reply(interaction, embed=embed, ephemeral=True)


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(Admin(bot))
