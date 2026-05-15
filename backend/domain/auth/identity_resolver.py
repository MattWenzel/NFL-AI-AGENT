"""Shared OAuth identity → user → session resolution.

Both `GoogleOAuthService._complete_signin` and
`CodexOAuthService._resolve_signin` previously re-implemented the same
3-branch decision tree to turn provider claims (sub + email) into a
signed-in user:

  1. Existing identity by (provider, sub) → issue session
  2. Existing user by email → auto-link the identity, issue session
  3. Neither → create user with OAuth sentinel password + identity,
     issue session

The provider-specific halves stay in their services: Codex still stores
the encrypted token bundle in `api_keys` after the session is issued,
and Google still handles authenticated-link, conflict-vs-different-user,
and unlink flows. This module owns the shared identity-resolution
skeleton + the consistent audit event emission across both flows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.data import AuditEvent, IdentityConflictError, RuntimeStore
from backend.domain.auth.audit import AuditContext, audit_log
from backend.domain.auth.lifecycle import (
    IdentitySeed,
    create_user_account,
    issue_session,
)
from backend.domain.auth.types import OAUTH_ONLY_SENTINEL_HASH, IssuedSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdentityClaims:
    """Provider's claim about who is signing in.

    Both Codex (subject decoded from the access-token JWT) and Google
    (subject from the verified ID token) hand back a stable subject id
    and an email. That's the entire input the resolver needs.
    """

    provider: str       # e.g. "openai", "google"
    subject: str        # stable provider-side identifier
    email: str          # caller must lowercase + verify non-empty


@dataclass(frozen=True)
class SignInOutcome:
    """Result of resolving an OAuth sign-in to an app user + session."""

    user_id: int
    user_email: str
    user_role: str
    session: IssuedSession
    is_new_user: bool


async def resolve_oauth_signin(
    store: RuntimeStore,
    claims: IdentityClaims,
    *,
    audit: AuditContext,
) -> SignInOutcome:
    """Resolve `claims` to a signed-in user, creating account/identity as needed.

    Emits the appropriate `OAUTH_SIGNIN_SUCCEEDED` audit event for every
    branch, plus `OAUTH_LINKED` on the auto-link and signup branches.
    Failures (identity conflicts, account creation conflicts) propagate
    to the caller after emitting an `OAUTH_SIGNIN_FAILED` event.
    """
    existing_via_identity = await store.get_user_by_identity(
        provider=claims.provider,
        provider_subject=claims.subject,
    )
    if existing_via_identity is not None:
        session = await issue_session(store, existing_via_identity.id)
        await audit_log(
            store,
            AuditEvent.OAUTH_SIGNIN_SUCCEEDED,
            existing_via_identity.id,
            audit,
            {"provider": claims.provider, "via": "existing_identity"},
        )
        return SignInOutcome(
            user_id=existing_via_identity.id,
            user_email=existing_via_identity.email,
            user_role=existing_via_identity.role,
            session=session,
            is_new_user=False,
        )

    existing_via_email = await store.get_user_by_email(claims.email)
    if existing_via_email is not None:
        try:
            await store.create_identity(
                user_id=existing_via_email.id,
                provider=claims.provider,
                provider_subject=claims.subject,
                email=claims.email,
            )
        except IdentityConflictError:
            # Should be unreachable (we just looked up by sub above and
            # got None), but log + fail rather than crash.
            await audit_log(
                store,
                AuditEvent.OAUTH_SIGNIN_FAILED,
                existing_via_email.id,
                audit,
                {"provider": claims.provider, "reason": "identity_conflict"},
            )
            raise
        session = await issue_session(store, existing_via_email.id)
        await audit_log(
            store,
            AuditEvent.OAUTH_LINKED,
            existing_via_email.id,
            audit,
            {"provider": claims.provider, "via": "auto_link_on_email_match"},
        )
        await audit_log(
            store,
            AuditEvent.OAUTH_SIGNIN_SUCCEEDED,
            existing_via_email.id,
            audit,
            {"provider": claims.provider, "via": "linked"},
        )
        return SignInOutcome(
            user_id=existing_via_email.id,
            user_email=existing_via_email.email,
            user_role=existing_via_email.role,
            session=session,
            is_new_user=False,
        )

    user = await create_user_account(
        store,
        email=claims.email,
        password_hash=OAUTH_ONLY_SENTINEL_HASH,
        verified=True,
        identity=IdentitySeed(
            provider=claims.provider,
            provider_subject=claims.subject,
            email=claims.email,
        ),
    )
    session = await issue_session(store, user.id)
    await audit_log(
        store,
        AuditEvent.OAUTH_LINKED,
        user.id,
        audit,
        {"provider": claims.provider, "via": "signup"},
    )
    await audit_log(
        store,
        AuditEvent.OAUTH_SIGNIN_SUCCEEDED,
        user.id,
        audit,
        {"provider": claims.provider, "via": "signup"},
    )
    return SignInOutcome(
        user_id=user.id,
        user_email=user.email,
        user_role=user.role,
        session=session,
        is_new_user=True,
    )
