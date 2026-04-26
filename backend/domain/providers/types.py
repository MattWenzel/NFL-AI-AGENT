"""Shared value types for LLM providers.

The ABC (`BaseLLMClient`) lives in `provider/base.py`; exceptions live
in `provider/errors.py`. This module is strictly for the data types
passed across the provider boundary — credential descriptors, tool-use
control, stop reasons, token usage, tool definitions, streaming event
variants, canonical message shapes — plus the canonical provider name
strings used as keys in the registry and stored in
`users_api_keys.provider`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


# Canonical LLM provider name strings. Keys in the in-process provider
# registry (see `provider/__init__.py`) and the values stored in
# `users_api_keys.provider`. Single source of truth; importing from here
# keeps the strings from drifting across modules.
ANTHROPIC = "anthropic"
OPENAI = "openai"
CODEX = "openai-codex"


# How a provider's credential is sourced. "api_key" is a raw string a user
# pastes into Settings; "codex_oauth" is an OAuth token bundle obtained via
# the device-code flow (see backend/server/routes/settings.py). The Settings UI
# uses this to decide which input to render.
CredentialShape = Literal["api_key", "codex_oauth"]

# Tool-use control. "auto" lets the model decide; "required" forces it to emit
# a tool call this turn; "none" forbids tool calls entirely. Callers pass None
# to use the provider's default (which is "auto" everywhere we support).
ToolChoice = Literal["auto", "required", "none"]


class StopReason(str, Enum):
    """Normalized stop reason across all providers."""
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"


@dataclass
class Usage:
    """Token usage for a single LLM call.

    `cache_read_tokens` and `cache_write_tokens` are populated by
    providers that support prompt caching (currently Anthropic) so
    callers can audit cache efficacy without grepping logs. Both stay 0
    on providers without cache support.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


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
class ProviderRetryingEvent:
    """Emitted by `stream_message` before sleeping on a retryable failure.

    The runtime forwards this to the UI as a "retrying after rate limit"
    notice so a long sleep doesn't look like a frozen stream. Provider
    implementations only emit this *before* any TextEvent/ToolUseEvent
    has been yielded — once real content has flown, retries become
    unsafe.

    Distinct from `backend.domain.agent.events.RetryingEvent`: that one
    is the agent-runtime variant (carries session_id/turn_id/iterations
    for the UI). This is the provider-side, lower-level signal that the
    runtime translates into the agent variant.
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
