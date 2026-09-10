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

from data.db import Database

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

    def __init__(self, words: list[str] | None = None, path: Path | None = None) -> None:
        self._path = path or DEFAULT_WORDS_FILE
        self.words: list[str] = words if words is not None else load_words(self._path)
        self._pattern: re.Pattern[str] | None = build_pattern(self.words)

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


class WordFilterService:
    """Per-guild word lists layered over the shared defaults from disk.

    One file on disk could not express two communities with different
    standards, so each guild adds and removes its own words through
    ``/words``. Compiled patterns are cached because this runs on every
    message; a guild's cache entry is dropped whenever its list changes.
    """

    def __init__(self, db: Database, path: Path | None = None) -> None:
        self._db = db
        self._path = path or DEFAULT_WORDS_FILE
        self.defaults: list[str] = load_words(self._path)
        self._cache: dict[int, WordFilter] = {}
        log.info("Word filter seeded with %d default word(s)", len(self.defaults))

    async def _filter_for(self, guild_id: int) -> WordFilter:
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        custom = await self._db.get_words(guild_id)
        word_filter = WordFilter(words=[*self.defaults, *custom], path=self._path)
        self._cache[guild_id] = word_filter
        return word_filter

    async def find(self, guild_id: int, text: str) -> str | None:
        """Return the offending word for this guild's list, or None."""
        return (await self._filter_for(guild_id)).find(text)

    async def matches(self, guild_id: int, text: str) -> bool:
        return await self.find(guild_id, text) is not None

    async def custom_words(self, guild_id: int) -> list[str]:
        return await self._db.get_words(guild_id)

    async def add(self, guild_id: int, words: list[str]) -> int:
        added = await self._db.add_words(guild_id, words)
        self.invalidate(guild_id)
        return added

    async def remove(self, guild_id: int, word: str) -> bool:
        removed = await self._db.remove_word(guild_id, word)
        self.invalidate(guild_id)
        return removed

    async def clear(self, guild_id: int) -> int:
        cleared = await self._db.clear_words(guild_id)
        self.invalidate(guild_id)
        return cleared

    def invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)

    def reload_defaults(self) -> int:
        """Re-read the seed list from disk and drop every compiled pattern."""
        self.defaults = load_words(self._path)
        self._cache.clear()
        log.info("Word filter reloaded %d default word(s)", len(self.defaults))
        return len(self.defaults)
