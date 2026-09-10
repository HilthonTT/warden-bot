"""Reusable permission and hierarchy guards for application commands.

Every command in this bot needs the same three declarations: the Discord-side
default permission (so the command is hidden in the UI), a server-side check
(so a permission override can't be used to bypass it), and guild-only scoping.
:func:`guild_permissions` applies all three from one source of truth, which
removes the hand-written "you need X" branch that used to open every callback.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import discord
from discord import app_commands

T = TypeVar("T")


def guild_permissions(**perms: bool) -> Callable[[T], T]:
    """Restrict a command to guild members holding ``perms``.

    Combines, in one decorator:

    * ``@app_commands.default_permissions`` — hides the command from members
      who can't use it (a client-side convenience Discord can override).
    * ``@app_commands.checks.has_permissions`` — the authoritative check; it
      raises :class:`~discord.app_commands.MissingPermissions`, which the
      global error handler renders consistently.
    * ``@app_commands.guild_only`` — permissions are meaningless in DMs.

    Administrators pass implicitly: Discord resolves ``interaction.permissions``
    with every bit set for them.
    """

    def decorator(func: T) -> T:
        func = app_commands.guild_only()(func)
        func = app_commands.default_permissions(**perms)(func)
        return app_commands.checks.has_permissions(**perms)(func)

    return decorator


def is_staff(member: discord.Member, staff_role_id: int | None) -> bool:
    """True if ``member`` should be treated as staff for automod exemptions.

    Deliberately a wide net — any of the standard moderation permissions, or
    the guild's configured staff role. It is far worse to auto-ban a moderator
    who fat-fingered the honeypot than to let one slip past the word filter.
    """
    perms = member.guild_permissions
    if (
        perms.administrator
        or perms.manage_guild
        or perms.manage_messages
        or perms.kick_members
        or perms.ban_members
    ):
        return True
    return staff_role_id is not None and any(r.id == staff_role_id for r in member.roles)


def hierarchy_error(
    interaction: discord.Interaction,
    target: discord.Member,
) -> str | None:
    """Return a refusal reason for moderating ``target``, or None if allowed.

    Guards, in order: self-moderation, the guild owner, the bot itself, roles
    at or above the bot's, and roles at or above the invoking moderator's.
    The guild owner is exempt from the last check — they outrank everyone.
    """
    guild = interaction.guild
    if guild is None:
        return "That command can only be used in a server."
    if target.id == interaction.user.id:
        return "You cannot moderate yourself."
    if target.id == guild.owner_id:
        return "You cannot moderate the server owner."
    if interaction.client.user is not None and target.id == interaction.client.user.id:
        return "I cannot moderate myself."

    me = guild.me
    if me is not None and target.top_role >= me.top_role:
        return "I cannot moderate someone whose top role is at or above mine."

    invoker = interaction.user
    if (
        isinstance(invoker, discord.Member)
        and invoker.id != guild.owner_id
        and target.top_role >= invoker.top_role
    ):
        return "You cannot moderate someone whose top role is at or above yours."
    return None
