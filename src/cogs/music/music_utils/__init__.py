"""Music helpers: yt-dlp resolution, FFmpeg options, and error types."""

from ._music_utils_config import FFMPEG_OPTIONS, YTDL_OPTIONS
from .music_exceptions import MusicError, TrackResolveError, VoiceConnectError
from .yt_dl_source import create_audio_source, resolve_track

__all__ = [
    "FFMPEG_OPTIONS",
    "YTDL_OPTIONS",
    "MusicError",
    "TrackResolveError",
    "VoiceConnectError",
    "create_audio_source",
    "resolve_track",
]
