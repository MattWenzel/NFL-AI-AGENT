"""Google OAuth: sign-in, sign-up, identity linking, and unlinking.

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
from dataclasses import dataclass

from backend.domain.auth import google_oauth
from backend.domain.auth.errors import GoogleOAuthError
from backend.domain.auth.identity_resolver import (
    IdentityClaims,
    SignInOutcome,
    resolve_oauth_signin,
)
from backend.domain.auth.types import (
    GOOGLE,
    OAUTH_ONLY_SENTINEL_HASH,
    PASSWORD,
    GoogleIdentity,
)
from backend.config import (
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    google_oauth_enabled,
    google_oauth_redirect_uri,
)
from backend.domain.auth.audit import AuditContext, audit_log
from backend.data import AuditEvent, IdentityConflictError, RuntimeStore
from backend.runtime_state import PendingGoogleOAuthFlows

logger = logging.getLogger(__name__)


# --- errors -----------------------------------------------------------------


class GoogleOAuthServiceError(Exception):
    """Base class for Google OAuth flow failures."""


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


# --- DTOs -------------------------------------------------------------------

# SignInOutcome moved to backend.domain.auth.identity_resolver — re-exported
# above so callers (api/routes/oauth_google.py) keep their import paths.


@dataclass(frozen=True)
class LinkOutcome:
    user_id: int


@dataclass(frozen=True)
class IdentitySummary:
    provider: str
    display: str
    linked_at: str
    removable: bool


# --- service ----------------------------------------------------------------


@dataclass
class GoogleOAuthService:
    store: RuntimeStore
    pending_flows: PendingGoogleOAuthFlows

    def enabled(self) -> bool:
        return google_oauth_enabled()

    async def begin_signin(self, audit: AuditContext) -> str:
        return await self._begin(user_id=None, audit=audit, event_type=AuditEvent.OAUTH_SIGNIN_STARTED)

    async def begin_link(self, *, user_id: int, audit: AuditContext) -> str:
        return await self._begin(user_id=user_id, audit=audit, event_type=AuditEvent.OAUTH_LINK_STARTED)

    async def _begin(self, *, user_id: int | None, audit: AuditContext, event_type: AuditEvent) -> str:
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
        await audit_log(self.store, event_type, user_id, audit, {})
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
            await audit_log(self.store, AuditEvent.OAUTH_SIGNIN_FAILED, None, audit, {"reason": "invalid_state"})
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
        except GoogleOAuthError as exc:
            await audit_log(self.store,
                AuditEvent.OAUTH_SIGNIN_FAILED, pending.user_id, audit, {"reason": "token_exchange", "detail": str(exc)}
            )
            raise GoogleOAuthServiceError("Google sign-in failed — try again.") from exc

        if not identity.email_verified:
            await audit_log(self.store,
                AuditEvent.OAUTH_SIGNIN_FAILED,
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
        identity: GoogleIdentity,
        audit: AuditContext,
    ) -> SignInOutcome:
        """Delegate to the shared OAuth identity resolver.

        Google already verified `identity.email` for us (we checked
        `email_verified` upstream in `complete_callback`), so the
        auto-link-on-email branch in the resolver is safe.
        """
        try:
            return await resolve_oauth_signin(
                self.store,
                IdentityClaims(
                    provider=GOOGLE,
                    subject=identity.sub,
                    email=identity.email,
                ),
                audit=audit,
            )
        except Exception as exc:
            # The resolver already audits OAUTH_SIGNIN_FAILED on the
            # paths that need it. Translate to the service's exception
            # type for the route layer.
            raise GoogleOAuthServiceError("Sign-in failed — try again.") from exc

    async def _complete_link(
        self,
        user_id: int,
        identity: GoogleIdentity,
        audit: AuditContext,
    ) -> LinkOutcome:
        # Already linked to someone? 409 via service error.
        existing_owner = await self.store.get_user_by_identity(
            provider=GOOGLE, provider_subject=identity.sub
        )
        if existing_owner is not None and existing_owner.id != user_id:
            await audit_log(self.store,
                AuditEvent.OAUTH_LINK_REJECTED,
                user_id,
                audit,
                {"provider": GOOGLE, "reason": "already_linked_to_other_user"},
            )
            raise GoogleOAuthLinkConflictError(
                "This Google account is already linked to a different user."
            )
        if existing_owner is not None and existing_owner.id == user_id:
            # No-op (already linked) — still audit so the UX isn't silent.
            await audit_log(self.store,
                AuditEvent.OAUTH_LINKED,
                user_id,
                audit,
                {"provider": GOOGLE, "via": "noop_already_linked"},
            )
            return LinkOutcome(user_id=user_id)

        try:
            await self.store.create_identity(
                user_id=user_id,
                provider=GOOGLE,
                provider_subject=identity.sub,
                email=identity.email,
            )
        except IdentityConflictError as exc:
            await audit_log(self.store,
                AuditEvent.OAUTH_LINK_REJECTED,
                user_id,
                audit,
                {"provider": GOOGLE, "reason": "conflict"},
            )
            raise GoogleOAuthLinkConflictError(str(exc)) from exc
        await audit_log(self.store,
            AuditEvent.OAUTH_LINKED,
            user_id,
            audit,
            {"provider": GOOGLE, "via": "settings"},
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
        only_password_remains = len(remaining) == 1 and remaining[0].provider == PASSWORD
        if only_password_remains:
            user = await self.store.get_user_by_id(user_id)
            if user is None or user.password_hash == OAUTH_ONLY_SENTINEL_HASH:
                raise GoogleOAuthLastIdentityError(
                    "You can't unlink Google — this account has no password set. "
                    "Set a password first, then unlink."
                )
        await self.store.delete_identity(user_id=user_id, provider=provider)
        await audit_log(self.store, AuditEvent.OAUTH_UNLINKED, user_id, audit, {"provider": provider})

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
                if other.provider == PASSWORD:
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
