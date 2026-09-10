from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite
import pytest

from data.db import Database
from data.models import CaseAction, GuildConfig

GUILD = 1
USER = 42
MOD = 7


async def warn(db: Database, *, guild: int = GUILD, user: int = USER, expires_at=None):
    return await db.add_case(guild, CaseAction.WARN, user, MOD, "reason", expires_at=expires_at)


async def test_get_config_materialises_defaults(db: Database) -> None:
    cfg = await db.get_config(GUILD)

    assert cfg == GuildConfig(guild_id=GUILD)
    assert (await db.get_config(GUILD)).warn_kick_threshold == 3


async def test_upsert_config_round_trips_every_field(db: Database) -> None:
    cfg = await db.get_config(GUILD)
    cfg.mod_log_channel_id = 99
    cfg.event_log_channel_id = 100
    cfg.automod_enabled = False
    cfg.antispam_enabled = True
    cfg.warn_expiry_days = 30
    cfg.min_account_age_hours = 12
    await db.upsert_config(cfg)

    stored = await db.get_config(GUILD)
    assert stored.mod_log_channel_id == 99
    assert stored.event_log_channel_id == 100
    assert stored.automod_enabled is False
    assert stored.antispam_enabled is True
    assert stored.warn_expiry_days == 30
    assert stored.min_account_age_hours == 12


async def test_case_numbers_increment_per_guild(db: Database) -> None:
    first, _ = await warn(db)
    second, _ = await db.add_case(GUILD, CaseAction.KICK, USER, MOD, "out")
    elsewhere, _ = await warn(db, guild=GUILD + 1)

    assert (first.number, second.number, elsewhere.number) == (1, 2, 1)


async def test_add_case_returns_the_running_warning_count(db: Database) -> None:
    _, first = await warn(db)
    _, second = await warn(db)
    _, after_kick = await db.add_case(GUILD, CaseAction.KICK, USER, MOD, "out")

    assert (first, second) == (1, 2)
    assert after_kick == 2, "a kick is not a warning"


async def test_warning_counts_are_scoped_per_guild(db: Database) -> None:
    await warn(db)
    _, count = await warn(db, guild=GUILD + 1)

    assert count == 1
    assert await db.count_active_warnings(GUILD, USER) == 1


async def test_concurrent_warnings_never_report_the_same_total(db: Database) -> None:
    results = await asyncio.gather(*(warn(db) for _ in range(10)))

    assert sorted(count for _, count in results) == list(range(1, 11))
    assert await db.count_active_warnings(GUILD, USER) == 10


async def test_expired_warnings_stop_counting(db: Database) -> None:
    past = int(time.time()) - 10
    future = int(time.time()) + 600
    await warn(db, expires_at=past)
    await warn(db, expires_at=future)
    await warn(db)

    assert await db.count_active_warnings(GUILD, USER) == 2
    assert await db.count_cases(GUILD, user_id=USER, action=CaseAction.WARN) == 3


async def test_expired_warning_is_reported_as_expired(db: Database) -> None:
    case, _ = await warn(db, expires_at=int(time.time()) - 1)

    assert case.is_expired
    assert not case.counts_toward_thresholds


async def test_get_cases_is_newest_first_and_pageable(db: Database) -> None:
    for _ in range(5):
        await warn(db)

    page = await db.get_cases(GUILD, user_id=USER, limit=2)
    assert [case.number for case in page] == [5, 4]

    second = await db.get_cases(GUILD, user_id=USER, limit=2, offset=2)
    assert [case.number for case in second] == [3, 2]


async def test_cases_filter_by_action(db: Database) -> None:
    await warn(db)
    await db.add_case(GUILD, CaseAction.BAN, USER, MOD, "out")

    bans = await db.get_cases(GUILD, user_id=USER, action=CaseAction.BAN)
    assert [case.action for case in bans] == [CaseAction.BAN]


async def test_deactivate_case_is_idempotent(db: Database) -> None:
    case, _ = await warn(db)

    assert (await db.deactivate_case(GUILD, case.number)) is not None
    assert (await db.deactivate_case(GUILD, case.number)) is None
    assert await db.count_active_warnings(GUILD, USER) == 0


async def test_deactivating_is_scoped_to_the_guild(db: Database) -> None:
    case, _ = await warn(db)

    assert (await db.deactivate_case(GUILD + 1, case.number)) is None
    assert await db.count_active_warnings(GUILD, USER) == 1


async def test_clear_warnings_leaves_other_actions_alone(db: Database) -> None:
    await warn(db)
    await warn(db)
    ban, _ = await db.add_case(GUILD, CaseAction.BAN, USER, MOD, "out")

    assert await db.clear_warnings(GUILD, USER) == 2
    assert await db.count_active_warnings(GUILD, USER) == 0

    stored = await db.get_case(GUILD, ban.number)
    assert stored is not None and stored.active


async def test_due_cases_returns_only_elapsed_tempbans(db: Database) -> None:
    now = int(time.time())
    elapsed, _ = await db.add_case(GUILD, CaseAction.TEMPBAN, USER, MOD, "spam", expires_at=now - 5)
    await db.add_case(GUILD, CaseAction.TEMPBAN, USER + 1, MOD, "spam", expires_at=now + 600)
    await db.add_case(GUILD, CaseAction.BAN, USER + 2, MOD, "permanent")

    due = await db.due_cases(CaseAction.TEMPBAN)
    assert [case.number for case in due] == [elapsed.number]


async def test_a_lifted_tempban_is_no_longer_due(db: Database) -> None:
    case, _ = await db.add_case(
        GUILD, CaseAction.TEMPBAN, USER, MOD, "spam", expires_at=int(time.time()) - 5
    )
    await db.deactivate_case(GUILD, case.number)

    assert await db.due_cases(CaseAction.TEMPBAN) == []


async def test_guild_words_round_trip(db: Database) -> None:
    assert await db.add_words(GUILD, ["Badger", "badger", " ", "stoat"]) == 2
    assert await db.get_words(GUILD) == ["badger", "stoat"]

    assert await db.add_words(GUILD, ["badger"]) == 0
    assert await db.remove_word(GUILD, "BADGER") is True
    assert await db.remove_word(GUILD, "badger") is False
    assert await db.get_words(GUILD) == ["stoat"]


async def test_guild_words_are_scoped_per_guild(db: Database) -> None:
    await db.add_words(GUILD, ["badger"])
    await db.add_words(GUILD + 1, ["stoat"])

    assert await db.get_words(GUILD) == ["badger"]
    assert await db.clear_words(GUILD) == 1
    assert await db.get_words(GUILD + 1) == ["stoat"]


async def test_ticket_numbers_increment_per_guild(db: Database) -> None:
    assert await db.create_ticket(100, GUILD, USER) == 1
    await db.close_ticket(100)
    assert await db.create_ticket(101, GUILD, USER) == 2
    assert await db.create_ticket(200, GUILD + 1, USER) == 1


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


async def test_connecting_twice_keeps_the_data(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    first = Database(path)
    await first.connect()
    await warn(first)
    await first.close()

    second = Database(path)
    await second.connect()
    try:
        assert await second.count_active_warnings(GUILD, USER) == 1
    finally:
        await second.close()


async def test_using_the_database_before_connect_is_an_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "bot.sqlite3")

    with pytest.raises(RuntimeError, match="not connected"):
        _ = database.conn
