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

import asyncio
import json
import logging
from typing import AsyncGenerator

from backend.domain.agent.events import (
    AssistantRequiresFollowupEvent,
    ReportCreatedEvent,
    RetryingEvent,
    RuntimeErrorEvent,
    RuntimeEvent,
    TableUpdatedEvent,
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
        kind: str = "chat",
    ) -> SessionRecord:
        info = get_provider(provider_name)
        return await self.store.get_or_create_session(
            conversation_id,
            provider=provider_name,
            model=client.model,
            context_window=info.effective_context_window,
            user_id=user_id,
            kind=kind,
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
        table_mode: str | None = None,
        table_max_rows: int | None = None,
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

            extra_tool_ctx = self._build_extra_tool_ctx(
                session=session,
                client=client,
                provider_name=provider_name,
                table_mode=table_mode,
                table_max_rows=table_max_rows,
            )

            turn = Turn(
                store=self.store,
                session=session,
                execute_tool=execute_tool_structured,
                initial_tool_choice=tool_choice,
                provider_name=provider_name,
                model_name=client.model,
                extra_tool_ctx=extra_tool_ctx,
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
                            system=get_base_prompt(
                                table_mode=table_mode is not None,
                                table_max_rows=table_max_rows,
                            ),
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
                            if (
                                tool_run.tool_name == "set_table"
                                and result.is_completed
                            ):
                                update_event = self._table_updated_event(
                                    session_id=session.id,
                                    turn_id=turn.active_assistant_turn_id,
                                    tool_run_id=tool_run.id,
                                    iterations=iterations,
                                    payload=result.content,
                                )
                                if update_event is not None:
                                    yield update_event
                            if (
                                tool_run.tool_name == "create_report"
                                and result.is_completed
                            ):
                                created_event = self._report_created_event(
                                    session_id=session.id,
                                    turn_id=turn.active_assistant_turn_id,
                                    tool_run_id=tool_run.id,
                                    iterations=iterations,
                                    payload=result.content,
                                )
                                if created_event is not None:
                                    yield created_event
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

    def _build_extra_tool_ctx(
        self,
        *,
        session: SessionRecord,
        client: BaseLLMClient,
        provider_name: str,
        table_mode: str | None,
        table_max_rows: int | None,
    ) -> dict | None:
        """Build the side-channel ctx merged into every tool call.

        - `create_report` is always provided (regular chats AND table chats)
          so the main agent can spin up a new Report from any conversation.
        - `set_table` plumbing (`persist_table` + `table_max_rows`) is only
          provided when the current session is in table-mode.

        Returns None when there's nothing to merge so regular Turn ctx stays
        unchanged.
        """
        loop = asyncio.get_running_loop()
        store = self.store

        def create_report(
            *,
            title: str,
            columns: list[str],
            rows: list[dict],
            last_sql: str | None,
            row_count: int,
            truncated: bool,
        ) -> str:
            async def _go() -> str:
                info = get_provider(provider_name)
                # Reports spawned from a regular chat carry source_session_id
                # so the sidebar can show "this chat created a report" on the
                # parent. Reports made from inside an Explore turn of another
                # report don't get a parent (avoid surfacing report→report
                # links in the chats list).
                parent_id = session.id if session.kind == "chat" else None
                new_session = await store.get_or_create_session(
                    provider=provider_name,
                    model=client.model,
                    context_window=info.effective_context_window,
                    user_id=session.user_id,
                    kind="table_chat",
                    source_session_id=parent_id,
                )
                if title:
                    await store.update_session(new_session.id, title=title)
                await store.upsert_table_state(
                    new_session.id,
                    columns=columns,
                    rows=rows,
                    last_sql=last_sql,
                    row_count=row_count,
                    truncated=truncated,
                )
                # Seed a summary turn so the new session's agent knows what
                # the table currently holds and how it was produced —
                # otherwise the transcript is empty and the agent has no
                # context for follow-up requests like "expand this with more
                # rows" (which would fail trying to query a `current_table`
                # that doesn't exist).
                column_list = ", ".join(columns) if columns else "(none)"
                sql_block = (
                    f"\n- Generated by this SQL:\n```sql\n{last_sql}\n```"
                    if last_sql else ""
                )
                summary_text = (
                    "This Report was created from another conversation. "
                    "The current table contains:\n"
                    f"- {row_count} rows × {len(columns)} columns\n"
                    f"- Columns: {column_list}"
                    f"{sql_block}\n\n"
                    "If the user asks to refine the table (more rows, different "
                    "filters, additional columns), call `set_table` with a new "
                    "SQL query — typically a variation of the SQL above. To "
                    "answer questions WITHOUT changing the table, use "
                    "`execute_sql` or any other research tool. The rows of the "
                    "current table are NOT a database table you can query — "
                    "they live only in the UI."
                )
                await store.seed_summary(new_session.id, summary_text)
                return new_session.id

            return asyncio.run_coroutine_threadsafe(_go(), loop).result()

        ctx: dict = {"create_report": create_report}

        if table_mode is not None:
            def persist_table(
                *,
                columns: list[str],
                rows: list[dict],
                sql: str | None,
                row_count: int,
                truncated: bool,
            ) -> None:
                future = asyncio.run_coroutine_threadsafe(
                    store.upsert_table_state(
                        session.id,
                        columns=columns,
                        rows=rows,
                        last_sql=sql,
                        row_count=row_count,
                        truncated=truncated,
                    ),
                    loop,
                )
                future.result()

            # `None` means the user picked "Auto"; set_table handler will
            # fall back to the sandbox's absolute 500-row ceiling so the
            # agent's own LIMIT clause is honored.
            ctx["table_max_rows"] = table_max_rows
            ctx["persist_table"] = persist_table

        return ctx

    def _report_created_event(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        tool_run_id: str,
        iterations: int,
        payload: str | None,
    ) -> ReportCreatedEvent | None:
        if not payload or turn_id is None:
            return None
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(parsed, dict) or parsed.get("status") != "success":
            return None
        report_id = parsed.get("report_id")
        if not isinstance(report_id, str):
            return None
        return ReportCreatedEvent(
            session_id=session_id,
            turn_id=turn_id,
            tool_run_id=tool_run_id,
            report_id=report_id,
            title=str(parsed.get("title") or ""),
            row_count=int(parsed.get("row_count") or 0),
            iterations=iterations,
        )

    def _table_updated_event(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        tool_run_id: str,
        iterations: int,
        payload: str | None,
    ) -> TableUpdatedEvent | None:
        if not payload or turn_id is None:
            return None
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(parsed, dict) or parsed.get("status") != "success":
            return None
        columns = parsed.get("columns") or []
        if not isinstance(columns, list):
            columns = []
        return TableUpdatedEvent(
            session_id=session_id,
            turn_id=turn_id,
            tool_run_id=tool_run_id,
            row_count=int(parsed.get("row_count") or 0),
            truncated=bool(parsed.get("truncated") or False),
            columns=[str(c) for c in columns],
            iterations=iterations,
        )
