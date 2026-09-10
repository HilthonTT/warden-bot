"""Upgrading a v1 database must not lose moderation history.

The v1 schema is pinned here verbatim rather than imported, so editing the
current schema can never quietly redefine what we claim to migrate *from*.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data.db import SCHEMA_VERSION, Database
from data.models import CaseAction

SCHEMA_V1 = """
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);

CREATE TABLE guild_config (
    guild_id            INTEGER PRIMARY KEY,
    mod_log_channel_id  INTEGER,
    honeypot_channel_id INTEGER,
    staff_role_id       INTEGER,
    ticket_category_id  INTEGER,
    warn_kick_threshold INTEGER NOT NULL DEFAULT 3,
    warn_ban_threshold  INTEGER NOT NULL DEFAULT 5,
    automod_enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE warnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason       TEXT    NOT NULL,
    created_at   INTEGER NOT NULL
);

CREATE TABLE tickets (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    number     INTEGER NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'open',
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX uq_tickets_open
    ON tickets(guild_id, user_id) WHERE status = 'open';

CREATE TABLE ticket_counter (
    guild_id INTEGER PRIMARY KEY,
    last_num INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    """A v1 database holding two guilds' warnings and one open ticket."""
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_V1)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute("INSERT INTO guild_config (guild_id, mod_log_channel_id) VALUES (10, 555)")
    conn.executemany(
        "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (10, 1, 99, "first", 1_700_000_000),
            (10, 1, 99, "second", 1_700_000_100),
            (10, 2, 99, "other member", 1_700_000_200),
            (20, 1, 99, "other guild", 1_700_000_300),
        ],
    )
    conn.execute(
        "INSERT INTO tickets (channel_id, guild_id, user_id, number, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (300, 10, 1, 7, "open", 1_700_000_000),
    )
    conn.execute("INSERT INTO ticket_counter (guild_id, last_num) VALUES (10, 7)")
    conn.commit()
    conn.close()
    return path


async def upgraded(path: Path) -> Database:
    database = Database(path)
    await database.connect()
    return database


async def test_migration_stamps_the_new_version(legacy_db: Path) -> None:
    database = await upgraded(legacy_db)
    try:
        async with database.conn.execute("SELECT version FROM schema_version") as cur:
            row = await cur.fetchone()
        assert row is not None and row["version"] == SCHEMA_VERSION
    finally:
        await database.close()


async def test_warnings_become_cases_numbered_per_guild(legacy_db: Path) -> None:
    database = await upgraded(legacy_db)
    try:
        cases = await database.get_cases(10)
        assert [case.number for case in cases] == [3, 2, 1]
        assert all(case.action is CaseAction.WARN for case in cases)
        assert {case.reason for case in cases} == {"first", "second", "other member"}

        other_guild = await database.get_cases(20)
        assert [case.number for case in other_guild] == [1]
    finally:
        await database.close()


async def test_migrated_warnings_still_count(legacy_db: Path) -> None:
    database = await upgraded(legacy_db)
    try:
        assert await database.count_active_warnings(10, 1) == 2
        assert await database.count_active_warnings(10, 2) == 1
        assert await database.count_active_warnings(20, 1) == 1
    finally:
        await database.close()


async def test_the_case_counter_continues_from_migrated_rows(legacy_db: Path) -> None:
    database = await upgraded(legacy_db)
    try:
        case, _ = await database.add_case(10, CaseAction.KICK, 1, 99, "after upgrade")
        assert case.number == 4
    finally:
        await database.close()


async def test_existing_config_survives_and_gains_defaults(legacy_db: Path) -> None:
    database = await upgraded(legacy_db)
    try:
        cfg = await database.get_config(10)
        assert cfg.mod_log_channel_id == 555
        assert cfg.event_log_channel_id is None
        assert cfg.warn_expiry_days == 0
        assert cfg.antispam_enabled is False
        assert cfg.raid_join_window_seconds == 60
    finally:
        await database.close()


async def test_tickets_are_untouched(legacy_db: Path) -> None:
    database = await upgraded(legacy_db)
    try:
        ticket = await database.get_ticket(300)
        assert ticket is not None
        assert ticket.number == 7
        assert ticket.is_open
        assert await database.create_ticket(301, 10, 5) == 8
    finally:
        await database.close()


async def test_the_old_table_is_gone(legacy_db: Path) -> None:
    database = await upgraded(legacy_db)
    try:
        assert await database._table_exists("warnings") is False
        assert await database._table_exists("cases") is True
    finally:
        await database.close()


async def test_migrating_is_not_repeated_on_the_next_start(legacy_db: Path) -> None:
    first = await upgraded(legacy_db)
    await first.close()

    second = await upgraded(legacy_db)
    try:
        assert await second.count_active_warnings(10, 1) == 2
        assert await second.count_cases(10) == 3
    finally:
        await second.close()
