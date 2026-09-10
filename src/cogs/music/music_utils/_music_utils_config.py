"""yt-dlp and FFmpeg options.

``FFMPEG_OPTIONS`` was previously defined but never passed to
``FFmpegPCMAudio``, so streams ran without ``-vn`` and without reconnect
handling: a momentary network blip ended playback silently, mid-song. The
reconnect flags make FFmpeg retry a dropped HTTP stream for up to five
seconds instead of exiting.
"""

from __future__ import annotations

from typing import Any

YTDL_OPTIONS: dict[str, Any] = {
    "format": "bestaudio/best",
    "outtmpl": "downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "skip_download": True,
}

FFMPEG_OPTIONS: dict[str, str] = {
    "before_options": "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
