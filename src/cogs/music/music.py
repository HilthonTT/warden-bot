"""Music commands.

  /join [channel]   — join a voice channel (yours by default)
  /play <search>    — search for a song and queue it
  /pause, /resume   — pause and resume playback
  /skip             — skip the current song
  /queue            — show what's coming up
  /nowplaying       — show the current song
  /volume <1-100>   — set playback volume
  /stop             — clear the queue and disconnect

The bot also leaves on its own once the voice channel empties, and after five
minutes with nothing queued.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.cog import MonitorCog
from core.constants import EMBED_FIELD_VALUE_MAX, truncate
from core.embeds import info_embed
from core.responses import fail, reply

from .music_player import MusicPlayer
from .music_utils import MusicError, resolve_track

if TYPE_CHECKING:
    from core.bot import MonitorBot

log = logging.getLogger(__name__)

QUEUE_PREVIEW = 10


class Music(MonitorCog):
    """🎵 Music commands."""

    def __init__(self, bot: MonitorBot) -> None:
        super().__init__(bot)
        self.players: dict[int, MusicPlayer] = {}

    async def cog_unload(self) -> None:
        for guild_id in list(self.players):
            await self.teardown(guild_id)

    async def teardown(self, guild_id: int) -> None:
        """Stop a guild's player and leave its voice channel."""
        player = self.players.pop(guild_id, None)
        if player is not None:
            await player.stop()

        guild = self.bot.get_guild(guild_id)
        voice = guild.voice_client if guild is not None else None
        if voice is not None:
            try:
                await voice.disconnect(force=False)
            except discord.HTTPException:
                log.debug("Voice disconnect failed for guild %s", guild_id, exc_info=True)

    async def _on_player_finished(self, guild_id: int) -> None:
        """Called by a player when its loop ends on its own (idle/disconnect)."""
        self.players.pop(guild_id, None)
        await self.teardown(guild_id)

    def _player_for(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
    ) -> MusicPlayer:
        player = self.players.get(guild.id)
        if player is None:
            player = MusicPlayer(
                bot=self.bot,
                guild=guild,
                channel=channel,
                on_finished=self._on_player_finished,
            )
            self.players[guild.id] = player
        return player

    async def _connect(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ) -> discord.VoiceClient | None:
        """Join (or move to) the requested channel, or the invoker's own.

        Returns None when no channel can be resolved or the bot lacks access;
        the caller has already been told why.
        """
        guild = interaction.guild
        assert guild is not None

        if channel is None:
            voice_state = (
                interaction.user.voice if isinstance(interaction.user, discord.Member) else None
            )
            channel = voice_state.channel if voice_state else None  # type: ignore[assignment]
        if channel is None:
            await fail(interaction, "Join a voice channel first, or name one.")
            return None

        me = guild.me
        if me is not None:
            permissions = channel.permissions_for(me)
            if not (permissions.connect and permissions.speak):
                await fail(
                    interaction,
                    f"I need **Connect** and **Speak** in {channel.mention}.",
                )
                return None

        voice = guild.voice_client
        try:
            if isinstance(voice, discord.VoiceClient) and voice.is_connected():
                if voice.channel.id != channel.id:
                    await voice.move_to(channel)
                return voice
            return await channel.connect()
        except (discord.ClientException, discord.HTTPException, TimeoutError) as exc:
            log.warning("Voice connect failed in guild %s", guild.id, exc_info=True)
            await fail(interaction, f"Couldn't join that voice channel: {exc}")
            return None

    def _connected(self, interaction: discord.Interaction) -> discord.VoiceClient | None:
        guild = interaction.guild
        voice = guild.voice_client if guild is not None else None
        if isinstance(voice, discord.VoiceClient) and voice.is_connected():
            return voice
        return None

    @app_commands.command(name="join", description="Have the bot join a voice channel.")
    @app_commands.describe(channel="The voice channel to join. Defaults to yours.")
    @app_commands.guild_only()
    async def join(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        voice = await self._connect(interaction, channel)
        if voice is None:
            return

        embed = info_embed("🎧 Connected", f"Joined **{voice.channel.name}**.")
        embed.set_footer(text="Use /stop to disconnect me at any time.")
        await reply(interaction, embed=embed)

    @app_commands.command(name="play", description="Search and play a song.")
    @app_commands.describe(search="A song name or URL.")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, search: str) -> None:
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(thinking=True)

        voice = await self._connect(interaction)
        if voice is None:
            return

        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await fail(interaction, "I can't post playback updates in this channel.")
            return

        try:
            track = await resolve_track(search, interaction.user, loop=self.bot.loop)
        except MusicError as exc:
            await fail(interaction, f"Couldn't queue that: {exc}")
            return

        player = self._player_for(guild, channel)
        if not player.enqueue(track):
            await fail(interaction, "The queue is full — try again after a few songs.")
            return

        embed = info_embed(
            "🎧 Added to the queue",
            f"🎹 **{track.title}**\n`{track.duration_label}` • position {len(player.queue)}",
        )
        embed.set_footer(text=f"Requested by {track.requester_name}")
        await reply(interaction, embed=embed)

    @app_commands.command(name="pause", description="Pause the current song.")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        voice = self._connected(interaction)
        if voice is None or not voice.is_playing():
            await fail(interaction, "I'm not playing anything right now.")
            return
        voice.pause()
        await reply(
            interaction,
            embed=info_embed("⏸️ Paused", f"Paused by **{interaction.user.display_name}**"),
        )

    @app_commands.command(name="resume", description="Resume playback.")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        voice = self._connected(interaction)
        if voice is None:
            await fail(interaction, "I'm not connected to voice.")
            return
        if not voice.is_paused():
            await fail(interaction, "I'm not paused.")
            return
        voice.resume()
        await reply(
            interaction,
            embed=info_embed("▶️ Resumed", f"Resumed by **{interaction.user.display_name}**"),
        )

    @app_commands.command(name="skip", description="Skip the current song.")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        voice = self._connected(interaction)
        if voice is None or not (voice.is_playing() or voice.is_paused()):
            await fail(interaction, "I'm not playing anything right now.")
            return
        voice.stop()
        await reply(
            interaction,
            embed=info_embed("⏭️ Skipped", f"Skipped by **{interaction.user.display_name}**"),
        )

    @app_commands.command(name="queue", description="Show the next songs in the queue.")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        player = self.players.get(guild.id)
        upcoming = player.upcoming if player is not None else []
        if player is None or not upcoming:
            await fail(interaction, "Nothing is queued.")
            return

        lines = [
            f"**{index}.** {truncate(track.title, 80)} `{track.duration_label}`"
            for index, track in enumerate(upcoming[:QUEUE_PREVIEW], start=1)
        ]
        if len(upcoming) > QUEUE_PREVIEW:
            lines.append(f"…and {len(upcoming) - QUEUE_PREVIEW} more")

        embed = info_embed(
            f"🎧 Queue — {len(upcoming)} song(s)",
            truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX),
        )
        if player.current is not None:
            embed.set_footer(text=f"Now playing: {player.current.title}")
        await reply(interaction, embed=embed)

    @app_commands.command(name="nowplaying", description="Show the current song.")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        player = self.players.get(guild.id)
        if self._connected(interaction) is None or player is None or player.current is None:
            await fail(interaction, "I'm not playing anything right now.")
            return

        track = player.current
        embed = info_embed(
            "🎧 Now Playing",
            f"🎵 **{track.title}**\n`{track.duration_label}`",
        )
        embed.set_footer(text=f"Requested by {track.requester_name}")
        await reply(interaction, embed=embed)

    @app_commands.command(name="volume", description="Set the player volume (1-100).")
    @app_commands.describe(volume="Playback volume between 1 and 100.")
    @app_commands.guild_only()
    async def volume(
        self,
        interaction: discord.Interaction,
        volume: app_commands.Range[int, 1, 100],
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if self._connected(interaction) is None:
            await fail(interaction, "I'm not connected to voice.")
            return

        player = self.players.get(guild.id)
        if player is None:
            await fail(interaction, "There's no active player to adjust.")
            return
        player.set_volume(volume / 100)

        await reply(
            interaction,
            embed=info_embed(
                "🔊 Volume changed",
                f"**{interaction.user.display_name}** set the volume to **{volume}%**",
            ),
        )

    @app_commands.command(
        name="stop",
        description="Clear the queue and disconnect from voice.",
    )
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        if self._connected(interaction) is None and guild.id not in self.players:
            await fail(interaction, "I'm not connected to voice.")
            return

        await interaction.response.defer(thinking=True)
        await self.teardown(guild.id)
        await reply(interaction, "👋 Queue cleared and disconnected.")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Leave once the bot is alone, instead of idling in an empty channel."""
        voice = member.guild.voice_client
        if not isinstance(voice, discord.VoiceClient):
            return

        if self.bot.user is not None and member.id == self.bot.user.id:
            if after.channel is None:
                await self.teardown(member.guild.id)
            return

        if before.channel is None or before.channel.id != voice.channel.id:
            return
        if any(not m.bot for m in before.channel.members):
            return

        log.info("Voice channel empty in guild %s — leaving", member.guild.id)
        await self.teardown(member.guild.id)


async def setup(bot: MonitorBot) -> None:
    await bot.add_cog(Music(bot))
