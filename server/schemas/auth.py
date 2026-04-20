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
