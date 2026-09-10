"""Spam and raid detection.

Both are judged on timing, so every test passes an explicit ``now`` rather
than sleeping.
"""

from __future__ import annotations

from data.models import GuildConfig
from services.raid import ALERT_COOLDOWN_SECONDS, RaidTracker
from services.spam import SpamTracker

GUILD = 1
USER = 2


def config(**overrides: object) -> GuildConfig:
    cfg = GuildConfig(guild_id=GUILD, antispam_enabled=True)
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg


def test_disabled_filter_never_reports_spam() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_enabled=False, antispam_message_limit=2)

    verdicts = [
        tracker.inspect(GUILD, USER, f"message {i}", 0, cfg, now=float(i)) for i in range(10)
    ]

    assert verdicts == [None] * 10


def test_flooding_is_caught_at_the_limit() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_message_limit=3, antispam_window_seconds=5)

    assert tracker.inspect(GUILD, USER, "a", 0, cfg, now=1.0) is None
    assert tracker.inspect(GUILD, USER, "b", 0, cfg, now=1.5) is None
    verdict = tracker.inspect(GUILD, USER, "c", 0, cfg, now=2.0)

    assert verdict is not None
    assert "flood" in verdict


def test_messages_outside_the_window_do_not_count() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_message_limit=3, antispam_window_seconds=5)

    assert tracker.inspect(GUILD, USER, "a", 0, cfg, now=0.0) is None
    assert tracker.inspect(GUILD, USER, "b", 0, cfg, now=100.0) is None
    assert tracker.inspect(GUILD, USER, "c", 0, cfg, now=200.0) is None


def test_repeated_messages_are_caught_even_when_slow() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_message_limit=50, antispam_window_seconds=5)

    assert tracker.inspect(GUILD, USER, "buy gold", 0, cfg, now=1.0) is None
    assert tracker.inspect(GUILD, USER, "BUY GOLD", 0, cfg, now=3.0) is None
    verdict = tracker.inspect(GUILD, USER, "  buy gold  ", 0, cfg, now=5.0)

    assert verdict is not None
    assert "repeated" in verdict


def test_distinct_messages_are_not_repetition() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_message_limit=50)

    for index in range(5):
        assert tracker.inspect(GUILD, USER, f"unique {index}", 0, cfg, now=index) is None


def test_mass_mentions_are_caught_on_one_message() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_mention_limit=4)

    verdict = tracker.inspect(GUILD, USER, "hi", 9, cfg, now=1.0)

    assert verdict is not None
    assert "mention" in verdict


def test_mentions_at_the_limit_are_allowed() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_mention_limit=4)

    assert tracker.inspect(GUILD, USER, "hi", 4, cfg, now=1.0) is None


def test_members_are_tracked_separately() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_message_limit=2, antispam_window_seconds=5)

    assert tracker.inspect(GUILD, USER, "a", 0, cfg, now=1.0) is None
    assert tracker.inspect(GUILD, USER + 1, "a", 0, cfg, now=1.1) is None


def test_forgetting_a_member_resets_their_history() -> None:
    tracker = SpamTracker()
    cfg = config(antispam_message_limit=2, antispam_window_seconds=5)

    tracker.inspect(GUILD, USER, "a", 0, cfg, now=1.0)
    tracker.forget(GUILD, USER)

    assert tracker.inspect(GUILD, USER, "b", 0, cfg, now=1.1) is None


def test_raid_detection_is_off_without_a_threshold() -> None:
    tracker = RaidTracker()

    verdicts = [
        tracker.record(GUILD, user, threshold=0, window_seconds=60, now=float(user))
        for user in range(20)
    ]

    assert verdicts == [None] * 20


def test_a_join_spike_raises_one_alert() -> None:
    tracker = RaidTracker()

    assert tracker.record(GUILD, 1, threshold=3, window_seconds=60, now=1.0) is None
    assert tracker.record(GUILD, 2, threshold=3, window_seconds=60, now=2.0) is None
    alert = tracker.record(GUILD, 3, threshold=3, window_seconds=60, now=3.0)

    assert alert is not None
    assert alert.joins == 3
    assert alert.user_ids == (1, 2, 3)


def test_a_sustained_raid_does_not_alert_on_every_join() -> None:
    tracker = RaidTracker()
    for user in range(1, 4):
        tracker.record(GUILD, user, threshold=3, window_seconds=60, now=float(user))

    repeat = tracker.record(GUILD, 4, threshold=3, window_seconds=60, now=4.0)

    assert repeat is None


def test_alerting_resumes_after_the_cooldown() -> None:
    tracker = RaidTracker()
    for user in range(1, 4):
        tracker.record(GUILD, user, threshold=3, window_seconds=60, now=float(user))

    later = ALERT_COOLDOWN_SECONDS + 10
    for user in range(4, 6):
        tracker.record(GUILD, user, threshold=3, window_seconds=60, now=later + user)
    alert = tracker.record(GUILD, 7, threshold=3, window_seconds=60, now=later + 7)

    assert alert is not None


def test_slow_joins_never_trigger_an_alert() -> None:
    tracker = RaidTracker()

    verdicts = [
        tracker.record(GUILD, user, threshold=3, window_seconds=60, now=user * 120.0)
        for user in range(1, 10)
    ]

    assert verdicts == [None] * 9


def test_guilds_are_tracked_separately() -> None:
    tracker = RaidTracker()
    for user in range(1, 3):
        tracker.record(GUILD, user, threshold=3, window_seconds=60, now=float(user))

    assert tracker.record(GUILD + 1, 9, threshold=3, window_seconds=60, now=3.0) is None
