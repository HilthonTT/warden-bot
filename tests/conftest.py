from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from data.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """A connected Database backed by a throwaway file."""
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def words_file(tmp_path: Path) -> Path:
    path = tmp_path / "bad_words.txt"
    path.write_text(
        "\n".join(["# a comment", "", "fuck", "shit", "cunt", "FUCK"]),
        encoding="utf-8",
    )
    return path
