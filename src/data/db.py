"""Async SQLite storage.

One connection is shared by the whole bot — discord.py runs everything on a
single event loop, so there is no thread-safety problem, but coroutines *do*
interleave at every ``await``. The connection therefore runs in autocommit
mode (``isolation_level=None``) and every write goes through
:meth:`Database.transaction`, which holds an :class:`asyncio.Lock`. Without
that, one coroutine's ``commit()`` could land in the middle of another's
multi-statement transaction and commit it half-finished.

WAL journalling plus ``busy_timeout`` keep the file usable if an operator
opens it with the ``sqlite3`` CLI while the bot is running.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import aiosqlite

from .models import TICKET_STATUS_CLOSED, Case, CaseAction, GuildConfig, Ticket

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id                 INTEGER PRIMARY KEY,
    mod_log_channel_id       INTEGER,
    event_log_channel_id     INTEGER,
    honeypot_channel_id      INTEGER,
    staff_role_id            INTEGER,
    ticket_category_id       INTEGER,
    warn_kick_threshold      INTEGER NOT NULL DEFAULT 3,
    warn_ban_threshold       INTEGER NOT NULL DEFAULT 5,
    warn_expiry_days         INTEGER NOT NULL DEFAULT 0,
    automod_enabled          INTEGER NOT NULL DEFAULT 1,
    antispam_enabled         INTEGER NOT NULL DEFAULT 0,
    antispam_message_limit   INTEGER NOT NULL DEFAULT 5,
    antispam_window_seconds  INTEGER NOT NULL DEFAULT 5,
    antispam_mention_limit   INTEGER NOT NULL DEFAULT 5,
    invite_filter_enabled    INTEGER NOT NULL DEFAULT 0,
    min_account_age_hours    INTEGER NOT NULL DEFAULT 0,
    raid_join_threshold      INTEGER NOT NULL DEFAULT 0,
    raid_join_window_seconds INTEGER NOT NULL DEFAULT 60
);

CREATE TABLE IF NOT EXISTS cases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    number       INTEGER NOT NULL,
    action       TEXT    NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason       TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER,
    active       INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cases_number ON cases(guild_id, number);
CREATE INDEX IF NOT EXISTS idx_cases_user ON cases(guild_id, user_id, action);
CREATE INDEX IF NOT EXISTS idx_cases_expiry ON cases(action, active, expires_at);

CREATE TABLE IF NOT EXISTS case_counter (
    guild_id INTEGER PRIMARY KEY,
    last_num INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS guild_words (
    guild_id INTEGER NOT NULL,
    word     TEXT    NOT NULL,
    PRIMARY KEY (guild_id, word)
);

CREATE TABLE IF NOT EXISTS tickets (
    channel_id   INTEGER PRIMARY KEY,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    number       INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'open',
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(guild_id, user_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tickets_open
    ON tickets(guild_id, user_id) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS ticket_counter (
    guild_id INTEGER PRIMARY KEY,
    last_num INTEGER NOT NULL DEFAULT 0
);
"""

MIGRATION_002_CASES = """
ALTER TABLE guild_config ADD COLUMN event_log_channel_id INTEGER;
ALTER TABLE guild_config ADD COLUMN warn_expiry_days INTEGER NOT NULL DEFAULT 0;
ALTER TABLE guild_config ADD COLUMN antispam_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE guild_config ADD COLUMN antispam_message_limit INTEGER NOT NULL DEFAULT 5;
ALTER TABLE guild_config ADD COLUMN antispam_window_seconds INTEGER NOT NULL DEFAULT 5;
ALTER TABLE guild_config ADD COLUMN antispam_mention_limit INTEGER NOT NULL DEFAULT 5;
ALTER TABLE guild_config ADD COLUMN invite_filter_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE guild_config ADD COLUMN min_account_age_hours INTEGER NOT NULL DEFAULT 0;
ALTER TABLE guild_config ADD COLUMN raid_join_threshold INTEGER NOT NULL DEFAULT 0;
ALTER TABLE guild_config ADD COLUMN raid_join_window_seconds INTEGER NOT NULL DEFAULT 60;

INSERT INTO cases (
    guild_id, number, action, user_id, moderator_id, reason, created_at, active
)
SELECT
    guild_id,
    ROW_NUMBER() OVER (PARTITION BY guild_id ORDER BY id),
    'warn',
    user_id,
    moderator_id,
    reason,
    created_at,
    1
FROM warnings;

INSERT INTO case_counter (guild_id, last_num)
SELECT guild_id, COUNT(*) FROM cases GROUP BY guild_id
ON CONFLICT(guild_id) DO UPDATE SET last_num = excluded.last_num;

DROP TABLE warnings;
"""

MIGRATIONS: tuple[tuple[int, str], ...] = ((2, MIGRATION_002_CASES),)


class Database:
    """Thin, typed repository over the bot's SQLite file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the connection, apply pragmas, and bring the schema up to date."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row

        for pragma in (
            "journal_mode=WAL",
            "synchronous=NORMAL",
            "busy_timeout=5000",
            "foreign_keys=ON",
        ):
            await self._conn.execute(f"PRAGMA {pragma};")

        fresh = not await self._table_exists("guild_config")
        await self._conn.executescript(BASE_SCHEMA)
        await self._migrate(fresh=fresh)
        log.info("Database ready at %s (schema v%d)", self.path, SCHEMA_VERSION)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._conn

    async def _table_exists(self, name: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ) as cur:
            return await cur.fetchone() is not None

    async def _migrate(self, *, fresh: bool) -> None:
        """Bring an existing database up to :data:`SCHEMA_VERSION`.

        A freshly created file already has the current schema from
        ``BASE_SCHEMA``, so it is stamped rather than migrated — replaying an
        ``ALTER TABLE ADD COLUMN`` against it would fail.
        """
        if fresh:
            await self._stamp_version()
            return

        async with self.conn.execute("SELECT version FROM schema_version LIMIT 1") as cur:
            row = await cur.fetchone()
        current = int(row["version"]) if row else 0

        for version, statement in MIGRATIONS:
            if version > current:
                log.info("Applying migration to schema v%d", version)
                await self.conn.executescript(statement)

        if current != SCHEMA_VERSION:
            await self._stamp_version()

    async def _stamp_version(self) -> None:
        async with self.transaction() as conn:
            await conn.execute("DELETE FROM schema_version;")
            await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Serialise a write transaction against every other writer.

        ``BEGIN IMMEDIATE`` takes the write lock up front so a reader can't
        upgrade mid-transaction and deadlock against a concurrent writer.
        """
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE;")
            try:
                yield self.conn
            except BaseException:
                await self.conn.rollback()
                raise
            await self.conn.commit()

    async def get_config(self, guild_id: int) -> GuildConfig:
        """Return the guild's config, materialising defaults on first use."""
        async with self.conn.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            cfg = GuildConfig(guild_id=guild_id)
            await self.upsert_config(cfg)
            return cfg
        return GuildConfig.from_row(row)

    async def upsert_config(self, cfg: GuildConfig) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO guild_config (
                    guild_id, mod_log_channel_id, event_log_channel_id,
                    honeypot_channel_id, staff_role_id, ticket_category_id,
                    warn_kick_threshold, warn_ban_threshold, warn_expiry_days,
                    automod_enabled, antispam_enabled, antispam_message_limit,
                    antispam_window_seconds, antispam_mention_limit,
                    invite_filter_enabled, min_account_age_hours,
                    raid_join_threshold, raid_join_window_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    mod_log_channel_id       = excluded.mod_log_channel_id,
                    event_log_channel_id     = excluded.event_log_channel_id,
                    honeypot_channel_id      = excluded.honeypot_channel_id,
                    staff_role_id            = excluded.staff_role_id,
                    ticket_category_id       = excluded.ticket_category_id,
                    warn_kick_threshold      = excluded.warn_kick_threshold,
                    warn_ban_threshold       = excluded.warn_ban_threshold,
                    warn_expiry_days         = excluded.warn_expiry_days,
                    automod_enabled          = excluded.automod_enabled,
                    antispam_enabled         = excluded.antispam_enabled,
                    antispam_message_limit   = excluded.antispam_message_limit,
                    antispam_window_seconds  = excluded.antispam_window_seconds,
                    antispam_mention_limit   = excluded.antispam_mention_limit,
                    invite_filter_enabled    = excluded.invite_filter_enabled,
                    min_account_age_hours    = excluded.min_account_age_hours,
                    raid_join_threshold      = excluded.raid_join_threshold,
                    raid_join_window_seconds = excluded.raid_join_window_seconds
                """,
                (
                    cfg.guild_id,
                    cfg.mod_log_channel_id,
                    cfg.event_log_channel_id,
                    cfg.honeypot_channel_id,
                    cfg.staff_role_id,
                    cfg.ticket_category_id,
                    cfg.warn_kick_threshold,
                    cfg.warn_ban_threshold,
                    cfg.warn_expiry_days,
                    int(cfg.automod_enabled),
                    int(cfg.antispam_enabled),
                    cfg.antispam_message_limit,
                    cfg.antispam_window_seconds,
                    cfg.antispam_mention_limit,
                    int(cfg.invite_filter_enabled),
                    cfg.min_account_age_hours,
                    cfg.raid_join_threshold,
                    cfg.raid_join_window_seconds,
                ),
            )

    async def add_case(
        self,
        guild_id: int,
        action: CaseAction,
        user_id: int,
        moderator_id: int,
        reason: str,
        *,
        expires_at: int | None = None,
    ) -> tuple[Case, int]:
        """Record a moderation action against a per-guild case number.

        Returns:
            ``(case, active_warnings)``. The warning count is read inside the
            same transaction as the insert, so two concurrent warns can't both
            report the same total and skip an escalation threshold.
        """
        now = int(time.time())
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO case_counter (guild_id, last_num) VALUES (?, 1) "
                "ON CONFLICT(guild_id) DO UPDATE SET last_num = last_num + 1",
                (guild_id,),
            )
            async with conn.execute(
                "SELECT last_num FROM case_counter WHERE guild_id = ?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
            number = int(row["last_num"]) if row else 1

            async with conn.execute(
                "INSERT INTO cases ("
                "guild_id, number, action, user_id, moderator_id, reason, "
                "created_at, expires_at, active"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    guild_id,
                    number,
                    str(action),
                    user_id,
                    moderator_id,
                    reason,
                    now,
                    expires_at,
                ),
            ) as cur:
                case_id = cur.lastrowid or 0

            async with conn.execute(
                "SELECT COUNT(*) AS n FROM cases WHERE guild_id = ? AND user_id = ? "
                "AND action = 'warn' AND active = 1 "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (guild_id, user_id, now),
            ) as cur:
                count_row = await cur.fetchone()

        case = Case(
            id=case_id,
            guild_id=guild_id,
            number=number,
            action=action,
            user_id=user_id,
            moderator_id=moderator_id,
            reason=reason,
            created_at=now,
            expires_at=expires_at,
            active=True,
        )
        return case, (int(count_row["n"]) if count_row else 0)

    async def count_active_warnings(self, guild_id: int, user_id: int) -> int:
        """Warnings that still count toward auto-kick / auto-ban thresholds."""
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM cases WHERE guild_id = ? AND user_id = ? "
            "AND action = 'warn' AND active = 1 "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (guild_id, user_id, int(time.time())),
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _case_filter(
        guild_id: int,
        user_id: int | None,
        action: CaseAction | None,
        active_only: bool,
    ) -> tuple[str, list[object]]:
        clauses = ["guild_id = ?"]
        params: list[object] = [guild_id]
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if action is not None:
            clauses.append("action = ?")
            params.append(str(action))
        if active_only:
            clauses.append("active = 1")
        return " AND ".join(clauses), params

    async def get_cases(
        self,
        guild_id: int,
        *,
        user_id: int | None = None,
        action: CaseAction | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Case]:
        where, params = self._case_filter(guild_id, user_id, action, active_only)
        sql = f"SELECT * FROM cases WHERE {where} ORDER BY number DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [Case.from_row(row) for row in rows]

    async def count_cases(
        self,
        guild_id: int,
        *,
        user_id: int | None = None,
        action: CaseAction | None = None,
        active_only: bool = False,
    ) -> int:
        where, params = self._case_filter(guild_id, user_id, action, active_only)
        async with self.conn.execute(
            f"SELECT COUNT(*) AS n FROM cases WHERE {where}", params
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def get_case(self, guild_id: int, number: int) -> Case | None:
        async with self.conn.execute(
            "SELECT * FROM cases WHERE guild_id = ? AND number = ?", (guild_id, number)
        ) as cur:
            row = await cur.fetchone()
        return Case.from_row(row) if row else None

    async def deactivate_case(self, guild_id: int, number: int) -> Case | None:
        """Retire a case by number. Returns it, or None if absent or already retired."""
        case = await self.get_case(guild_id, number)
        if case is None or not case.active:
            return None
        async with self.transaction() as conn:
            await conn.execute(
                "UPDATE cases SET active = 0 WHERE guild_id = ? AND number = ?",
                (guild_id, number),
            )
        return case

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        """Retire every active warning for a user. Returns how many."""
        async with (
            self.transaction() as conn,
            conn.execute(
                "UPDATE cases SET active = 0 WHERE guild_id = ? AND user_id = ? "
                "AND action = 'warn' AND active = 1",
                (guild_id, user_id),
            ) as cur,
        ):
            return cur.rowcount

    async def due_cases(self, action: CaseAction, *, now: int | None = None) -> list[Case]:
        """Active cases of ``action`` whose expiry has passed (due tempbans)."""
        async with self.conn.execute(
            "SELECT * FROM cases WHERE action = ? AND active = 1 "
            "AND expires_at IS NOT NULL AND expires_at <= ? ORDER BY expires_at",
            (str(action), now if now is not None else int(time.time())),
        ) as cur:
            rows = await cur.fetchall()
        return [Case.from_row(row) for row in rows]

    async def get_words(self, guild_id: int) -> list[str]:
        async with self.conn.execute(
            "SELECT word FROM guild_words WHERE guild_id = ? ORDER BY word", (guild_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [row["word"] for row in rows]

    async def add_words(self, guild_id: int, words: Iterable[str]) -> int:
        """Add words to a guild's list. Returns how many were new."""
        added = 0
        async with self.transaction() as conn:
            for word in words:
                cleaned = word.strip().lower()
                if not cleaned:
                    continue
                async with conn.execute(
                    "INSERT INTO guild_words (guild_id, word) VALUES (?, ?) "
                    "ON CONFLICT(guild_id, word) DO NOTHING",
                    (guild_id, cleaned),
                ) as cur:
                    added += max(cur.rowcount, 0)
        return added

    async def remove_word(self, guild_id: int, word: str) -> bool:
        async with (
            self.transaction() as conn,
            conn.execute(
                "DELETE FROM guild_words WHERE guild_id = ? AND word = ?",
                (guild_id, word.strip().lower()),
            ) as cur,
        ):
            return cur.rowcount > 0

    async def clear_words(self, guild_id: int) -> int:
        async with (
            self.transaction() as conn,
            conn.execute("DELETE FROM guild_words WHERE guild_id = ?", (guild_id,)) as cur,
        ):
            return cur.rowcount

    async def get_open_ticket(self, guild_id: int, user_id: int) -> int | None:
        async with self.conn.execute(
            "SELECT channel_id FROM tickets "
            "WHERE guild_id = ? AND user_id = ? AND status = 'open' LIMIT 1",
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return row["channel_id"] if row else None

    async def create_ticket(self, channel_id: int, guild_id: int, user_id: int) -> int:
        """Allocate the next ticket number and insert the row atomically.

        The per-guild counter avoids the ``MAX(number)+1`` race; the UNIQUE
        partial index on open tickets is the storage-layer backstop against a
        double-click opening two tickets.

        Returns:
            The allocated ticket number.
        """
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO ticket_counter (guild_id, last_num) VALUES (?, 1) "
                "ON CONFLICT(guild_id) DO UPDATE SET last_num = last_num + 1",
                (guild_id,),
            )
            async with conn.execute(
                "SELECT last_num FROM ticket_counter WHERE guild_id = ?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
            number = int(row["last_num"]) if row else 1

            await conn.execute(
                "INSERT INTO tickets "
                "(channel_id, guild_id, user_id, number, status, created_at) "
                "VALUES (?, ?, ?, ?, 'open', ?)",
                (channel_id, guild_id, user_id, number, int(time.time())),
            )
        return number

    async def close_ticket(self, channel_id: int) -> bool:
        """Mark a ticket closed. False if it was already closed or unknown."""
        async with (
            self.transaction() as conn,
            conn.execute(
                "UPDATE tickets SET status = ? WHERE channel_id = ? AND status != ?",
                (TICKET_STATUS_CLOSED, channel_id, TICKET_STATUS_CLOSED),
            ) as cur,
        ):
            return cur.rowcount > 0

    async def get_ticket(self, channel_id: int) -> Ticket | None:
        async with self.conn.execute(
            "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
        ) as cur:
            row = await cur.fetchone()
        return Ticket.from_row(row) if row else None
