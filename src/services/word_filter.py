"""Obfuscation-tolerant word filter.

Extracted from the AutoMod cog so the matching rules can be unit-tested
without a Discord connection — the normalisation is the part most likely to
regress, and the part with the highest cost when it does (a false positive
auto-warns a real member).

Matching strategy, in order:

1. Strip zero-width characters (the cheapest bypass) and lowercase.
2. Translate leet substitutions, but *only* between two letters, so ``sh!t``
   normalises to ``shit`` while ``fuck!`` is not rewritten to ``fucki``
   (which would break the trailing word boundary).
3. Drop a single non-letter sandwiched between letters, collapsing ``f.u.c.k``.
4. Replace remaining non-letter runs with a space so they act as boundaries.
5. Match an alternation in which every letter is quantified with ``+``, so
   ``fuuuck`` and ``fuckkkk`` hit without mutating the input.

Word boundaries (``\\b``) keep ``scunthorpe`` from matching ``cunt``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_WORDS_FILE = Path(__file__).resolve().parent.parent / "data" / "bad_words.txt"

MAX_INPUT_LEN = 4_000

LEET_LOOKUP = {
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "@": "a",
    "$": "s",
    "!": "i",
}
LEET_INNER_RE = re.compile(
    r"(?<=[a-z])([" + re.escape("".join(LEET_LOOKUP)) + r"])(?=[a-z])",
)
ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200D\uFEFF]")
LETTERS_ONLY_RE = re.compile(r"[^a-z]+")
SQUASH_INNER_RE = re.compile(r"(?<=[a-z])[^a-z\s](?=[a-z])")
NON_LETTER_RUN_RE = re.compile(r"[^a-z\s]+")


def normalize(text: str) -> str:
    """Canonicalise ``text`` for matching. See the module docstring."""
    normalized = ZERO_WIDTH_RE.sub("", text).lower()
    normalized = LEET_INNER_RE.sub(lambda m: LEET_LOOKUP[m.group(1)], normalized)
    normalized = SQUASH_INNER_RE.sub("", normalized)
    return NON_LETTER_RUN_RE.sub(" ", normalized)


def load_words(path: Path | None = None) -> list[str]:
    """Read the word list, skipping blanks and ``#`` comments.

    A missing file is not fatal — the filter simply matches nothing, which is
    the safe direction to fail in.
    """
    words_file = path or DEFAULT_WORDS_FILE
    if not words_file.is_file():
        log.warning("Word list missing at %s — the language filter is inactive.", words_file)
        return []

    seen: dict[str, None] = {}
    for line in words_file.read_text(encoding="utf-8").splitlines():
        word = line.strip().lower()
        if word and not word.startswith("#"):
            seen.setdefault(word, None)
    return list(seen)


def build_pattern(words: list[str]) -> re.Pattern[str] | None:
    """Compile the word list into a single repetition-tolerant alternation."""
    parts = []
    for word in words:
        cleaned = LETTERS_ONLY_RE.sub("", word.lower())
        if cleaned:
            parts.append("".join(f"{re.escape(char)}+" for char in cleaned))
    if not parts:
        return None
    return re.compile(rf"\b(?:{'|'.join(parts)})\b")


class WordFilter:
    """Holds the compiled word list and answers "is this message a hit?"."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_WORDS_FILE
        self.words: list[str] = []
        self._pattern: re.Pattern[str] | None = None
        self.reload()

    def reload(self) -> int:
        """Re-read the word list from disk. Returns the number of words loaded."""
        self.words = load_words(self._path)
        self._pattern = build_pattern(self.words)
        log.info("Word filter loaded %d word(s) from %s", len(self.words), self._path)
        return len(self.words)

    @property
    def active(self) -> bool:
        return self._pattern is not None

    def find(self, text: str) -> str | None:
        """Return the offending (normalised) word, or None if the text is clean."""
        if self._pattern is None or not text:
            return None
        match = self._pattern.search(normalize(text[:MAX_INPUT_LEN]))
        return match.group(0) if match else None

    def matches(self, text: str) -> bool:
        return self.find(text) is not None
