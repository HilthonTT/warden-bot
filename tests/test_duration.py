from __future__ import annotations

from datetime import timedelta

import pytest

from core.duration import DurationError, format_duration, parse_duration


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("10m", timedelta(minutes=10)),
        ("2h", timedelta(hours=2)),
        ("7d", timedelta(days=7)),
        ("2w", timedelta(weeks=2)),
        ("2h30m", timedelta(hours=2, minutes=30)),
        ("1d12h", timedelta(days=1, hours=12)),
        ("  45 m  ", timedelta(minutes=45)),
        ("1H", timedelta(hours=1)),
    ],
)
def test_parse_duration(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   ", "soon", "10", "m", "10x", "1h nonsense", "-5m", "10.5m"],
)
def test_malformed_durations_are_rejected(text: str) -> None:
    with pytest.raises(DurationError):
        parse_duration(text)


def test_zero_length_durations_are_rejected() -> None:
    with pytest.raises(DurationError, match="longer than zero"):
        parse_duration("0m")


def test_repeated_units_add_up() -> None:
    assert parse_duration("1h1h") == timedelta(hours=2)


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=0), "0 seconds"),
        (timedelta(seconds=1), "1 second"),
        (timedelta(seconds=90), "1 minute 30 seconds"),
        (timedelta(hours=2, minutes=30), "2 hours 30 minutes"),
        (timedelta(days=8), "1 week 1 day"),
    ],
)
def test_format_duration(delta: timedelta, expected: str) -> None:
    assert format_duration(delta) == expected


def test_parse_and_format_round_trip() -> None:
    assert format_duration(parse_duration("2h30m")) == "2 hours 30 minutes"
