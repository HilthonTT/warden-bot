"""Application services shared across cogs.

A service owns one cross-cutting concern (guild configuration, mod-log
delivery, word filtering) so cogs don't reach into each other. Before this
layer existed, ``admin`` and ``tickets`` both had to know that ``automod``
keeps a config cache and poke it by name through ``bot.get_cog``.
"""

from .escalation import EscalationService
from .guild_config import GuildConfigService
from .modlog import ModLogService
from .word_filter import WordFilter

__all__ = [
    "EscalationService",
    "GuildConfigService",
    "ModLogService",
    "WordFilter",
]
