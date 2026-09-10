"""Human duration parsing for command options.

Discord has no duration option type, so ``/timeout`` and ``/tempban`` take a
string. Accepting "10m", "2h30m" or "7d" is the difference between a usable
command and one moderators get wrong under pressure.
"""

from __future__ import annotations

import re
from datetime import timedelta

UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3_600,
    "d": 86_400,
    "w": 604_800,
}

UNIT_NAMES = {
    "s": "second",
    "m": "minute",
    "h": "hour",
    "d": "day",
    "w": "week",
}

_PART_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
_VALID_RE = re.compile(r"^(?:\s*\d+\s*[smhdw]\s*)+$", re.IGNORECASE)

DISCORD_MAX_TIMEOUT = timedelta(days=28)


class DurationError(ValueError):
    """The supplied duration could not be understood."""


def parse_duration(text: str) -> timedelta:
    """Parse ``10m``, ``2h30m``, ``7d`` into a :class:`~datetime.timedelta`.

    Raises:
        DurationError: on an empty, malformed, or zero-length duration.
    """
    cleaned = text.strip()
    if not cleaned:
        raise DurationError("Give a duration such as `10m`, `2h30m` or `7d`.")
    if not _VALID_RE.match(cleaned):
        raise DurationError(
            f"`{text}` isn't a duration I understand. Use units s, m, h, d or w — "
            f"for example `30m`, `2h30m`, `7d`."
        )

    seconds = 0
    for value, unit in _PART_RE.findall(cleaned):
        seconds += int(value) * UNIT_SECONDS[unit.lower()]

    if seconds <= 0:
        raise DurationError("The duration must be longer than zero.")
    return timedelta(seconds=seconds)


def format_duration(delta: timedelta) -> str:
    """Render a timedelta as a short human phrase, e.g. ``2 hours 30 minutes``."""
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "0 seconds"

    parts: list[str] = []
    for unit in ("w", "d", "h", "m", "s"):
        size = UNIT_SECONDS[unit]
        count, seconds = divmod(seconds, size)
        if count:
            name = UNIT_NAMES[unit]
            parts.append(f"{count} {name}{'s' if count != 1 else ''}")
    return " ".join(parts)
