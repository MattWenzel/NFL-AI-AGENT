"""Persisted runtime loop for the NFL stats agent.

Drives one user turn through the model → tool loop → persistence
pipeline. Shared by the FastAPI router and the CLI.

Concerns that used to live here are now in sibling modules:
- RuntimeEvent, RuntimeLoopError       → events.py
- Token estimation + compaction        → compaction.py
- Doom-loop detection                  → loop_detector.py

Tool schemas + the pre-built TOOLS list live in agent.tools.definitions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from infra.persistence.runtime_store import (
    RuntimeStore,
    SessionRecord,
    TurnRecord,
)
from infra.providers import (
    BaseLLMClient,
    TextEvent,
    ToolDefinition,
    ToolUseEvent,
    Usage,
    get_provider,
)

from agent.prompts.hints import get_system_prompt
from agent.runtime.compaction import compact_if_needed
from agent.runtime.events import RuntimeEvent, RuntimeLoopError
from agent.runtime.loop_detector import raise_if_doom_loop
from agent.tools import execute_tool_structured

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
TITLE_PREVIEW_CHARS = 80


class ChatRuntime:
    """Shared runtime used by API and CLI."""

    def __init__(self, store: RuntimeStore):
        self.store = store

    def prepare_session(
        self,
        client: BaseLLMClient,
        provider_name: str,
        conversation_id: str | None = None,
        *,
        user_id: int | None = None,
    ) -> SessionRecord:
        """Resolve (or create) the session backing a chat turn.

        Called by both the API router and the CLI before `run_session` so
        the two entry points agree on how sessions are keyed to providers
        and how the context window is derived from the provider's
        effective window. `user_id` is threaded through from the HTTP layer
        so new sessions are owned by the authenticated user; the CLI omits
        it since it bypasses auth.
        """
        info = get_provider(provider_name)
        return self.store.get_or_create_session(
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
    ) -> AsyncIterator[RuntimeEvent]:
        """Drive one user turn through the model, tool loop, and persistence.

        Always consumes `client.stream_message` — the streamed events are
        accumulated here, and non-streaming consumers (like `/chat/message`)
        buffer them at the boundary. This is the same pattern opencode uses
        with Vercel's `streamText` and keeps a single code path through the
        runtime.
        """
        lock = self.store.lock(session.id)
        async with lock:
            assistant_turn: TurnRecord | None = None
            tool_runs = []
            user_turn = self.store.create_turn(session.id, "user", text=user_text, status="completed")
            if not session.title:
                session.title = user_text[:TITLE_PREVIEW_CHARS]
            session.provider = provider_name
            session.model = client.model
            self.store.update_session(session)
            yield RuntimeEvent(type="turn_started", session_id=session.id, turn_id=user_turn.id)
            iterations = 0
            try:
                for _ in range(MAX_TOOL_ITERATIONS):
                    iterations += 1
                    compaction_info = compact_if_needed(self.store, session)
                    if compaction_info is not None:
                        yield RuntimeEvent(
                            type="compaction_started",
                            session_id=session.id,
                            turn_id=compaction_info["summary_turn_id"],
                            iterations=iterations,
                            meta=compaction_info,
                        )
                    assistant_turn = self.store.create_turn(session.id, "assistant", status="running")
                    tool_runs = []
                    yield RuntimeEvent(type="assistant_started", session_id=session.id, turn_id=assistant_turn.id, iterations=iterations)

                    # Reset per-turn usage so a prior turn's tokens don't leak into
                    # this one's accounting if the provider never emits a usage event.
                    client.last_usage = Usage()
                    try:
                        async for event in client.stream_message(
                            messages=self.store.build_model_messages(session.id),
                            tools=tools,
                            system=get_system_prompt(provider_name),
                        ):
                            if isinstance(event, TextEvent):
                                self.store.append_turn_text(assistant_turn.id, event.text)
                                self.store.add_part(session.id, assistant_turn.id, "text", event.text)
                                yield RuntimeEvent(
                                    type="text_delta",
                                    session_id=session.id,
                                    turn_id=assistant_turn.id,
                                    text=event.text,
                                    iterations=iterations,
                                )
                            elif isinstance(event, ToolUseEvent):
                                tool_run = self.store.create_tool_run(
                                    session.id, assistant_turn.id, event.name, event.input, status="pending"
                                )
                                self.store.add_part(
                                    session.id,
                                    assistant_turn.id,
                                    "tool_call",
                                    json.dumps(event.input, separators=(",", ":"), sort_keys=True),
                                    name=event.name,
                                    tool_run_id=tool_run.id,
                                )
                                tool_runs.append(tool_run)
                                yield RuntimeEvent(
                                    type="tool_pending",
                                    session_id=session.id,
                                    turn_id=assistant_turn.id,
                                    tool_run_id=tool_run.id,
                                    name=event.name,
                                    input=event.input,
                                    iterations=iterations,
                                )
                        final_usage = client.last_usage
                        self.store.update_turn(
                            assistant_turn.id,
                            status="completed",
                            input_tokens=final_usage.input_tokens,
                            output_tokens=final_usage.output_tokens,
                        )
                        if not tool_runs:
                            yield RuntimeEvent(type="turn_finished", session_id=session.id, turn_id=assistant_turn.id, iterations=iterations)
                            assistant_turn = None
                            return

                        raise_if_doom_loop(self.store, session.id, tool_runs)

                        results = await asyncio.gather(*(self._execute_tool(session.id, assistant_turn, tool_run) for tool_run in tool_runs))
                        for tool_run, result in zip(tool_runs, results):
                            yield RuntimeEvent(
                                type="tool_completed" if result["status"] == "completed" else "tool_failed",
                                session_id=session.id,
                                turn_id=assistant_turn.id,
                                tool_run_id=tool_run.id,
                                name=tool_run.tool_name,
                                result=result["content"],
                                error=result.get("error"),
                                iterations=iterations,
                            )

                        yield RuntimeEvent(type="assistant_requires_followup", session_id=session.id, turn_id=assistant_turn.id, iterations=iterations)
                        assistant_turn = None
                        tool_runs = []
                    except RuntimeLoopError as exc:
                        failed_turn_id = assistant_turn.id
                        self.store.update_turn(
                            assistant_turn.id,
                            status="error",
                            error=str(exc),
                            input_tokens=client.last_usage.input_tokens,
                            output_tokens=client.last_usage.output_tokens,
                        )
                        assistant_turn = None
                        yield RuntimeEvent(
                            type="runtime_error",
                            session_id=session.id,
                            turn_id=failed_turn_id,
                            error=str(exc),
                            iterations=iterations,
                        )
                        return
                    except Exception as exc:
                        self.store.update_turn(
                            assistant_turn.id,
                            status="error",
                            error=str(exc),
                            input_tokens=client.last_usage.input_tokens,
                            output_tokens=client.last_usage.output_tokens,
                        )
                        raise
                yield RuntimeEvent(
                    type="runtime_error",
                    session_id=session.id,
                    error=f"Reached maximum tool iterations ({MAX_TOOL_ITERATIONS})",
                    iterations=MAX_TOOL_ITERATIONS,
                )
            finally:
                if assistant_turn is not None:
                    current = self.store.get_turn(assistant_turn.id)
                    if current is not None and current.status == "running":
                        self.store.update_turn(
                            assistant_turn.id,
                            status="interrupted",
                            error="Assistant turn interrupted before completion",
                        )
                for tool_run in tool_runs:
                    current = self.store.get_tool_run(tool_run.id)
                    if current is not None and current.status in {"pending", "running"}:
                        self.store.update_tool_run(
                            tool_run.id,
                            status="interrupted",
                            error_text="Tool execution interrupted before completion",
                        )

    async def _execute_tool(self, session_id: str, assistant_turn: TurnRecord, tool_run) -> dict:
        self.store.update_tool_run(tool_run.id, status="running")
        self.store.add_part(
            session_id,
            assistant_turn.id,
            "tool_status",
            "running",
            name=tool_run.tool_name,
            tool_run_id=tool_run.id,
        )
        try:
            tool_input = json.loads(tool_run.input_json) if tool_run.input_json else {}
        except json.JSONDecodeError as exc:
            err = f"Malformed tool input JSON: {exc}"
            logger.warning("tool_run %s has malformed input_json: %s", tool_run.id, exc)
            self.store.update_tool_run(
                tool_run.id, status="error", error_text=err, result_text=err
            )
            self.store.add_part(
                session_id, assistant_turn.id, "tool_result", err,
                name=tool_run.tool_name, tool_run_id=tool_run.id,
            )
            return {"status": "error", "content": err, "error": err}
        # Side-channel hooks tools may use (e.g. create_csv_export registers
        # the file in the export library). Closure captures session + tool_run
        # so the handler doesn't need to know about persistence.
        ctx = {
            "register_export": lambda meta: self.store.register_export(
                **meta,
                source_session_id=session_id,
                source_tool_run_id=tool_run.id,
            ),
        }
        result = await execute_tool_structured(tool_run.tool_name, tool_input, ctx=ctx)
        status = "completed" if result["status"] == "completed" else "error"
        self.store.update_tool_run(
            tool_run.id,
            status=status,
            result_text=result["content"],
            error_text=result.get("error"),
            hint=result.get("hint"),
            duration_ms=result.get("duration_ms"),
        )
        self.store.add_part(
            session_id,
            assistant_turn.id,
            "tool_result",
            result["content"],
            name=tool_run.tool_name,
            tool_run_id=tool_run.id,
        )
        return result
