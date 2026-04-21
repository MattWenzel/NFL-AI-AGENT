"""Persisted runtime loop for the NFL stats agent.

Drives one user turn through the model → tool loop → persistence
pipeline. Shared by the FastAPI transport and any future non-HTTP entry point.

Concerns that used to live here are now in sibling modules:
- RuntimeEvent, RuntimeLoopError       → events.py
- Token estimation + compaction        → compaction.py
- Doom-loop detection                  → loop_detector.py

Tool schemas + the pre-built TOOLS list live in tools.definitions.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from storage import (
    RuntimeStore,
    SessionRecord,
)
from provider import (
    BaseLLMClient,
    RetryingEvent,
    TextEvent,
    ToolChoice,
    ToolDefinition,
    ToolUseEvent,
    Usage,
    get_provider,
)
from provider.base import ContextOverflowError

from agent.system_prompt import get_base_prompt
from agent.events import RuntimeEvent, RuntimeLoopError
from agent.message_builder import build_model_messages
from agent.persistence import RuntimePersistence
from agent.runtime_policy import RuntimeLoopState
from agent.tool_execution import ToolExecutionService
from agent.turn_manager import AssistantTurnContext, AssistantTurnManager, TITLE_PREVIEW_CHARS
from tools import execute_tool_structured

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10

class ChatRuntime:
    """Shared runtime used by the API and any future non-HTTP caller."""

    def __init__(self, store: RuntimeStore):
        self.store = store
        self.persistence = RuntimePersistence(store)
        self.turns = AssistantTurnManager(self.persistence)
        self.tools = ToolExecutionService(
            store,
            self.persistence,
            execute_tool=lambda *args, **kwargs: execute_tool_structured(*args, **kwargs),
        )

    async def prepare_session_async(
        self,
        client: BaseLLMClient,
        provider_name: str,
        conversation_id: str | None = None,
        *,
        user_id: int | None = None,
    ) -> SessionRecord:
        info = get_provider(provider_name)
        return await self.store.get_or_create_session_async(
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
            active_turn: AssistantTurnContext | None = None
            user_turn = await self.persistence.create_user_turn(session.id, user_text)
            await self.persistence.update_session_metadata(
                session,
                provider_name=provider_name,
                model=client.model,
                title_preview_chars=TITLE_PREVIEW_CHARS,
                user_text=user_text,
            )
            yield RuntimeEvent(type="turn_started", session_id=session.id, turn_id=user_turn.id)
            loop_state = RuntimeLoopState(initial_tool_choice=tool_choice)
            try:
                for _ in range(MAX_TOOL_ITERATIONS):
                    iterations, iter_tool_choice = loop_state.begin_iteration()
                    compaction_info = await loop_state.compact_if_needed(
                        self.store,
                        session,
                        client,
                        provider_name=provider_name,
                    )
                    if compaction_info is not None:
                        yield loop_state.compaction_event(session.id, compaction_info)
                    active_turn, start_event = await self.turns.open_turn(
                        session.id,
                        iterations=iterations,
                    )
                    yield start_event

                    # Reset per-turn usage so a prior turn's tokens don't leak into
                    # this one's accounting if the provider never emits a usage event.
                    client.last_usage = Usage()
                    client.last_stop_reason = None
                    try:
                        transcript = await self.store.get_transcript_async(session.id)
                        async for event in client.stream_message(
                            messages=build_model_messages(transcript),
                            tools=tools,
                            system=get_base_prompt(),
                            tool_choice=iter_tool_choice,
                        ):
                            if isinstance(event, RetryingEvent):
                                # Provider hit a transient error before any
                                # content streamed; surface it so the UI shows
                                # progress instead of a silent stall.
                                yield RuntimeEvent(
                                    type="retrying",
                                    session_id=session.id,
                                    turn_id=active_turn.assistant_turn.id,
                                    error=event.error_message,
                                    attempt=event.attempt,
                                    delay_seconds=event.delay_seconds,
                                    iterations=iterations,
                                )
                            elif isinstance(event, TextEvent):
                                yield await self.turns.record_text_delta(
                                    active_turn,
                                    session.id,
                                    event.text,
                                    iterations=iterations,
                                )
                            elif isinstance(event, ToolUseEvent):
                                yield await self.turns.record_tool_call(
                                    active_turn,
                                    session.id,
                                    event,
                                    iterations=iterations,
                                )
                        final_usage = client.last_usage
                        finished_event = await self.turns.complete_turn(
                            active_turn,
                            session.id,
                            usage=final_usage,
                            stop_reason=client.last_stop_reason,
                            iterations=iterations,
                        )
                        if finished_event is not None:
                            yield finished_event
                            active_turn = None
                            return

                        loop_state.record_tool_runs(active_turn.tool_runs)

                        results = await self.tools.execute_many(
                            session.id,
                            active_turn.assistant_turn,
                            active_turn.tool_runs,
                        )
                        for tool_run, result in zip(active_turn.tool_runs, results):
                            yield RuntimeEvent(
                                type="tool_completed" if result.is_completed else "tool_failed",
                                session_id=session.id,
                                turn_id=active_turn.assistant_turn.id,
                                tool_run_id=tool_run.id,
                                name=tool_run.tool_name,
                                result=result.content,
                                error=result.error,
                                iterations=iterations,
                            )

                        yield RuntimeEvent(
                            type="assistant_requires_followup",
                            session_id=session.id,
                            turn_id=active_turn.assistant_turn.id,
                            iterations=iterations,
                        )
                        active_turn = None
                    except ContextOverflowError as exc:
                        # Provider says the prompt is too long even though our
                        # estimator was happy. Mark this attempt as errored, set
                        # the forced-compaction flag, and let the outer loop run
                        # one more iteration to compact and retry. One-shot per
                        # user turn — a second overflow falls through to the
                        # generic LLMError branch below.
                        await self.turns.mark_turn_error(
                            active_turn,
                            error=str(exc),
                            usage=client.last_usage,
                        )
                        active_turn = None
                        if not loop_state.handle_overflow():
                            yield RuntimeEvent(
                                type="runtime_error",
                                session_id=session.id,
                                error=str(exc),
                                iterations=iterations,
                            )
                            return
                        continue
                    except RuntimeLoopError as exc:
                        failed_turn_id = active_turn.assistant_turn.id
                        await self.turns.mark_turn_error(
                            active_turn,
                            error=str(exc),
                            usage=client.last_usage,
                        )
                        active_turn = None
                        yield RuntimeEvent(
                            type="runtime_error",
                            session_id=session.id,
                            turn_id=failed_turn_id,
                            error=str(exc),
                            iterations=iterations,
                        )
                        return
                    except Exception as exc:
                        await self.turns.mark_turn_error(
                            active_turn,
                            error=str(exc),
                            usage=client.last_usage,
                        )
                        active_turn = None
                        raise
                yield loop_state.max_iterations_event(session.id, MAX_TOOL_ITERATIONS)
            finally:
                if active_turn is not None:
                    await self.turns.cleanup_interrupted(active_turn)
