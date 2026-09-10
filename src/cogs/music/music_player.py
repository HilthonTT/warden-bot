"""Per-guild playback loop.

Fixes carried over from the previous implementation:

* The loop task outlived its player. ``/stop`` dropped the player from the
  cog's dict but left the task waiting on a queue nobody could reach, so every
  stop/play cycle leaked a task and a queue. The task is now owned by the
  player, cancelled by :meth:`stop`, and always reports its own death through
  the ``on_finished`` callback.
* ``guild.voice_client.play`` was called without checking the connection. A
  disconnect (kick from the channel, network drop) raised ``AttributeError``
  inside the task, which asyncio swallowed — playback stopped with no log.
* The "now playing" message was only deleted on the happy path, so cancelled
  or failed tracks left it behind forever.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable

import discord
from discord.ext import commands

from .music_utils import MusicError, create_audio_source
from .track import Track

log = logging.getLogger(__name__)

IDLE_TIMEOUT = 300.0
MAX_QUEUE_SIZE = 100
ERROR_NOTICE_DELETE_AFTER = 20


class TrackQueue:
    """An unbounded-read, bounded-write FIFO that can be inspected.

    ``asyncio.Queue`` has no supported way to look at pending items, so
    ``/queue`` used to read its private ``_queue`` deque.
    """

    def __init__(self, maxsize: int) -> None:
        self._items: deque[Track] = deque()
        self._maxsize = maxsize
        self._has_items = asyncio.Event()

    def __len__(self) -> int:
        return len(self._items)

    @property
    def full(self) -> bool:
        return len(self._items) >= self._maxsize

    def put(self, track: Track) -> bool:
        """Append a track. False if the queue is at capacity."""
        if self.full:
            return False
        self._items.append(track)
        self._has_items.set()
        return True

    async def get(self) -> Track:
        """Wait for and remove the next track."""
        while not self._items:
            self._has_items.clear()
            await self._has_items.wait()
        track = self._items.popleft()
        if not self._items:
            self._has_items.clear()
        return track

    def snapshot(self) -> list[Track]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._has_items.clear()


class MusicPlayer:
    """Owns one guild's queue and the task that drains it."""

    def __init__(
        self,
        *,
        bot: commands.Bot,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        on_finished: Callable[[int], Awaitable[None]],
    ) -> None:
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self._on_finished = on_finished

        self.queue = TrackQueue(maxsize=MAX_QUEUE_SIZE)
        self.current: Track | None = None
        self.volume: float = 0.5
        self.now_playing_message: discord.Message | None = None

        self._advance = asyncio.Event()
        self._task = bot.loop.create_task(
            self._run(),
            name=f"music-player-{guild.id}",
        )

    @property
    def upcoming(self) -> list[Track]:
        """Queued tracks in play order, without consuming them."""
        return self.queue.snapshot()

    def enqueue(self, track: Track) -> bool:
        """Add a track. False if the queue is full."""
        return self.queue.put(track)

    def skip(self) -> None:
        voice = self.guild.voice_client
        if isinstance(voice, discord.VoiceClient) and (voice.is_playing() or voice.is_paused()):
            voice.stop()

    def set_volume(self, volume: float) -> None:
        """Apply a volume to the current source and to everything after it."""
        self.volume = volume
        voice = self.guild.voice_client
        if isinstance(voice, discord.VoiceClient) and isinstance(
            voice.source,
            discord.PCMVolumeTransformer,
        ):
            voice.source.volume = volume

    def clear(self) -> None:
        self.queue.clear()

    async def stop(self) -> None:
        """Cancel the playback task, wait for it to unwind, and tidy up.

        Cleanup lives here rather than in the task's ``finally``: the first
        ``await`` inside a cancelled task re-raises ``CancelledError``, so a
        finally block cannot reliably delete the now-playing message.
        """
        self.clear()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("Player task for guild %s ended", self.guild.id, exc_info=True)
        await self._clear_now_playing()

    async def _run(self) -> None:
        """Drain the queue until it idles out or the connection drops."""
        try:
            await self.bot.wait_until_ready()
            while not self.bot.is_closed():
                if not await self._play_next():
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Music player for guild %s crashed", self.guild.id)

        await self._clear_now_playing()
        await self._on_finished(self.guild.id)

    async def _play_next(self) -> bool:
        """Play one track. Returns False when the player should shut down."""
        self._advance.clear()
        try:
            async with asyncio.timeout(IDLE_TIMEOUT):
                track = await self.queue.get()
        except TimeoutError:
            log.info("Music player for guild %s idle — disconnecting", self.guild.id)
            return False

        voice = self.guild.voice_client
        if not isinstance(voice, discord.VoiceClient) or not voice.is_connected():
            log.info("Voice connection lost in guild %s — stopping player", self.guild.id)
            return False

        try:
            source = await create_audio_source(
                track,
                volume=self.volume,
                loop=self.bot.loop,
            )
        except MusicError as exc:
            await self._notify(f"❌ Skipping **{track.title}** — {exc}")
            return True

        try:
            voice.play(source, after=self._after_play)
        except discord.ClientException:
            log.warning("Could not start playback in guild %s", self.guild.id, exc_info=True)
            source.cleanup()
            return True

        self.current = track
        await self._announce(track)
        await self._advance.wait()

        self.current = None
        source.cleanup()
        await self._clear_now_playing()
        return True

    def _after_play(self, error: Exception | None) -> None:
        if error is not None:
            log.warning("Playback error in guild %s: %s", self.guild.id, error)
        self.bot.loop.call_soon_threadsafe(self._advance.set)

    async def _announce(self, track: Track) -> None:
        embed = discord.Embed(
            title="🎧 Now Playing",
            description=f"🎵 **{track.title}**\n`{track.duration_label}`",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Requested by {track.requester_name}")
        try:
            self.now_playing_message = await self.channel.send(embed=embed)
        except discord.HTTPException:
            log.debug("Could not post the now-playing message", exc_info=True)

    async def _clear_now_playing(self) -> None:
        message, self.now_playing_message = self.now_playing_message, None
        if message is None:
            return
        try:
            await message.delete()
        except discord.HTTPException:
            log.debug("Could not delete the now-playing message", exc_info=True)

    async def _notify(self, content: str) -> None:
        try:
            await self.channel.send(content, delete_after=ERROR_NOTICE_DELETE_AFTER)
        except discord.HTTPException:
            log.debug("Could not post a player notice", exc_info=True)
