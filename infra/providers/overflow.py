"""Provider-agnostic context-overflow detection from API error bodies.

Our pre-call token estimate (`infra.token_counting.count_text_tokens`)
is good enough to decide *when* to compact, but it doesn't account for
the provider's exact tokenizer, tool-definition encoding, or per-message
overhead. When it under-shoots, the provider returns a 4xx like
"prompt is too long" or "context window exceeded". We catch those here,
raise `ContextOverflowError`, and the runtime forces one more compaction
pass and retries the iteration.

Patterns harvested from current Anthropic / OpenAI behavior (Apr 2026).
Conservative — we only match phrases unambiguously about context length;
generic 400s pass through as regular LLMError.
"""

from __future__ import annotations

import re

# Match if any pattern is found in the error message or response body.
_OVERFLOW_PATTERNS = [
    re.compile(r"prompt is too long", re.IGNORECASE),
    re.compile(r"input is too long", re.IGNORECASE),
    re.compile(r"context (?:window|length).*exceed", re.IGNORECASE),
    re.compile(r"exceed.*context (?:window|length)", re.IGNORECASE),
    re.compile(r"maximum (?:prompt|context) length", re.IGNORECASE),
    re.compile(r"context_length_exceeded", re.IGNORECASE),
    re.compile(r"input token count.*exceed", re.IGNORECASE),
    re.compile(r"too many (?:input )?tokens", re.IGNORECASE),
]


def is_context_overflow(message: str) -> bool:
    """True if `message` looks like a context-window overflow error.

    Pass the full text you have — exception message + response body
    when both are available — so we catch cases where one is empty.
    """
    if not message:
        return False
    return any(pattern.search(message) for pattern in _OVERFLOW_PATTERNS)
