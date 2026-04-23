"""Auth endpoint shapes: register, login, status, password change, delete account."""

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    id: int
    email: str
    role: str = "user"


class AuthStatusResponse(BaseModel):
    has_users: bool
    authenticated: bool
    user: AuthUser | None = None
    invite_required: bool = False
    verification_required: bool = False
    google_oauth_enabled: bool = False


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


class RegistrationPendingResponse(BaseModel):
    """Returned from /auth/register when EMAIL_VERIFICATION_REQUIRED=1.

    We intentionally do NOT issue a session until the user verifies — no
    token in the body, no cookies set. The client shows a "check your
    email" screen and calls /auth/verify-email when the user clicks the
    link.
    """
    status: str = "verification_pending"
    email: str


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=128)


class ResendVerificationRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)


class DeleteAccountRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)


class AuthOkResponse(BaseModel):
    ok: bool
