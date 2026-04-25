"""Unified turn lifecycle for the chat runtime.

Owns all state belonging to one user message — the per-user-turn
bookkeeping (iteration counter, overflow-retry one-shot flag, doom-loop
fingerprint list), the active assistant iteration's buffers and tool
runs, and the transitions between them.

This module replaces three earlier files (`turn_manager.py`,
`tool_execution.py`, `runtime_policy.py`). The split was dividing one
conceptual state machine across multiple classes with implicit ordering
constraints; consolidating into `Turn` eliminates the fragmentation
without changing observable behavior.

Does NOT own:

- The session-level `asyncio.Lock` — acquired by `ChatRuntime` before
  instantiating the `Turn`, held for its full lifetime.
- The user `TurnRecord` for this message — created by `ChatRuntime`
  before handing off to the `Turn`.
- Compaction logic itself — lives in `agent.compaction`; the `Turn`
  just decides when to call it.
"""

from __future__ import annotations

import asyncio
import json
import logging

from backend.agent.compaction import compact_if_needed
from backend.agent.events import (
    AssistantStartedEvent,
    CompactionStartedEvent,
    RuntimeErrorEvent,
    TextDeltaEvent,
    ToolPendingEvent,
    TurnFinishedEvent,
)
from backend.agent.types import ToolExecutionResult, ToolExecutor
from backend.providers.base import BaseLLMClient
from backend.providers.types import StopReason, ToolChoice, ToolUseEvent, Usage
from backend.persistence import RuntimeStore, SessionRecord, ToolRunRecord, TurnRecord

logger = logging.getLogger(__name__)


class RuntimeLoopError(Exception):
    """Raised when the runtime detects an unrecoverable loop condition."""


TEXT_PERSIST_FLUSH_CHARS = 2048
TITLE_PREVIEW_CHARS = 80

# Doom-loop detector: if the agent calls the same tool with the same
# input this many times in a row within a single user turn, raise so the
# caller can surface a clear error instead of burning the iteration
# budget. Scope is one user turn — `Turn.user_turn_tool_runs` resets when
# the next user message arrives, so repeating a query in a follow-up
# ("try again", "rerun that") is legitimate and won't trip this.
DOOM_LOOP_MATCH = 3


def raise_if_doom_loop(tool_runs: list[ToolRunRecord]) -> None:
    """Raise RuntimeLoopError if the last DOOM_LOOP_MATCH tool runs are identical."""
    if len(tool_runs) < DOOM_LOOP_MATCH:
        return
    # Canonical JSON of input makes the fingerprint insensitive to dict
    # key ordering — two equivalent inputs fingerprint identically.
    # Matches the invariant the storage layer already enforces via
    # ToolInputJSON.
    tail = [
        (r.tool_name, json.dumps(r.input, sort_keys=True))
        for r in tool_runs[-DOOM_LOOP_MATCH:]
    ]
    if all(fp == tail[0] for fp in tail):
        raise RuntimeLoopError(
            f"Detected repeated tool loop on {tail[0][0]} with identical input"
        )


class _TextBuffer:
    """Coalesce streamed assistant text before persisting.

    Flushes at TEXT_PERSIST_FLUSH_CHARS boundaries to bound SQLite write
    frequency during streaming; callers flush explicitly before any
    non-text event or turn completion so text is never lost.
    """

    def __init__(self, store: RuntimeStore, session_id: str, turn_id: str):
        self._store = store
        self._session_id = session_id
        self._turn_id = turn_id
        self._chunks: list[str] = []
        self._char_count = 0

    async def append(self, text: str) -> None:
        if not text:
            return
        self._chunks.append(text)
        self._char_count += len(text)
        if self._char_count >= TEXT_PERSIST_FLUSH_CHARS:
            await self.flush()

    async def flush(self) -> None:
        if not self._chunks:
            return
        text = "".join(self._chunks)
        self._chunks.clear()
        self._char_count = 0
        await self._store.append_assistant_text(
            self._session_id, self._turn_id, text
        )


class Turn:
    """Lifecycle state for one user message + the assistant iterations that resolve it."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        session: SessionRecord,
        execute_tool: ToolExecutor,
        initial_tool_choice: ToolChoice | None = None,
    ):
        self._store = store
        self._session = session
        self._execute_tool = execute_tool

        # Per-user-turn bookkeeping (was RuntimeLoopState)
        self.iterations = 0
        self.force_tool_choice_next_iter: ToolChoice | None = initial_tool_choice
        self.force_overflow_compaction = False
        self.overflow_retry_used = False
        self.user_turn_tool_runs: list[ToolRunRecord] = []

        # Active assistant iteration (was AssistantTurnContext). Reset
        # between iterations so `cleanup_interrupted_assistant_turn`
        # only fires on a genuine mid-iteration abort.
        self._active_assistant_turn: TurnRecord | None = None
        self._active_tool_runs: list[ToolRunRecord] = []
        self._active_text_buffer: _TextBuffer | None = None

    # -------------------- per-user-turn state --------------------

    def begin_iteration(self) -> tuple[int, ToolChoice | None]:
        self.iterations += 1
        iter_tool_choice = self.force_tool_choice_next_iter
        self.force_tool_choice_next_iter = None
        return self.iterations, iter_tool_choice

    async def compact_if_needed(
        self,
        client: BaseLLMClient,
        *,
        provider_name: str,
    ) -> dict | None:
        if self.force_overflow_compaction:
            info = await compact_if_needed(
                self._store,
                self._session,
                client,
                provider_name=provider_name,
                force=True,
                retention_budget_override=self._session.context_window // 4,
            )
            self.force_overflow_compaction = False
            return info
        return await compact_if_needed(
            self._store,
            self._session,
            client,
            provider_name=provider_name,
        )

    def compaction_event(self, compaction_info: dict) -> CompactionStartedEvent:
        return CompactionStartedEvent(
            session_id=self._session.id,
            turn_id=compaction_info["summary_turn_id"],
            iterations=self.iterations,
            meta=compaction_info,
        )

    def record_tool_runs(self, tool_runs: list[ToolRunRecord]) -> None:
        """Extend the user-turn fingerprint list and raise if we've looped."""
        self.user_turn_tool_runs.extend(tool_runs)
        raise_if_doom_loop(self.user_turn_tool_runs)

    def handle_overflow(self) -> bool:
        """Arm overflow compaction. Returns True on the first overflow
        (retry permitted), False on the second (terminal)."""
        if self.overflow_retry_used:
            return False
        self.overflow_retry_used = True
        self.force_overflow_compaction = True
        return True

    def max_iterations_event(self, max_iterations: int) -> RuntimeErrorEvent:
        return RuntimeErrorEvent(
            session_id=self._session.id,
            error=f"Reached maximum tool iterations ({max_iterations})",
            iterations=max_iterations,
        )

    # -------------------- active-iteration accessors --------------------

    @property
    def has_active_assistant_turn(self) -> bool:
        return self._active_assistant_turn is not None

    @property
    def active_assistant_turn_id(self) -> str | None:
        return self._active_assistant_turn.id if self._active_assistant_turn else None

    @property
    def active_tool_runs(self) -> list[ToolRunRecord]:
        return self._active_tool_runs

    # -------------------- assistant iteration lifecycle --------------------

    async def open_assistant_turn(self) -> AssistantStartedEvent:
        """Begin a new assistant iteration. Caller must have completed,
        errored, or reset the previous iteration before opening a new one."""
        assistant_turn = await self._store.create_turn(
            self._session.id, "assistant", status="running"
        )
        self._active_assistant_turn = assistant_turn
        self._active_tool_runs = []
        self._active_text_buffer = _TextBuffer(
            self._store, self._session.id, assistant_turn.id
        )
        return AssistantStartedEvent(
            session_id=self._session.id,
            turn_id=assistant_turn.id,
            iterations=self.iterations,
        )

    async def record_text_delta(self, text: str) -> TextDeltaEvent:
        assert self._active_assistant_turn and self._active_text_buffer, \
            "record_text_delta called without an active assistant turn"
        await self._active_text_buffer.append(text)
        return TextDeltaEvent(
            session_id=self._session.id,
            turn_id=self._active_assistant_turn.id,
            text=text,
            iterations=self.iterations,
        )

    async def record_tool_call(self, event: ToolUseEvent) -> ToolPendingEvent:
        assert self._active_assistant_turn and self._active_text_buffer, \
            "record_tool_call called without an active assistant turn"
        await self._active_text_buffer.flush()
        tool_run = await self._store.create_tool_run(
            self._session.id,
            self._active_assistant_turn.id,
            event.name,
            event.input,
            status="pending",
        )
        await self._store.add_part(
            self._session.id,
            self._active_assistant_turn.id,
            "tool_call",
            json.dumps(event.input, separators=(",", ":"), sort_keys=True),
            name=event.name,
            tool_run_id=tool_run.id,
        )
        self._active_tool_runs.append(tool_run)
        return ToolPendingEvent(
            session_id=self._session.id,
            turn_id=self._active_assistant_turn.id,
            tool_run_id=tool_run.id,
            name=event.name,
            input=event.input,
            iterations=self.iterations,
        )

    async def complete_assistant_turn(
        self,
        *,
        usage: Usage,
        stop_reason: StopReason | None,
    ) -> TurnFinishedEvent | None:
        """Mark the active iteration completed.

        Returns a `turn_finished` event if this closes the user message
        (no tool calls queued), None if the runtime should keep looping,
        and raises RuntimeLoopError if the model hit max_tokens mid-turn
        without emitting any tool call.
        """
        assert self._active_assistant_turn and self._active_text_buffer, \
            "complete_assistant_turn called without an active assistant turn"
        await self._active_text_buffer.flush()
        await self._store.update_turn(
            self._active_assistant_turn.id,
            status="completed",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        if self._active_tool_runs:
            return None
        if stop_reason == StopReason.MAX_TOKENS:
            raise RuntimeLoopError(
                "Response truncated — the model hit its output token limit "
                "mid-turn without emitting a tool call. Ask a more focused "
                "question, or reply 'continue' to resume."
            )
        return TurnFinishedEvent(
            session_id=self._session.id,
            turn_id=self._active_assistant_turn.id,
            iterations=self.iterations,
        )

    async def mark_assistant_turn_error(
        self,
        *,
        error: str,
        usage: Usage,
    ) -> None:
        """Record an error on the active iteration. No-op if no iteration is active."""
        if self._active_assistant_turn is None or self._active_text_buffer is None:
            return
        await self._active_text_buffer.flush()
        await self._store.update_turn(
            self._active_assistant_turn.id,
            status="error",
            error=error,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    async def cleanup_interrupted_assistant_turn(self) -> None:
        """Surface an interrupted iteration. Runtime calls this from its
        finally block when a turn was active at exit. No-op when no
        iteration is active or when the existing turn is already past
        'running' — the status transition is forward-only, so we never
        regress `completed` or `error` back to `interrupted`."""
        if self._active_assistant_turn is None or self._active_text_buffer is None:
            return
        await self._active_text_buffer.flush()
        current = await self._store.get_turn(self._active_assistant_turn.id)
        if current is not None and current.status == "running":
            await self._store.update_turn(
                self._active_assistant_turn.id,
                status="interrupted",
                error="Assistant turn interrupted before completion",
            )
        for tool_run in self._active_tool_runs:
            current_tool_run = await self._store.get_tool_run(tool_run.id)
            if current_tool_run is not None and current_tool_run.status in {"pending", "running"}:
                await self._store.update_tool_run(
                    tool_run.id,
                    status="interrupted",
                    error="Tool execution interrupted before completion",
                )

    def reset_active_iteration(self) -> None:
        """Clear active-iteration state so the finally-block cleanup only
        fires on an abnormal mid-iteration exit. Called by the runtime
        after each iteration completes, errors, or requires follow-up."""
        self._active_assistant_turn = None
        self._active_tool_runs = []
        self._active_text_buffer = None

    # -------------------- tool execution --------------------

    async def execute_tools(self) -> list[ToolExecutionResult]:
        """Run all queued tool calls in parallel via asyncio.gather.

        Results come back in the same order as `active_tool_runs`, so the
        runtime can zip them for event emission.
        """
        assert self._active_assistant_turn is not None, \
            "execute_tools called without an active assistant turn"
        return await asyncio.gather(
            *(
                self._execute_one_tool(tool_run)
                for tool_run in self._active_tool_runs
            )
        )

    async def _execute_one_tool(
        self,
        tool_run: ToolRunRecord,
    ) -> ToolExecutionResult:
        assert self._active_assistant_turn is not None
        assistant_turn_id = self._active_assistant_turn.id
        session_id = self._session.id

        await self._store.update_tool_run(tool_run.id, status="running")
        await self._store.add_part(
            session_id,
            assistant_turn_id,
            "tool_status",
            "running",
            name=tool_run.tool_name,
            tool_run_id=tool_run.id,
        )
        # The handler runs in asyncio.to_thread (a worker thread) but
        # `store.register_export` is an async coroutine. Bridge via
        # run_coroutine_threadsafe so the worker thread actually waits on
        # the registration to complete — without this wrapper the lambda
        # returns a coroutine object that gets silently discarded, the
        # DB row never gets written, and the CSV is orphaned on disk.
        loop = asyncio.get_running_loop()

        def register_export(meta: dict):
            future = asyncio.run_coroutine_threadsafe(
                self._store.register_export(
                    **meta,
                    source_session_id=session_id,
                    source_tool_run_id=tool_run.id,
                ),
                loop,
            )
            return future.result()

        ctx = {"register_export": register_export}
        raw_result = await self._execute_tool(
            tool_run.tool_name, tool_run.input, ctx=ctx
        )
        result = ToolExecutionResult(
            status="completed" if raw_result["status"] == "completed" else "error",
            content=raw_result["content"],
            error=raw_result.get("error"),
            hint=raw_result.get("hint"),
            duration_ms=raw_result.get("duration_ms"),
        )
        await self._store.update_tool_run(
            tool_run.id,
            status=result.status,
            result=result.content,
            error=result.error,
            hint=result.hint,
            duration_ms=result.duration_ms,
        )
        await self._store.add_part(
            session_id,
            assistant_turn_id,
            "tool_result",
            result.content,
            name=tool_run.tool_name,
            tool_run_id=tool_run.id,
        )
        return result
