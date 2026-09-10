"""Music-specific errors."""

from __future__ import annotations


class MusicError(Exception):
    """Base class for music failures that are safe to show to a user."""


class TrackResolveError(MusicError):
    """yt-dlp could not turn a query into a playable track."""


class VoiceConnectError(MusicError):
    """The bot could not join or move to a voice channel."""
