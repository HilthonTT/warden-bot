"""The language filter decides whether a real member gets auto-warned, so
both directions matter: bypasses must be caught and innocent words must not
be."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.word_filter import WordFilter, build_pattern, load_words, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FUCK", "fuck"),
        ("sh!t", "shit"),
        ("f4ck", "fack"),
        ("b1tch", "bitch"),
        ("f.u.c.k", "fuck"),
        ("f_u_c_k", "fuck"),
        ("fu​ck", "fuck"),
        ("fuck!", "fuck "),
        ("hello, world", "hello  world"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "fuck",
        "FUCK you",
        "what the f.u.c.k",
        "sh!t happens",
        "fuuuuck",
        "fuckkkk",
        "you're a cunt",
        "fu​ck",
    ],
)
def test_matches_bypasses(words_file: Path, text: str) -> None:
    assert WordFilter(path=words_file).matches(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello there",
        "scunthorpe is a town",
        "I love ducks",
        "classic",
    ],
)
def test_ignores_clean_text(words_file: Path, text: str) -> None:
    assert not WordFilter(path=words_file).matches(text)


def test_load_words_skips_comments_blanks_and_duplicates(words_file: Path) -> None:
    assert load_words(words_file) == ["fuck", "shit", "cunt"]


def test_missing_word_list_disables_the_filter(tmp_path: Path) -> None:
    word_filter = WordFilter(path=tmp_path / "nope.txt")
    assert not word_filter.active
    assert not word_filter.matches("fuck")


def test_empty_word_list_builds_no_pattern() -> None:
    assert build_pattern([]) is None
    assert build_pattern(["", "###"]) is None


def test_reload_picks_up_edits(words_file: Path) -> None:
    word_filter = WordFilter(path=words_file)
    assert not word_filter.matches("banana")

    words_file.write_text("banana\n", encoding="utf-8")
    assert word_filter.reload() == 1
    assert word_filter.matches("banana")
    assert not word_filter.matches("fuck")


def test_find_returns_the_offending_word(words_file: Path) -> None:
    assert WordFilter(path=words_file).find("oh sh!t") == "shit"


def test_long_input_is_bounded(words_file: Path) -> None:
    from services.word_filter import MAX_INPUT_LEN

    padding = "a" * (MAX_INPUT_LEN + 100)
    assert not WordFilter(path=words_file).matches(padding + " fuck")
