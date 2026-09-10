"""Framework-level building blocks shared by every cog.

Nothing in here may import from ``cogs`` — the dependency arrow points
one way: ``cogs`` -> ``services`` -> ``core``/``data``.
"""
