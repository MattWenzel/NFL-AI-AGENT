"""Token counting for compaction decisions.

Used by `agent/compaction.py` to estimate whether the active
transcript has grown past the session's context window. Accuracy matters
only enough to pick a compaction boundary — not billing — so we use a
single tiktoken encoder (cl100k_base) for both Anthropic and OpenAI.
Their real tokenizers differ, but cl100k is materially closer to both
than the naïve `len(text) // 4` heuristic, especially for dense
SQL/JSON tool results where ÷4 substantially under-counts.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN_FALLBACK = 4

try:
    import tiktoken

    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception as exc:  # ImportError, or tiktoken data-fetch failure offline
    _ENCODER = None
    logger.warning(
        "tiktoken unavailable (%s) — falling back to len(text)//%d for token estimates",
        exc,
        _CHARS_PER_TOKEN_FALLBACK,
    )


def count_text_tokens(text: str) -> int:
    """Estimate token count for `text`. Returns >= 1 for non-empty input."""
    if not text:
        return 0
    if _ENCODER is None:
        return max(1, len(text) // _CHARS_PER_TOKEN_FALLBACK)
    return max(1, len(_ENCODER.encode(text, disallowed_special=())))
