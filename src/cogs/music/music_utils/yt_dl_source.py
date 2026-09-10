"""Turning a search query into playable audio.

Two responsibilities, deliberately kept apart:

* :func:`resolve_track` runs a yt-dlp metadata lookup and returns a
  :class:`~cogs.music.track.Track`. It is called when a user queues a song.
* :func:`create_audio_source` re-resolves the direct media URL and builds the
  FFmpeg source. It is called when the track reaches the front of the queue,
  because media URLs expire minutes after they are issued.

Both run yt-dlp in a thread executor: it performs blocking network I/O, and
running it inline would stall the event loop for every guild.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

import discord
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from cogs.music.track import Track

from ._music_utils_config import FFMPEG_OPTIONS, YTDL_OPTIONS
from .music_exceptions import TrackResolveError

log = logging.getLogger(__name__)

ytdl = YoutubeDL(YTDL_OPTIONS)


def _first_entry(data: dict[str, Any]) -> dict[str, Any]:
    """Reduce a playlist/search result to a single entry."""
    while "entries" in data:
        entries = [entry for entry in (data.get("entries") or []) if entry]
        if not entries:
            raise TrackResolveError("No results for that search.")
        data = entries[0]
    return data


async def _extract(query: str, loop: asyncio.AbstractEventLoop) -> dict[str, Any]:
    call = partial(ytdl.extract_info, url=query, download=False)
    try:
        data = await loop.run_in_executor(None, call)
    except DownloadError as exc:
        raise TrackResolveError(str(exc).splitlines()[0]) from exc
    except Exception as exc:
        log.exception("yt-dlp failed for %r", query)
        raise TrackResolveError("Could not read that source.") from exc

    if not data:
        raise TrackResolveError("No results for that search.")
    return _first_entry(data)


async def resolve_track(
    query: str,
    requester: discord.abc.User,
    *,
    loop: asyncio.AbstractEventLoop,
) -> Track:
    """Resolve a search string or URL into queueable :class:`Track` metadata.

    Raises:
        TrackResolveError: if the query yields nothing playable.
    """
    data = await _extract(query, loop)
    url = data.get("webpage_url") or data.get("original_url") or data.get("url")
    if not url:
        raise TrackResolveError("That result has no playable URL.")

    return Track(
        title=data.get("title") or "Unknown title",
        webpage_url=url,
        requester_id=requester.id,
        requester_name=requester.display_name,
        duration=data.get("duration"),
    )


async def create_audio_source(
    track: Track,
    *,
    volume: float,
    loop: asyncio.AbstractEventLoop,
) -> discord.PCMVolumeTransformer:
    """Build a fresh, volume-controlled FFmpeg source for ``track``.

    Raises:
        TrackResolveError: if the stream URL cannot be resolved.
    """
    data = await _extract(track.webpage_url, loop)
    stream_url = data.get("url")
    if not stream_url:
        raise TrackResolveError("That track has no playable stream.")

    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            stream_url,
            before_options=FFMPEG_OPTIONS["before_options"],
            options=FFMPEG_OPTIONS["options"],
        ),
        volume=volume,
    )
