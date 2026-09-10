"""Music package.

Exports ``setup`` so the extension loader can treat the directory as a single
cog package (``cogs.music``).
"""

from .music import Music, setup

__all__ = ["Music", "setup"]
