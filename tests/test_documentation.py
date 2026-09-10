from __future__ import annotations

import discord

from cogs.documentation import (
    OTHER_TIER,
    Documentation,
    chunk_lines,
    group_by_tier,
    required_permission,
)


class FakeCommand:
    def __init__(self, name: str, permissions: discord.Permissions | None) -> None:
        self.qualified_name = name
        self.description = f"does {name}"
        self.default_permissions = permissions


def test_ungated_commands_have_no_tier_permission() -> None:
    assert required_permission(FakeCommand("trivia", None)) is None  # type: ignore[arg-type]


def test_gated_commands_map_to_their_tier() -> None:
    command = FakeCommand("ban", discord.Permissions(ban_members=True))

    assert required_permission(command) == "ban_members"  # type: ignore[arg-type]


def test_unknown_permissions_fall_into_the_other_tier() -> None:
    command = FakeCommand("weird", discord.Permissions(manage_emojis=True))

    assert required_permission(command) == OTHER_TIER  # type: ignore[arg-type]


def test_group_by_tier_sorts_commands_within_a_tier() -> None:
    commands = [
        FakeCommand("warn", discord.Permissions(kick_members=True)),
        FakeCommand("kick", discord.Permissions(kick_members=True)),
        FakeCommand("trivia", None),
    ]

    grouped = group_by_tier(commands)  # type: ignore[arg-type]

    assert [c.qualified_name for c in grouped["kick_members"]] == ["kick", "warn"]
    assert [c.qualified_name for c in grouped[None]] == ["trivia"]


def test_chunk_lines_keeps_blocks_within_the_field_cap() -> None:
    lines = [f"line {i} " + "x" * 100 for i in range(50)]

    blocks = chunk_lines(lines)

    assert len(blocks) > 1
    assert all(len(block) <= 1024 for block in blocks)
    assert "\n".join(blocks).count("line 0 ") == 1


def test_chunk_lines_never_splits_a_single_long_line() -> None:
    assert chunk_lines(["y" * 3_000]) == ["y" * 3_000]


def test_visibility_follows_permissions() -> None:
    member = discord.Permissions(kick_members=True)
    admin = discord.Permissions.all()

    assert Documentation._visible(member, None)
    assert Documentation._visible(member, "kick_members")
    assert not Documentation._visible(member, "ban_members")
    assert not Documentation._visible(member, OTHER_TIER)
    assert Documentation._visible(admin, "ban_members")
    assert Documentation._visible(admin, OTHER_TIER)
