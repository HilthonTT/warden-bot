"""Smoke test: every cog must actually load into a real bot instance.

This catches the class of mistake unit tests miss — a mis-ordered command
decorator, a duplicate command name, a bad import — without touching the
network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from discord import app_commands

import core.bot as core_bot
from core.bot import WardenBot, discover_extensions
from core.settings import Settings

COGS_DIR = Path(core_bot.__file__).resolve().parent.parent / "cogs"

EXPECTED_COMMANDS = {
    "automod",
    "automod_reload",
    "avatar",
    "ban",
    "clear_honeypot",
    "clearwarnings",
    "config",
    "delwarn",
    "documentation",
    "join",
    "kick",
    "nowplaying",
    "pause",
    "play",
    "queue",
    "resume",
    "set_honeypot",
    "set_modlog",
    "set_warn_thresholds",
    "skip",
    "stop",
    "ticket_add",
    "ticket_close",
    "ticket_config",
    "ticket_open",
    "ticket_panel",
    "ticket_remove",
    "trivia",
    "unban",
    "userinfo",
    "volume",
    "warn",
    "warnings",
}


@pytest_asyncio.fixture
async def bot(tmp_path: Path) -> AsyncIterator[WardenBot]:
    instance = WardenBot(Settings(token="test", db_path=tmp_path / "bot.sqlite3"))
    try:
        yield instance
    finally:
        await instance.close()


async def test_every_extension_loads(bot: WardenBot) -> None:
    for extension in discover_extensions(COGS_DIR):
        await bot.load_extension(extension)

    assert bot.cogs


async def test_the_expected_command_tree_is_registered(bot: WardenBot) -> None:
    for extension in discover_extensions(COGS_DIR):
        await bot.load_extension(extension)

    names = {command.name for command in bot.tree.get_commands()}
    assert names >= EXPECTED_COMMANDS


async def test_moderation_commands_declare_their_permissions(bot: WardenBot) -> None:
    await bot.load_extension("cogs.moderation")

    commands = {c.name: c for c in bot.tree.get_commands()}
    ban = commands["ban"]
    assert isinstance(ban, app_commands.Command)
    assert ban.default_permissions is not None
    assert ban.default_permissions.ban_members
    assert ban.guild_only
    assert ban.checks, "the runtime permission check must be attached too"


async def test_the_user_info_context_menu_is_registered(bot: WardenBot) -> None:
    await bot.load_extension("cogs.user_info")

    assert any(
        isinstance(command, app_commands.ContextMenu) and command.name == "User Info"
        for command in bot.tree.get_commands()
    )


async def test_unloading_removes_the_context_menu(bot: WardenBot) -> None:
    await bot.load_extension("cogs.user_info")
    await bot.unload_extension("cogs.user_info")

    assert not bot.tree.get_commands()


async def test_loading_an_extension_twice_is_refused(bot: WardenBot) -> None:
    await bot.load_extension("cogs.admin")

    with pytest.raises(Exception, match="already loaded"):
        await bot.load_extension("cogs.admin")
