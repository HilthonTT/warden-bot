from __future__ import annotations

import pytest

from data.db import Database
from data.models import GuildConfig
from services.guild_config import GuildConfigService

GUILD = 1


async def test_get_returns_defaults_for_an_unknown_guild(db: Database) -> None:
    service = GuildConfigService(db)

    assert await service.get(GUILD) == GuildConfig(guild_id=GUILD)


async def test_reads_are_served_from_cache(db: Database, monkeypatch) -> None:
    service = GuildConfigService(db)
    await service.get(GUILD)

    async def explode(_: int) -> GuildConfig:
        raise AssertionError("the cache should have answered this")

    monkeypatch.setattr(db, "get_config", explode)
    assert (await service.get(GUILD)).guild_id == GUILD


async def test_update_persists_and_refreshes_the_cache(db: Database) -> None:
    service = GuildConfigService(db)

    updated = await service.update(GUILD, mod_log_channel_id=99, automod_enabled=False)

    assert updated.mod_log_channel_id == 99
    assert (await service.get(GUILD)).automod_enabled is False
    assert (await db.get_config(GUILD)).mod_log_channel_id == 99


async def test_mutating_a_returned_config_does_not_change_the_cache(db: Database) -> None:
    service = GuildConfigService(db)

    cfg = await service.get(GUILD)
    cfg.mod_log_channel_id = 12345

    assert (await service.get(GUILD)).mod_log_channel_id is None


async def test_unknown_field_names_are_rejected(db: Database) -> None:
    service = GuildConfigService(db)

    with pytest.raises(TypeError, match="nonsense"):
        await service.update(GUILD, nonsense=1)


async def test_invalidate_forces_a_reread(db: Database) -> None:
    service = GuildConfigService(db)
    await service.get(GUILD)

    cfg = await db.get_config(GUILD)
    cfg.warn_kick_threshold = 9
    await db.upsert_config(cfg)

    assert (await service.get(GUILD)).warn_kick_threshold == 3
    service.invalidate(GUILD)
    assert (await service.get(GUILD)).warn_kick_threshold == 9
