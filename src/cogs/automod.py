"""Automod: honeypot and bad-language filter.

Two behaviours hang off ``on_message``:

1. **Honeypot** — any non-staff message in the configured honeypot channel
   triggers an immediate ban plus 24h of message cleanup. It exists to catch
   self-propagating spam bots that post in every channel they can read.
2. **Language filter** — the message is normalised (see
   :mod:`services.word_filter`) and matched against the guild's word list. A
   hit deletes the message and records a warning through the same pipeline as
   ``/warn``, so it counts toward the auto-kick / auto-ban thresholds.

Staff (any moderation permission, or the configured staff role) are exempt
from both.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.checks import guild_permissions, is_staff
from core.cog import WardenCog
from core.constants import truncate
from core.embeds import base_embed
from core.responses import reply, try_dm
from data.models import GuildConfig
from services.word_filter import WordFilter

if TYPE_CHECKING:
    from core.bot import WardenBot

log = logging.getLogger(__name__)

HONEYPOT_DELETE_HISTORY = 86_400
PREVIEW_CHARS = 500
REASON_PREVIEW_CHARS = 200
WARN_NOTICE_DELETE_AFTER = 8


class AutoMod(WardenCog):
    """Bad-language filter and honeypot."""

    def __init__(self, bot: WardenBot) -> None:
        super().__init__(bot)
        self.filter = WordFilter()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        cfg = await self.config.get(message.guild.id)

        if cfg.honeypot_channel_id is not None and message.channel.id == cfg.honeypot_channel_id:
            await self._handle_honeypot(message, message.author, cfg)
            return

        if not cfg.automod_enabled:
            return
        if is_staff(message.author, cfg.staff_role_id):
            return
        if self.filter.matches(message.content):
            await self._handle_bad_language(message, message.author, cfg)

    async def _handle_honeypot(
        self,
        message: discord.Message,
        member: discord.Member,
        cfg: GuildConfig,
    ) -> None:
        guild = message.guild
        assert guild is not None

        if is_staff(member, cfg.staff_role_id):
            log.info("Honeypot hit by privileged user %s — ignored.", member.id)
            return

        await self._delete(message, "honeypot")

        channel_name = getattr(message.channel, "name", "unknown")
        reason = truncate(
            f"Honeypot triggered in #{channel_name} (automatic spam-bot detection)",
            512,
        )
        try:
            await guild.ban(
                member,
                reason=reason,
                delete_message_seconds=HONEYPOT_DELETE_HISTORY,
            )
        except discord.Forbidden:
            log.warning("Honeypot: missing Ban Members permission in guild %s", guild.id)
            return
        except discord.HTTPException:
            log.warning("Honeypot: ban failed for %s", member.id, exc_info=True)
            return

        joined = f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "—"
        embed = base_embed(
            "🍯 Honeypot Auto-Ban",
            color=discord.Color.dark_red(),
            description=(
                f"**User:** {member.mention} `{member}` (`{member.id}`)\n"
                f"**Channel:** {self._channel_label(message.channel)}\n"
                f"**Account age:** <t:{int(member.created_at.timestamp())}:R>\n"
                f"**Joined server:** {joined}\n"
                f"**Message preview:**\n{self._preview(message.content)}"
            ),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.modlog.send(guild, embed)

    async def _handle_bad_language(
        self,
        message: discord.Message,
        member: discord.Member,
        cfg: GuildConfig,
    ) -> None:
        guild = message.guild
        assert guild is not None

        await self._delete(message, "language filter")

        reason = truncate(
            f'Inappropriate language: "{message.content[:REASON_PREVIEW_CHARS]}"',
            512,
        )
        bot_id = self.bot.user.id if self.bot.user else 0
        warning_id, count = await self.db.add_warning(guild.id, member.id, bot_id, reason)

        await try_dm(
            member,
            f"⚠️ Your message in **{guild.name}** was removed for inappropriate "
            f"language.\nThis is warning **#{count}**.",
        )

        if isinstance(message.channel, discord.abc.Messageable):
            try:
                await message.channel.send(
                    f"{member.mention}, please watch your language. (warning #{count})",
                    delete_after=WARN_NOTICE_DELETE_AFTER,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                log.debug("Could not post the warning notice", exc_info=True)

        embed = base_embed(
            "🤖 Auto-Warn (language)",
            color=discord.Color.gold(),
            description=(
                f"**User:** {member.mention} (`{member.id}`)\n"
                f"**Channel:** {self._channel_label(message.channel)}\n"
                f"**Warning:** `#{warning_id}` • **Total:** {count}\n"
                f"**Message:** {self._preview(message.content)}"
            ),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.modlog.send(guild, embed)

        await self.escalation.apply(guild, member, count, cfg)

    @staticmethod
    async def _delete(message: discord.Message, context: str) -> None:
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            log.warning(
                "Missing Manage Messages to delete a message (%s) in channel %s",
                context,
                message.channel.id,
            )
        except discord.HTTPException:
            log.debug("Could not delete message (%s)", context, exc_info=True)

    @staticmethod
    def _channel_label(channel: object) -> str:
        """A channel mention when the type has one, else its name."""
        mention = getattr(channel, "mention", None)
        return str(mention) if mention else f"#{getattr(channel, 'name', 'unknown')}"

    @staticmethod
    def _preview(content: str) -> str:
        """Escaped, length-capped quote of a user message for an embed."""
        if not content:
            return "*(no text)*"
        return discord.utils.escape_markdown(truncate(content, PREVIEW_CHARS))

    @app_commands.command(
        name="automod",
        description="Toggle the bad-language auto-filter.",
    )
    @app_commands.describe(enabled="Whether to enable the filter for this server.")
    @guild_permissions(manage_guild=True)
    async def automod_toggle(
        self,
        interaction: discord.Interaction,
        enabled: bool,
    ) -> None:
        assert interaction.guild is not None
        await self.config.update(interaction.guild.id, automod_enabled=enabled)
        await reply(
            interaction,
            f"✅ The language filter is now **{'enabled' if enabled else 'disabled'}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="automod_reload",
        description="Reload the bad-words list from disk without restarting.",
    )
    @guild_permissions(manage_guild=True)
    async def automod_reload(self, interaction: discord.Interaction) -> None:
        count = self.filter.reload()
        await reply(interaction, f"✅ Reloaded {count} word(s).", ephemeral=True)


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(AutoMod(bot))
