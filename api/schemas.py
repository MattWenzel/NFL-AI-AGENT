"""Pydantic request and response models for the chat API.

One file for all HTTP contracts so changes to the wire format are
colocated — easier to scan, easier to version, and easier for frontend
authors to find.
"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000, description="User message")
    conversation_id: str | None = Field(None, description="Existing conversation ID (omit to create new)")
    provider: str | None = Field(None, description="LLM provider (anthropic, openai)")
    model: str | None = Field(None, description="Model name override")


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    tool_calls: list[dict] = Field(default_factory=list)
    truncated: bool = Field(False, description="True when the agent hit its iteration limit")


class ConversationInfo(BaseModel):
    id: str
    message_count: int
    title: str
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None


class ConversationTranscriptResponse(BaseModel):
    session_id: str
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None
    turns: list[dict]
    parts: list[dict]
    tool_runs: list[dict]
    summaries: list[dict]


class ProviderResponse(BaseModel):
    name: str
    display_name: str
    models: list[str]
    default_model: str
    available: bool
    context_window: int
    supports_streaming: bool
    supports_tools: bool
