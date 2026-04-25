"""Google OAuth end-to-end coverage.

Network and JWKS-verification paths are stubbed — we patch
`auth.google_oauth.exchange_code_for_identity` to return a canned
`GoogleIdentity` and drive the service directly. That keeps the tests
hermetic while still exercising: pending-flow bookkeeping, the state
parameter check, the three branches of `complete_callback` (existing
identity, auto-link on email match, brand-new user), the link flow from
settings, the unlink guard, and the password-sentinel behavior.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

import pytest
from cryptography.fernet import Fernet

from backend.credentials import google_oauth as google_oauth_module
from backend.credentials import encryption
from backend.credentials.primitives import verify_password
from backend.features.auth import service as auth_service_module
from backend.features.auth.service import AuthService
from backend.features.oauth.google.service import (
    GoogleOAuthEmailUnverifiedError,
    GoogleOAuthInvalidStateError,
    GoogleOAuthLastIdentityError,
    GoogleOAuthLinkConflictError,
    GoogleOAuthService,
)
from backend.credentials.audit import AuditContext
from backend.features.oauth.google.types import LinkOutcome, SignInOutcome
from backend.storage import RuntimeStore
from backend.runtime_state import PendingGoogleOAuthFlows


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encryption.reset_cache()
    yield
    encryption.reset_cache()


@pytest.fixture(autouse=True)
def _clear_invite_code(monkeypatch):
    # Tests assume open registration; clear any REGISTRATION_INVITE_CODE
    # leaking in from the dev `.env`.
    monkeypatch.setattr(auth_service_module, "REGISTRATION_INVITE_CODE", None)


@pytest.fixture(autouse=True)
def _google_env(monkeypatch):
    # Patch the service's view of the client id/secret — the service reads
    # via imports from `config`. Patching the module attributes directly
    # keeps tests from depending on process env.
    import backend.config as config
    from backend.features.oauth.google import service as svc_mod

    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(svc_mod, "GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(svc_mod, "GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")

    def _enabled() -> bool:
        return True

    monkeypatch.setattr(svc_mod, "google_oauth_enabled", _enabled)
    yield


@pytest.fixture
def store(tmp_path):
    return RuntimeStore(tmp_path / "r.sqlite3")


@pytest.fixture
def pending_flows():
    return PendingGoogleOAuthFlows()


@pytest.fixture
def svc(store, pending_flows):
    return GoogleOAuthService(store, pending_flows)


@pytest.fixture
def auth_svc(store, tmp_path):
    return AuthService(store, tmp_path / "exports")


def _patch_identity(monkeypatch, *, sub: str, email: str, email_verified: bool = True, name: str = "T"):
    """Stub `exchange_code_for_identity` to return a canned identity."""

    async def _stub(**_kwargs):
        return google_oauth_module.GoogleIdentity(
            sub=sub, email=email, email_verified=email_verified, name=name
        )

    monkeypatch.setattr(google_oauth_module, "exchange_code_for_identity", _stub)


class TestPKCE:
    def test_pair_lengths_and_roundtrip(self):
        v, c = google_oauth_module.pkce_pair()
        assert len(v) == 43
        assert len(c) == 43
        recomputed = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
        assert recomputed == c

    def test_unique_per_call(self):
        a = google_oauth_module.pkce_pair()
        b = google_oauth_module.pkce_pair()
        assert a != b


class TestAuthorizationUrl:
    def test_contains_required_params(self):
        url = google_oauth_module.build_authorization_url(
            client_id="cid",
            redirect_uri="http://x/cb",
            state="S",
            code_challenge="C",
            nonce="N",
        )
        assert "client_id=cid" in url
        assert "redirect_uri=http%3A%2F%2Fx%2Fcb" in url
        assert "state=S" in url
        assert "code_challenge=C" in url
        assert "code_challenge_method=S256" in url
        assert "nonce=N" in url
        assert "scope=openid+email+profile" in url
        assert "response_type=code" in url


class TestBeginFlow:
    async def test_begin_signin_stashes_pending_flow(self, svc, pending_flows):
        audit = AuditContext(ip="1.2.3.4", user_agent="ua")
        url = await svc.begin_signin(audit)
        assert url.startswith(google_oauth_module.GOOGLE_AUTH_URL)
        # Extract state from the URL and look it up in the registry.
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(url).query)
        state = qs["state"][0]
        flow = pending_flows.pop(state)
        assert flow is not None
        assert flow.user_id is None  # sign-in flow
        assert flow.code_verifier
        assert flow.nonce

    async def test_begin_link_tags_user_id(self, svc, pending_flows):
        url = await svc.begin_link(user_id=42, audit=AuditContext())
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(url).query)
        state = qs["state"][0]
        flow = pending_flows.pop(state)
        assert flow is not None
        assert flow.user_id == 42


class TestCompleteCallback:
    async def test_new_user_signup_creates_account_and_issues_session(
        self, svc, store, pending_flows, monkeypatch
    ):
        _patch_identity(monkeypatch, sub="goog-sub-1", email="new@e.com")
        await svc.begin_signin(AuditContext())
        # Pull the most recent state out — fixture doesn't expose state, so
        # we peek via the pop of the only entry.
        state = list(pending_flows._flows.keys())[0]
        outcome = await svc.complete_callback(
            code="abc", state=state, audit=AuditContext(ip="1.1.1.1")
        )
        assert isinstance(outcome, SignInOutcome)
        assert outcome.is_new_user is True
        user = await store.get_user_by_email("new@e.com")
        assert user is not None
        assert user.password_hash == "!"
        assert user.email_verified_at is not None
        identity = await store.get_identity(user_id=user.id, provider="google")
        assert identity is not None
        assert identity.provider_subject == "goog-sub-1"

    async def test_auto_link_on_email_match_for_existing_password_user(
        self, svc, store, auth_svc, pending_flows, monkeypatch
    ):
        # Pre-create a password user.
        await auth_svc.register(
            email="alice@e.com",
            password="pw12345678",
            invite_code=None,
            audit=AuditContext(),
        )
        pre_password_hash = (await store.get_user_by_email("alice@e.com")).password_hash
        _patch_identity(monkeypatch, sub="goog-sub-2", email="alice@e.com")
        await svc.begin_signin(AuditContext())
        state = list(pending_flows._flows.keys())[0]
        outcome = await svc.complete_callback(
            code="abc", state=state, audit=AuditContext()
        )
        assert isinstance(outcome, SignInOutcome)
        assert outcome.is_new_user is False
        user = await store.get_user_by_email("alice@e.com")
        # Password untouched
        assert user.password_hash == pre_password_hash
        # Google identity now present
        ids = await store.list_identities_for_user(user.id)
        providers = {i.provider for i in ids}
        assert providers == {"password", "google"}
        events = await store.list_security_events_for_user(user.id, limit=50)
        assert any(e.event_type == "oauth_linked" for e in events)
        assert any(e.event_type == "oauth_signin_succeeded" for e in events)

    async def test_returning_user_reuses_existing_identity(
        self, svc, store, pending_flows, monkeypatch
    ):
        # First Google sign-in creates account.
        _patch_identity(monkeypatch, sub="goog-sub-3", email="ret@e.com")
        await svc.begin_signin(AuditContext())
        s1 = list(pending_flows._flows.keys())[0]
        await svc.complete_callback(code="c", state=s1, audit=AuditContext())
        count_before = len(await store.list_identities_for_user(
            (await store.get_user_by_email("ret@e.com")).id
        ))
        # Second sign-in with same sub — no new identity row.
        await svc.begin_signin(AuditContext())
        s2 = list(pending_flows._flows.keys())[0]
        await svc.complete_callback(code="c", state=s2, audit=AuditContext())
        count_after = len(await store.list_identities_for_user(
            (await store.get_user_by_email("ret@e.com")).id
        ))
        assert count_before == count_after

    async def test_email_unverified_rejected(
        self, svc, pending_flows, monkeypatch
    ):
        _patch_identity(monkeypatch, sub="x", email="x@e.com", email_verified=False)
        await svc.begin_signin(AuditContext())
        state = list(pending_flows._flows.keys())[0]
        with pytest.raises(GoogleOAuthEmailUnverifiedError):
            await svc.complete_callback(code="c", state=state, audit=AuditContext())

    async def test_state_mismatch_rejected(self, svc, monkeypatch):
        _patch_identity(monkeypatch, sub="x", email="x@e.com")
        with pytest.raises(GoogleOAuthInvalidStateError):
            await svc.complete_callback(code="c", state="not-a-real-state", audit=AuditContext())


class TestLinkFlow:
    async def test_link_flow_from_settings_attaches_to_authenticated_user(
        self, svc, store, auth_svc, pending_flows, monkeypatch
    ):
        await auth_svc.register(
            email="bob@e.com", password="pw12345678", invite_code=None, audit=AuditContext()
        )
        user = await store.get_user_by_email("bob@e.com")
        _patch_identity(monkeypatch, sub="goog-bob-1", email="otheraddr@e.com")
        await svc.begin_link(user_id=user.id, audit=AuditContext())
        state = list(pending_flows._flows.keys())[0]
        outcome = await svc.complete_callback(code="c", state=state, audit=AuditContext())
        assert isinstance(outcome, LinkOutcome)
        ids = await store.list_identities_for_user(user.id)
        assert any(i.provider == "google" and i.provider_subject == "goog-bob-1" for i in ids)

    async def test_link_flow_rejects_google_identity_owned_by_another_user(
        self, svc, store, auth_svc, pending_flows, monkeypatch
    ):
        # User A signs in with Google (claims sub)
        _patch_identity(monkeypatch, sub="shared-sub", email="a@e.com")
        await svc.begin_signin(AuditContext())
        s1 = list(pending_flows._flows.keys())[0]
        await svc.complete_callback(code="c", state=s1, audit=AuditContext())
        # User B, who has a password account, tries to LINK the same Google sub.
        await auth_svc.register(
            email="b@e.com", password="pw12345678", invite_code=None, audit=AuditContext()
        )
        user_b = await store.get_user_by_email("b@e.com")
        _patch_identity(monkeypatch, sub="shared-sub", email="a@e.com")
        await svc.begin_link(user_id=user_b.id, audit=AuditContext())
        s2 = list(pending_flows._flows.keys())[0]
        with pytest.raises(GoogleOAuthLinkConflictError):
            await svc.complete_callback(code="c", state=s2, audit=AuditContext())


class TestUnlink:
    async def test_unlink_succeeds_when_another_identity_remains(
        self, svc, store, auth_svc, pending_flows, monkeypatch
    ):
        await auth_svc.register(
            email="carol@e.com", password="pw12345678", invite_code=None, audit=AuditContext()
        )
        user = await store.get_user_by_email("carol@e.com")
        _patch_identity(monkeypatch, sub="goog-carol", email="carol@e.com")
        await svc.begin_signin(AuditContext())
        state = list(pending_flows._flows.keys())[0]
        await svc.complete_callback(code="c", state=state, audit=AuditContext())
        # Now two identities — unlinking Google should succeed.
        await svc.unlink(user_id=user.id, provider="google", audit=AuditContext())
        ids = await store.list_identities_for_user(user.id)
        assert {i.provider for i in ids} == {"password"}

    async def test_unlink_rejects_when_only_identity_left(
        self, svc, store, pending_flows, monkeypatch
    ):
        # OAuth-only user: password_hash is "!"
        _patch_identity(monkeypatch, sub="lonely-sub", email="lonely@e.com")
        await svc.begin_signin(AuditContext())
        state = list(pending_flows._flows.keys())[0]
        await svc.complete_callback(code="c", state=state, audit=AuditContext())
        user = await store.get_user_by_email("lonely@e.com")
        with pytest.raises(GoogleOAuthLastIdentityError):
            await svc.unlink(user_id=user.id, provider="google", audit=AuditContext())


class TestPasswordSentinel:
    def test_verify_password_rejects_sentinel(self):
        assert verify_password("anything", "!") is False
        assert verify_password("", "!") is False
