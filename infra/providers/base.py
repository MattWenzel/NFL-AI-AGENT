"""ABC and shared types for LLM providers."""

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Literal


# How a provider's credential is sourced. "api_key" is a raw string a user
# pastes into Settings; "codex_oauth" is an OAuth token bundle obtained via
# the device-code flow (see api/routers/oauth_codex.py). The Settings UI
# uses this to decide which input to render.
CredentialShape = Literal["api_key", "codex_oauth"]

# Tool-use control. "auto" lets the model decide; "required" forces it to emit
# a tool call this turn; "none" forbids tool calls entirely. Callers pass None
# to use the provider's default (which is "auto" everywhere we support).
ToolChoice = Literal["auto", "required", "none"]


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


class StopReason(str, Enum):
    """Normalized stop reason across all providers."""
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"


@dataclass
class Usage:
    """Token usage for a single LLM call."""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolDefinition:
    """Canonical tool definition (Anthropic convention)."""
    name: str
    description: str
    input_schema: dict  # JSON Schema

    def to_dict(self) -> dict:
        """Convert to Anthropic-format dict."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolDefinition":
        """Create from an Anthropic-format dict."""
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            input_schema=d.get("input_schema", {}),
        )


@dataclass
class TextEvent:
    """A chunk of streamed text."""
    text: str


@dataclass
class ToolUseEvent:
    """A tool call from the model."""
    id: str
    name: str
    input: dict


@dataclass
class RetryingEvent:
    """Emitted by `stream_message` before sleeping on a retryable failure.

    The runtime forwards this to the UI as a "retrying after rate limit"
    notice so a long sleep doesn't look like a frozen stream. Provider
    implementations only emit this *before* any TextEvent/ToolUseEvent
    has been yielded — once real content has flown, retries become
    unsafe.
    """
    attempt: int
    delay_seconds: float
    error_message: str


@dataclass
class MessageResponse:
    """Complete (non-streamed) message response."""
    content: list  # list of TextEvent | ToolUseEvent
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)


@dataclass
class Message:
    """Provider-agnostic message format.

    - role="user": text is set
    - role="assistant": text and/or tool_calls are set
    - role="tool_result": tool_use_id and tool_content are set
    """
    role: str  # "user", "assistant", "tool_result"
    text: str | None = None
    tool_calls: list[ToolUseEvent] | None = None
    tool_use_id: str | None = None
    tool_content: str | None = None


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
