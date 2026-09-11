"""Channel controls: the standard first response to a raid or a pile-on.

  /lock      stop @everyone posting in a channel
  /unlock    restore posting
  /slowmode  rate-limit a channel

All three require Manage Channels and report to the mod log, because locking
a channel is a moderation action even though it targets no one in particular.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from core.checks import guild_permissions
from core.cog import WardenCog
from core.constants import AUDIT_REASON_MAX, truncate
from core.duration import DurationError, format_duration, parse_duration
from core.embeds import add_field, base_embed
from core.responses import fail, reply

if TYPE_CHECKING:
    from core.bot import WardenBot

log = logging.getLogger(__name__)

NO_REASON = "No reason provided"
SLOWMODE_MAX_SECONDS = 21_600

#: Accepted ways to ask for slowmode to be turned off; see
#: :func:`clears_slowmode`.
SLOWMODE_OFF_WORDS = frozenset({"off", "none", "clear", "0"})
ZERO_DURATION_RE = re.compile(r"^0+\s*[smhdw]?$", re.IGNORECASE)

Lockable = discord.TextChannel | discord.VoiceChannel | discord.ForumChannel


def clears_slowmode(text: str) -> bool:
    """Whether ``text`` asks for slowmode to be turned off.

    ``/slowmode`` documents 0 as "clear", so every spelling of zero has to
    mean the same thing: ``0s`` and ``0m`` used to reach
    :func:`~core.duration.parse_duration` and come back as the unhelpful
    "the duration must be longer than zero".
    """
    cleaned = text.strip()
    return cleaned.lower() in SLOWMODE_OFF_WORDS or ZERO_DURATION_RE.match(cleaned) is not None


class Channels(WardenCog):
    """Lock, unlock, and slow down channels."""

    async def _resolve(
        self, interaction: discord.Interaction, channel: Lockable | None
    ) -> Lockable | None:
        target = channel or interaction.channel
        if not isinstance(target, Lockable):
            await fail(interaction, "Pick a text, voice, or forum channel.")
            return None
        return target

    async def _set_locked(
        self,
        interaction: discord.Interaction,
        channel: Lockable,
        reason: str,
        *,
        locked: bool,
    ) -> bool:
        guild = interaction.guild
        assert guild is not None
        overwrite = channel.overwrites_for(guild.default_role)
        overwrite.update(send_messages=False if locked else None)
        try:
            await channel.set_permissions(
                guild.default_role,
                overwrite=overwrite,
                reason=truncate(f"By {interaction.user} — {reason}", AUDIT_REASON_MAX),
            )
        except discord.Forbidden:
            await fail(interaction, f"I lack permission to edit {channel.mention}.")
            return False
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return False
        return True

    @app_commands.command(name="lock", description="Stop @everyone posting in a channel.")
    @app_commands.describe(
        channel="The channel to lock. Defaults to this one.",
        reason="Why you're locking it.",
    )
    @guild_permissions(manage_channels=True)
    async def lock(
        self,
        interaction: discord.Interaction,
        channel: Lockable | None = None,
        reason: str | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        target = await self._resolve(interaction, channel)
        if target is None:
            return

        reason = reason or NO_REASON
        await interaction.response.defer(thinking=True)
        if not await self._set_locked(interaction, target, reason, locked=True):
            return

        embed = base_embed(
            "🔒 Channel locked",
            color=discord.Color.orange(),
            description=f"{target.mention} is locked. Existing members can still read it.",
        )
        add_field(embed, "Moderator", interaction.user.mention, inline=True)
        add_field(embed, "Reason", reason)
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(name="unlock", description="Let @everyone post in a channel again.")
    @app_commands.describe(
        channel="The channel to unlock. Defaults to this one.",
        reason="Why you're unlocking it.",
    )
    @guild_permissions(manage_channels=True)
    async def unlock(
        self,
        interaction: discord.Interaction,
        channel: Lockable | None = None,
        reason: str | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        target = await self._resolve(interaction, channel)
        if target is None:
            return

        reason = reason or NO_REASON
        await interaction.response.defer(thinking=True)
        if not await self._set_locked(interaction, target, reason, locked=False):
            return

        embed = base_embed(
            "🔓 Channel unlocked",
            color=discord.Color.green(),
            description=f"{target.mention} is open again.",
        )
        add_field(embed, "Moderator", interaction.user.mention, inline=True)
        add_field(embed, "Reason", reason)
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)

    @app_commands.command(name="slowmode", description="Rate-limit messages in a channel.")
    @app_commands.describe(
        delay="How long members must wait between messages, e.g. 10s, 2m. Use 0 to clear.",
        channel="The channel to change. Defaults to this one.",
    )
    @guild_permissions(manage_channels=True)
    async def slowmode(
        self,
        interaction: discord.Interaction,
        delay: str,
        channel: discord.TextChannel | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await fail(interaction, "Slowmode only applies to text channels.")
            return

        cleared = clears_slowmode(delay)
        if cleared:
            seconds = 0
        else:
            try:
                seconds = int(parse_duration(delay).total_seconds())
            except DurationError as exc:
                await fail(interaction, str(exc))
                return
            if seconds > SLOWMODE_MAX_SECONDS:
                await fail(interaction, "Discord caps slowmode at 6 hours.")
                return

        await interaction.response.defer(thinking=True)
        try:
            await target.edit(
                slowmode_delay=seconds,
                reason=truncate(f"By {interaction.user}", AUDIT_REASON_MAX),
            )
        except discord.Forbidden:
            await fail(interaction, f"I lack permission to edit {target.mention}.")
            return
        except discord.HTTPException as exc:
            await fail(interaction, f"Discord error: {exc}")
            return

        spelled = "off" if seconds == 0 else format_duration(timedelta(seconds=seconds))
        embed = base_embed(
            "🐌 Slowmode updated",
            color=discord.Color.blurple(),
            description=f"{target.mention} is now set to **{spelled}**.",
        )
        add_field(embed, "Moderator", interaction.user.mention, inline=True)
        await reply(interaction, embed=embed)
        await self.modlog.send(guild, embed)


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(Channels(bot))
