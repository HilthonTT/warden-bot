"""Paging the case log.

Discord rejects an embed whose total text passes 6000 characters, so a page
has to close on length as well as on a case count. Eight cases with long
reasons overflowed it and failed ``/warnings`` outright.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from cogs.moderation import CASES_PER_PAGE, PAGE_CHAR_BUDGET, Moderation
from core.constants import EMBED_TOTAL_MAX
from data.models import Case, CaseAction


def cog() -> Moderation:
    return Moderation(Mock())


def guild() -> Any:
    fake = Mock()
    fake.get_member.return_value = None
    return fake


def user() -> Any:
    fake = Mock()
    fake.display_avatar.url = "https://cdn.example/avatar.png"
    return fake


def case(number: int, reason: str = "spam") -> Case:
    return Case(
        id=number,
        guild_id=1,
        number=number,
        action=CaseAction.WARN,
        user_id=2,
        moderator_id=3,
        reason=reason,
        created_at=0,
    )


def test_short_cases_fill_a_page_up_to_the_count_limit() -> None:
    records = [case(n) for n in range(1, CASES_PER_PAGE + 1)]

    pages = cog()._case_pages(guild(), user(), records, "Warnings")

    assert len(pages) == 1
    assert len(pages[0].fields) == CASES_PER_PAGE


def test_the_count_limit_still_starts_a_new_page() -> None:
    records = [case(n) for n in range(1, CASES_PER_PAGE + 2)]

    pages = cog()._case_pages(guild(), user(), records, "Warnings")

    assert [len(page.fields) for page in pages] == [CASES_PER_PAGE, 1]


def test_long_reasons_split_the_page_before_discord_would_reject_it() -> None:
    records = [case(n, reason="x" * 1_000) for n in range(1, CASES_PER_PAGE + 1)]

    pages = cog()._case_pages(guild(), user(), records, "Warnings")

    assert len(pages) > 1
    assert all(len(page) <= PAGE_CHAR_BUDGET for page in pages)
    assert sum(len(page.fields) for page in pages) == len(records)


def test_a_single_oversized_case_still_gets_its_own_page() -> None:
    pages = cog()._case_pages(guild(), user(), [case(1, reason="y" * 6_000)], "Warnings")

    assert len(pages) == 1
    assert len(pages[0]) <= EMBED_TOTAL_MAX
