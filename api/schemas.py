"""Pydantic request and response models for the chat API.

One file for all HTTP contracts so changes to the wire format are
colocated — easier to scan, easier to version, and easier for frontend
authors to find.

The nested transcript models (TurnModel, TurnPartModel, ToolRunModel,
SummaryModel) mirror the shapes hand-built in api/routers/conversations.py
from the dataclasses in infra/persistence/runtime_store.py. They exist so
OpenAPI exposes concrete schemas (not `object`) for codegen and
frontend autocomplete.
"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000, description="User message")
    conversation_id: str | None = Field(None, description="Existing conversation ID (omit to create new)")
    provider: str | None = Field(None, description="LLM provider (anthropic, openai)")
    model: str | None = Field(None, description="Model name override")


class ToolCallPreview(BaseModel):
    tool: str
    input: dict
    result_preview: str


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    tool_calls: list[ToolCallPreview] = Field(default_factory=list)
    truncated: bool = Field(False, description="True when the agent hit its iteration limit")


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


class ExportInfo(BaseModel):
    id: str
    filename: str
    title: str
    row_count: int
    columns: list[str]
    file_size: int
    created_at: str
    updated_at: str
    download_url: str
    source_session_id: str | None = None


class ExportDetail(ExportInfo):
    sql: str
    preview_rows: list[dict]
    preview_truncated: bool


class ExportUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class NewSessionFromExportRequest(BaseModel):
    provider: str | None = Field(None, description="LLM provider for the new session")
    model: str | None = Field(None, description="Model override for the new session")


class NewSessionFromExportResponse(BaseModel):
    conversation_id: str


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


class ProviderResponse(BaseModel):
    name: str
    display_name: str
    models: list[str]
    default_model: str
    available: bool
    context_window: int
    supports_streaming: bool
    supports_tools: bool


# ---------------- auth + settings ----------------

class AuthUser(BaseModel):
    id: int
    email: str
    role: str = "user"


class AuthStatusResponse(BaseModel):
    has_users: bool
    authenticated: bool
    user: AuthUser | None = None
    invite_required: bool = False


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=8, max_length=200)
    invite_code: str | None = Field(None, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)


class AuthTokenResponse(BaseModel):
    token: str
    user: AuthUser


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)


class DeleteAccountRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)


class AuthOkResponse(BaseModel):
    ok: bool


class ApiKeyStatus(BaseModel):
    provider: str
    display_name: str
    has_key: bool
    updated_at: str | None = None
    # OAuth-only fields — populated for providers with credential_shape="codex_oauth".
    # `email` surfaces which ChatGPT account is linked; `credential_shape` lets the
    # Settings UI pick the right input type without hard-coding provider names.
    credential_shape: str = "api_key"
    email: str | None = None
    expires_at: int | None = Field(None, description="Epoch ms; OAuth tokens only")


class ApiKeyUpdate(BaseModel):
    api_key: str | None = Field(None, description="Plaintext key to store (null to delete)")


# ---------------- Codex OAuth device-code flow ----------------

class CodexOAuthStartResponse(BaseModel):
    pending_id: str
    user_code: str
    verification_url: str
    expires_in: int = Field(900, description="Seconds until the user_code expires")


class CodexOAuthStatusResponse(BaseModel):
    status: str = Field(..., description="pending | complete | expired | error")
    email: str | None = None
    error: str | None = None
