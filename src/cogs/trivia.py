"""``/trivia`` — a multiple-choice question from the Open Trivia Database.

The previous implementation called ``requests.get`` directly inside the
command callback. That blocks the event loop for the entire round trip, which
stalls *every* other command, the automod listener, and the voice keepalive.
This version uses the bot's shared :class:`aiohttp.ClientSession`.
"""

from __future__ import annotations

import html
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from discord import app_commands, ui

from core.cog import MonitorCog
from core.constants import BUTTON_LABEL_MAX, truncate
from core.embeds import base_embed, info_embed
from core.responses import fail, reply

if TYPE_CHECKING:
    from core.bot import MonitorBot

log = logging.getLogger(__name__)

TRIVIA_URL = "https://opentdb.com/api.php"
TRIVIA_PARAMS = {"amount": "1", "category": "18", "type": "multiple"}
ANSWER_TIMEOUT = 60.0

OPENTDB_SUCCESS = 0


class TriviaQuestion:
    """One normalised question from the API."""

    __slots__ = ("answers", "category", "correct", "difficulty", "question")

    def __init__(self, payload: dict[str, Any]) -> None:
        self.question = html.unescape(payload["question"])
        self.correct = html.unescape(payload["correct_answer"])
        self.category = html.unescape(payload.get("category", "Unknown"))
        self.difficulty = str(payload.get("difficulty", "unknown"))
        self.answers = [html.unescape(answer) for answer in payload["incorrect_answers"]]
        self.answers.append(self.correct)
        random.shuffle(self.answers)


class TriviaView(ui.View):
    """Answer buttons for a single question. First answer settles the round."""

    def __init__(self, question: TriviaQuestion) -> None:
        super().__init__(timeout=ANSWER_TIMEOUT)
        self.question = question
        self.answered = False
        self.message: discord.Message | None = None

        for index, answer in enumerate(question.answers):
            button: ui.Button[ui.View] = ui.Button(
                label=truncate(answer, BUTTON_LABEL_MAX),
                style=discord.ButtonStyle.primary,
                row=index // 2,
            )
            button.callback = self._make_callback(button, answer)  # type: ignore[assignment,method-assign]
            self.add_item(button)

    def _make_callback(
        self,
        button: ui.Button[ui.View],
        answer: str,
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if self.answered:
                await interaction.response.send_message(
                    "Someone already answered this one!",
                    ephemeral=True,
                )
                return
            self.answered = True
            self.stop()

            correct = answer == self.question.correct
            self._settle(highlight=button, correct=correct)

            if correct:
                embed = base_embed(
                    "✅ Correct!",
                    color=discord.Color.green(),
                    description=f"{interaction.user.mention} got it: **{answer}**",
                )
            else:
                embed = base_embed(
                    "❌ Wrong answer",
                    color=discord.Color.red(),
                    description=(
                        f"{interaction.user.mention} answered **{answer}**.\n"
                        f"The correct answer was **{self.question.correct}**."
                    ),
                )
            await interaction.response.edit_message(embed=embed, view=self)

        return callback

    def _settle(self, *, highlight: ui.Button[ui.View] | None, correct: bool) -> None:
        """Disable every button and colour the outcome."""
        for child in self.children:
            if not isinstance(child, ui.Button):
                continue
            child.disabled = True
            if child.label == truncate(self.question.correct, BUTTON_LABEL_MAX):
                child.style = discord.ButtonStyle.success
            elif child is highlight and not correct:
                child.style = discord.ButtonStyle.danger
            else:
                child.style = discord.ButtonStyle.secondary

    async def on_timeout(self) -> None:
        """Reveal the answer instead of leaving live buttons on a dead round."""
        if self.answered or self.message is None:
            return
        self._settle(highlight=None, correct=False)
        embed = base_embed(
            "⏰ Time's up",
            color=discord.Color.greyple(),
            description=f"The correct answer was **{self.question.correct}**.",
        )
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            log.debug("Could not edit the timed-out trivia message", exc_info=True)


class Trivia(MonitorCog):
    """Trivia game."""

    async def fetch_question(self) -> TriviaQuestion | None:
        """Fetch one question, or None if the API is unavailable/empty."""
        async with self.bot.session.get(TRIVIA_URL, params=TRIVIA_PARAMS) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)

        if payload.get("response_code") != OPENTDB_SUCCESS or not payload.get("results"):
            log.warning("Trivia API returned no question: %s", payload.get("response_code"))
            return None
        return TriviaQuestion(payload["results"][0])

    @app_commands.command(
        name="trivia",
        description="Play a science & computers trivia question.",
    )
    @app_commands.checks.cooldown(3, 10.0, key=lambda i: i.user.id)
    async def trivia(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        try:
            question = await self.fetch_question()
        except (TimeoutError, aiohttp.ClientError):
            log.warning("Trivia API request failed", exc_info=True)
            await fail(interaction, "The trivia service isn't responding. Try again shortly.")
            return
        except (KeyError, ValueError):
            log.exception("Unexpected trivia API payload")
            await fail(interaction, "The trivia service sent something I couldn't read.")
            return

        if question is None:
            await fail(interaction, "The trivia service had no question for me. Try again.")
            return

        embed = info_embed("🧠 Trivia Question", question.question)
        embed.set_footer(
            text=(
                f"{question.category} • {question.difficulty} • {int(ANSWER_TIMEOUT)}s to answer"
            ),
        )

        view = TriviaView(question)
        await reply(interaction, embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot: MonitorBot) -> None:
    await bot.add_cog(Trivia(bot))
