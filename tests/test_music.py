from __future__ import annotations

import asyncio

import pytest

from cogs.music.music_player import TrackQueue
from cogs.music.track import Track


def make_track(title: str = "Song") -> Track:
    return Track(
        title=title,
        webpage_url=f"https://example.invalid/{title}",
        requester_id=1,
        requester_name="someone",
        duration=90,
    )


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (0, "live"),
        (None, "live"),
        (9, "0:09"),
        (90, "1:30"),
        (3_600, "1:00:00"),
        (3_661, "1:01:01"),
    ],
)
def test_duration_label(duration: int | None, expected: str) -> None:
    track = Track("t", "u", 1, "r", duration)

    assert track.duration_label == expected


def test_queue_is_fifo_and_inspectable() -> None:
    queue = TrackQueue(maxsize=10)
    queue.put(make_track("first"))
    queue.put(make_track("second"))

    assert [t.title for t in queue.snapshot()] == ["first", "second"]
    assert len(queue) == 2


def test_queue_rejects_tracks_beyond_maxsize() -> None:
    queue = TrackQueue(maxsize=1)

    assert queue.put(make_track("a")) is True
    assert queue.put(make_track("b")) is False
    assert len(queue) == 1


def test_clear_empties_the_queue() -> None:
    queue = TrackQueue(maxsize=10)
    queue.put(make_track())

    queue.clear()

    assert len(queue) == 0
    assert queue.snapshot() == []


async def test_get_returns_queued_tracks_in_order() -> None:
    queue = TrackQueue(maxsize=10)
    queue.put(make_track("first"))
    queue.put(make_track("second"))

    assert (await queue.get()).title == "first"
    assert (await queue.get()).title == "second"


async def test_get_waits_until_a_track_arrives() -> None:
    queue = TrackQueue(maxsize=10)
    pending = asyncio.ensure_future(queue.get())

    await asyncio.sleep(0)
    assert not pending.done()

    queue.put(make_track("later"))
    assert (await asyncio.wait_for(pending, timeout=1)).title == "later"


async def test_get_times_out_on_an_empty_queue() -> None:
    queue = TrackQueue(maxsize=10)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await queue.get()
