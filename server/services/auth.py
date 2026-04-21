"""Application service for auth and account lifecycle."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from auth.primitives import AuthenticatedUser, generate_token, hash_password, verify_password
from config import AUTH_TOKEN_TTL_DAYS, REGISTRATION_INVITE_CODE
from storage import RuntimeStore


class AuthServiceError(Exception):
    pass


class AuthConflictError(AuthServiceError):
    pass


class AuthValidationError(AuthServiceError):
    pass


class AuthCredentialsError(AuthServiceError):
    pass


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class AuthUserView:
    id: int
    email: str
    role: str = "user"


@dataclass(frozen=True)
class AuthStatusResult:
    has_users: bool
    authenticated: bool
    user: AuthUserView | None = None
    invite_required: bool = False


@dataclass(frozen=True)
class AuthTokenResult:
    token: str
    user: AuthUserView


def _to_auth_user(user: AuthenticatedUser | object) -> AuthUserView:
    return AuthUserView(id=user.id, email=user.email, role=user.role)


@dataclass
class AuthApplicationService:
    store: RuntimeStore
    exports_dir: Path

    def validate_email(self, email: str) -> str:
        normalized = email.strip().lower()
        if not _EMAIL_RE.match(normalized):
            raise AuthValidationError("Invalid email address")
        return normalized

    async def auth_status(self, user: AuthenticatedUser | None) -> AuthStatusResult:
        return AuthStatusResult(
            has_users=await self.store.count_users_async() > 0,
            authenticated=user is not None,
            user=_to_auth_user(user) if user else None,
            invite_required=REGISTRATION_INVITE_CODE is not None,
        )

    async def register(self, *, email: str, password: str, invite_code: str | None) -> AuthTokenResult:
        if REGISTRATION_INVITE_CODE is not None:
            provided = (invite_code or "").strip()
            if not provided or not secrets.compare_digest(provided, REGISTRATION_INVITE_CODE):
                raise AuthValidationError("Invalid invite code")
        normalized = self.validate_email(email)
        user = await self._create_user_from_verified_identity(
            email=normalized,
            password_hash=hash_password(password),
            verified=False,
        )
        token = await self._issue_session(user.id)
        return AuthTokenResult(token=token, user=_to_auth_user(user))

    async def login(self, *, email: str, password: str) -> AuthTokenResult:
        normalized = email.strip().lower()
        user = await self.store.get_user_by_email_async(normalized)
        dummy_hash = "$2b$12$CwTycUXWue0Thq9StjUM0uJ8.zYtCbCpTqiq2CkP.QrTq3QSnGXFm"
        target_hash = user.password_hash if user else dummy_hash
        if not verify_password(password, target_hash) or user is None:
            raise AuthCredentialsError("Invalid email or password")
        token = await self._issue_session(user.id)
        return AuthTokenResult(token=token, user=_to_auth_user(user))

    async def logout(self, bearer_token: str | None) -> None:
        if bearer_token:
            await self.store.delete_auth_session_async(bearer_token)

    async def change_password(
        self,
        *,
        user: AuthenticatedUser,
        current_password: str,
        new_password: str,
        keep_token: str,
    ) -> None:
        record = await self.store.get_user_by_id_async(user.id)
        if record is None or not verify_password(current_password, record.password_hash):
            raise AuthCredentialsError("Current password is incorrect")
        await self.store.update_user_password_async(user.id, hash_password(new_password))
        await self.store.invalidate_other_auth_sessions_async(user.id, keep_token=keep_token)

    async def delete_account(self, *, user: AuthenticatedUser, password: str) -> int:
        record = await self.store.get_user_by_id_async(user.id)
        if record is None or not verify_password(password, record.password_hash):
            raise AuthCredentialsError("Password is incorrect")
        filenames = await self.store.delete_user_async(user.id)
        for filename in filenames:
            try:
                (self.exports_dir / filename).unlink(missing_ok=True)
            except OSError:
                pass
        return len(filenames)

    async def _create_user_from_verified_identity(
        self,
        *,
        email: str,
        password_hash: str,
        verified: bool,
    ):
        if await self.store.get_user_by_email_async(email) is not None:
            raise AuthConflictError("An account with this email already exists.")
        is_first_user = await self.store.count_users_async() == 0
        role = "admin" if is_first_user else "user"
        verified_at = datetime.now(timezone.utc).isoformat() if verified else None
        user = await self.store.create_user_async(
            email=email,
            password_hash=password_hash,
            role=role,
            email_verified_at=verified_at,
        )
        if is_first_user:
            await self.store.backfill_orphan_ownership_async(user.id)
        return user

    async def _issue_session(self, user_id: int) -> str:
        token = generate_token()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=AUTH_TOKEN_TTL_DAYS)).isoformat()
        await self.store.create_auth_session_async(token=token, user_id=user_id, expires_at=expires_at)
        return token
