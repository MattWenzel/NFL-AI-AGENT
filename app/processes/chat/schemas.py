"""Chat request/response shapes for POST /chat/message and POST /chat/stream."""

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000, description="User message")
    conversation_id: str | None = Field(None, description="Existing conversation ID (omit to create new)")
    provider: str | None = Field(None, description="LLM provider (anthropic, openai)")
    model: str | None = Field(None, description="Model name override")
    tool_choice: Literal["auto", "required", "none"] | None = Field(
        None,
        description=(
            "Tool-use control for this turn: 'auto' (default — model chooses), "
            "'required' (force a tool call), 'none' (text only). Omit or null "
            "to use the model's default behavior."
        ),
    )


class ToolCallPreview(BaseModel):
    tool: str
    input: dict
    result_preview: str


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    tool_calls: list[ToolCallPreview] = Field(default_factory=list)
    truncated: bool = Field(False, description="True when the agent hit its iteration limit")
