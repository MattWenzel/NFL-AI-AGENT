"""Settings endpoint request and response models."""

from pydantic import BaseModel, Field


class ApiKeyStatus(BaseModel):
    provider: str
    display_name: str
    has_key: bool
    updated_at: str | None = None
    # OAuth-only fields populated for credential_shape="codex_oauth".
    credential_shape: str = "api_key"
    email: str | None = None
    expires_at: int | None = Field(None, description="Epoch ms; OAuth tokens only")


class ApiKeyUpdate(BaseModel):
    api_key: str | None = Field(None, description="Plaintext key to store (null to delete)")


class IdentitySummaryResponse(BaseModel):
    provider: str
    display: str
    linked_at: str
    removable: bool


class LinkGoogleStartResponse(BaseModel):
    auth_url: str
