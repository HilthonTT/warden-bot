"""Base class giving every cog typed access to the bot's shared services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from data.db import Database
    from services import EscalationService, GuildConfigService, ModLogService

    from .bot import MonitorBot


class MonitorCog(commands.Cog):
    """Cog base that carries a :class:`~core.bot.MonitorBot`.

    Cogs used to reach services through ``self.bot.db  # type: ignore`` and
    ``bot.get_cog("AutoMod")``; both hid real coupling from the type checker.
    """

    def __init__(self, bot: MonitorBot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db

    @property
    def config(self) -> GuildConfigService:
        return self.bot.config

    @property
    def modlog(self) -> ModLogService:
        return self.bot.modlog

    @property
    def escalation(self) -> EscalationService:
        return self.bot.escalation
