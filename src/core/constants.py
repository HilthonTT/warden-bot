"""Hard limits imposed by the Discord API.

Centralised so call sites truncate against a named constant instead of a
magic number, and so a change in the API only needs one edit.
"""

from __future__ import annotations

AUDIT_REASON_MAX = 512

EMBED_TITLE_MAX = 256
EMBED_DESCRIPTION_MAX = 4_096
EMBED_FIELD_NAME_MAX = 256
EMBED_FIELD_VALUE_MAX = 1_024
EMBED_FOOTER_MAX = 2_048
EMBED_TOTAL_MAX = 6_000
EMBED_MAX_FIELDS = 25

BUTTON_LABEL_MAX = 80

MESSAGE_CONTENT_MAX = 2_000
CHANNEL_TOPIC_MAX = 1_024


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    """Shorten ``text`` to ``limit`` characters, marking elision with ``suffix``.

    Discord rejects the whole request when a single field is over its cap, so
    every user-controlled string that lands in an embed goes through here.
    """
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)] + suffix
