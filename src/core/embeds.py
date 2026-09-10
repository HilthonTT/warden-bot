"""Embed factories.

All user-controlled text is truncated to Discord's per-field caps here. A
single over-long ``reason`` used to make the whole API call fail with a 400,
which surfaced to the moderator as "something went wrong" *after* the kick
had already happened.
"""

from __future__ import annotations

import discord

from .constants import (
    EMBED_DESCRIPTION_MAX,
    EMBED_FIELD_VALUE_MAX,
    EMBED_TITLE_MAX,
    truncate,
)

NO_VALUE = "—"


def base_embed(
    title: str,
    *,
    color: discord.Color,
    description: str | None = None,
) -> discord.Embed:
    """Timestamped embed with title/description pre-truncated."""
    return discord.Embed(
        title=truncate(title, EMBED_TITLE_MAX),
        description=(truncate(description, EMBED_DESCRIPTION_MAX) if description else None),
        color=color,
        timestamp=discord.utils.utcnow(),
    )


def add_field(
    embed: discord.Embed,
    name: str,
    value: str,
    *,
    inline: bool = False,
) -> discord.Embed:
    """Add a field, truncating the value and substituting a dash when empty."""
    return embed.add_field(
        name=name,
        value=truncate(value or NO_VALUE, EMBED_FIELD_VALUE_MAX),
        inline=inline,
    )


def action_embed(
    action: str,
    color: discord.Color,
    target: discord.abc.User,
    moderator: discord.abc.User,
    reason: str,
    extra: dict[str, str] | None = None,
) -> discord.Embed:
    """Standard mod-action report: who, by whom, why."""
    embed = base_embed(f"User {action}", color=color)
    embed.set_thumbnail(url=target.display_avatar.url)
    add_field(embed, "User", f"{target.mention}\n`{target}` (`{target.id}`)")
    add_field(embed, "Moderator", moderator.mention, inline=True)
    add_field(embed, "Reason", reason)
    for key, value in (extra or {}).items():
        add_field(embed, key, value, inline=True)
    return embed


def error_embed(message: str) -> discord.Embed:
    return base_embed("❌ Error", color=discord.Color.red(), description=message)


def success_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(title, color=discord.Color.green(), description=description)


def info_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(title, color=discord.Color.blurple(), description=description)


def channel_label(channel: object) -> str:
    """A channel mention when the type has one, else a readable name.

    Message events can arrive from channel types that have no ``mention``
    (a DM, a group channel), and a log line is not worth an AttributeError.
    """
    mention = getattr(channel, "mention", None)
    if mention:
        return str(mention)
    name = getattr(channel, "name", None)
    return f"#{name}" if name else "an unknown channel"
