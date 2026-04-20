"""Settings endpoint shapes: per-user API key status + update."""

from pydantic import BaseModel, Field


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
