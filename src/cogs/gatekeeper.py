"""Join-time defences: account-age gating and raid alerts.

A raid doesn't look like a bad message, it looks like twenty accounts created
this morning arriving in the same minute. Neither the word filter nor the spam
filter can see that, because it happens before anyone speaks.

Both defences are off by default and configured with ``/gatekeeper``:

* **Minimum account age** kicks (never bans — a false positive should be
  recoverable) accounts younger than the configured age.
* **Raid alerts** watch the join rate and report a spike to the mod log, with
  a cooldown so a sustained raid produces one alert rather than one per joiner.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from core.checks import is_staff
from core.cog import WardenCog
from core.constants import AUDIT_REASON_MAX, EMBED_FIELD_VALUE_MAX, truncate
from core.embeds import add_field, base_embed
from core.responses import try_dm
from data.models import CaseAction

if TYPE_CHECKING:
    from core.bot import WardenBot

log = logging.getLogger(__name__)

SECONDS_PER_HOUR = 3_600
ALERT_USER_LIMIT = 15


class Gatekeeper(WardenCog):
    """Account-age gating and raid detection."""

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        cfg = await self.config.get(member.guild.id)
        await self._check_raid(member, cfg.raid_join_threshold, cfg.raid_join_window_seconds)
        await self._check_account_age(member, cfg.min_account_age_hours, cfg.staff_role_id)

    async def _check_raid(
        self, member: discord.Member, threshold: int, window_seconds: int
    ) -> None:
        alert = self.raid.record(
            member.guild.id,
            member.id,
            threshold=threshold,
            window_seconds=window_seconds,
        )
        if alert is None:
            return

        log.warning(
            "Possible raid in guild %s: %d joins in %ds",
            member.guild.id,
            alert.joins,
            alert.window_seconds,
        )

        listed = alert.user_ids[:ALERT_USER_LIMIT]
        names = "\n".join(f"<@{user_id}> (`{user_id}`)" for user_id in listed)
        if len(alert.user_ids) > len(listed):
            names += f"\n…and {len(alert.user_ids) - len(listed)} more"

        embed = base_embed(
            "🚨 Possible raid",
            color=discord.Color.dark_red(),
            description=(
                f"**{alert.joins}** accounts joined within "
                f"**{alert.window_seconds}s**.\n\n"
                f"Consider `/lock`, raising the server's verification level, or "
                f"`/gatekeeper min_account_age_hours:` to hold new accounts off."
            ),
        )
        add_field(embed, "Recent joins", truncate(names, EMBED_FIELD_VALUE_MAX))
        await self.modlog.send(member.guild, embed)

    async def _check_account_age(
        self, member: discord.Member, minimum_hours: int, staff_role_id: int | None
    ) -> None:
        if minimum_hours <= 0:
            return
        if is_staff(member, staff_role_id):
            return

        age_seconds = (discord.utils.utcnow() - member.created_at).total_seconds()
        age_hours = age_seconds / SECONDS_PER_HOUR
        if age_hours >= minimum_hours:
            return

        reason = f"Account is {age_hours:.1f}h old; this server requires {minimum_hours}h"
        await try_dm(
            member,
            f"**{member.guild.name}** requires accounts to be at least "
            f"{minimum_hours} hour(s) old before joining. You're welcome to come "
            f"back once your account is older.",
        )

        try:
            await member.kick(reason=truncate(reason, AUDIT_REASON_MAX))
        except discord.Forbidden:
            log.warning(
                "Cannot enforce the account-age gate in guild %s — missing Kick Members",
                member.guild.id,
            )
            return
        except discord.HTTPException:
            log.warning("Account-age kick failed for %s", member.id, exc_info=True)
            return

        bot_id = self.bot.user.id if self.bot.user else member.id
        case, _ = await self.db.add_case(
            member.guild.id, CaseAction.KICK, member.id, bot_id, reason
        )

        embed = base_embed(
            "🚪 New account turned away",
            color=discord.Color.orange(),
            description=(
                f"**User:** {member} (`{member.id}`)\n"
                f"**Case:** #{case.number}\n"
                f"**Account created:** <t:{int(member.created_at.timestamp())}:R>\n"
                f"**Minimum age:** {minimum_hours}h"
            ),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.modlog.send(member.guild, embed)


async def setup(bot: WardenBot) -> None:
    await bot.add_cog(Gatekeeper(bot))
