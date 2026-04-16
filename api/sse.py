"""Server-sent-event payload serialization for chat streaming.

Translates a domain `RuntimeEvent` into the wire dict that the browser
receives over SSE. Kept separate from the router so the mapping between
runtime events and client-visible event types is easy to audit.

Returning `None` means "suppress this event" — used for internal
signals (assistant_started, turn_started, etc.) that the client doesn't
need.
"""

from agent.runtime import RuntimeEvent


def event_to_sse_payload(event: RuntimeEvent) -> dict | None:
    if event.type == "assistant_started":
        return {"type": "assistant_started", "turn_id": event.turn_id, "iterations": event.iterations}
    if event.type == "text_delta":
        return {"type": "text", "text": event.text}
    if event.type == "tool_pending":
        return {"type": "tool_call", "tool_run_id": event.tool_run_id, "name": event.name, "input": event.input}
    if event.type == "compaction_started":
        return {"type": "compaction", "meta": event.meta}
    if event.type == "tool_completed":
        return {"type": "tool_result", "tool_run_id": event.tool_run_id, "name": event.name}
    if event.type == "tool_failed":
        return {"type": "tool_failed", "tool_run_id": event.tool_run_id, "name": event.name, "message": event.error or f"{event.name} failed"}
    if event.type == "runtime_error":
        return {"type": "error", "message": event.error or "Runtime error"}
    return None
