"""Rate-based spam detection.

The word filter only catches what someone says, not how fast or how often.
"hi" fifty times in ten seconds, or a message mentioning forty people, sails
past a word list untouched — and those are the patterns an actual raid uses.

State is per (guild, member) and lives in memory only: it describes the last
few seconds of activity, so losing it on restart costs nothing.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from data.models import GuildConfig

DUPLICATE_LIMIT = 3
DUPLICATE_WINDOW_FACTOR = 3
MAX_TRACKED_MEMBERS = 10_000
HISTORY_PER_MEMBER = 20


@dataclass(slots=True)
class _Activity:
    """Recent message timestamps and content fingerprints for one member."""

    events: deque[tuple[float, int]] = field(
        default_factory=lambda: deque(maxlen=HISTORY_PER_MEMBER)
    )

    def prune(self, cutoff: float) -> None:
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()


class SpamTracker:
    """Answers "is this member spamming right now?" for one message at a time."""

    def __init__(self) -> None:
        self._activity: dict[tuple[int, int], _Activity] = {}

    def clear_guild(self, guild_id: int) -> None:
        for key in [k for k in self._activity if k[0] == guild_id]:
            del self._activity[key]

    def forget(self, guild_id: int, user_id: int) -> None:
        self._activity.pop((guild_id, user_id), None)

    def _activity_for(self, guild_id: int, user_id: int) -> _Activity:
        key = (guild_id, user_id)
        activity = self._activity.get(key)
        if activity is None:
            if len(self._activity) >= MAX_TRACKED_MEMBERS:
                self._evict()
            activity = _Activity()
            self._activity[key] = activity
        return activity

    def _evict(self) -> None:
        """Drop the least recently active half of the table.

        A busy bot would otherwise hold one deque per member it has ever seen.
        """
        ranked = sorted(
            self._activity.items(),
            key=lambda item: item[1].events[-1][0] if item[1].events else 0.0,
        )
        for key, _ in ranked[: len(ranked) // 2]:
            del self._activity[key]

    def inspect(
        self,
        guild_id: int,
        user_id: int,
        content: str,
        mention_count: int,
        cfg: GuildConfig,
        *,
        now: float | None = None,
    ) -> str | None:
        """Record a message and return why it is spam, or None if it isn't.

        Mass mentions are judged on the single message; flooding and repetition
        need the member's recent history.
        """
        if not cfg.antispam_enabled:
            return None

        moment = time.monotonic() if now is None else now
        window = max(1, cfg.antispam_window_seconds)
        activity = self._activity_for(guild_id, user_id)
        activity.prune(moment - window * DUPLICATE_WINDOW_FACTOR)
        activity.events.append((moment, hash(content.strip().lower())))

        if mention_count > cfg.antispam_mention_limit:
            return f"mass mentions ({mention_count} in one message)"

        recent = [event for event in activity.events if event[0] >= moment - window]
        if len(recent) >= max(2, cfg.antispam_message_limit):
            return f"message flood ({len(recent)} messages in {window}s)"

        if content.strip():
            fingerprint = hash(content.strip().lower())
            duplicates = sum(1 for _, digest in activity.events if digest == fingerprint)
            if duplicates >= DUPLICATE_LIMIT:
                return f"repeated message ({duplicates} times)"

        return None
