"""Join-rate tracking for raid detection.

A raid looks like a join spike, not like a bad message. This keeps a short
sliding window of joins per guild and reports when the rate crosses the
guild's configured threshold, with a cooldown so a sustained raid produces one
alert rather than one per joiner.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

ALERT_COOLDOWN_SECONDS = 300
MAX_TRACKED_JOINS = 200


@dataclass(slots=True)
class RaidAlert:
    """A join spike worth telling moderators about."""

    joins: int
    window_seconds: int
    user_ids: tuple[int, ...]


@dataclass(slots=True)
class _GuildJoins:
    events: deque[tuple[float, int]] = field(
        default_factory=lambda: deque(maxlen=MAX_TRACKED_JOINS)
    )
    last_alert: float | None = None


class RaidTracker:
    def __init__(self) -> None:
        self._guilds: dict[int, _GuildJoins] = {}

    def forget(self, guild_id: int) -> None:
        self._guilds.pop(guild_id, None)

    def record(
        self,
        guild_id: int,
        user_id: int,
        *,
        threshold: int,
        window_seconds: int,
        now: float | None = None,
    ) -> RaidAlert | None:
        """Record a join and return an alert if the rate crossed the threshold.

        A threshold of zero disables detection for the guild.
        """
        if threshold <= 0:
            return None

        moment = time.monotonic() if now is None else now
        window = max(1, window_seconds)
        state = self._guilds.setdefault(guild_id, _GuildJoins())

        while state.events and state.events[0][0] < moment - window:
            state.events.popleft()
        state.events.append((moment, user_id))

        if len(state.events) < threshold:
            return None
        if state.last_alert is not None and moment - state.last_alert < ALERT_COOLDOWN_SECONDS:
            return None

        state.last_alert = moment
        return RaidAlert(
            joins=len(state.events),
            window_seconds=window,
            user_ids=tuple(user_id for _, user_id in state.events),
        )
