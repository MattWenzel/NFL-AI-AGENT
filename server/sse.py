"""Server-sent-event payload serialization for chat streaming.

Translates a domain `RuntimeEvent` into the wire dict that the browser
receives over SSE. Kept separate from the router so the mapping between
runtime events and client-visible event types is easy to audit.

Returning `None` means "suppress this event". The suppression list is
explicit (see `_INTERNAL_EVENTS`) so that a new runtime event type can't
be silently dropped on the floor after a refactor — unmapped events
fall through to a `logger.warning` and still return `None` so the
stream stays intact.
"""

import logging

from agent.events import RuntimeEvent

logger = logging.getLogger(__name__)


# Runtime events that drive server-side loop state (turn lifecycle,
# followup iteration) and deliberately don't surface to the browser.
# Listed explicitly so any NEW event type added to the runtime without a
# matching SSE mapping trips the warning below instead of vanishing.
_INTERNAL_EVENTS = frozenset({
    "turn_started",
    "turn_finished",
    "assistant_requires_followup",
})


def event_to_sse_payload(event: RuntimeEvent) -> dict | None:
    if event.type == "assistant_started":
        return {"type": "assistant_started", "turn_id": event.turn_id, "iterations": event.iterations}
    if event.type == "text_delta":
        return {"type": "text", "text": event.text}
    if event.type == "tool_pending":
        return {"type": "tool_call", "tool_run_id": event.tool_run_id, "name": event.name, "input": event.input}
    if event.type == "compaction_started":
        return {"type": "compaction", "meta": event.meta}
    if event.type == "retrying":
        return {
            "type": "retrying",
            "attempt": event.attempt,
            "delay_seconds": event.delay_seconds,
            "message": event.error or "Retrying after transient error",
        }
    if event.type == "tool_completed":
        return {"type": "tool_result", "tool_run_id": event.tool_run_id, "name": event.name}
    if event.type == "tool_failed":
        return {"type": "tool_failed", "tool_run_id": event.tool_run_id, "name": event.name, "message": event.error or f"{event.name} failed"}
    if event.type == "runtime_error":
        return {"type": "error", "message": event.error or "Runtime error"}
    if event.type in _INTERNAL_EVENTS:
        return None
    logger.warning("Unmapped runtime event type %r dropped from SSE stream", event.type)
    return None
