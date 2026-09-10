"""``/documentation`` — a help command generated from the live command tree.

The previous version kept a hand-written list of every command and its
summary, which drifted the moment a cog was added: ``/trivia`` and the whole
music cog were missing from it. Here the listing is derived from the commands
actually registered on the tree, grouped by the permission each one declares,
so a new command shows up the moment it is loaded.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from core.cog import WardenCog
from core.constants import EMBED_FIELD_VALUE_MAX, EMBED_MAX_FIELDS
from core.embeds import add_field, info_embed
from core.responses import reply

if TYPE_CHECKING:
    from core.bot import WardenBot

TIERS: tuple[tuple[str, str | None], ...] = (
    ("📖 Everyone", None),
    ("🛡️ Moderator — Kick Members", "kick_members"),
    ("⚖️ Senior Moderator — Ban Members", "ban_members"),
    ("🎟️ Staff — Manage Channels", "manage_channels"),
    ("👑 Administrator — Manage Server", "manage_guild"),
)
OTHER_TIER = "🔒 Other"


def required_permission(command: app_commands.Command) -> str | None:
    """Return the tier permission a command is gated on, if any.

    Reads ``@app_commands.default_permissions``, which
    :func:`core.checks.guild_permissions` sets alongside the runtime check.
    """
    declared = getattr(command, "default_permissions", None)
    if declared is None:
        return None
    for _, permission in TIERS:
        if permission is not None and getattr(declared, permission, False):
            return permission
    return OTHER_TIER


def walk_commands(
    commands: Iterable[app_commands.Command | app_commands.Group | app_commands.ContextMenu],
) -> list[app_commands.Command]:
    """Flatten groups into leaf commands, dropping context menus.

    Context menus are invoked by right-clicking, not by name, so they are
    mentioned in the footer instead of listed as slash commands.
    """
    leaves: list[app_commands.Command] = []
    for command in commands:
        if isinstance(command, app_commands.Group):
            leaves.extend(walk_commands(command.commands))
        elif isinstance(command, app_commands.Command):
            leaves.append(command)
    return leaves


def group_by_tier(
    commands: Iterable[app_commands.Command],
) -> dict[str | None, list[app_commands.Command]]:
    grouped: dict[str | None, list[app_commands.Command]] = {}
    for command in commands:
        grouped.setdefault(required_permission(command), []).append(command)
    for entries in grouped.values():
        entries.sort(key=lambda c: c.qualified_name)
    return grouped


def chunk_lines(lines: list[str], limit: int = EMBED_FIELD_VALUE_MAX) -> list[str]:
    """Pack lines into embed-field-sized blocks without splitting a line."""
    blocks: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        if current and length + len(line) + 1 > limit:
            blocks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        blocks.append("\n".join(current))
    return blocks


class Documentation(WardenCog):
    """Self-documenting help."""

    @staticmethod
    def _visible(perms: discord.Permissions, permission: str | None) -> bool:
        """Whether a tier should be shown to a member with ``perms``."""
        if permission is None:
            return True
        if permission == OTHER_TIER:
            return perms.administrator
        return getattr(perms, permission, False)

    @app_commands.command(
        name="documentation",
        description="Show every command you can use, grouped by permission tier.",
    )
    @app_commands.guild_only()
    async def documentation(self, interaction: discord.Interaction) -> None:
        perms = interaction.permissions
        grouped = group_by_tier(walk_commands(self.bot.tree.get_commands()))

        embed = info_embed(
            "Warden — Commands",
            "Commands you have access to in this server. Anything you can't run is hidden.",
        )

        fields = 0
        for title, permission in (*TIERS, (OTHER_TIER, OTHER_TIER)):
            entries = grouped.get(permission, [])
            if not entries or not self._visible(perms, permission):
                continue

            lines = [f"`/{c.qualified_name}` — {c.description}" for c in entries]
            for index, block in enumerate(chunk_lines(lines)):
                if fields >= EMBED_MAX_FIELDS:
                    break
                add_field(embed, title if index == 0 else f"{title} (cont.)", block)
                fields += 1

        if fields == 0:
            add_field(
                embed,
                "No commands available",
                "You don't have access to any commands in this server.",
            )

        embed.set_footer(text="Right-click a user → Apps → User Info also works.")
        await reply(interaction, embed=embed, ephemeral=True)


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(Documentation(bot))
