"""Warning-threshold escalation.

Both ``/warn`` and the automod language filter must escalate identically, and
neither is a good owner of the rule. Previously AutoMod reached into the
Moderation cog with ``bot.get_cog("Moderation")`` and a ``hasattr`` guard, so
escalation silently stopped working if that cog failed to load.
"""

from __future__ import annotations

import logging

import discord

from core.constants import AUDIT_REASON_MAX, truncate
from core.embeds import action_embed
from core.responses import try_dm
from data.models import GuildConfig

from .modlog import ModLogService

log = logging.getLogger(__name__)


class EscalationService:
    def __init__(self, modlog: ModLogService) -> None:
        self._modlog = modlog

    async def apply(
        self,
        guild: discord.Guild,
        member: discord.Member,
        warning_count: int,
        cfg: GuildConfig,
    ) -> str | None:
        """Kick or ban ``member`` if their warning count crossed a threshold.

        Returns:
            ``"ban"``, ``"kick"``, or None if no threshold was crossed or the
            action failed. Failures are logged rather than raised: the warning
            that triggered this has already been recorded and reported.
        """
        ban = warning_count >= cfg.warn_ban_threshold
        kick = not ban and warning_count >= cfg.warn_kick_threshold
        if not (ban or kick):
            return None

        action = "ban" if ban else "kick"
        verb = "banned" if ban else "kicked"
        threshold = cfg.warn_ban_threshold if ban else cfg.warn_kick_threshold
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

        embed = action_embed(
            f"Auto-{verb}",
            discord.Color.dark_red() if ban else discord.Color.dark_orange(),
            member,
            guild.me or member,
            reason,
            extra={"Warnings": str(warning_count)},
        )
        await self._modlog.send(guild, embed)
        return action
