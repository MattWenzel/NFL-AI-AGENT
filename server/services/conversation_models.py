"""Internal typed results for conversation application services."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationSummary:
    id: str
    message_count: int
    title: str
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None
    pinned_at: str | None = None
    source_csv_id: str | None = None


@dataclass(frozen=True)
class ConversationTurn:
    id: str
    role: str
    status: str
    text: str
    compacted: bool
    error: str | None
    input_tokens: int
    output_tokens: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConversationPart:
    id: str
    turn_id: str
    kind: str
    order_index: int
    content: str
    name: str | None
    tool_run_id: str | None
    created_at: str


@dataclass(frozen=True)
class ConversationToolRun:
    id: str
    turn_id: str
    tool_name: str
    status: str
    input: dict
    result: str | None
    error: str | None
    hint: str | None
    duration_ms: int | None
    compacted: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConversationSummaryRecord:
    id: str
    summary_turn_id: str
    source_turn_ids: list[str]
    created_at: str


@dataclass(frozen=True)
class ConversationTranscript:
    session_id: str
    title: str | None
    provider: str | None
    model: str | None
    updated_at: str | None
    turns: list[ConversationTurn]
    parts: list[ConversationPart]
    tool_runs: list[ConversationToolRun]
    summaries: list[ConversationSummaryRecord]
