"""Stateless agent loop — same shape as `ChatRuntime.run_session` but
without persistence.

Used by the Database browser's helper chat: the message history lives in
the browser, the request carries it on every turn, and the loop runs
through the model + tool dispatch without writing anything to the
runtime store. No session lock, no Turn object, no compaction — those
all live in `runtime.py`/`turn.py` and exist to manage persisted state
this loop deliberately doesn't have.

The five tools whitelisted for the helper (`get_schema`, `get_guide`,
`execute_sql`, `search_players`, `get_player_info`) all take `ctx=None`
without needing any of the persist closures wired by the regular runtime
(`persist_table`, `create_report`, `register_export`). Anything outside
that whitelist is rejected at dispatch time with a `ToolFailedEvent` so
a misbehaving model doesn't get a free pass to spawn Reports from this
endpoint.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncGenerator

from backend.domain.agent.events import (
    RetryingEvent,
    RuntimeErrorEvent,
    RuntimeEvent,
    TextDeltaEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolPendingEvent,
)
from backend.domain.providers.base import BaseLLMClient
from backend.domain.providers.errors import LLMError
from backend.domain.providers.types import (
    Message,
    ProviderRetryingEvent,
    TextEvent,
    ToolChoice,
    ToolDefinition,
    ToolUseEvent,
)
from backend.domain.tools import execute_tool

logger = logging.getLogger(__name__)


# Mirrors `MAX_TOOL_ITERATIONS` in `runtime.py` but a bit lower — helper
# chats are short by design; if the model is bouncing through 8 tool
# calls without answering, something is off and we'd rather surface it.
MAX_HELPER_ITERATIONS = 8

# Hard list of tools the helper is allowed to call. Anything outside this
# set is rejected even if the model emits it (e.g. `set_table` would have
# nowhere to persist to without the closures the regular runtime wires
# up). Kept here, not on the request, so the API contract is enforced
# server-side rather than relying on the client to pass the right list.
ALLOWED_HELPER_TOOLS: frozenset[str] = frozenset(
    {
        "get_schema",
        "get_guide",
        "execute_sql",
        "search_players",
        "get_player_info",
        "run_in_editor",
    }
)

# Synthetic session/turn ids. The event variants share `session_id` /
# `turn_id` fields with the persisted runtime; we fill them with constants
# so the existing event types can be reused without making either field
# optional. Nothing downstream of the helper consumes these values.
_HELPER_SESSION_ID = "db-helper"
_HELPER_TURN_ID = "db-helper-turn"


async def run_stateless_turn(
    *,
    messages: list[Message],
    client: BaseLLMClient,
    tools: list[ToolDefinition],
    system: str,
    tool_choice: ToolChoice | None = None,
    max_iterations: int = MAX_HELPER_ITERATIONS,
) -> AsyncGenerator[RuntimeEvent, None]:
    """Run one user turn through the model + tool loop.

    `messages` is the wire history (provider-shaped), already including
    the latest user message. The caller appends nothing; this function
    drives the loop until the model stops calling tools (returning) or
    `max_iterations` is hit.
    """
    history: list[Message] = list(messages)
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        pending_tools: list[ToolUseEvent] = []
        text_buffer: list[str] = []

        try:
            async for event in client.stream_message(
                messages=history,
                tools=tools,
                system=system,
                tool_choice=tool_choice,
            ):
                if isinstance(event, ProviderRetryingEvent):
                    yield RetryingEvent(
                        session_id=_HELPER_SESSION_ID,
                        turn_id=_HELPER_TURN_ID,
                        error=event.error_message,
                        attempt=event.attempt,
                        delay_seconds=event.delay_seconds,
                        iterations=iterations,
                    )
                elif isinstance(event, TextEvent):
                    text_buffer.append(event.text)
                    yield TextDeltaEvent(
                        session_id=_HELPER_SESSION_ID,
                        turn_id=_HELPER_TURN_ID,
                        text=event.text,
                        iterations=iterations,
                    )
                elif isinstance(event, ToolUseEvent):
                    pending_tools.append(event)
                    yield ToolPendingEvent(
                        session_id=_HELPER_SESSION_ID,
                        turn_id=_HELPER_TURN_ID,
                        tool_run_id=event.id,
                        name=event.name,
                        input=event.input,
                        iterations=iterations,
                    )
        except LLMError as exc:
            # Provider-side error after streaming started or before any
            # event landed. Surface to the caller; the route maps it to an
            # SSE error frame.
            yield RuntimeErrorEvent(
                session_id=_HELPER_SESSION_ID,
                error=str(exc),
                iterations=iterations,
            )
            return
        except Exception as exc:
            logger.exception("Unexpected error in stateless helper loop")
            yield RuntimeErrorEvent(
                session_id=_HELPER_SESSION_ID,
                error=str(exc),
                iterations=iterations,
            )
            return

        # Record the assistant turn (text + any tool calls) into the
        # in-memory history so the next iteration's stream_message sees
        # it. Skip when both are empty (unlikely but guarded so we never
        # send an empty assistant message back to the provider).
        assistant_text = "".join(text_buffer).rstrip() or None
        if assistant_text or pending_tools:
            history.append(Message(
                role="assistant",
                text=assistant_text,
                tool_calls=list(pending_tools) or None,
            ))

        if not pending_tools:
            return

        for tool_call in pending_tools:
            if tool_call.name not in ALLOWED_HELPER_TOOLS:
                error = (
                    f"Tool '{tool_call.name}' is not available in the database "
                    "helper. Available tools: " + ", ".join(sorted(ALLOWED_HELPER_TOOLS))
                )
                content = json.dumps({"error": error})
                yield ToolFailedEvent(
                    session_id=_HELPER_SESSION_ID,
                    turn_id=_HELPER_TURN_ID,
                    tool_run_id=tool_call.id,
                    name=tool_call.name,
                    result=content,
                    error=error,
                    iterations=iterations,
                )
                history.append(Message(
                    role="tool_result",
                    tool_use_id=tool_call.id,
                    tool_content=content,
                ))
                continue

            try:
                content = await execute_tool(
                    tool_call.name, tool_call.input, ctx=None
                )
            except Exception as exc:  # pragma: no cover — execute_tool catches its own
                logger.exception("Tool %s raised", tool_call.name)
                content = json.dumps({"error": str(exc)})

            error: str | None = None
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    err = parsed.get("error")
                    if isinstance(err, str) and err:
                        error = err
            except (json.JSONDecodeError, TypeError):
                # Non-JSON tool output is treated as a successful raw
                # string result.
                pass

            cls = ToolFailedEvent if error else ToolCompletedEvent
            yield cls(
                session_id=_HELPER_SESSION_ID,
                turn_id=_HELPER_TURN_ID,
                tool_run_id=tool_call.id,
                name=tool_call.name,
                result=content,
                error=error,
                iterations=iterations,
            )
            history.append(Message(
                role="tool_result",
                tool_use_id=tool_call.id,
                tool_content=content,
            ))

    yield RuntimeErrorEvent(
        session_id=_HELPER_SESSION_ID,
        error=(
            f"Stopped after {max_iterations} tool iterations without a final "
            "answer. Try rephrasing or asking a smaller question."
        ),
        iterations=iterations,
    )


def new_helper_tool_call_id() -> str:
    """Caller-side helper for unit tests that fabricate ToolUseEvents."""
    return f"toolu_helper_{uuid.uuid4().hex[:12]}"
