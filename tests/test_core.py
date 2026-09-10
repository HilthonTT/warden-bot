from __future__ import annotations

from pathlib import Path

import discord
import pytest

from core.bot import discover_extensions
from core.constants import (
    EMBED_DESCRIPTION_MAX,
    EMBED_FIELD_VALUE_MAX,
    EMBED_TITLE_MAX,
    truncate,
)
from core.embeds import action_embed, add_field, base_embed
from data.models import GuildConfig, Ticket


class FakeUser:
    def __init__(self, user_id: int, name: str) -> None:
        self.id = user_id
        self.name = name
        self.mention = f"<@{user_id}>"
        self.display_avatar = type("Asset", (), {"url": "https://example.invalid/a.png"})()

    def __str__(self) -> str:
        return self.name


def test_truncate_leaves_short_text_alone() -> None:
    assert truncate("hello", 10) == "hello"


def test_truncate_marks_elision_and_respects_the_limit() -> None:
    result = truncate("x" * 50, 10)

    assert len(result) == 10
    assert result.endswith("…")


def test_truncate_handles_a_limit_shorter_than_the_suffix() -> None:
    assert truncate("abcdef", 1) == "a"


def test_add_field_substitutes_a_dash_for_empty_values() -> None:
    embed = base_embed("Title", color=discord.Color.blurple())

    add_field(embed, "Reason", "")

    assert embed.fields[0].value == "—"


def test_add_field_truncates_to_the_discord_cap() -> None:
    embed = base_embed("Title", color=discord.Color.blurple())

    add_field(embed, "Reason", "y" * 5_000)

    assert len(embed.fields[0].value or "") == EMBED_FIELD_VALUE_MAX


def test_base_embed_truncates_title_and_description() -> None:
    embed = base_embed(
        "t" * 1_000,
        color=discord.Color.blurple(),
        description="d" * 10_000,
    )

    assert len(embed.title or "") == EMBED_TITLE_MAX
    assert len(embed.description or "") == EMBED_DESCRIPTION_MAX


def test_action_embed_survives_an_over_long_reason() -> None:
    target = FakeUser(1, "target")
    moderator = FakeUser(2, "mod")

    embed = action_embed(
        "Banned",
        discord.Color.red(),
        target,
        moderator,
        "why " * 2_000,  # type: ignore[arg-type]
    )

    assert all(len(field.value or "") <= EMBED_FIELD_VALUE_MAX for field in embed.fields)


def test_ticket_label_is_zero_padded() -> None:
    ticket = Ticket(1, 2, 3, number=7, status="open", created_at=0)

    assert ticket.label == "0007"
    assert ticket.is_open


def test_closed_ticket_is_not_open() -> None:
    assert not Ticket(1, 2, 3, 7, "closed", 0).is_open


def test_guild_config_from_row_coerces_the_automod_flag() -> None:
    row = {
        "guild_id": 1,
        "mod_log_channel_id": None,
        "event_log_channel_id": None,
        "honeypot_channel_id": None,
        "staff_role_id": None,
        "ticket_category_id": None,
        "warn_kick_threshold": 3,
        "warn_ban_threshold": 5,
        "warn_expiry_days": 0,
        "automod_enabled": 0,
        "antispam_enabled": 1,
        "antispam_message_limit": 5,
        "antispam_window_seconds": 5,
        "antispam_mention_limit": 5,
        "invite_filter_enabled": 0,
        "min_account_age_hours": 0,
        "raid_join_threshold": 0,
        "raid_join_window_seconds": 60,
    }

    config = GuildConfig.from_row(row)

    assert config.automod_enabled is False
    assert config.antispam_enabled is True


@pytest.fixture
def cogs_tree(tmp_path: Path) -> Path:
    (tmp_path / "admin.py").write_text("", encoding="utf-8")
    (tmp_path / "tickets.py").write_text("", encoding="utf-8")
    (tmp_path / "_helpers.py").write_text("", encoding="utf-8")
    (tmp_path / "music").mkdir()
    (tmp_path / "music" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "notacog").mkdir()
    (tmp_path / "notacog" / "thing.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    return tmp_path


def test_discover_extensions_finds_modules_and_packages(cogs_tree: Path) -> None:
    assert discover_extensions(cogs_tree) == [
        "cogs.admin",
        "cogs.tickets",
        "cogs.music",
    ]


def test_discover_extensions_tolerates_a_missing_directory(tmp_path: Path) -> None:
    assert discover_extensions(tmp_path / "absent") == []
