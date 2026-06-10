"""Abstract LLM client contract.

Value types live in `provider/types.py`; exceptions live in
`provider/errors.py`. This file is strictly the `BaseLLMClient` ABC
that every concrete adapter (`provider/anthropic.py`, `openai.py`,
`codex.py`) implements.

The retry-on-stream-open policy is owned here: `stream_message` is a
concrete template method that loops up to `MAX_ATTEMPTS`, classifies
exceptions via the subclass's `_classify_stream_error`, sleeps per
`compute_delay`, and emits a `ProviderRetryingEvent` so the UI can show
a "retrying" notice. Subclasses only implement `_stream_once` (one open
+ iterate + cleanup attempt that may raise SDK exceptions) and
`_classify_stream_error`. Once any event has been yielded, retries
become unsafe — the template translates and re-raises instead of
retrying, since partial output has already flown.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncIterator

from backend.domain.providers.errors import LLMError, RetryableError
from backend.domain.providers.retry import MAX_ATTEMPTS, compute_delay
from backend.domain.providers.types import (
    Message,
    MessageResponse,
    ProviderRetryingEvent,
    StopReason,
    TextEvent,
    ToolChoice,
    Tool,
    ToolUseEvent,
    Usage,
)

logger = logging.getLogger(__name__)


class BaseLLMClient(ABC):
    """Abstract base class for LLM provider clients."""

    def __init__(self, model: str, *, max_output_tokens: int = 16384):
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.last_usage = Usage()
        self.last_stop_reason: StopReason | None = None

    def _set_last_usage(self, usage: Usage | None) -> None:
        self.last_usage = usage or Usage()

    def _set_last_stop_reason(self, reason: StopReason | None) -> None:
        self.last_stop_reason = reason

    @abstractmethod
    async def create_message(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        system: str | None = None,
        model: str | None = None,
    ) -> MessageResponse:
        """Send a message and get a complete response.

        `model` overrides `self.model` for this single call (e.g. so the
        compaction summarizer can fire a cheap sibling model without
        mutating the long-lived client). When None, the client's default
        model is used.
        """

    @abstractmethod
    def _stream_once(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        system: str | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent]:
        """One streaming attempt: open the SDK stream, iterate it, clean up.

        May raise SDK exceptions during stream-open (before yielding) —
        the template `stream_message` catches them and decides whether
        to retry via `_classify_stream_error`. May also raise mid-stream
        after events have been yielded; in that case the template
        translates the exception and re-raises (no retry).

        Subclasses must call `_set_last_usage` and `_set_last_stop_reason`
        before this iterator exhausts so the chat runtime can record
        accurate per-turn usage.
        """

    @abstractmethod
    def _classify_stream_error(self, exc: Exception) -> RetryableError | None:
        """Decide whether an SDK exception is safe to retry.

        Return a `RetryableError` (with optional retry_after_seconds
        parsed from response headers) when retry is safe — typically
        rate-limit, 5xx, connection, and timeout errors. Return None for
        auth, 4xx, and other propagate-immediately errors. Used by both
        `stream_message` (for stream-open retries) and `create_message`
        (via `with_retries`), so classification stays single-sourced.
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g. 'anthropic', 'openai')."""

    @abstractmethod
    def _translate_error(self, exc: Exception) -> LLMError:
        """Map a provider SDK exception to an LLMError.

        Each provider overrides this once to handle auth, rate-limit,
        and generic API errors from its SDK.
        """

    async def aclose(self) -> None:
        """Release any provider-owned resources."""
        return None

    @asynccontextmanager
    async def _wrap_api_errors(self):
        """Async context manager that catches SDK exceptions and delegates to _translate_error."""
        try:
            yield
        except LLMError:
            raise
        except Exception as exc:
            raise self._translate_error(exc)

    async def stream_message(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        system: str | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent | ProviderRetryingEvent]:
        """Stream a message response, retrying transient stream-open failures.

        Template method. Subclasses implement `_stream_once`; this loop
        owns the retry policy (MAX_ATTEMPTS, header-aware backoff,
        retrying-event emission). Once any TextEvent or ToolUseEvent has
        been yielded, an exception is translated and re-raised instead
        of retried — partial output has already reached the UI and a
        second attempt would double-emit.
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            events_yielded = False
            try:
                async for event in self._stream_once(messages, tools, system, tool_choice):
                    events_yielded = True
                    yield event
                return
            except LLMError:
                raise
            except Exception as exc:
                if events_yielded:
                    raise self._translate_error(exc)
                classification = self._classify_stream_error(exc)
                if classification is None or attempt >= MAX_ATTEMPTS:
                    raise self._translate_error(exc)
                delay = compute_delay(attempt, classification.retry_after_seconds)
                logger.info(
                    "%s stream open failed (attempt %d/%d, sleep %.1fs): %s",
                    self.provider_name, attempt, MAX_ATTEMPTS, delay, exc,
                )
                yield ProviderRetryingEvent(
                    attempt=attempt,
                    delay_seconds=delay,
                    error_message=str(exc),
                )
                await asyncio.sleep(delay)
        # Loop exits via either return or raise; this is unreachable.
        raise LLMError(f"{self.provider_name}: stream open exhausted retries")
