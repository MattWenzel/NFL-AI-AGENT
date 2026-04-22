"""Assistant turn lifecycle management for the runtime loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from agent.events import RuntimeEvent, RuntimeLoopError
from agent.persistence import RuntimePersistence
from provider import StopReason, ToolUseEvent, Usage
from storage import ToolRunRecord, TurnRecord

TEXT_PERSIST_FLUSH_CHARS = 256
TITLE_PREVIEW_CHARS = 80


class AssistantTextBuffer:
    """Coalesce streamed assistant text before persisting it."""

    def __init__(self, persistence: RuntimePersistence, session_id: str, turn_id: str):
        self._persistence = persistence
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
        await self._persistence.append_assistant_text(self._session_id, self._turn_id, text)


@dataclass
class AssistantTurnContext:
    assistant_turn: TurnRecord
    text_buffer: AssistantTextBuffer
    tool_runs: list[ToolRunRecord] = field(default_factory=list)


@dataclass
class AssistantTurnManager:
    persistence: RuntimePersistence

    async def open_turn(self, session_id: str, *, iterations: int) -> tuple[AssistantTurnContext, RuntimeEvent]:
        assistant_turn = await self.persistence.open_assistant_turn(session_id)
        context = AssistantTurnContext(
            assistant_turn=assistant_turn,
            text_buffer=AssistantTextBuffer(self.persistence, session_id, assistant_turn.id),
        )
        event = RuntimeEvent(
            type="assistant_started",
            session_id=session_id,
            turn_id=assistant_turn.id,
            iterations=iterations,
        )
        return context, event

    async def record_text_delta(
        self,
        context: AssistantTurnContext,
        session_id: str,
        text: str,
        *,
        iterations: int,
    ) -> RuntimeEvent:
        await context.text_buffer.append(text)
        return RuntimeEvent(
            type="text_delta",
            session_id=session_id,
            turn_id=context.assistant_turn.id,
            text=text,
            iterations=iterations,
        )

    async def record_tool_call(
        self,
        context: AssistantTurnContext,
        session_id: str,
        event: ToolUseEvent,
        *,
        iterations: int,
    ) -> RuntimeEvent:
        await context.text_buffer.flush()
        tool_run = await self.persistence.record_tool_call(
            session_id,
            context.assistant_turn.id,
            tool_name=event.name,
            input_data=event.input,
            tool_call_json=json.dumps(event.input, separators=(",", ":"), sort_keys=True),
        )
        context.tool_runs.append(tool_run)
        return RuntimeEvent(
            type="tool_pending",
            session_id=session_id,
            turn_id=context.assistant_turn.id,
            tool_run_id=tool_run.id,
            name=event.name,
            input=event.input,
            iterations=iterations,
        )

    async def complete_turn(
        self,
        context: AssistantTurnContext,
        session_id: str,
        *,
        usage: Usage,
        stop_reason: StopReason | None,
        iterations: int,
    ) -> RuntimeEvent | None:
        await context.text_buffer.flush()
        await self.persistence.update_turn(
            context.assistant_turn.id,
            status="completed",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        if context.tool_runs:
            return None
        if stop_reason == StopReason.MAX_TOKENS:
            raise RuntimeLoopError(
                "Response truncated — the model hit its output token limit "
                "mid-turn without emitting a tool call. Ask a more focused "
                "question, or reply 'continue' to resume."
            )
        return RuntimeEvent(
            type="turn_finished",
            session_id=session_id,
            turn_id=context.assistant_turn.id,
            iterations=iterations,
        )

    async def mark_turn_error(
        self,
        context: AssistantTurnContext,
        *,
        error: str,
        usage: Usage,
    ) -> None:
        await context.text_buffer.flush()
        await self.persistence.update_turn(
            context.assistant_turn.id,
            status="error",
            error=error,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    async def cleanup_interrupted(self, context: AssistantTurnContext) -> None:
        await context.text_buffer.flush()
        current = await self.persistence.get_turn(context.assistant_turn.id)
        if current is not None and current.status == "running":
            await self.persistence.interrupt_turn(
                context.assistant_turn.id,
                "Assistant turn interrupted before completion",
            )
        for tool_run in context.tool_runs:
            current_tool_run = await self.persistence.get_tool_run(tool_run.id)
            if current_tool_run is not None and current_tool_run.status in {"pending", "running"}:
                await self.persistence.interrupt_tool_run(
                    tool_run.id,
                    "Tool execution interrupted before completion",
                )
