"""Application service for Google OAuth sign-in and identity linking.

Owns three flows, all driven by the same `/auth/oauth/google/callback` route:

  1. Sign in with existing Google identity → issue session
  2. Sign up via Google (unknown sub + verified email) → create user with
     password_hash sentinel "!", insert identity, issue session
  3. Link Google to an authenticated user (started from settings) → insert
     identity, keep existing session intact

The password sentinel `"!"` is guaranteed to fail `verify_password` (bcrypt
treats non-bcrypt strings as malformed and the helper returns False), so
password login is naturally blocked for OAuth-only accounts without a
users-table rebuild.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from auth import google_oauth
from config import (
    AUTH_TOKEN_TTL_DAYS,
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    google_oauth_enabled,
    google_oauth_redirect_uri,
)
from server.process_state import PendingGoogleOAuthFlowStore
from server.services.auth import AuditContext, IssuedSession
from storage import IdentityConflictError, RuntimeStore

logger = logging.getLogger(__name__)

GOOGLE_PROVIDER = "google"
PASSWORD_PROVIDER = "password"  # used for the password-identity sentinel
OAUTH_ONLY_SENTINEL_HASH = "!"  # stored in users.password_hash for OAuth-only


class GoogleOAuthServiceError(Exception):
    """Base class for Google OAuth flow failures.

    Route code catches this and redirects to `/?oauth_error=<reason>` so
    the frontend can surface a banner. Messages are safe-to-show to users.
    """


class GoogleOAuthDisabledError(GoogleOAuthServiceError):
    pass


class GoogleOAuthInvalidStateError(GoogleOAuthServiceError):
    pass


class GoogleOAuthEmailUnverifiedError(GoogleOAuthServiceError):
    pass


class GoogleOAuthLinkConflictError(GoogleOAuthServiceError):
    """Raised when the Google identity is already linked to a different user."""


class GoogleOAuthLastIdentityError(GoogleOAuthServiceError):
    """Raised when unlinking would leave the user with no login method."""


@dataclass(frozen=True)
class SignInOutcome:
    user_id: int
    user_email: str
    user_role: str
    session: IssuedSession
    is_new_user: bool


@dataclass(frozen=True)
class LinkOutcome:
    user_id: int


@dataclass(frozen=True)
class IdentitySummary:
    provider: str
    display: str
    linked_at: str
    removable: bool


@dataclass
class GoogleOAuthService:
    store: RuntimeStore
    pending_flows: PendingGoogleOAuthFlowStore
    event_logger: logging.Logger = field(default_factory=lambda: logging.getLogger("security_events"))

    def enabled(self) -> bool:
        return google_oauth_enabled()

    async def begin_signin(self, audit: AuditContext) -> str:
        return await self._begin(user_id=None, audit=audit, event_type="oauth_signin_started")

    async def begin_link(self, *, user_id: int, audit: AuditContext) -> str:
        return await self._begin(user_id=user_id, audit=audit, event_type="oauth_link_started")

    async def _begin(self, *, user_id: int | None, audit: AuditContext, event_type: str) -> str:
        if not self.enabled():
            raise GoogleOAuthDisabledError("Google sign-in is not configured on this deployment.")
        verifier, challenge = google_oauth.pkce_pair()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        self.pending_flows.create(
            state,
            code_verifier=verifier,
            nonce=nonce,
            user_id=user_id,
        )
        await self._audit(event_type, user_id, audit, {})
        return google_oauth.build_authorization_url(
            client_id=GOOGLE_OAUTH_CLIENT_ID or "",
            redirect_uri=google_oauth_redirect_uri(),
            state=state,
            code_challenge=challenge,
            nonce=nonce,
        )

    async def complete_callback(
        self,
        *,
        code: str,
        state: str,
        audit: AuditContext,
    ) -> SignInOutcome | LinkOutcome:
        if not self.enabled():
            raise GoogleOAuthDisabledError("Google sign-in is not configured on this deployment.")
        pending = self.pending_flows.pop(state)
        if pending is None:
            await self._audit("oauth_signin_failed", None, audit, {"reason": "invalid_state"})
            raise GoogleOAuthInvalidStateError("Sign-in state mismatch — try again.")

        try:
            identity = await google_oauth.exchange_code_for_identity(
                code=code,
                code_verifier=pending.code_verifier,
                client_id=GOOGLE_OAUTH_CLIENT_ID or "",
                client_secret=GOOGLE_OAUTH_CLIENT_SECRET or "",
                redirect_uri=google_oauth_redirect_uri(),
                expected_nonce=pending.nonce,
            )
        except google_oauth.GoogleOAuthError as exc:
            await self._audit(
                "oauth_signin_failed", pending.user_id, audit, {"reason": "token_exchange", "detail": str(exc)}
            )
            raise GoogleOAuthServiceError("Google sign-in failed — try again.") from exc

        if not identity.email_verified:
            await self._audit(
                "oauth_signin_failed",
                pending.user_id,
                audit,
                {"reason": "email_unverified", "email": identity.email},
            )
            raise GoogleOAuthEmailUnverifiedError(
                "Your Google account email isn't verified. Verify it with Google first."
            )

        if pending.user_id is not None:
            return await self._complete_link(pending.user_id, identity, audit)
        return await self._complete_signin(identity, audit)

    async def _complete_signin(
        self,
        identity: google_oauth.GoogleIdentity,
        audit: AuditContext,
    ) -> SignInOutcome:
        existing_via_identity = await self.store.get_user_by_identity(
            provider=GOOGLE_PROVIDER, provider_subject=identity.sub
        )
        if existing_via_identity is not None:
            session = await self._issue_session(existing_via_identity.id)
            await self._audit(
                "oauth_signin_succeeded",
                existing_via_identity.id,
                audit,
                {"provider": "google", "via": "existing_identity"},
            )
            return SignInOutcome(
                user_id=existing_via_identity.id,
                user_email=existing_via_identity.email,
                user_role=existing_via_identity.role,
                session=session,
                is_new_user=False,
            )

        # No Google identity → try auto-link on email match (Google already
        # verified the email for us, so we trust it).
        existing_via_email = await self.store.get_user_by_email(identity.email)
        if existing_via_email is not None:
            try:
                await self.store.create_identity(
                    user_id=existing_via_email.id,
                    provider=GOOGLE_PROVIDER,
                    provider_subject=identity.sub,
                    email=identity.email,
                )
            except IdentityConflictError:
                # Should be impossible (we just checked by sub above), but
                # handle it as a generic failure rather than crash.
                await self._audit(
                    "oauth_signin_failed",
                    existing_via_email.id,
                    audit,
                    {"reason": "identity_conflict"},
                )
                raise GoogleOAuthServiceError("Sign-in failed — try again.")
            session = await self._issue_session(existing_via_email.id)
            await self._audit(
                "oauth_linked",
                existing_via_email.id,
                audit,
                {"provider": "google", "via": "auto_link_on_email_match"},
            )
            await self._audit(
                "oauth_signin_succeeded",
                existing_via_email.id,
                audit,
                {"provider": "google", "via": "linked"},
            )
            return SignInOutcome(
                user_id=existing_via_email.id,
                user_email=existing_via_email.email,
                user_role=existing_via_email.role,
                session=session,
                is_new_user=False,
            )

        # Brand new user: create with OAuth sentinel password, seed identity.
        is_first = await self.store.count_users() == 0
        role = "admin" if is_first else "user"
        verified_at = datetime.now(timezone.utc).isoformat()
        user = await self.store.create_user(
            email=identity.email,
            password_hash=OAUTH_ONLY_SENTINEL_HASH,
            role=role,
            email_verified_at=verified_at,
        )
        if is_first:
            await self.store.backfill_orphan_ownership(user.id)
        await self.store.create_identity(
            user_id=user.id,
            provider=GOOGLE_PROVIDER,
            provider_subject=identity.sub,
            email=identity.email,
        )
        session = await self._issue_session(user.id)
        await self._audit(
            "oauth_linked",
            user.id,
            audit,
            {"provider": "google", "via": "signup"},
        )
        await self._audit(
            "oauth_signin_succeeded",
            user.id,
            audit,
            {"provider": "google", "via": "signup"},
        )
        return SignInOutcome(
            user_id=user.id,
            user_email=user.email,
            user_role=user.role,
            session=session,
            is_new_user=True,
        )

    async def _complete_link(
        self,
        user_id: int,
        identity: google_oauth.GoogleIdentity,
        audit: AuditContext,
    ) -> LinkOutcome:
        # Already linked to someone? 409 via service error.
        existing_owner = await self.store.get_user_by_identity(
            provider=GOOGLE_PROVIDER, provider_subject=identity.sub
        )
        if existing_owner is not None and existing_owner.id != user_id:
            await self._audit(
                "oauth_link_rejected",
                user_id,
                audit,
                {"provider": "google", "reason": "already_linked_to_other_user"},
            )
            raise GoogleOAuthLinkConflictError(
                "This Google account is already linked to a different user."
            )
        if existing_owner is not None and existing_owner.id == user_id:
            # No-op (already linked) — still audit so the UX isn't silent.
            await self._audit(
                "oauth_linked",
                user_id,
                audit,
                {"provider": "google", "via": "noop_already_linked"},
            )
            return LinkOutcome(user_id=user_id)

        try:
            await self.store.create_identity(
                user_id=user_id,
                provider=GOOGLE_PROVIDER,
                provider_subject=identity.sub,
                email=identity.email,
            )
        except IdentityConflictError as exc:
            await self._audit(
                "oauth_link_rejected",
                user_id,
                audit,
                {"provider": "google", "reason": "conflict"},
            )
            raise GoogleOAuthLinkConflictError(str(exc)) from exc
        await self._audit(
            "oauth_linked",
            user_id,
            audit,
            {"provider": "google", "via": "settings"},
        )
        return LinkOutcome(user_id=user_id)

    async def unlink(
        self,
        *,
        user_id: int,
        provider: str,
        audit: AuditContext,
    ) -> None:
        identities = await self.store.list_identities_for_user(user_id)
        if not any(i.provider == provider for i in identities):
            # Idempotent — already gone.
            return
        # Guard: at least one other identity must remain, AND if only a
        # password identity is left, that user must actually have a real
        # password (not the "!" sentinel). Otherwise the user would be
        # unable to sign in afterwards.
        remaining = [i for i in identities if i.provider != provider]
        if not remaining:
            raise GoogleOAuthLastIdentityError(
                "You can't unlink your only sign-in method. Add another first."
            )
        only_password_remains = len(remaining) == 1 and remaining[0].provider == PASSWORD_PROVIDER
        if only_password_remains:
            user = await self.store.get_user_by_id(user_id)
            if user is None or user.password_hash == OAUTH_ONLY_SENTINEL_HASH:
                raise GoogleOAuthLastIdentityError(
                    "You can't unlink Google — this account has no password set. "
                    "Set a password first, then unlink."
                )
        await self.store.delete_identity(user_id=user_id, provider=provider)
        await self._audit("oauth_unlinked", user_id, audit, {"provider": provider})

    async def list_identities(self, user_id: int) -> list[IdentitySummary]:
        rows = await self.store.list_identities_for_user(user_id)
        summaries: list[IdentitySummary] = []
        for row in rows:
            display = row.email or row.provider_subject
            removable = len(rows) > 1
            # If only one identity remains and it's the password sentinel,
            # still report removable=False to match the unlink guard.
            if removable and len(rows) == 2:
                # Both identities present; consider whether removing this
                # one would leave only password-sentinel (unusable).
                other = next(r for r in rows if r is not row)
                if other.provider == PASSWORD_PROVIDER:
                    user = await self.store.get_user_by_id(user_id)
                    if user is not None and user.password_hash == OAUTH_ONLY_SENTINEL_HASH:
                        removable = False
            summaries.append(
                IdentitySummary(
                    provider=row.provider,
                    display=display,
                    linked_at=row.created_at,
                    removable=removable,
                )
            )
        return summaries

    # ---------------- internals ----------------

    async def _issue_session(self, user_id: int) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(days=AUTH_TOKEN_TTL_DAYS)
        await self.store.create_auth_session(
            token=token,
            user_id=user_id,
            expires_at=expires_at.isoformat(),
        )
        return IssuedSession(token=token, expires_at=expires_at)

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
        except Exception:  # noqa: BLE001
            logger.exception("Failed to record %s audit event", event_type)
        self.event_logger.info(
            "security_event",
            extra={
                "event_type": event_type,
                "user_id": user_id,
                "ip": audit.ip,
                "metadata": metadata,
            },
        )
