"""Message and member event logging.

The mod log only ever recorded what the bot itself did. When a member deleted
their own message or quietly edited it, nothing was captured — which is
exactly the evidence moderators come looking for afterwards.

Events go to the channel set with ``/set_eventlog``, kept separate from the
mod log so a busy edit/delete feed doesn't bury the record of moderator
actions. With no event log configured, this cog does nothing.

These listeners fire from the message cache, so an edit or deletion of a
message from before the last restart won't be logged. That's a deliberate
trade: the raw events carry no content, which would make the log useless.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from core.cog import WardenCog
from core.constants import EMBED_FIELD_VALUE_MAX, truncate
from core.embeds import add_field, base_embed, channel_label

if TYPE_CHECKING:
    from core.bot import WardenBot

log = logging.getLogger(__name__)

CONTENT_PREVIEW = 900
NEW_ACCOUNT_HOURS = 24
SECONDS_PER_HOUR = 3_600


def quote(content: str) -> str:
    """Escaped, length-capped message content for an embed field."""
    if not content:
        return "*(no text)*"
    return truncate(
        discord.utils.escape_markdown(content), min(CONTENT_PREVIEW, EMBED_FIELD_VALUE_MAX)
    )


class EventLog(WardenCog):
    """Passive logging of message and membership changes."""

    @staticmethod
    def _loggable(message: discord.Message) -> bool:
        return message.guild is not None and not message.author.bot

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not self._loggable(message):
            return
        assert message.guild is not None

        embed = base_embed("🗑️ Message deleted", color=discord.Color.red())
        add_field(embed, "Author", f"{message.author.mention} (`{message.author.id}`)", inline=True)
        add_field(embed, "Channel", channel_label(message.channel), inline=True)
        add_field(embed, "Content", quote(message.content))
        if message.attachments:
            add_field(
                embed,
                f"Attachments ({len(message.attachments)})",
                "\n".join(attachment.filename for attachment in message.attachments),
            )
        embed.set_footer(text=f"Message ID: {message.id}")
        await self.modlog.send_event(message.guild, embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        """Summarise a bulk delete instead of posting one embed per message."""
        if not messages:
            return
        first = messages[0]
        if first.guild is None:
            return

        authors = {message.author.id for message in messages if not message.author.bot}
        embed = base_embed("🗑️ Messages bulk-deleted", color=discord.Color.dark_red())
        add_field(embed, "Channel", channel_label(first.channel), inline=True)
        add_field(embed, "Messages", str(len(messages)), inline=True)
        add_field(embed, "Distinct authors", str(len(authors)), inline=True)
        await self.modlog.send_event(first.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not self._loggable(before) or before.content == after.content:
            return
        assert before.guild is not None

        embed = base_embed("✏️ Message edited", color=discord.Color.gold())
        add_field(embed, "Author", f"{before.author.mention} (`{before.author.id}`)", inline=True)
        add_field(embed, "Channel", channel_label(before.channel), inline=True)
        add_field(embed, "Before", quote(before.content))
        add_field(embed, "After", quote(after.content))
        embed.set_footer(text=f"Message ID: {before.id}")
        await self.modlog.send_event(before.guild, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        created = int(member.created_at.timestamp())
        age_hours = (discord.utils.utcnow() - member.created_at).total_seconds() / SECONDS_PER_HOUR

        embed = base_embed(
            "📥 Member joined",
            color=discord.Color.orange()
            if age_hours < NEW_ACCOUNT_HOURS
            else discord.Color.green(),
            description=f"{member.mention} `{member}` (`{member.id}`)",
        )
        add_field(embed, "Account created", f"<t:{created}:F>\n(<t:{created}:R>)", inline=True)
        add_field(embed, "Members now", str(member.guild.member_count or "?"), inline=True)
        if age_hours < NEW_ACCOUNT_HOURS:
            add_field(embed, "⚠️ Note", "This account is less than a day old.", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.modlog.send_event(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        roles = [role.mention for role in reversed(member.roles) if not role.is_default()]
        embed = base_embed(
            "📤 Member left",
            color=discord.Color.dark_grey(),
            description=f"{member} (`{member.id}`)",
        )
        if member.joined_at is not None:
            add_field(
                embed,
                "Joined",
                f"<t:{int(member.joined_at.timestamp())}:R>",
                inline=True,
            )
        add_field(
            embed,
            f"Roles ({len(roles)})",
            truncate(" ".join(roles), EMBED_FIELD_VALUE_MAX) if roles else "—",
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.modlog.send_event(member.guild, embed)


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(EventLog(bot))
