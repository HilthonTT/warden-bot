"""Warning-threshold escalation.

Both ``/warn`` and the automod filters must escalate identically, and neither
is a good owner of the rule. Previously AutoMod reached into the Moderation
cog with ``bot.get_cog("Moderation")`` and a ``hasattr`` guard, so escalation
silently stopped working if that cog failed to load.

An auto-kick or auto-ban is recorded as a case of its own, so ``/case`` can
explain why someone was removed without a moderator reconstructing it from
the warning history.
"""

from __future__ import annotations

import logging

import discord

from core.constants import AUDIT_REASON_MAX, truncate
from core.embeds import action_embed
from core.responses import try_dm
from data.db import Database
from data.models import CaseAction, GuildConfig

from .modlog import ModLogService

log = logging.getLogger(__name__)


class EscalationService:
    def __init__(self, db: Database, modlog: ModLogService) -> None:
        self._db = db
        self._modlog = modlog

    async def apply(
        self,
        guild: discord.Guild,
        member: discord.Member,
        warning_count: int,
        cfg: GuildConfig,
    ) -> CaseAction | None:
        """Kick or ban ``member`` if their warning count crossed a threshold.

        Returns:
            The action taken, or None if no threshold was crossed or the action
            failed. Failures are logged rather than raised: the warning that
            triggered this has already been recorded and reported.
        """
        ban = warning_count >= cfg.warn_ban_threshold
        kick = not ban and warning_count >= cfg.warn_kick_threshold
        if not (ban or kick):
            return None

        action = CaseAction.BAN if ban else CaseAction.KICK
        threshold = cfg.warn_ban_threshold if ban else cfg.warn_kick_threshold
        verb = "banned" if ban else "kicked"
        reason = f"Auto-{action}: reached {warning_count} warnings (threshold {threshold})"

        await try_dm(member, f"You were auto-{verb} from **{guild.name}**: {reason}")

        try:
            if ban:
                await guild.ban(
                    member,
                    reason=truncate(reason, AUDIT_REASON_MAX),
                    delete_message_seconds=0,
                )
            else:
                await member.kick(reason=truncate(reason, AUDIT_REASON_MAX))
        except discord.Forbidden:
            log.warning(
                "Cannot auto-%s %s in guild %s — missing permission or role hierarchy",
                action,
                member.id,
                guild.id,
            )
            return None
        except discord.HTTPException:
            log.warning("Auto-%s failed for %s", action, member.id, exc_info=True)
            return None

        moderator = guild.me or member
        case, _ = await self._db.add_case(guild.id, action, member.id, moderator.id, reason)

        embed = action_embed(
            f"Auto-{verb}",
            discord.Color.dark_red() if ban else discord.Color.dark_orange(),
            member,
            moderator,
            reason,
            extra={"Case": f"#{case.number}", "Warnings": str(warning_count)},
        )
        await self._modlog.send(guild, embed)
        return action
