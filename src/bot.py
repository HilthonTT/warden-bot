"""Entry point.

Responsibilities are deliberately narrow: load the environment, build
:class:`~core.bot.WardenBot`, and translate startup failures into a message
an operator can act on. Everything else lives in ``core``, ``services``, and
``cogs``.

Run with ``python src/bot.py`` (or ``python -m bot`` from ``src/``).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import discord
from dotenv import load_dotenv

from core.bot import WardenBot
from core.settings import ConfigError, Settings

log = logging.getLogger("bot")

EXIT_OK = 0
EXIT_CONFIG = 2

INTENTS_HELP = (
    "Privileged intents are not enabled for this application.\n"
    "Open https://discord.com/developers/applications -> your app -> Bot, and "
    "enable SERVER MEMBERS INTENT, PRESENCE INTENT and MESSAGE CONTENT INTENT."
)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, bot: WardenBot) -> None:
    """Close the bot cleanly on SIGTERM/SIGINT where the platform allows it.

    ``add_signal_handler`` is POSIX-only; on Windows we fall back to the
    default KeyboardInterrupt path, which ``main`` already handles.
    """

    def request_close() -> None:
        log.info("Signal received, shutting down…")
        loop.create_task(bot.close())

    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, request_close)
        except (NotImplementedError, RuntimeError):
            log.debug("Signal handler for %s unavailable on this platform", name)


async def run() -> int:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO)
        log.error("%s", exc)
        return EXIT_CONFIG

    settings.configure_logging()

    loop = asyncio.get_running_loop()
    async with WardenBot(settings) as bot:
        _install_signal_handlers(loop, bot)
        try:
            await bot.start(settings.token)
        except discord.LoginFailure:
            log.error("Discord rejected DISCORD_TOKEN — check it hasn't been reset.")
            return EXIT_CONFIG
        except discord.PrivilegedIntentsRequired:
            log.error("%s", INTENTS_HELP)
            return EXIT_CONFIG
    return EXIT_OK


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Shutting down.")
        return EXIT_OK


if __name__ == "__main__":
    load_dotenv()
    sys.exit(main())
