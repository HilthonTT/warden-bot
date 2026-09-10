"""Domain records returned by the storage layer.

Cogs receive these instead of raw ``aiosqlite.Row`` objects, so a schema
change can't silently propagate into a ``KeyError`` halfway across the
codebase, and the shape of a ticket is documented in exactly one place.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Protocol


class Row(Protocol):
    """Anything indexable by column name, such as :class:`aiosqlite.Row`."""

    def __getitem__(self, key: str, /) -> Any: ...


TICKET_STATUS_OPEN = "open"
TICKET_STATUS_CLOSED = "closed"


@dataclasses.dataclass(slots=True)
class GuildConfig:
    """Per-guild settings. Absent columns fall back to these defaults."""

    guild_id: int
    mod_log_channel_id: int | None = None
    honeypot_channel_id: int | None = None
    staff_role_id: int | None = None
    ticket_category_id: int | None = None
    warn_kick_threshold: int = 3
    warn_ban_threshold: int = 5
    automod_enabled: bool = True

    @classmethod
    def from_row(cls, row: Row) -> GuildConfig:
        return cls(
            guild_id=row["guild_id"],
            mod_log_channel_id=row["mod_log_channel_id"],
            honeypot_channel_id=row["honeypot_channel_id"],
            staff_role_id=row["staff_role_id"],
            ticket_category_id=row["ticket_category_id"],
            warn_kick_threshold=row["warn_kick_threshold"],
            warn_ban_threshold=row["warn_ban_threshold"],
            automod_enabled=bool(row["automod_enabled"]),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class WarningRecord:
    id: int
    guild_id: int
    user_id: int
    moderator_id: int
    reason: str
    created_at: int

    @classmethod
    def from_row(cls, row: Row) -> WarningRecord:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            moderator_id=row["moderator_id"],
            reason=row["reason"],
            created_at=row["created_at"],
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Ticket:
    channel_id: int
    guild_id: int
    user_id: int
    number: int
    status: str
    created_at: int

    @property
    def is_open(self) -> bool:
        return self.status == TICKET_STATUS_OPEN

    @property
    def label(self) -> str:
        """Zero-padded display number, e.g. ``0007``."""
        return f"{self.number:04d}"

    @classmethod
    def from_row(cls, row: Row) -> Ticket:
        return cls(
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            number=row["number"],
            status=row["status"],
            created_at=row["created_at"],
        )
