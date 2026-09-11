"""Input handling for ``/words add``.

The stored list drives auto-warns, so an entry that can never match is worse
than useless: it looks configured and does nothing.
"""

from __future__ import annotations

from cogs.automod import MAX_WORDS_PER_ADD, AutoMod


def test_words_are_lowercased_and_split_on_commas_and_spaces() -> None:
    assert AutoMod._clean_words("Foo, bar  BAZ") == (["foo", "bar", "baz"], 0)


def test_repeats_are_collapsed_so_the_added_count_is_honest() -> None:
    candidates, skipped = AutoMod._clean_words("spam SPAM spam")

    assert candidates == ["spam"]
    assert skipped == 0


def test_entries_without_letters_are_skipped() -> None:
    """The filter compiles a-z only, so "123" would match nothing forever."""
    candidates, skipped = AutoMod._clean_words("123 fuck !!! ???")

    assert candidates == ["fuck"]
    assert skipped == 3


def test_nothing_usable_yields_no_candidates() -> None:
    assert AutoMod._clean_words("  ,, ") == ([], 0)


def test_the_batch_size_is_capped() -> None:
    candidates, _ = AutoMod._clean_words(" ".join(f"word{i}" for i in range(100)))

    assert len(candidates) == MAX_WORDS_PER_ADD
