"""Domain records returned by the storage layer.

Cogs receive these instead of raw ``aiosqlite.Row`` objects, so a schema
change can't silently propagate into a ``KeyError`` halfway across the
codebase, and the shape of a ticket is documented in exactly one place.
"""

from __future__ import annotations

import dataclasses
import enum
import time
from typing import Any, Protocol


class Row(Protocol):
    """Anything indexable by column name, such as :class:`aiosqlite.Row`."""

    def __getitem__(self, key: str, /) -> Any: ...


TICKET_STATUS_OPEN = "open"
TICKET_STATUS_CLOSED = "closed"


class CaseAction(enum.StrEnum):
    """Every moderation action the bot records.

    Warnings are cases like any other, which is what lets a warning expire
    (``expires_at``) and lets ``/case`` address a kick or a ban by number.
    """

    WARN = "warn"
    KICK = "kick"
    BAN = "ban"
    TEMPBAN = "tempban"
    UNBAN = "unban"
    TIMEOUT = "timeout"
    UNTIMEOUT = "untimeout"

    @property
    def label(self) -> str:
        return {
            CaseAction.WARN: "Warned",
            CaseAction.KICK: "Kicked",
            CaseAction.BAN: "Banned",
            CaseAction.TEMPBAN: "Temporarily banned",
            CaseAction.UNBAN: "Unbanned",
            CaseAction.TIMEOUT: "Timed out",
            CaseAction.UNTIMEOUT: "Timeout removed",
        }[self]

    @property
    def emoji(self) -> str:
        return {
            CaseAction.WARN: "⚠️",
            CaseAction.KICK: "👢",
            CaseAction.BAN: "🔨",
            CaseAction.TEMPBAN: "⏳",
            CaseAction.UNBAN: "🕊️",
            CaseAction.TIMEOUT: "🔇",
            CaseAction.UNTIMEOUT: "🔊",
        }[self]


@dataclasses.dataclass(slots=True)
class GuildConfig:
    """Per-guild settings. Absent columns fall back to these defaults."""

    guild_id: int
    mod_log_channel_id: int | None = None
    event_log_channel_id: int | None = None
    honeypot_channel_id: int | None = None
    staff_role_id: int | None = None
    ticket_category_id: int | None = None
    warn_kick_threshold: int = 3
    warn_ban_threshold: int = 5
    warn_expiry_days: int = 0
    automod_enabled: bool = True
    antispam_enabled: bool = False
    antispam_message_limit: int = 5
    antispam_window_seconds: int = 5
    antispam_mention_limit: int = 5
    invite_filter_enabled: bool = False
    min_account_age_hours: int = 0
    raid_join_threshold: int = 0
    raid_join_window_seconds: int = 60

    @classmethod
    def from_row(cls, row: Row) -> GuildConfig:
        return cls(
            guild_id=row["guild_id"],
            mod_log_channel_id=row["mod_log_channel_id"],
            event_log_channel_id=row["event_log_channel_id"],
            honeypot_channel_id=row["honeypot_channel_id"],
            staff_role_id=row["staff_role_id"],
            ticket_category_id=row["ticket_category_id"],
            warn_kick_threshold=row["warn_kick_threshold"],
            warn_ban_threshold=row["warn_ban_threshold"],
            warn_expiry_days=row["warn_expiry_days"],
            automod_enabled=bool(row["automod_enabled"]),
            antispam_enabled=bool(row["antispam_enabled"]),
            antispam_message_limit=row["antispam_message_limit"],
            antispam_window_seconds=row["antispam_window_seconds"],
            antispam_mention_limit=row["antispam_mention_limit"],
            invite_filter_enabled=bool(row["invite_filter_enabled"]),
            min_account_age_hours=row["min_account_age_hours"],
            raid_join_threshold=row["raid_join_threshold"],
            raid_join_window_seconds=row["raid_join_window_seconds"],
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One recorded moderation action."""

    id: int
    guild_id: int
    number: int
    action: CaseAction
    user_id: int
    moderator_id: int
    reason: str
    created_at: int
    expires_at: int | None = None
    active: bool = True

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= int(time.time())

    @property
    def counts_toward_thresholds(self) -> bool:
        """Whether this warning still counts toward auto-kick / auto-ban."""
        return self.action is CaseAction.WARN and self.active and not self.is_expired

    @classmethod
    def from_row(cls, row: Row) -> Case:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            number=row["number"],
            action=CaseAction(row["action"]),
            user_id=row["user_id"],
            moderator_id=row["moderator_id"],
            reason=row["reason"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            active=bool(row["active"]),
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
