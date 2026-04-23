"""Application service for auth and account lifecycle."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auth import email as email_sender
from auth.primitives import AuthenticatedUser, generate_token, hash_password, verify_password
from config import (
    AUTH_TOKEN_TTL_DAYS,
    EMAIL_VERIFICATION_REQUIRED,
    LOGIN_LOCKOUT_DURATION_SECONDS,
    LOGIN_LOCKOUT_MAX_FAILURES,
    LOGIN_LOCKOUT_WINDOW_SECONDS,
    REGISTRATION_INVITE_CODE,
    google_oauth_enabled,
)
from server.schemas.auth import (
    AuthStatusResponse,
    AuthTokenResponse,
    AuthUser,
    RegistrationPendingResponse,
)
from storage import RuntimeStore

logger = logging.getLogger(__name__)


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
    """Raised when EMAIL_VERIFICATION_REQUIRED=1 and the account is unverified."""


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _to_auth_user(user: AuthenticatedUser | object) -> AuthUser:
    return AuthUser(id=user.id, email=user.email, role=user.role)


@dataclass(frozen=True)
class AuditContext:
    """Request-scoped audit metadata passed from routes to the service.

    Services stay free of FastAPI imports; routes pull `ip`/`user_agent`
    off `Request` and hand them to the service via this dataclass.
    """
    ip: str | None = None
    user_agent: str | None = None


@dataclass
class IssuedSession:
    token: str
    expires_at: datetime


@dataclass
class RegistrationResult:
    """One of: (a) a fully-issued session for the just-registered user, or
    (b) a "verification pending" signal telling the caller to send a
    verification email and not set session cookies."""
    user: object
    session: IssuedSession | None = None
    verification_token: str | None = None


@dataclass
class AuthService:
    store: RuntimeStore
    exports_dir: Path
    event_logger: logging.Logger = field(default_factory=lambda: logging.getLogger("security_events"))

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
        user = await self._create_user_from_verified_identity(
            email=normalized,
            password_hash=hash_password(password),
            verified=False,
        )
        if EMAIL_VERIFICATION_REQUIRED:
            record = await self.store.create_verification(user_id=user.id, purpose="signup")
            try:
                email_sender.send_verification_email(to=normalized, token=record.token)
            except email_sender.EmailError:
                logger.exception("Verification email send failed for %s", normalized)
            await self._audit("registration_pending", user.id, audit, {"email": normalized})
            return RegistrationResult(user=user, verification_token=record.token)

        session = await self._issue_session(user.id)
        await self._audit("login_success", user.id, audit, {"via": "registration"})
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

        # Progressive delay — each prior failure adds a small sleep that
        # resets on success. Deliberate UX friction that makes password
        # spraying uneconomic without blocking legit users.
        failures = await self.store.get_login_failures(normalized)
        delay = _progressive_delay(failures.failure_count if failures else 0)
        if delay > 0:
            await asyncio.sleep(delay)

        if not verify_password(password, target_hash) or user is None:
            await self._record_login_failure(normalized, audit)
            raise AuthCredentialsError("Invalid email or password")

        if EMAIL_VERIFICATION_REQUIRED and user.email_verified_at is None:
            await self._audit("login_blocked_unverified", user.id, audit, {})
            raise AuthEmailUnverifiedError(
                "Please verify your email before signing in. Check your inbox for the verification link."
            )

        await self.store.clear_login_failures(normalized)
        session = await self._issue_session(user.id)
        await self._audit("login_success", user.id, audit, {})
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
            await self._audit("logout", user_id, audit, {})

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
        await self._audit("password_changed", user.id, audit, {})

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
        # Audit the deletion BEFORE the row is gone so the user_id reference
        # points at a live user in the log; SET NULL on cascade keeps the row
        # afterward with user_id=null for historical context.
        await self._audit("account_deleted", user.id, audit, {"email": record.email})
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
            from storage.models import UserRecord

            await session.execute(
                sql_update(UserRecord)
                .where(UserRecord.id == user_id)
                .values(email_verified_at=now, updated_at=now)
            )
            await session.commit()
        user = await self.store.get_user_by_id(user_id)
        issued = await self._issue_session(user_id)
        await self._audit("email_verified", user_id, audit, {})
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
            await self._audit(
                "verification_resent_ignored", user.id if user else None, audit, {"email": normalized}
            )
            return
        record = await self.store.create_verification(user_id=user.id, purpose="signup")
        try:
            email_sender.send_verification_email(to=normalized, token=record.token)
        except email_sender.EmailError:
            logger.exception("Resend verification failed for %s", normalized)
        await self._audit("verification_resent", user.id, audit, {})

    # ---------------- internals ----------------

    async def _create_user_from_verified_identity(
        self,
        *,
        email: str,
        password_hash: str,
        verified: bool,
    ):
        if await self.store.get_user_by_email(email) is not None:
            raise AuthConflictError("An account with this email already exists.")
        is_first_user = await self.store.count_users() == 0
        role = "admin" if is_first_user else "user"
        verified_at = datetime.now(timezone.utc).isoformat() if verified else None
        user = await self.store.create_user(
            email=email,
            password_hash=password_hash,
            role=role,
            email_verified_at=verified_at,
        )
        # Seed a `password` identity row only for real passwords, not the
        # OAuth sentinel "!". Keeps `count_identities` honest so the unlink
        # guard in Google OAuth service knows whether a password is set.
        if password_hash != "!":
            try:
                await self.store.create_identity(
                    user_id=user.id,
                    provider="password",
                    provider_subject=email,
                    email=email,
                )
            except Exception:  # noqa: BLE001 — non-fatal; identity seeding is advisory
                logger.exception("Failed to seed password identity for user %d", user.id)
        if is_first_user:
            await self.store.backfill_orphan_ownership(user.id)
        return user

    async def _issue_session(self, user_id: int) -> IssuedSession:
        token = generate_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=AUTH_TOKEN_TTL_DAYS)
        await self.store.create_auth_session(
            token=token,
            user_id=user_id,
            expires_at=expires_at.isoformat(),
        )
        return IssuedSession(token=token, expires_at=expires_at)

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
        """Record a failed attempt, roll over the counter if the window
        elapsed, and set lockout when the threshold is hit."""
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
        # Audit regardless of whether the email exists — enumeration timing
        # is already mitigated by the dummy-hash fallback, and the entry lets
        # us spot password-spray attacks across multiple unknown emails.
        user = await self.store.get_user_by_email(email)
        meta = {"email": email, "failure_count": projected_count, "locked": locked_until is not None}
        event_type = "login_locked" if locked_until else "login_failure"
        await self._audit(event_type, user.id if user else None, audit, meta)

    async def _audit(
        self,
        event_type: str,
        user_id: int | None,
        audit: AuditContext,
        metadata: dict,
    ) -> None:
        try:
            await self.store.record_security_event(
                event_type=event_type,
                user_id=user_id,
                ip=audit.ip,
                user_agent=audit.user_agent,
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001 — audit failures shouldn't block the main flow
            logger.exception("Failed to record %s audit event", event_type)
        # Structured log line alongside the DB row for real-time visibility.
        self.event_logger.info(
            "security_event",
            extra={
                "event_type": event_type,
                "user_id": user_id,
                "ip": audit.ip,
                "metadata": metadata,
            },
        )


def _progressive_delay(failure_count: int) -> float:
    """Exponential backoff, capped at 4 seconds.

    0 failures → 0s, 1 → 0.25s, 2 → 0.5s, 3 → 1s, 4 → 2s, 5+ → 4s.
    """
    if failure_count <= 0:
        return 0.0
    return min(2 ** (failure_count - 1) * 0.25, 4.0)
