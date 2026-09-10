"""Delivery of moderation reports to a guild's configured mod-log channel.

Three cogs posted to the mod log with three slightly different copies of the
same "look up config, resolve channel, swallow HTTP errors" block. One of
them logged at WARNING and another at DEBUG for the same failure.
"""

from __future__ import annotations

import logging

import discord

from .guild_config import GuildConfigService

log = logging.getLogger(__name__)


class ModLogService:
    def __init__(self, config: GuildConfigService) -> None:
        self._config = config

    async def channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Resolve the guild's mod-log channel, or None if unset/deleted."""
        cfg = await self._config.get(guild.id)
        if cfg.mod_log_channel_id is None:
            return None
        chan = guild.get_channel(cfg.mod_log_channel_id)
        if not isinstance(chan, discord.TextChannel):
            log.debug(
                "Mod log channel %s for guild %s is missing or not a text channel",
                cfg.mod_log_channel_id,
                guild.id,
            )
            return None
        return chan

    async def send(
        self,
        guild: discord.Guild,
        embed: discord.Embed,
        *,
        file: discord.File | None = None,
    ) -> bool:
        """Post to the mod log.

        Returns:
            True if the message was delivered. Callers that must not lose the
            payload (ticket transcripts) branch on this.
        """
        chan = await self.channel(guild)
        if chan is None:
            return False
        try:
            if file is not None:
                await chan.send(embed=embed, file=file)
            else:
                await chan.send(embed=embed)
        except discord.Forbidden:
            log.warning(
                "Missing permission to post in mod log %s (guild %s)",
                chan.id,
                guild.id,
            )
            return False
        except discord.HTTPException:
            log.warning("Failed to post mod log to %s", chan.id, exc_info=True)
            return False
        return True
