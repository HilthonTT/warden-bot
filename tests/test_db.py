from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from data.db import Database
from data.models import GuildConfig

GUILD = 1
USER = 42
MOD = 7


async def test_get_config_materialises_defaults(db: Database) -> None:
    cfg = await db.get_config(GUILD)

    assert cfg == GuildConfig(guild_id=GUILD)
    assert (await db.get_config(GUILD)).warn_kick_threshold == 3


async def test_upsert_config_round_trips(db: Database) -> None:
    cfg = await db.get_config(GUILD)
    cfg.mod_log_channel_id = 99
    cfg.automod_enabled = False
    await db.upsert_config(cfg)

    stored = await db.get_config(GUILD)
    assert stored.mod_log_channel_id == 99
    assert stored.automod_enabled is False


async def test_add_warning_returns_id_and_running_total(db: Database) -> None:
    first_id, first_count = await db.add_warning(GUILD, USER, MOD, "one")
    second_id, second_count = await db.add_warning(GUILD, USER, MOD, "two")

    assert (first_count, second_count) == (1, 2)
    assert second_id > first_id


async def test_warning_counts_are_scoped_per_guild(db: Database) -> None:
    await db.add_warning(GUILD, USER, MOD, "here")
    _, count = await db.add_warning(GUILD + 1, USER, MOD, "elsewhere")

    assert count == 1
    assert await db.count_warnings(GUILD, USER) == 1


async def test_concurrent_warnings_never_report_the_same_total(db: Database) -> None:
    results = await asyncio.gather(
        *(db.add_warning(GUILD, USER, MOD, f"warn {i}") for i in range(10)),
    )

    assert sorted(count for _, count in results) == list(range(1, 11))
    assert await db.count_warnings(GUILD, USER) == 10


async def test_get_warnings_is_newest_first_and_limited(db: Database) -> None:
    for index in range(5):
        await db.add_warning(GUILD, USER, MOD, f"reason {index}")

    page = await db.get_warnings(GUILD, USER, limit=2)
    assert len(page) == 2
    assert page[0].reason == "reason 4"


async def test_remove_and_clear_warnings(db: Database) -> None:
    warning_id, _ = await db.add_warning(GUILD, USER, MOD, "one")
    await db.add_warning(GUILD, USER, MOD, "two")

    assert await db.remove_warning(GUILD, warning_id) is True
    assert await db.remove_warning(GUILD, warning_id) is False
    assert await db.remove_warning(GUILD + 1, warning_id) is False
    assert await db.clear_warnings(GUILD, USER) == 1
    assert await db.count_warnings(GUILD, USER) == 0


async def test_ticket_numbers_increment_per_guild(db: Database) -> None:
    assert await db.create_ticket(100, GUILD, USER) == 1
    await db.close_ticket(100)
    assert await db.create_ticket(101, GUILD, USER) == 2
    assert await db.create_ticket(200, GUILD + 1, USER) == 1


async def test_ticket_numbers_do_not_reuse_after_close(db: Database) -> None:
    await db.create_ticket(100, GUILD, USER)
    await db.close_ticket(100)
    await db.create_ticket(101, GUILD, USER)
    await db.close_ticket(101)

    assert await db.create_ticket(102, GUILD, USER) == 3


async def test_only_one_open_ticket_per_user(db: Database) -> None:
    await db.create_ticket(100, GUILD, USER)

    with pytest.raises(aiosqlite.IntegrityError):
        await db.create_ticket(101, GUILD, USER)

    assert await db.get_open_ticket(GUILD, USER) == 100


async def test_failed_ticket_insert_rolls_back_the_counter(db: Database) -> None:
    await db.create_ticket(100, GUILD, USER)
    with pytest.raises(aiosqlite.IntegrityError):
        await db.create_ticket(101, GUILD, USER)

    await db.close_ticket(100)
    assert await db.create_ticket(102, GUILD, USER) == 2


async def test_close_ticket_reports_whether_it_changed_anything(db: Database) -> None:
    await db.create_ticket(100, GUILD, USER)

    assert await db.close_ticket(100) is True
    assert await db.close_ticket(100) is False
    assert await db.close_ticket(999) is False
    assert await db.get_open_ticket(GUILD, USER) is None


async def test_get_ticket_returns_a_typed_record(db: Database) -> None:
    await db.create_ticket(100, GUILD, USER)

    ticket = await db.get_ticket(100)
    assert ticket is not None
    assert ticket.user_id == USER
    assert ticket.is_open
    assert ticket.label == "0001"
    assert await db.get_ticket(404) is None


async def test_connect_creates_missing_directories(tmp_path: Path) -> None:
    database = Database(tmp_path / "nested" / "deeper" / "bot.sqlite3")
    await database.connect()
    try:
        assert database.path.exists()
    finally:
        await database.close()


async def test_using_the_database_before_connect_is_an_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "bot.sqlite3")

    with pytest.raises(RuntimeError, match="not connected"):
        _ = database.conn
