"""Auth process: schemas, errors, DTOs, lifecycle helpers, and service."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from backend.audit import AuditContext, audit_log
from backend.config import (
    AUTH_TOKEN_TTL_DAYS,
    EMAIL_VERIFICATION_REQUIRED,
    LOGIN_LOCKOUT_DURATION_SECONDS,
    LOGIN_LOCKOUT_MAX_FAILURES,
    LOGIN_LOCKOUT_WINDOW_SECONDS,
    REGISTRATION_INVITE_CODE,
    google_oauth_enabled,
)
from backend.persistence import AuditEvent, RuntimeStore
from backend.security import email as email_sender
from backend.security.primitives import generate_token, hash_password, verify_password
from backend.security.types import AuthenticatedUser, PASSWORD

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthServiceError(Exception):
    pass


class AuthConflictError(AuthServiceError):
    pass


class AuthValidationError(AuthServiceError):
    pass


class AuthCredentialsError(AuthServiceError):
    pass


class AuthLockedError(AuthServiceError):
    """Raised when an account is temporarily locked after too many failures."""

    def __init__(self, retry_after_seconds: int, message: str = "Account temporarily locked"):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AuthEmailUnverifiedError(AuthServiceError):
    """Raised when email verification is required and the account is unverified."""


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
    """Returned from /auth/register when EMAIL_VERIFICATION_REQUIRED=1."""

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


@dataclass
class IssuedSession:
    token: str
    expires_at: datetime


@dataclass
class RegistrationResult:
    user: object
    session: IssuedSession | None = None
    verification_token: str | None = None


@dataclass(frozen=True)
class IdentitySeed:
    provider: str
    provider_subject: str
    email: str | None = None
    required: bool = True


async def issue_session(store: RuntimeStore, user_id: int) -> IssuedSession:
    token = generate_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=AUTH_TOKEN_TTL_DAYS)
    await store.create_auth_session(
        token=token,
        user_id=user_id,
        expires_at=expires_at.isoformat(),
    )
    return IssuedSession(token=token, expires_at=expires_at)


async def create_user_account(
    store: RuntimeStore,
    *,
    email: str,
    password_hash: str,
    verified: bool,
    identity: IdentitySeed | None = None,
):
    if await store.get_user_by_email(email) is not None:
        raise AuthConflictError("An account with this email already exists.")
    is_first_user = await store.count_users() == 0
    role = "admin" if is_first_user else "user"
    verified_at = datetime.now(timezone.utc).isoformat() if verified else None
    user = await store.create_user(
        email=email,
        password_hash=password_hash,
        role=role,
        email_verified_at=verified_at,
    )
    if identity is not None:
        try:
            await store.create_identity(
                user_id=user.id,
                provider=identity.provider,
                provider_subject=identity.provider_subject,
                email=identity.email,
            )
        except Exception:
            if identity.required:
                raise
            logger.exception("Failed to seed %s identity for user %d", identity.provider, user.id)
    if is_first_user:
        await store.backfill_orphan_ownership(user.id)
    return user


def _to_auth_user(user: AuthenticatedUser | object) -> AuthUser:
    return AuthUser(id=user.id, email=user.email, role=user.role)


@dataclass
class AuthService:
    store: RuntimeStore
    exports_dir: Path

    def validate_email(self, email: str) -> str:
        normalized = email.strip().lower()
        if not _EMAIL_RE.match(normalized):
            raise AuthValidationError("Invalid email address")
        return normalized

    async def auth_status(self, user: AuthenticatedUser | None) -> AuthStatusResponse:
        return AuthStatusResponse(
            has_users=await self.store.count_users() > 0,
            authenticated=user is not None,
            user=_to_auth_user(user) if user else None,
            invite_required=REGISTRATION_INVITE_CODE is not None,
            verification_required=EMAIL_VERIFICATION_REQUIRED,
            google_oauth_enabled=google_oauth_enabled(),
        )

    async def register(
        self,
        *,
        email: str,
        password: str,
        invite_code: str | None,
        audit: AuditContext = AuditContext(),
    ) -> RegistrationResult:
        if REGISTRATION_INVITE_CODE is not None:
            provided = (invite_code or "").strip()
            if not provided or not secrets.compare_digest(provided, REGISTRATION_INVITE_CODE):
                raise AuthValidationError("Invalid invite code")
        normalized = self.validate_email(email)
        user = await create_user_account(
            self.store,
            email=normalized,
            password_hash=hash_password(password),
            verified=False,
            identity=IdentitySeed(
                provider=PASSWORD,
                provider_subject=normalized,
                email=normalized,
                required=False,
            ),
        )
        if EMAIL_VERIFICATION_REQUIRED:
            record = await self.store.create_verification(user_id=user.id, purpose="signup")
            try:
                email_sender.send_verification_email(to=normalized, token=record.token)
            except email_sender.EmailError:
                logger.exception("Verification email send failed for %s", normalized)
            await audit_log(self.store, AuditEvent.REGISTRATION_PENDING, user.id, audit, {"email": normalized})
            return RegistrationResult(user=user, verification_token=record.token)

        session = await issue_session(self.store, user.id)
        await audit_log(self.store, AuditEvent.LOGIN_SUCCESS, user.id, audit, {"via": "registration"})
        return RegistrationResult(user=user, session=session)

    async def login(
        self,
        *,
        email: str,
        password: str,
        audit: AuditContext = AuditContext(),
    ) -> tuple[object, IssuedSession]:
        normalized = email.strip().lower()

        await self._check_lockout(normalized)

        user = await self.store.get_user_by_email(normalized)
        dummy_hash = "$2b$12$CwTycUXWue0Thq9StjUM0uJ8.zYtCbCpTqiq2CkP.QrTq3QSnGXFm"
        target_hash = user.password_hash if user else dummy_hash

        failures = await self.store.get_login_failures(normalized)
        delay = _progressive_delay(failures.failure_count if failures else 0)
        if delay > 0:
            await asyncio.sleep(delay)

        if not verify_password(password, target_hash) or user is None:
            await self._record_login_failure(normalized, audit)
            raise AuthCredentialsError("Invalid email or password")

        if EMAIL_VERIFICATION_REQUIRED and user.email_verified_at is None:
            await audit_log(self.store, AuditEvent.LOGIN_BLOCKED_UNVERIFIED, user.id, audit, {})
            raise AuthEmailUnverifiedError(
                "Please verify your email before signing in. Check your inbox for the verification link."
            )

        await self.store.clear_login_failures(normalized)
        session = await issue_session(self.store, user.id)
        await audit_log(self.store, AuditEvent.LOGIN_SUCCESS, user.id, audit, {})
        return user, session

    async def logout(
        self,
        bearer_token: str | None,
        *,
        user_id: int | None = None,
        audit: AuditContext = AuditContext(),
    ) -> None:
        if bearer_token:
            await self.store.delete_auth_session(bearer_token)
        if user_id is not None:
            await audit_log(self.store, AuditEvent.LOGOUT, user_id, audit, {})

    async def change_password(
        self,
        *,
        user: AuthenticatedUser,
        current_password: str,
        new_password: str,
        keep_token: str,
        audit: AuditContext = AuditContext(),
    ) -> None:
        record = await self.store.get_user_by_id(user.id)
        if record is None or not verify_password(current_password, record.password_hash):
            raise AuthCredentialsError("Current password is incorrect")
        await self.store.update_user_password(user.id, hash_password(new_password))
        await self.store.invalidate_other_auth_sessions(user.id, keep_token=keep_token)
        await audit_log(self.store, AuditEvent.PASSWORD_CHANGED, user.id, audit, {})

    async def delete_account(
        self,
        *,
        user: AuthenticatedUser,
        password: str,
        audit: AuditContext = AuditContext(),
    ) -> int:
        record = await self.store.get_user_by_id(user.id)
        if record is None or not verify_password(password, record.password_hash):
            raise AuthCredentialsError("Password is incorrect")
        await audit_log(self.store, AuditEvent.ACCOUNT_DELETED, user.id, audit, {"email": record.email})
        filenames = await self.store.delete_user(user.id)
        for filename in filenames:
            try:
                (self.exports_dir / filename).unlink(missing_ok=True)
            except OSError:
                pass
        return len(filenames)

    async def verify_email(
        self,
        token: str,
        *,
        audit: AuditContext = AuditContext(),
    ) -> tuple[object, IssuedSession]:
        user_id = await self.store.consume_verification(token, purpose="signup")
        if user_id is None:
            raise AuthValidationError("Verification link is invalid or has expired. Request a new one.")
        now = datetime.now(timezone.utc).isoformat()
        async with self.store._async_session() as session:
            from sqlalchemy import update as sql_update
            from backend.persistence.models import UserRecord

            await session.execute(
                sql_update(UserRecord)
                .where(UserRecord.id == user_id)
                .values(email_verified_at=now, updated_at=now)
            )
            await session.commit()
        user = await self.store.get_user_by_id(user_id)
        issued = await issue_session(self.store, user_id)
        await audit_log(self.store, AuditEvent.EMAIL_VERIFIED, user_id, audit, {})
        return user, issued

    async def resend_verification(
        self,
        email: str,
        *,
        audit: AuditContext = AuditContext(),
    ) -> None:
        """Always appears to succeed — we don't leak account existence here."""
        normalized = email.strip().lower()
        user = await self.store.get_user_by_email(normalized)
        if user is None or user.email_verified_at is not None:
            await audit_log(self.store, 
                AuditEvent.VERIFICATION_RESENT_IGNORED, user.id if user else None, audit, {"email": normalized}
            )
            return
        record = await self.store.create_verification(user_id=user.id, purpose="signup")
        try:
            email_sender.send_verification_email(to=normalized, token=record.token)
        except email_sender.EmailError:
            logger.exception("Resend verification failed for %s", normalized)
        await audit_log(self.store, AuditEvent.VERIFICATION_RESENT, user.id, audit, {})

    async def _check_lockout(self, email: str) -> None:
        failures = await self.store.get_login_failures(email)
        if failures is None or failures.locked_until is None:
            return
        try:
            locked_until = datetime.fromisoformat(failures.locked_until)
        except ValueError:
            return
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if locked_until > now:
            retry_after = int((locked_until - now).total_seconds()) + 1
            raise AuthLockedError(
                retry_after_seconds=retry_after,
                message=f"Account temporarily locked after {failures.failure_count} failed attempts. "
                f"Try again in {retry_after} seconds.",
            )

    async def _record_login_failure(self, email: str, audit: AuditContext) -> None:
        existing = await self.store.get_login_failures(email)
        now = datetime.now(timezone.utc)
        reset_count = False
        if existing is not None:
            try:
                last = datetime.fromisoformat(existing.last_failure_at)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() > LOGIN_LOCKOUT_WINDOW_SECONDS:
                    reset_count = True
            except ValueError:
                pass
        projected_count = 1 if reset_count or existing is None else existing.failure_count + 1
        locked_until = None
        if projected_count >= LOGIN_LOCKOUT_MAX_FAILURES:
            locked_until = (now + timedelta(seconds=LOGIN_LOCKOUT_DURATION_SECONDS)).isoformat()
        await self.store.record_login_failure(
            email, locked_until=locked_until, reset_count=reset_count
        )
        user = await self.store.get_user_by_email(email)
        meta = {"email": email, "failure_count": projected_count, "locked": locked_until is not None}
        event_type = AuditEvent.LOGIN_LOCKED if locked_until else AuditEvent.LOGIN_FAILURE
        await audit_log(self.store, event_type, user.id if user else None, audit, meta)


def _progressive_delay(failure_count: int) -> float:
    """Exponential backoff, capped at 4 seconds."""
    if failure_count <= 0:
        return 0.0
    return min(2 ** (failure_count - 1) * 0.25, 4.0)
