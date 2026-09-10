"""Environment configuration, parsed and validated once at startup.

Reading ``os.getenv`` from arbitrary modules makes the bot's contract with
its environment impossible to see. Everything it needs is declared here,
validated eagerly, and passed down explicitly.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from pathlib import Path

DEFAULT_DB_PATH = "data/bot.sqlite3"
DEFAULT_ACTIVITY = "Always monitoring your behavior"
VALID_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


class ConfigError(RuntimeError):
    """Raised when the environment is missing or malformed."""


def _optional_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    """Fully resolved runtime configuration."""

    token: str
    db_path: Path
    dev_guild_id: int | None = None
    activity_status: str = DEFAULT_ACTIVITY
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the process environment.

        Raises:
            ConfigError: if a required variable is absent or a value is malformed.
        """
        token = (os.getenv("DISCORD_TOKEN") or "").strip()
        if not token:
            raise ConfigError("DISCORD_TOKEN is not set — copy .env.example to .env")

        level = (os.getenv("LOG_LEVEL") or "INFO").strip().upper() or "INFO"
        if level not in VALID_LOG_LEVELS:
            raise ConfigError(
                f"LOG_LEVEL must be one of {', '.join(VALID_LOG_LEVELS)}, got {level!r}",
            )

        activity = (os.getenv("ACTIVITY_STATUS") or "").strip().strip('"').strip("'")

        return cls(
            token=token,
            db_path=Path((os.getenv("BOT_DB_PATH") or "").strip() or DEFAULT_DB_PATH),
            dev_guild_id=_optional_int("DEV_GUILD_ID"),
            activity_status=activity or DEFAULT_ACTIVITY,
            log_level=level,
        )

    def configure_logging(self) -> None:
        """Apply the configured level to the root logger."""
        logging.basicConfig(
            level=getattr(logging, self.log_level, logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        if self.log_level != "DEBUG":
            logging.getLogger("discord").setLevel(logging.WARNING)
