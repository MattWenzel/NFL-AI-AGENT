"""Runtime event variants surfaced by the chat loop.

Each variant is a frozen dataclass carrying only the fields that variant
emits. Downstream transports branch with `isinstance`, so it is impossible
to access a field that does not belong on a given event type. The shared
`type` string is kept as a human-readable tag for logs.
"""

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True, kw_only=True)
class TurnStartedEvent:
    session_id: str
    turn_id: str
    type: str = "turn_started"


@dataclass(frozen=True, kw_only=True)
class AssistantStartedEvent:
    session_id: str
    turn_id: str
    iterations: int
    type: str = "assistant_started"


@dataclass(frozen=True, kw_only=True)
class TextDeltaEvent:
    session_id: str
    turn_id: str
    text: str
    iterations: int
    type: str = "text_delta"


@dataclass(frozen=True, kw_only=True)
class ToolPendingEvent:
    session_id: str
    turn_id: str
    tool_run_id: str
    name: str
    input: dict
    iterations: int
    type: str = "tool_pending"


@dataclass(frozen=True, kw_only=True)
class ToolCompletedEvent:
    session_id: str
    turn_id: str
    tool_run_id: str
    name: str
    result: str | None = None
    error: str | None = None
    iterations: int
    type: str = "tool_completed"


@dataclass(frozen=True, kw_only=True)
class ToolFailedEvent:
    session_id: str
    turn_id: str
    tool_run_id: str
    name: str
    result: str | None = None
    error: str | None = None
    iterations: int
    type: str = "tool_failed"


@dataclass(frozen=True, kw_only=True)
class AssistantRequiresFollowupEvent:
    session_id: str
    turn_id: str
    iterations: int
    type: str = "assistant_requires_followup"


@dataclass(frozen=True, kw_only=True)
class TurnFinishedEvent:
    session_id: str
    turn_id: str
    iterations: int
    type: str = "turn_finished"


@dataclass(frozen=True, kw_only=True)
class RuntimeErrorEvent:
    session_id: str
    error: str
    turn_id: str | None = None
    iterations: int | None = None
    type: str = "runtime_error"


@dataclass(frozen=True, kw_only=True)
class CompactionStartedEvent:
    session_id: str
    turn_id: str
    iterations: int
    meta: dict
    type: str = "compaction_started"


@dataclass(frozen=True, kw_only=True)
class RetryingEvent:
    session_id: str
    error: str
    attempt: int
    delay_seconds: float
    iterations: int
    turn_id: str | None = None
    type: str = "retrying"


RuntimeEvent = Union[
    TurnStartedEvent,
    AssistantStartedEvent,
    TextDeltaEvent,
    ToolPendingEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    AssistantRequiresFollowupEvent,
    TurnFinishedEvent,
    RuntimeErrorEvent,
    CompactionStartedEvent,
    RetryingEvent,
]
