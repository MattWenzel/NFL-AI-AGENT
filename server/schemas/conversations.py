"""Conversation list / transcript / update shapes.

The nested transcript models (TurnModel, TurnPartModel, ToolRunModel,
SummaryModel) mirror the dataclasses in `storage/runtime_store.py` so
OpenAPI exposes concrete schemas (not `object`) for codegen and
frontend autocomplete.
"""

from pydantic import BaseModel, Field


class ConversationInfo(BaseModel):
    id: str
    message_count: int
    title: str
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None
    pinned_at: str | None = None
    source_csv_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200, description="New conversation title")
    pinned: bool | None = Field(None, description="Pin or unpin this conversation")


class TurnModel(BaseModel):
    id: str
    role: str
    status: str
    text: str
    compacted: bool
    error: str | None = None
    input_tokens: int
    output_tokens: int
    created_at: str
    updated_at: str


class TurnPartModel(BaseModel):
    id: str
    turn_id: str
    kind: str
    order_index: int
    content: str
    name: str | None = None
    tool_run_id: str | None = None
    created_at: str


class ToolRunModel(BaseModel):
    id: str
    turn_id: str
    tool_name: str
    status: str
    input: dict
    result: str | None = None
    error: str | None = None
    hint: str | None = None
    duration_ms: int | None = None
    compacted: bool
    created_at: str
    updated_at: str


class SummaryModel(BaseModel):
    id: str
    summary_turn_id: str
    source_turn_ids: list[str]
    created_at: str


class ConversationTranscriptResponse(BaseModel):
    session_id: str
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None
    turns: list[TurnModel]
    parts: list[TurnPartModel]
    tool_runs: list[ToolRunModel]
    summaries: list[SummaryModel]
