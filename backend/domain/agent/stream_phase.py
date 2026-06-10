"""Provider-stream → agent-event translation, shared by both runtimes.

Both `ChatRuntime.run_session` (persistent) and `run_stateless_turn`
(in-memory) consume `client.stream_message` and translate the
provider's `ProviderRetryingEvent` into the agent-runtime
`RetryingEvent` so callers don't have to recognize two retrying-event
types. `TextEvent` and `ToolUseEvent` pass through unchanged; each
runtime decides whether to persist them or buffer them in memory.

The bulk of each runtime's per-iteration logic stays in its own module
— ChatRuntime owns persistence (assistant turns, parts, tool_runs,
compaction, doom-loop detection) and stateless owns in-memory history
mutation + tool whitelisting. Trying to share more than the
event-translation seam would force a heavy persistence-sink interface
that's mostly no-ops on the stateless side.
"""

from __future__ import annotations

from typing import AsyncIterator

from backend.domain.agent.events import RetryingEvent
from backend.domain.providers.base import BaseLLMClient
from backend.domain.providers.types import (
    Message,
    ProviderRetryingEvent,
    TextEvent,
    ToolChoice,
    Tool,
    ToolUseEvent,
)


async def iterate_agent_stream(
    client: BaseLLMClient,
    *,
    messages: list[Message],
    tools: list[Tool],
    system: str,
    tool_choice: ToolChoice | None,
    session_id: str,
    turn_id: str | None,
    iterations: int,
) -> AsyncIterator[RetryingEvent | TextEvent | ToolUseEvent]:
    """Drain one `client.stream_message` call, translating retry events.

    Yields:
      - `RetryingEvent` when the provider hit a transient error before
        any content streamed (so the UI shows progress instead of a
        silent stall);
      - `TextEvent` / `ToolUseEvent` verbatim — callers decide what to
        do with them (persist via Turn, or buffer in memory).
    """
    async for event in client.stream_message(
        messages=messages,
        tools=tools,
        system=system,
        tool_choice=tool_choice,
    ):
        if isinstance(event, ProviderRetryingEvent):
            yield RetryingEvent(
                session_id=session_id,
                turn_id=turn_id,
                error=event.error_message,
                attempt=event.attempt,
                delay_seconds=event.delay_seconds,
                iterations=iterations,
            )
        else:
            yield event
