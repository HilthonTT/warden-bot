"""The queue's unit of work.

Queued items used to be raw dicts, and the audio source class grew a
``__getitem__`` that forwarded to ``getattr`` so both shapes could be indexed
the same way. A single record type removes that ambiguity: the queue holds
:class:`Track` metadata, and an audio source is built from it only when the
track reaches the front.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class Track:
    """Resolved metadata for one queued song."""

    title: str
    webpage_url: str
    requester_id: int
    requester_name: str
    duration: int | None = None

    @property
    def duration_label(self) -> str:
        """``mm:ss`` (or ``h:mm:ss``), or ``live`` when the length is unknown."""
        if not self.duration:
            return "live"
        minutes, seconds = divmod(int(self.duration), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def __str__(self) -> str:
        return f"{self.title} [{self.duration_label}]"
