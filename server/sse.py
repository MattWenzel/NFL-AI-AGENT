"""Server-sent-event payload serialization for chat streaming.

Translates a domain `RuntimeEvent` into the wire dict that the browser
receives over SSE. Kept separate from the router so the mapping between
runtime events and client-visible event types is easy to audit.

Returning `None` means "suppress this event". Events that drive
server-side loop state (turn lifecycle / followup iteration) are
deliberately unmapped and return `None`. Any event variant without an
explicit arm here trips the catch-all warning below — new variants
can't be silently dropped after a refactor.
"""

import logging

from agent.events import (
    AssistantRequiresFollowupEvent,
    AssistantStartedEvent,
    CompactionStartedEvent,
    RetryingEvent,
    RuntimeErrorEvent,
    RuntimeEvent,
    TextDeltaEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolPendingEvent,
    TurnFinishedEvent,
    TurnStartedEvent,
)

logger = logging.getLogger(__name__)


def event_to_sse_payload(event: RuntimeEvent) -> dict | None:
    if isinstance(event, AssistantStartedEvent):
        return {"type": "assistant_started", "turn_id": event.turn_id, "iterations": event.iterations}
    if isinstance(event, TextDeltaEvent):
        return {"type": "text", "text": event.text}
    if isinstance(event, ToolPendingEvent):
        return {"type": "tool_call", "tool_run_id": event.tool_run_id, "name": event.name, "input": event.input}
    if isinstance(event, CompactionStartedEvent):
        return {"type": "compaction", "meta": event.meta}
    if isinstance(event, RetryingEvent):
        return {
            "type": "retrying",
            "attempt": event.attempt,
            "delay_seconds": event.delay_seconds,
            "message": event.error or "Retrying after transient error",
        }
    if isinstance(event, ToolCompletedEvent):
        return {"type": "tool_result", "tool_run_id": event.tool_run_id, "name": event.name}
    if isinstance(event, ToolFailedEvent):
        return {"type": "tool_failed", "tool_run_id": event.tool_run_id, "name": event.name, "message": event.error or f"{event.name} failed"}
    if isinstance(event, RuntimeErrorEvent):
        return {"type": "error", "message": event.error or "Runtime error"}
    if isinstance(event, (TurnStartedEvent, TurnFinishedEvent, AssistantRequiresFollowupEvent)):
        return None
    logger.warning("Unmapped runtime event variant %r dropped from SSE stream", type(event).__name__)
    return None
