"""Persisted runtime loop for the NFL stats agent."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

from agent.provider_hints import get_system_prompt
from agent.providers import BaseLLMClient, LLMError, TextEvent, ToolUseEvent, ToolDefinition, Usage
from agent.runtime_store import RuntimeStore, SessionRecord, TurnRecord, safe_load_tool_input
from agent.tools import TOOL_DEFINITIONS, execute_tool_structured

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
RECENT_RAW_TURNS = 6
CHARS_PER_TOKEN = 4
RECENT_RAW_TOOL_RUNS = 12
TITLE_PREVIEW_CHARS = 80
# Rough allowance for the tool-call JSON envelope (tokens) on top of the
# character-based estimate for the inner input payload.
TOOL_CALL_OVERHEAD_TOKENS = 20
COMPACTION_TEXT_PREVIEW_CHARS = 240
COMPACTION_TOOL_INPUT_PREVIEW_CHARS = 160
# Doom-loop detector: if the last N tool invocations (N = DOOM_LOOP_MATCH)
# have identical (name, input), abort. The detector scans the recent tool
# history up to DOOM_LOOP_WINDOW entries deep.
DOOM_LOOP_WINDOW = 6
DOOM_LOOP_MATCH = 3


@dataclass
class RuntimeEvent:
    type: str
    session_id: str
    turn_id: str | None = None
    text: str | None = None
    tool_run_id: str | None = None
    name: str | None = None
    input: dict | None = None
    result: str | None = None
    error: str | None = None
    iterations: int | None = None
    status: str | None = None
    meta: dict | None = None


class RuntimeLoopError(Exception):
    """Raised when the runtime detects an unrecoverable loop condition."""


class ChatRuntime:
    """Shared runtime used by API and CLI."""

    def __init__(self, store: RuntimeStore):
        self.store = store

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
                    compaction_info = self._compact_if_needed(session)
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

                        self._raise_if_doom_loop(session.id, tool_runs)

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
        result = await execute_tool_structured(tool_run.tool_name, tool_input)
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

    def _estimate_active_tokens(self, session_id: str) -> int:
        transcript = self.store.get_transcript(session_id)
        total = 0
        for turn in transcript.turns:
            if turn.compacted:
                continue
            total += self._estimate_turn_tokens(turn)
            for tool_run in transcript.tool_runs_by_turn.get(turn.id, []):
                if not tool_run.compacted and tool_run.result_text:
                    total += max(1, len(tool_run.result_text) // CHARS_PER_TOKEN)
            for part in transcript.parts_by_turn.get(turn.id, []):
                if part.kind == "tool_call":
                    total += max(1, len(part.content) // CHARS_PER_TOKEN) + TOOL_CALL_OVERHEAD_TOKENS
        return total

    @staticmethod
    def _estimate_turn_tokens(turn: TurnRecord) -> int:
        if turn.role == "assistant" and (turn.input_tokens or turn.output_tokens):
            return max(1, turn.input_tokens + turn.output_tokens)
        return max(1, len(turn.text or "") // CHARS_PER_TOKEN)

    def _compact_if_needed(self, session: SessionRecord) -> dict | None:
        if not session.context_window:
            return None
        active_tokens = self._estimate_active_tokens(session.id)
        if active_tokens <= session.context_window:
            return None
        transcript = self.store.get_transcript(session.id)
        active_turns = [t for t in transcript.turns if not t.compacted and t.role in {"user", "assistant"}]
        if len(active_turns) <= RECENT_RAW_TURNS:
            return None
        source_turns = active_turns[:-RECENT_RAW_TURNS]
        if not source_turns:
            return None
        summary_lines = ["Earlier conversation summary:"]
        for turn in source_turns:
            text = (turn.text or "").strip()
            if text:
                preview = text.replace("\n", " ")[:COMPACTION_TEXT_PREVIEW_CHARS]
                summary_lines.append(f"- {turn.role}: {preview}")
            for tool_run in transcript.tool_runs_by_turn.get(turn.id, []):
                input_data = safe_load_tool_input(tool_run.input_json, tool_run_id=tool_run.id)
                summary_lines.append(
                    f"- tool {tool_run.tool_name} ({tool_run.status}): "
                    f"input={json.dumps(input_data, sort_keys=True)[:COMPACTION_TOOL_INPUT_PREVIEW_CHARS]}"
                )
        summary = self.store.record_compaction(
            session.id,
            "\n".join(summary_lines),
            [turn.id for turn in source_turns],
        )
        self._compact_old_tool_runs(session.id)
        return {
            "summary_turn_id": summary.summary_turn_id,
            "source_turn_ids": summary.source_turn_ids,
            "active_tokens_before": active_tokens,
            "context_window": session.context_window,
        }

    def _compact_old_tool_runs(self, session_id: str) -> None:
        transcript = self.store.get_transcript(session_id)
        active_completed = [
            run
            for runs in transcript.tool_runs_by_turn.values()
            for run in runs
            if not run.compacted and run.status == "completed"
        ]
        if len(active_completed) <= RECENT_RAW_TOOL_RUNS:
            return
        stale = active_completed[:-RECENT_RAW_TOOL_RUNS]
        for run in stale:
            self.store.update_tool_run(run.id, compacted=1)

    def _raise_if_doom_loop(self, session_id: str, tool_runs) -> None:
        recent = self.store.get_recent_tool_runs(session_id, limit=DOOM_LOOP_WINDOW)
        fingerprints = [(r.tool_name, r.input_json) for r in reversed(recent)]
        fingerprints.extend((r.tool_name, r.input_json) for r in tool_runs)
        if len(fingerprints) < DOOM_LOOP_MATCH:
            return
        tail = fingerprints[-DOOM_LOOP_MATCH:]
        if all(fp == tail[0] for fp in tail):
            raise RuntimeLoopError(
                f"Detected repeated tool loop on {tail[0][0]} with identical input"
            )


TOOLS = [ToolDefinition.from_dict(d) for d in TOOL_DEFINITIONS]
