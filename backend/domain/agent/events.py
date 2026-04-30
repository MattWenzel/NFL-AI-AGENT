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


@dataclass(frozen=True, kw_only=True)
class ReportCreatedEvent:
    """Emitted after a successful `create_report` tool call. The frontend
    refreshes the sidebar list and renders a clickable link card in the
    agent's response so the user can open the new Report on demand.
    """
    session_id: str
    turn_id: str
    tool_run_id: str
    report_id: str
    title: str
    row_count: int
    iterations: int
    type: str = "report_created"


@dataclass(frozen=True, kw_only=True)
class TableUpdatedEvent:
    """Emitted after a successful `set_table` tool call.

    Carries only the summary the UI needs to decide whether/how to
    refetch the live table — the rows themselves stream over a separate
    HTTP fetch, not via SSE, since they may be large.
    """
    session_id: str
    turn_id: str
    tool_run_id: str
    row_count: int
    truncated: bool
    columns: list[str]
    iterations: int
    type: str = "table_updated"


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
    TableUpdatedEvent,
    ReportCreatedEvent,
]
