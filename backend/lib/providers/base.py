"""Abstract LLM client contract.

Value types live in `provider/types.py`; exceptions live in
`provider/errors.py`. This file is strictly the `BaseLLMClient` ABC
that every concrete adapter (`provider/anthropic.py`, `openai.py`,
`codex.py`) implements.
"""

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncIterator

from backend.lib.providers.errors import LLMError
from backend.lib.providers.types import (
    Message,
    MessageResponse,
    RetryingEvent,
    StopReason,
    TextEvent,
    ToolChoice,
    ToolDefinition,
    ToolUseEvent,
    Usage,
)


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
        tools: list[ToolDefinition] | None = None,
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
    async def stream_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent | RetryingEvent]:
        """Stream a message response, yielding text chunks and tool calls.

        `tool_choice` overrides the provider's default when set. `None`
        means "use the SDK default" (which is "auto" on every provider
        we wire). Each provider translates the canonical string into its
        own wire format (e.g. Anthropic's `{"type": "any"}` for
        "required").
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
