"""Cached access to per-guild configuration.

Automod reads the config for *every* message, so it cannot go to SQLite each
time. The cache used to live inside the AutoMod cog, which meant any cog that
wrote config had to know AutoMod existed and call its ``invalidate`` by name.
Ownership now sits here: writes go through :meth:`update`, which refreshes the
cache as part of the write, so no invalidation call can be forgotten.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from data.db import Database
from data.models import GuildConfig

log = logging.getLogger(__name__)


class GuildConfigService:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: dict[int, GuildConfig] = {}

    async def get(self, guild_id: int) -> GuildConfig:
        """Return the guild's config, reading through the cache."""
        cached = self._cache.get(guild_id)
        if cached is not None:
            return dataclasses.replace(cached)

        cfg = await self._db.get_config(guild_id)
        self._cache[guild_id] = cfg
        return dataclasses.replace(cfg)

    async def update(self, guild_id: int, **fields: Any) -> GuildConfig:
        """Persist ``fields`` for a guild and return the new config.

        Raises:
            TypeError: if a field name isn't part of :class:`GuildConfig`.
        """
        known = {f.name for f in dataclasses.fields(GuildConfig)}
        unknown = set(fields) - known
        if unknown:
            raise TypeError(f"Unknown guild config field(s): {', '.join(sorted(unknown))}")

        cfg = await self.get(guild_id)
        updated = dataclasses.replace(cfg, **fields)
        await self._db.upsert_config(updated)
        self._cache[guild_id] = updated
        return dataclasses.replace(updated)

    def invalidate(self, guild_id: int) -> None:
        """Drop a guild's cached config (used when the bot leaves a guild)."""
        self._cache.pop(guild_id, None)

    def clear(self) -> None:
        self._cache.clear()
