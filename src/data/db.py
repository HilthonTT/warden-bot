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
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite

from .models import TICKET_STATUS_CLOSED, GuildConfig, Ticket, WarningRecord

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id            INTEGER PRIMARY KEY,
    mod_log_channel_id  INTEGER,
    honeypot_channel_id INTEGER,
    staff_role_id       INTEGER,
    ticket_category_id  INTEGER,
    warn_kick_threshold INTEGER NOT NULL DEFAULT 3,
    warn_ban_threshold  INTEGER NOT NULL DEFAULT 5,
    automod_enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS warnings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    moderator_id  INTEGER NOT NULL,
    reason        TEXT    NOT NULL,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings(guild_id, user_id);

CREATE TABLE IF NOT EXISTS tickets (
    channel_id   INTEGER PRIMARY KEY,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    number       INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'open',
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(guild_id, user_id, status);

-- One open ticket per (guild, user). Closed tickets are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tickets_open
    ON tickets(guild_id, user_id) WHERE status = 'open';

-- Per-guild monotonic ticket counter. Avoids the MAX()+1 race.
CREATE TABLE IF NOT EXISTS ticket_counter (
    guild_id INTEGER PRIMARY KEY,
    last_num INTEGER NOT NULL DEFAULT 0
);
"""

MIGRATIONS: tuple[tuple[int, str], ...] = ()


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

        await self._conn.executescript(BASE_SCHEMA)
        await self._migrate()
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

    async def _migrate(self) -> None:
        async with self.conn.execute("SELECT version FROM schema_version LIMIT 1") as cur:
            row = await cur.fetchone()
        current = int(row["version"]) if row else 0

        for version, statement in MIGRATIONS:
            if version > current:
                log.info("Applying migration to schema v%d", version)
                await self.conn.executescript(statement)

        if current != SCHEMA_VERSION:
            async with self.transaction() as conn:
                await conn.execute("DELETE FROM schema_version;")
                await conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )

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
            "SELECT * FROM guild_config WHERE guild_id = ?",
            (guild_id,),
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
                    guild_id, mod_log_channel_id, honeypot_channel_id,
                    staff_role_id, ticket_category_id,
                    warn_kick_threshold, warn_ban_threshold, automod_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    mod_log_channel_id  = excluded.mod_log_channel_id,
                    honeypot_channel_id = excluded.honeypot_channel_id,
                    staff_role_id       = excluded.staff_role_id,
                    ticket_category_id  = excluded.ticket_category_id,
                    warn_kick_threshold = excluded.warn_kick_threshold,
                    warn_ban_threshold  = excluded.warn_ban_threshold,
                    automod_enabled     = excluded.automod_enabled
                """,
                (
                    cfg.guild_id,
                    cfg.mod_log_channel_id,
                    cfg.honeypot_channel_id,
                    cfg.staff_role_id,
                    cfg.ticket_category_id,
                    cfg.warn_kick_threshold,
                    cfg.warn_ban_threshold,
                    int(cfg.automod_enabled),
                ),
            )

    async def add_warning(
        self,
        guild_id: int,
        user_id: int,
        mod_id: int,
        reason: str,
    ) -> tuple[int, int]:
        """Record a warning.

        Returns:
            ``(warning_id, total_warnings)`` — the count is read inside the
            same transaction as the insert, so two concurrent warns can't
            both report the same total and skip an escalation threshold.
        """
        async with self.transaction() as conn:
            async with conn.execute(
                "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (guild_id, user_id, mod_id, reason, int(time.time())),
            ) as cur:
                warning_id = cur.lastrowid or 0
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM warnings WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cur:
                row = await cur.fetchone()
        return warning_id, (int(row["n"]) if row else 0)

    async def get_warnings(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int | None = None,
    ) -> list[WarningRecord]:
        sql = (
            "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? "
            "ORDER BY created_at DESC, id DESC"
        )
        params: tuple[object, ...] = (guild_id, user_id)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [WarningRecord.from_row(r) for r in rows]

    async def count_warnings(self, guild_id: int, user_id: int) -> int:
        """Total warnings without loading every row (used on the hot path)."""
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        async with (
            self.transaction() as conn,
            conn.execute(
                "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cur,
        ):
            return cur.rowcount

    async def remove_warning(self, guild_id: int, warning_id: int) -> bool:
        async with (
            self.transaction() as conn,
            conn.execute(
                "DELETE FROM warnings WHERE guild_id = ? AND id = ?",
                (guild_id, warning_id),
            ) as cur,
        ):
            return cur.rowcount > 0

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
                "SELECT last_num FROM ticket_counter WHERE guild_id = ?",
                (guild_id,),
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
            "SELECT * FROM tickets WHERE channel_id = ?",
            (channel_id,),
        ) as cur:
            row = await cur.fetchone()
        return Ticket.from_row(row) if row else None
