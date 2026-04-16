"""ABC and shared types for LLM providers."""

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator


class LLMError(Exception):
    """Raised when the LLM API call fails with a user-readable message."""


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

    def __init__(self, model: str, *, max_output_tokens: int = 4096):
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.last_usage = Usage()

    def _set_last_usage(self, usage: Usage | None) -> None:
        self.last_usage = usage or Usage()

    @abstractmethod
    async def create_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
    ) -> MessageResponse:
        """Send a message and get a complete response."""

    @abstractmethod
    async def stream_message(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[TextEvent | ToolUseEvent]:
        """Stream a message response, yielding text chunks and tool calls."""

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
