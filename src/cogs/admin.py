"""Per-guild configuration commands (all require Manage Server).

/set_modlog          choose the channel for moderation embeds
/set_honeypot        arm a channel as a spam-bot honeypot
/clear_honeypot      disarm the honeypot
/set_warn_thresholds tune auto-kick / auto-ban warning thresholds
/config              show the current configuration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from core.checks import guild_permissions
from core.cog import MonitorCog
from core.embeds import add_field, info_embed, success_embed
from core.responses import fail, reply

if TYPE_CHECKING:
    from core.bot import MonitorBot

HONEYPOT_ADVICE = (
    "For best results deny **View Channel** for @everyone, keep the channel out "
    "of search, and never link it. Spam bots that enumerate every readable "
    "channel through the API will still find it."
)


class Admin(MonitorCog):
    """Server configuration."""

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

        me = interaction.guild.me
        if me is not None and not channel.permissions_for(me).send_messages:
            await fail(interaction, f"I can't send messages in {channel.mention}.")
            return

        await self.config.update(interaction.guild.id, mod_log_channel_id=channel.id)
        await reply(interaction, f"✅ Mod log set to {channel.mention}", ephemeral=True)

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
        kick_threshold="Number of warnings that triggers an auto-kick.",
        ban_threshold="Number of warnings that triggers an auto-ban.",
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
        name="config",
        description="Show the current bot configuration for this server.",
    )
    @guild_permissions(manage_guild=True)
    async def show_config(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        cfg = await self.config.get(guild.id)

        def channel(channel_id: int | None) -> str:
            if channel_id is None:
                return "*not set*"
            found = guild.get_channel(channel_id)
            return found.mention if found else f"`{channel_id}` *(deleted)*"

        def role(role_id: int | None) -> str:
            if role_id is None:
                return "*not set*"
            found = guild.get_role(role_id)
            return found.mention if found else f"`{role_id}` *(deleted)*"

        embed = info_embed(f"Configuration — {guild.name}")
        add_field(embed, "Mod log", channel(cfg.mod_log_channel_id), inline=True)
        add_field(embed, "Honeypot", channel(cfg.honeypot_channel_id), inline=True)
        add_field(embed, "Staff role", role(cfg.staff_role_id), inline=True)
        add_field(embed, "Ticket category", channel(cfg.ticket_category_id), inline=True)
        add_field(embed, "Auto-kick at", f"{cfg.warn_kick_threshold} warning(s)", inline=True)
        add_field(embed, "Auto-ban at", f"{cfg.warn_ban_threshold} warning(s)", inline=True)
        add_field(
            embed,
            "Language filter",
            "enabled" if cfg.automod_enabled else "disabled",
            inline=True,
        )
        embed.set_footer(text="Staff role and ticket category are set with /ticket_config.")
        await reply(interaction, embed=embed, ephemeral=True)


async def setup(bot: MonitorBot) -> None:
    await bot.add_cog(Admin(bot))
