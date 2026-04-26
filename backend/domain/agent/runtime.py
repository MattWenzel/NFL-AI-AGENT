"""Persisted runtime loop for the NFL stats agent.

Drives one user turn through the model → tool loop → persistence
pipeline. Shared by the FastAPI transport and any future non-HTTP entry
point.

Concerns that used to live here are now in sibling modules:

- `RuntimeEvent` variants                        → `events.py`
- `RuntimeLoopError`                             → `errors.py`
- Token estimation + compaction                  → `compaction.py`
- Turn lifecycle + doom-loop policy + tool exec  → `turn.py`

Tool schemas + the pre-built TOOLS list live in `tools.definitions`.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from backend.domain.agent.events import (
    AssistantRequiresFollowupEvent,
    RetryingEvent,
    RuntimeErrorEvent,
    RuntimeEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    TurnStartedEvent,
)
from backend.domain.providers.base import BaseLLMClient
from backend.domain.providers.errors import ContextOverflowError
from backend.domain.providers.types import (
    ProviderRetryingEvent,
    TextEvent,
    ToolChoice,
    ToolDefinition,
    ToolUseEvent,
    Usage,
)
from backend.domain.agent.message_builder import build_model_messages
from backend.domain.agent.system_prompt import get_base_prompt
from backend.domain.agent.turn import RuntimeLoopError, TITLE_PREVIEW_CHARS, Turn
from backend.domain.providers import get_provider
from backend.data import RuntimeStore, SessionRecord
from backend.domain.tools import execute_tool_structured

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10


class ChatRuntime:
    """Shared runtime used by the API and any future non-HTTP caller."""

    def __init__(self, store: RuntimeStore):
        self.store = store

    async def prepare_session(
        self,
        client: BaseLLMClient,
        provider_name: str,
        conversation_id: str | None = None,
        *,
        user_id: int | None = None,
    ) -> SessionRecord:
        info = get_provider(provider_name)
        return await self.store.get_or_create_session(
            conversation_id,
            provider=provider_name,
            model=client.model,
            context_window=info.effective_context_window,
            user_id=user_id,
        )

    async def run_session(
        self,
        session: SessionRecord,
        user_text: str,
        client: BaseLLMClient,
        *,
        tools: list[ToolDefinition],
        provider_name: str,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """Drive one user turn through the model, tool loop, and persistence.

        Always consumes `client.stream_message` — the streamed events are
        accumulated here, and non-streaming consumers (like `/chat/message`)
        buffer them at the boundary. This is the same pattern opencode uses
        with Vercel's `streamText` and keeps a single code path through the
        runtime.
        """
        lock = self.store.lock(session.id)
        async with lock:
            user_turn = await self.store.create_turn(
                session.id, "user", text=user_text, status="completed"
            )
            session_changes: dict[str, object] = {
                "provider": provider_name,
                "model": client.model,
            }
            if not session.title:
                session_changes["title"] = user_text[:TITLE_PREVIEW_CHARS]
            await self.store.update_session(session.id, **session_changes)
            yield TurnStartedEvent(session_id=session.id, turn_id=user_turn.id)
            turn = Turn(
                store=self.store,
                session=session,
                execute_tool=execute_tool_structured,
                initial_tool_choice=tool_choice,
            )
            try:
                for _ in range(MAX_TOOL_ITERATIONS):
                    iterations, iter_tool_choice = turn.begin_iteration()
                    compaction_info = await turn.compact_if_needed(
                        client, provider_name=provider_name
                    )
                    if compaction_info is not None:
                        yield turn.compaction_event(compaction_info)
                    yield await turn.open_assistant_turn()

                    # Reset per-turn usage so a prior iteration's tokens
                    # don't leak into this one's accounting if the provider
                    # never emits a usage event.
                    client.last_usage = Usage()
                    client.last_stop_reason = None
                    try:
                        transcript = await self.store.get_transcript(session.id)
                        async for event in client.stream_message(
                            messages=build_model_messages(transcript),
                            tools=tools,
                            system=get_base_prompt(),
                            tool_choice=iter_tool_choice,
                        ):
                            if isinstance(event, ProviderRetryingEvent):
                                # Provider hit a transient error before any
                                # content streamed; surface it so the UI
                                # shows progress instead of a silent stall.
                                yield RetryingEvent(
                                    session_id=session.id,
                                    turn_id=turn.active_assistant_turn_id,
                                    error=event.error_message,
                                    attempt=event.attempt,
                                    delay_seconds=event.delay_seconds,
                                    iterations=iterations,
                                )
                            elif isinstance(event, TextEvent):
                                yield await turn.record_text_delta(event.text)
                            elif isinstance(event, ToolUseEvent):
                                yield await turn.record_tool_call(event)

                        finished_event = await turn.complete_assistant_turn(
                            usage=client.last_usage,
                            stop_reason=client.last_stop_reason,
                        )
                        if finished_event is not None:
                            yield finished_event
                            turn.reset_active_iteration()
                            return

                        turn.record_tool_runs(turn.active_tool_runs)
                        results = await turn.execute_tools()

                        for tool_run, result in zip(turn.active_tool_runs, results):
                            cls = ToolCompletedEvent if result.is_completed else ToolFailedEvent
                            yield cls(
                                session_id=session.id,
                                turn_id=turn.active_assistant_turn_id,
                                tool_run_id=tool_run.id,
                                name=tool_run.tool_name,
                                result=result.content,
                                error=result.error,
                                iterations=iterations,
                            )
                        yield AssistantRequiresFollowupEvent(
                            session_id=session.id,
                            turn_id=turn.active_assistant_turn_id,
                            iterations=iterations,
                        )
                        turn.reset_active_iteration()
                    except ContextOverflowError as exc:
                        # Provider says the prompt is too long even though
                        # our estimator was happy. Mark this attempt as
                        # errored, arm forced compaction, and let the outer
                        # loop run one more iteration to compact and retry.
                        # One-shot per user turn — a second overflow falls
                        # through to the `runtime_error` branch below.
                        await turn.mark_assistant_turn_error(
                            error=str(exc),
                            usage=client.last_usage,
                        )
                        turn.reset_active_iteration()
                        if not turn.handle_overflow():
                            yield RuntimeErrorEvent(
                                session_id=session.id,
                                error=str(exc),
                                iterations=iterations,
                            )
                            return
                        continue
                    except RuntimeLoopError as exc:
                        failed_turn_id = turn.active_assistant_turn_id
                        await turn.mark_assistant_turn_error(
                            error=str(exc),
                            usage=client.last_usage,
                        )
                        turn.reset_active_iteration()
                        yield RuntimeErrorEvent(
                            session_id=session.id,
                            turn_id=failed_turn_id,
                            error=str(exc),
                            iterations=iterations,
                        )
                        return
                    except Exception as exc:
                        await turn.mark_assistant_turn_error(
                            error=str(exc),
                            usage=client.last_usage,
                        )
                        turn.reset_active_iteration()
                        raise
                yield turn.max_iterations_event(MAX_TOOL_ITERATIONS)
            finally:
                if turn.has_active_assistant_turn:
                    await turn.cleanup_interrupted_assistant_turn()
