"""Global application-command error handling."""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from .responses import reply

log = logging.getLogger(__name__)


def _describe(error: app_commands.AppCommandError) -> str | None:
    """Map an expected error to a user-facing message, or None if unexpected."""
    if isinstance(error, app_commands.MissingPermissions):
        return f"❌ You're missing permission: `{', '.join(error.missing_permissions)}`"
    if isinstance(error, app_commands.BotMissingPermissions):
        return f"❌ I'm missing permission: `{', '.join(error.missing_permissions)}`"
    if isinstance(error, app_commands.CommandOnCooldown):
        return f"❌ On cooldown — try again in {error.retry_after:.1f}s."
    if isinstance(error, app_commands.NoPrivateMessage):
        return "❌ That command can only be used in a server."
    if isinstance(error, app_commands.TransformerError):
        return "❌ I couldn't understand one of the options you gave."
    if isinstance(error, app_commands.CheckFailure):
        return "❌ You can't use that command here."
    return None


async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    """Report expected failures plainly; log unexpected ones with context."""
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original  # type: ignore[assignment]

    message = _describe(error) if isinstance(error, app_commands.AppCommandError) else None
    if message is None:
        log.exception(
            "Unhandled error in /%s (guild=%s user=%s)",
            interaction.command.qualified_name if interaction.command else "?",
            interaction.guild_id,
            interaction.user.id,
            exc_info=error,
        )
        message = "⚠️ Something went wrong while running that command."

    await reply(interaction, message, ephemeral=True)
