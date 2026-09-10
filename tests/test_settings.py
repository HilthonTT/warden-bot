from __future__ import annotations

from pathlib import Path

import pytest

from core.settings import DEFAULT_ACTIVITY, ConfigError, Settings

ENV_VARS = (
    "DISCORD_TOKEN",
    "DEV_GUILD_ID",
    "ACTIVITY_STATUS",
    "BOT_DB_PATH",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_missing_token_is_rejected() -> None:
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        Settings.from_env()


def test_blank_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "   ")

    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        Settings.from_env()


def test_defaults_when_only_the_token_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "abc")

    settings = Settings.from_env()

    assert settings.token == "abc"
    assert settings.dev_guild_id is None
    assert settings.log_level == "INFO"
    assert settings.activity_status == DEFAULT_ACTIVITY
    assert settings.db_path == Path("data/bot.sqlite3")


def test_empty_optionals_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "abc")
    for name in ("DEV_GUILD_ID", "ACTIVITY_STATUS", "BOT_DB_PATH", "LOG_LEVEL"):
        monkeypatch.setenv(name, "")

    settings = Settings.from_env()

    assert settings.dev_guild_id is None
    assert settings.log_level == "INFO"
    assert settings.db_path == Path("data/bot.sqlite3")


def test_malformed_dev_guild_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "abc")
    monkeypatch.setenv("DEV_GUILD_ID", "not-a-number")

    with pytest.raises(ConfigError, match="DEV_GUILD_ID"):
        Settings.from_env()


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "abc")
    monkeypatch.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        Settings.from_env()


def test_quoted_activity_status_is_unwrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "abc")
    monkeypatch.setenv("ACTIVITY_STATUS", '"Watching everything"')

    assert Settings.from_env().activity_status == "Watching everything"


def test_values_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "abc")
    monkeypatch.setenv("DEV_GUILD_ID", "123")
    monkeypatch.setenv("BOT_DB_PATH", "/data/bot.sqlite3")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings.from_env()

    assert settings.dev_guild_id == 123
    assert settings.db_path == Path("/data/bot.sqlite3")
    assert settings.log_level == "DEBUG"
