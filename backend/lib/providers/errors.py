"""Exceptions raised by LLM provider adapters.

`LLMError` is the user-facing catch-all; `ContextOverflowError` is a
recoverable subclass the runtime traps to trigger an extra compaction
pass before retrying. `RetryableError` is the classifier-return type
used by `provider/retry.py` to mark which SDK exceptions are safe to
retry.
"""

from __future__ import annotations


class LLMError(Exception):
    """Raised when the LLM API call fails with a user-readable message."""


class ContextOverflowError(LLMError):
    """Raised when the provider rejected the request as too-long-for-context.

    Distinct from generic LLMError so the runtime can catch it, force one
    extra compaction pass, and retry the iteration. Our pre-call estimator
    (tiktoken cl100k_base) is close but not exact for Anthropic/OpenAI
    counts of tool definitions and system overhead — when it under-shoots,
    this is the recovery path.
    """


class RetryableError(Exception):
    """Marker returned by a classifier to say "retry this exception".

    Carries the original exception plus an optional retry_after_seconds
    extracted from response headers. The classifier doesn't raise this
    — it returns it (or None) from a pure function.
    """

    def __init__(self, original: Exception, retry_after_seconds: float | None = None):
        super().__init__(str(original))
        self.original = original
        self.retry_after_seconds = retry_after_seconds
