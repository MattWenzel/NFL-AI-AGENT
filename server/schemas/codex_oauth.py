"""Codex OAuth device-code flow response shapes."""

from pydantic import BaseModel, Field


class CodexOAuthStartResponse(BaseModel):
    pending_id: str
    user_code: str
    verification_url: str
    expires_in: int = Field(900, description="Seconds until the user_code expires")


class CodexOAuthStatusResponse(BaseModel):
    status: str = Field(..., description="pending | complete | expired | error")
    email: str | None = None
    error: str | None = None
