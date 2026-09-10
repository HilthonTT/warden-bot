"""A paginated embed view.

``/warnings`` used to render at most 25 entries and print "Showing 25 of 40"
with no way to reach the rest. Any listing that can outgrow one embed uses
this instead.
"""

from __future__ import annotations

import logging
from typing import TypeVar

import discord

T = TypeVar("T")

log = logging.getLogger(__name__)

PAGE_TIMEOUT = 180.0


class Paginator(discord.ui.View):
    """First/previous/next/last buttons over a list of prepared embeds.

    Only the member who ran the command may turn pages; anyone else gets an
    ephemeral nudge rather than silently moving someone else's view.
    """

    def __init__(self, pages: list[discord.Embed], *, owner_id: int) -> None:
        super().__init__(timeout=PAGE_TIMEOUT)
        if not pages:
            raise ValueError("Paginator needs at least one page")
        self.pages = pages
        self.owner_id = owner_id
        self.index = 0
        self.message: discord.Message | None = None
        self._sync_buttons()

    @property
    def current(self) -> discord.Embed:
        embed = self.pages[self.index]
        embed.set_footer(text=f"Page {self.index + 1} of {len(self.pages)}")
        return embed

    @property
    def single_page(self) -> bool:
        return len(self.pages) == 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Run the command yourself to page through the results.", ephemeral=True
        )
        return False

    def _sync_buttons(self) -> None:
        at_start = self.index == 0
        at_end = self.index >= len(self.pages) - 1
        self.first_page.disabled = at_start
        self.previous_page.disabled = at_start
        self.next_page.disabled = at_end
        self.last_page.disabled = at_end

    async def _show(self, interaction: discord.Interaction, index: int) -> None:
        self.index = max(0, min(index, len(self.pages) - 1))
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current, view=self)

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def first_page(
        self, interaction: discord.Interaction, button: discord.ui.Button[Paginator]
    ) -> None:
        await self._show(interaction, 0)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.primary)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button[Paginator]
    ) -> None:
        await self._show(interaction, self.index - 1)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button[Paginator]
    ) -> None:
        await self._show(interaction, self.index + 1)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def last_page(
        self, interaction: discord.Interaction, button: discord.ui.Button[Paginator]
    ) -> None:
        await self._show(interaction, len(self.pages) - 1)

    async def on_timeout(self) -> None:
        """Disable the controls so a dead view doesn't look interactive."""
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            log.debug("Could not disable a timed-out paginator", exc_info=True)


def chunk(items: list[T], size: int) -> list[list[T]]:
    """Split ``items`` into lists of at most ``size``."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[start : start + size] for start in range(0, len(items), size)] or [[]]
