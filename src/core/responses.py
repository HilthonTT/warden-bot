"""Interaction reply helpers.

An interaction can only be responded to once; every later reply has to go
through the followup webhook. Cogs that ``defer()`` and then hit an error
path got this wrong in several places, so the branch lives here instead.
"""

from __future__ import annotations

import logging

import discord
from discord.utils import MISSING

log = logging.getLogger(__name__)


async def reply(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = False,
) -> None:
    """Reply to ``interaction`` whether or not it has already been answered.

    Never raises: a failed error message must not mask the original error.
    """
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content=content if content is not None else MISSING,
                embed=embed if embed is not None else MISSING,
                view=view if view is not None else MISSING,
                ephemeral=ephemeral,
            )
        else:
            await interaction.response.send_message(
                content=content,
                embed=embed if embed is not None else MISSING,
                view=view if view is not None else MISSING,
                ephemeral=ephemeral,
            )
    except discord.HTTPException:
        log.debug("Could not deliver interaction reply", exc_info=True)


async def fail(interaction: discord.Interaction, message: str) -> None:
    """Send an ephemeral failure notice."""
    await reply(interaction, f"❌ {message}", ephemeral=True)


async def try_dm(user: discord.abc.Messageable, message: str) -> bool:
    """Best-effort DM.

    Closed DMs are a normal user preference, not an operational error, so the
    caller gets a bool instead of an exception.
    """
    try:
        await user.send(message)
    except discord.HTTPException:
        log.debug("Could not DM user", exc_info=True)
        return False
    return True
