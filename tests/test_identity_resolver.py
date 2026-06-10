"""OAuth identity resolver — auto-link pre-hijack guard.

The dangerous branch is auto-link-on-email-match: the OAuth provider
vouches for the person signing in, not for the existing password account
they'd be linked into. If that account never verified its email, whoever
registered it may not own the address — linking would hand them the OAuth
user's data. The resolver must refuse.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.data import RuntimeStore
from backend.domain.auth.audit import AuditContext
from backend.domain.auth.errors import UnverifiedAccountAutoLinkError
from backend.domain.auth.identity_resolver import IdentityClaims, resolve_oauth_signin


@pytest.fixture
def store(tmp_path):
    return RuntimeStore(tmp_path / "r.sqlite3")


def _claims(email: str = "victim@example.com") -> IdentityClaims:
    return IdentityClaims(provider="google", subject="google-sub-1", email=email)


@pytest.mark.asyncio
async def test_auto_link_onto_unverified_account_is_refused(store):
    # Attacker pre-registers the victim's email; verification never happened.
    attacker = await store.create_user(
        email="victim@example.com", password_hash="h"
    )

    with pytest.raises(UnverifiedAccountAutoLinkError):
        await resolve_oauth_signin(store, _claims(), audit=AuditContext())

    # No identity row may exist — the victim must not land in this account.
    linked = await store.get_user_by_identity(
        provider="google", provider_subject="google-sub-1"
    )
    assert linked is None
    assert attacker.email_verified_at is None


@pytest.mark.asyncio
async def test_auto_link_onto_verified_account_succeeds(store):
    user = await store.create_user(email="victim@example.com", password_hash="h")
    await store.mark_user_email_verified(
        user.id, datetime.now(timezone.utc).isoformat()
    )

    outcome = await resolve_oauth_signin(store, _claims(), audit=AuditContext())

    assert outcome.user_id == user.id
    assert outcome.is_new_user is False
    linked = await store.get_user_by_identity(
        provider="google", provider_subject="google-sub-1"
    )
    assert linked is not None and linked.id == user.id


@pytest.mark.asyncio
async def test_oauth_signup_creates_verified_account_that_can_auto_link_later(store):
    # Fresh OAuth signup (branch 3) marks the email verified...
    outcome = await resolve_oauth_signin(store, _claims(), audit=AuditContext())
    assert outcome.is_new_user is True

    created = await store.get_user_by_email("victim@example.com")
    assert created.email_verified_at is not None

    # ...so a later sign-in from a *different* provider with the same email
    # passes the guard and auto-links instead of being refused.
    second = await resolve_oauth_signin(
        store,
        IdentityClaims(
            provider="openai", subject="oai-sub-9", email="victim@example.com"
        ),
        audit=AuditContext(),
    )
    assert second.user_id == outcome.user_id
    assert second.is_new_user is False
