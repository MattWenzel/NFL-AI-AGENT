"""Internal typed results for chat application services."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCallPreviewResult:
    tool: str
    input: dict
    result_preview: str = ""


@dataclass(frozen=True)
class ChatCompletionResult:
    conversation_id: str
    response: str
    tool_calls: list[ToolCallPreviewResult]
    truncated: bool = False
