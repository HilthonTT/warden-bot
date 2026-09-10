"""Automod: honeypot, language filter, spam filter, and invite filter.

Everything hangs off ``on_message``, in order of urgency:

1. **Honeypot** — any non-staff message in the configured honeypot channel
   triggers an immediate ban plus 24h of message cleanup. It exists to catch
   self-propagating spam bots that post in every channel they can read.
2. **Spam filter** — flooding, repeated messages, and mass mentions, judged
   from a short in-memory window of the member's recent activity.
3. **Invite filter** — links to other Discord servers.
4. **Language filter** — the message is normalised (see
   :mod:`services.word_filter`) and matched against the guild's own word list.

Every hit after the honeypot funnels into the same place: the message is
deleted and a warning case is recorded, so automod warnings count toward the
auto-kick and auto-ban thresholds exactly like a moderator's ``/warn``.

Staff (any moderation permission, or the configured staff role) are exempt
from all four.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.checks import guild_permissions, is_staff
from core.cog import WardenCog
from core.constants import EMBED_DESCRIPTION_MAX, truncate
from core.embeds import base_embed, channel_label, info_embed
from core.responses import fail, reply, try_dm
from data.models import CaseAction, GuildConfig

if TYPE_CHECKING:
    from core.bot import WardenBot

log = logging.getLogger(__name__)

HONEYPOT_DELETE_HISTORY = 86_400
PREVIEW_CHARS = 500
REASON_PREVIEW_CHARS = 200
WARN_NOTICE_DELETE_AFTER = 8
SECONDS_PER_DAY = 86_400

PUNISH_COOLDOWN_SECONDS = 10
MAX_WORDS_PER_ADD = 25

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg|discord\.me)/\S+",
    re.IGNORECASE,
)


class AutoMod(WardenCog):
    """Language filter, spam filter, and honeypot."""

    def __init__(self, bot: WardenBot) -> None:
        super().__init__(bot)
        self._recently_punished: dict[tuple[int, int], float] = {}

    word_list = app_commands.Group(
        name="words",
        description="Manage this server's filtered words.",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        member = message.author
        cfg = await self.config.get(message.guild.id)

        if cfg.honeypot_channel_id is not None and message.channel.id == cfg.honeypot_channel_id:
            await self._handle_honeypot(message, member, cfg)
            return

        if is_staff(member, cfg.staff_role_id):
            return

        spam = self.spam.inspect(
            message.guild.id,
            member.id,
            message.content,
            len(message.mentions) + len(message.role_mentions),
            cfg,
        )
        if spam is not None:
            await self._punish(message, member, cfg, f"Spam: {spam}", "🚫 Spam blocked")
            return

        if cfg.invite_filter_enabled and INVITE_RE.search(message.content):
            await self._punish(
                message, member, cfg, "Posted a Discord invite link", "🔗 Invite blocked"
            )
            return

        if cfg.automod_enabled and await self.words.matches(message.guild.id, message.content):
            await self._punish(
                message,
                member,
                cfg,
                f'Inappropriate language: "{message.content[:REASON_PREVIEW_CHARS]}"',
                "🤖 Auto-warn (language)",
            )

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
            f"Honeypot triggered in #{channel_name} (automatic spam-bot detection)", 512
        )
        try:
            await guild.ban(member, reason=reason, delete_message_seconds=HONEYPOT_DELETE_HISTORY)
        except discord.Forbidden:
            log.warning("Honeypot: missing Ban Members permission in guild %s", guild.id)
            return
        except discord.HTTPException:
            log.warning("Honeypot: ban failed for %s", member.id, exc_info=True)
            return

        bot_id = self.bot.user.id if self.bot.user else member.id
        case, _ = await self.db.add_case(guild.id, CaseAction.BAN, member.id, bot_id, reason)

        joined = f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "—"
        embed = base_embed(
            "🍯 Honeypot auto-ban",
            color=discord.Color.dark_red(),
            description=truncate(
                f"**User:** {member.mention} `{member}` (`{member.id}`)\n"
                f"**Case:** #{case.number}\n"
                f"**Channel:** {channel_label(message.channel)}\n"
                f"**Account age:** <t:{int(member.created_at.timestamp())}:R>\n"
                f"**Joined server:** {joined}\n"
                f"**Message preview:**\n{self._preview(message.content)}",
                EMBED_DESCRIPTION_MAX,
            ),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.modlog.send(guild, embed)

    async def _punish(
        self,
        message: discord.Message,
        member: discord.Member,
        cfg: GuildConfig,
        reason: str,
        title: str,
    ) -> None:
        """Delete the message and record a warning case.

        A flood produces one warning, not one per message: the same member is
        ignored for a few seconds after being punished, so ten spam messages
        don't push someone straight past the auto-ban threshold.
        """
        guild = message.guild
        assert guild is not None

        await self._delete(message, title)
        if self._on_cooldown(guild.id, member.id):
            return

        bot_id = self.bot.user.id if self.bot.user else member.id
        expires_at = (
            int(time.time()) + cfg.warn_expiry_days * SECONDS_PER_DAY
            if cfg.warn_expiry_days > 0
            else None
        )
        case, count = await self.db.add_case(
            guild.id,
            CaseAction.WARN,
            member.id,
            bot_id,
            truncate(reason, 512),
            expires_at=expires_at,
        )

        await try_dm(
            member,
            f"⚠️ Your message in **{guild.name}** was removed.\n"
            f"Reason: {reason}\nThis is warning **#{count}**.",
        )

        if isinstance(message.channel, discord.abc.Messageable):
            try:
                await message.channel.send(
                    f"{member.mention}, that message was removed. (warning #{count})",
                    delete_after=WARN_NOTICE_DELETE_AFTER,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                log.debug("Could not post the warning notice", exc_info=True)

        embed = base_embed(
            title,
            color=discord.Color.gold(),
            description=truncate(
                f"**User:** {member.mention} (`{member.id}`)\n"
                f"**Case:** #{case.number} • **Active warnings:** {count}\n"
                f"**Channel:** {channel_label(message.channel)}\n"
                f"**Reason:** {reason}\n"
                f"**Message:** {self._preview(message.content)}",
                EMBED_DESCRIPTION_MAX,
            ),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.modlog.send(guild, embed)

        await self.escalation.apply(guild, member, count, cfg)

    def _on_cooldown(self, guild_id: int, user_id: int) -> bool:
        """True if this member was punished moments ago; records the punishment."""
        key = (guild_id, user_id)
        now = time.monotonic()
        last = self._recently_punished.get(key)
        if last is not None and now - last < PUNISH_COOLDOWN_SECONDS:
            return True
        if len(self._recently_punished) > 10_000:
            self._recently_punished.clear()
        self._recently_punished[key] = now
        return False

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
    def _preview(content: str) -> str:
        """Escaped, length-capped quote of a user message for an embed."""
        if not content:
            return "*(no text)*"
        return discord.utils.escape_markdown(truncate(content, PREVIEW_CHARS))

    @app_commands.command(name="automod", description="Toggle the bad-language auto-filter.")
    @app_commands.describe(enabled="Whether to enable the filter for this server.")
    @guild_permissions(manage_guild=True)
    async def automod_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        assert interaction.guild is not None
        await self.config.update(interaction.guild.id, automod_enabled=enabled)
        await reply(
            interaction,
            f"✅ The language filter is now **{'enabled' if enabled else 'disabled'}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="automod_reload",
        description="Reload the default word list from disk without restarting.",
    )
    @guild_permissions(manage_guild=True)
    async def automod_reload(self, interaction: discord.Interaction) -> None:
        count = self.words.reload_defaults()
        await reply(
            interaction,
            f"✅ Reloaded {count} default word(s). Per-server words are unaffected.",
            ephemeral=True,
        )

    @word_list.command(name="add", description="Add words to this server's filter.")
    @app_commands.describe(words="One or more words, separated by spaces or commas.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def words_add(self, interaction: discord.Interaction, words: str) -> None:
        assert interaction.guild is not None
        candidates = [part for part in re.split(r"[,\s]+", words) if part][:MAX_WORDS_PER_ADD]
        if not candidates:
            await fail(interaction, "Give at least one word to add.")
            return

        added = await self.words.add(interaction.guild.id, candidates)
        await reply(
            interaction,
            f"✅ Added **{added}** new word(s); {len(candidates) - added} were already "
            f"on the list.",
            ephemeral=True,
        )

    @word_list.command(name="remove", description="Remove a word from this server's filter.")
    @app_commands.describe(word="The word to remove.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def words_remove(self, interaction: discord.Interaction, word: str) -> None:
        assert interaction.guild is not None
        if await self.words.remove(interaction.guild.id, word):
            await reply(interaction, f"✅ Removed `{truncate(word, 60)}`.", ephemeral=True)
            return
        await fail(
            interaction,
            f"`{truncate(word, 60)}` isn't in this server's list. Words from the "
            f"shipped default list can't be removed individually.",
        )

    @word_list.command(name="list", description="Show this server's added words.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def words_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        custom = await self.words.custom_words(interaction.guild.id)
        embed = info_embed(
            "Filtered words",
            f"**{len(self.words.defaults)}** shipped default(s) apply to every server.\n"
            + (
                f"**{len(custom)}** added here:\n"
                + truncate(", ".join(f"`{word}`" for word in custom), 3_000)
                if custom
                else "This server hasn't added any of its own."
            ),
        )
        await reply(interaction, embed=embed, ephemeral=True)

    @word_list.command(name="reset", description="Remove every word this server added.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def words_reset(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        cleared = await self.words.clear(interaction.guild.id)
        await reply(
            interaction,
            f"✅ Removed **{cleared}** custom word(s). The shipped defaults still apply.",
            ephemeral=True,
        )


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(AutoMod(bot))
