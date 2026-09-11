"""``/slowmode`` option parsing.

The option is a duration string because Discord has no duration type, which
leaves the command to decide what "0" means.
"""

from __future__ import annotations

import pytest

from cogs.channels import clears_slowmode


@pytest.mark.parametrize("text", ["0", "0s", "0m", "00", "0 h", " 0 ", "off", "OFF", "none"])
def test_every_spelling_of_zero_clears_slowmode(text: str) -> None:
    assert clears_slowmode(text)


@pytest.mark.parametrize("text", ["10s", "2m", "1h", "soon", "", "10"])
def test_real_delays_and_junk_are_left_to_the_duration_parser(text: str) -> None:
    assert not clears_slowmode(text)
