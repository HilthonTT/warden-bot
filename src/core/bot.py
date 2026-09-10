"""The bot class: wiring, lifecycle, and extension discovery."""

from __future__ import annotations

import logging
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

from data.db import Database
from services import EscalationService, GuildConfigService, ModLogService

from .errors import on_app_command_error
from .settings import Settings

log = logging.getLogger(__name__)

COGS_PACKAGE = "cogs"
COGS_DIR = Path(__file__).resolve().parent.parent / COGS_PACKAGE


def discover_extensions(cogs_dir: Path) -> list[str]:
    """List importable extensions under ``cogs_dir``.

    An extension is either a top-level ``cogs/<name>.py`` module or a
    ``cogs/<name>/`` package whose ``__init__`` exports ``setup``. Names
    starting with ``_`` are skipped, which is how a module opts out of
    auto-loading.
    """
    extensions: list[str] = []
    if not cogs_dir.is_dir():
        log.error("Cogs directory not found at %s", cogs_dir)
        return extensions

    for module in sorted(cogs_dir.glob("*.py")):
        if not module.stem.startswith("_"):
            extensions.append(f"{COGS_PACKAGE}.{module.stem}")

    for package in sorted(p for p in cogs_dir.iterdir() if p.is_dir()):
        if package.name.startswith("_") or package.name == "__pycache__":
            continue
        if (package / "__init__.py").is_file():
            extensions.append(f"{COGS_PACKAGE}.{package.name}")
        else:
            log.warning(
                "Skipping %s: a cog package needs an __init__.py exporting setup()",
                package.name,
            )
    return extensions


class MonitorBot(commands.Bot):
    """Owns the shared services every cog depends on.

    Cogs reach these through :class:`core.cog.MonitorCog` rather than
    ``bot.get_cog(...)``, so the dependency graph is explicit and there is no
    load-order coupling between cogs.
    """

    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.presences = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.db = Database(settings.db_path)
        self.config = GuildConfigService(self.db)
        self.modlog = ModLogService(self.config)
        self.escalation = EscalationService(self.modlog)
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        """Shared HTTP session for outbound API calls made by cogs."""
        if self._session is None or self._session.closed:
            raise RuntimeError("HTTP session is not available — bot is not started")
        return self._session

    async def setup_hook(self) -> None:
        await self.db.connect()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "TheMonitorBot (+https://github.com/HilthonTT/TheMonitorBot)"},
        )

        from cogs.tickets import TicketCloseView, TicketPanelView

        self.add_view(TicketPanelView())
        self.add_view(TicketCloseView())

        await self._load_extensions()
        self.tree.on_error = on_app_command_error  # type: ignore[method-assign]
        await self._sync_commands()

    async def _load_extensions(self) -> None:
        loaded = 0
        for extension in discover_extensions(COGS_DIR):
            try:
                await self.load_extension(extension)
            except commands.ExtensionError:
                log.exception("Failed to load %s", extension)
            else:
                loaded += 1
                log.info("Loaded extension %s", extension)
        log.info("Loaded %d extension(s)", loaded)

    async def _sync_commands(self) -> None:
        """Publish the command tree.

        A dev guild syncs instantly; a global sync can take up to an hour to
        propagate, so DEV_GUILD_ID is the right setting while iterating.
        """
        try:
            if self.settings.dev_guild_id is not None:
                guild = discord.Object(id=self.settings.dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(
                    "Synced %d command(s) to dev guild %s",
                    len(synced),
                    self.settings.dev_guild_id,
                )
            else:
                synced = await self.tree.sync()
                log.info("Synced %d command(s) globally", len(synced))
        except discord.HTTPException:
            log.exception("Command sync failed; continuing with the published tree")

    async def close(self) -> None:
        await super().close()
        if self._session is not None and not self._session.closed:
            await self._session.close()
        await self.db.close()

    async def on_ready(self) -> None:
        if self.user is not None:
            log.info("Logged in as %s (id=%s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=self.settings.activity_status,
            ),
        )

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.config.invalidate(guild.id)
