"""HTTP wire-format models split by domain.

Import from the matching submodule (e.g. `from server.schemas.chat import
ChatRequest`). This `__init__.py` re-exports everything for convenience
when a caller wants a flat import surface.
"""

from server.schemas.auth import (
    AuthOkResponse,
    AuthStatusResponse,
    AuthTokenResponse,
    AuthUser,
    DeleteAccountRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
)
from server.schemas.chat import ChatRequest, ChatResponse, ToolCallPreview
from server.schemas.codex_oauth import (
    CodexOAuthStartResponse,
    CodexOAuthStatusResponse,
)
from server.schemas.conversations import (
    ConversationInfo,
    ConversationTranscriptResponse,
    ConversationUpdate,
    SummaryModel,
    ToolRunModel,
    TurnModel,
    TurnPartModel,
)
from server.schemas.exports import (
    ExportDetail,
    ExportInfo,
    ExportUpdate,
    NewSessionFromExportRequest,
    NewSessionFromExportResponse,
)
from server.schemas.providers import ProviderResponse
from server.schemas.settings import ApiKeyStatus, ApiKeyUpdate

__all__ = [
    # auth
    "AuthOkResponse", "AuthStatusResponse", "AuthTokenResponse", "AuthUser",
    "DeleteAccountRequest", "LoginRequest", "PasswordChangeRequest", "RegisterRequest",
    # chat
    "ChatRequest", "ChatResponse", "ToolCallPreview",
    # codex oauth
    "CodexOAuthStartResponse", "CodexOAuthStatusResponse",
    # conversations
    "ConversationInfo", "ConversationTranscriptResponse", "ConversationUpdate",
    "SummaryModel", "ToolRunModel", "TurnModel", "TurnPartModel",
    # exports
    "ExportDetail", "ExportInfo", "ExportUpdate",
    "NewSessionFromExportRequest", "NewSessionFromExportResponse",
    # providers
    "ProviderResponse",
    # settings
    "ApiKeyStatus", "ApiKeyUpdate",
]
