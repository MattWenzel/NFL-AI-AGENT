"""Tests for the auth + settings layer.

Covers:
  - `auth/encryption.py` (round-trip, tampered ciphertext, missing key)
  - `auth/primitives.py` primitives (hash/verify/token)
  - `RuntimeStore` CRUD for users / api_keys / auth_sessions
  - `backend/server/routes/auth.py` (open multi-user registration, first user=admin,
    login, logout, status, rate limits)
  - `backend/server/routes/settings.py` (PUT stores ciphertext, GET never leaks plaintext)
  - `backend/server/routes/providers.py` (availability considers user keys)
  - IDOR probes: one user can't see/mutate another user's data
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from backend.domain.auth.primitives import generate_token, hash_password, verify_password
from tests.app_factory import build_test_app, managed_test_client
from backend.api.routes.auth import router as auth_router
from backend.application import auth as auth_service_module
from backend.api.routes.conversations import router as conversations_router
from backend.api.routes.exports import router as csvs_router
from backend.api.routes.providers import router as providers_router
from backend.api.routes.settings import router as settings_router
from backend.domain.auth import encryption
from backend.data import RuntimeStore


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Each test gets a fresh app-owned process state; nothing to reset."""
    yield


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encryption.reset_cache()
    yield
    encryption.reset_cache()


@pytest.fixture(autouse=True)
def _clear_invite_code(monkeypatch):
    """Ensure a stray REGISTRATION_INVITE_CODE in the shell env (from .env
    in local dev) doesn't leak into tests that expect an open gate. Tests
    that exercise the gate re-set the module attribute explicitly."""
    monkeypatch.setattr(auth_service_module, "REGISTRATION_INVITE_CODE", None)


@pytest.fixture(autouse=True)
def _disable_google_oauth(monkeypatch):
    """Local .env commonly has GOOGLE_OAUTH_CLIENT_ID/SECRET set, which flips
    google_oauth_enabled() to True and breaks the auth-status test that
    expects the default-off state. Null the module-level values so the default
    for this file is "OAuth disabled"; tests that exercise the OAuth path
    re-set them explicitly."""
    import backend.config as _config
    monkeypatch.setattr(_config, "GOOGLE_OAUTH_CLIENT_ID", None)
    monkeypatch.setattr(_config, "GOOGLE_OAUTH_CLIENT_SECRET", None)


@pytest.fixture
def store(tmp_path):
    return RuntimeStore(tmp_path / "r.sqlite3")


@pytest.fixture
def client(store):
    """App with auth + settings + providers wired up; LLM provider env vars cleared."""
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    app = build_test_app(runtime_store=store)
    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(providers_router)
    with managed_test_client(app) as client:
        yield client


@pytest.fixture
def auth_headers(client):
    r = client.post("/auth/register", json={"email": "matt@e.com", "password": "hunter22"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------- encryption ----------------

class TestEncryption:
    def test_round_trip(self):
        ct = encryption.encrypt("sk-ant-xyz")
        assert ct != "sk-ant-xyz"
        assert encryption.decrypt(ct) == "sk-ant-xyz"

    def test_tampered_ciphertext_raises_valueerror(self):
        ct = encryption.encrypt("sk-ant-xyz")
        tampered = ct[:-4] + "AAAA"
        with pytest.raises(ValueError):
            encryption.decrypt(tampered)

    def test_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY", raising=False)
        encryption.reset_cache()
        with pytest.raises(encryption.EncryptionKeyMissing):
            encryption.require_configured()

    def test_invalid_env_raises(self, monkeypatch):
        monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", "not-a-fernet-key")
        encryption.reset_cache()
        with pytest.raises(encryption.EncryptionKeyInvalid):
            encryption.require_configured()


# ---------------- auth primitives ----------------

class TestAuthPrimitives:
    def test_hash_verify_round_trip(self):
        h = hash_password("hunter22")
        assert verify_password("hunter22", h)
        assert not verify_password("wrong", h)

    def test_verify_tolerates_malformed_hash(self):
        assert verify_password("anything", "not-a-bcrypt-hash") is False

    def test_hash_is_salted(self):
        # Same password → different hashes.
        assert hash_password("x") != hash_password("x")

    def test_generate_token_is_long_and_unique(self):
        a, b = generate_token(), generate_token()
        assert a != b
        assert len(a) > 40


# ---------------- store CRUD ----------------

class TestStoreCRUD:
    async def test_user_lifecycle(self, store):
        assert await store.count_users() == 0
        u = await store.create_user(email="Matt@Example.com", password_hash="h")
        assert u.email == "matt@example.com"  # normalized
        assert await store.count_users() == 1
        assert (await store.get_user_by_email("MATT@example.com")).id == u.id

    async def test_api_key_upsert_preserves_created_at(self, store):
        u = await store.create_user(email="a@b.com", password_hash="h")
        rec1 = await store.upsert_api_key(user_id=u.id, provider="anthropic", encrypted_key="v1")
        rec2 = await store.upsert_api_key(user_id=u.id, provider="anthropic", encrypted_key="v2")
        assert rec1.created_at == rec2.created_at
        assert rec1.updated_at != rec2.updated_at
        assert (await store.get_api_key(user_id=u.id, provider="anthropic")).encrypted_key == "v2"

    async def test_auth_session_lifecycle(self, store):
        u = await store.create_user(email="a@b.com", password_hash="h")
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await store.create_auth_session(token="tok", user_id=u.id, expires_at=future)
        assert (await store.get_auth_session("tok")).user_id == u.id
        assert await store.delete_auth_session("tok") is True
        assert await store.get_auth_session("tok") is None

    async def test_touch_auth_session_can_skip_recent_write(self, store):
        u = await store.create_user(email="a@b.com", password_hash="h")
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        rec = await store.create_auth_session(token="tok", user_id=u.id, expires_at=future)
        assert await store.touch_auth_session(
            "tok",
            min_interval_seconds=300,
            last_used_at=rec.last_used_at,
        ) is False
        assert (await store.get_auth_session("tok")).last_used_at == rec.last_used_at

    async def test_touch_auth_session_updates_stale_session(self, store):
        u = await store.create_user(email="a@b.com", password_hash="h")
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        rec = await store.create_auth_session(token="tok", user_id=u.id, expires_at=future)
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        from sqlalchemy import update as sa_update
        from backend.data.models import AuthSessionRecord
        async with store._async_session() as session:
            await session.execute(
                sa_update(AuthSessionRecord)
                .where(AuthSessionRecord.token == "tok")
                .values(last_used_at=stale)
            )
            await session.commit()
        assert await store.touch_auth_session(
            "tok",
            min_interval_seconds=300,
            last_used_at=stale,
        ) is True
        assert (await store.get_auth_session("tok")).last_used_at != rec.last_used_at

    async def test_purge_expired_auth_sessions(self, store):
        u = await store.create_user(email="a@b.com", password_hash="h")
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await store.create_auth_session(token="stale", user_id=u.id, expires_at=past)
        await store.create_auth_session(token="fresh", user_id=u.id, expires_at=future)
        assert await store.purge_expired_auth_sessions() == 1
        assert await store.get_auth_session("fresh") is not None
        assert await store.get_auth_session("stale") is None


# ---------------- auth router ----------------

class TestAuthRouter:
    def test_status_no_users(self, client):
        r = client.get("/auth/status")
        assert r.status_code == 200
        assert r.json() == {
            "has_users": False,
            "authenticated": False,
            "user": None,
            "invite_required": False,
            "verification_required": False,
            "google_oauth_enabled": False,
        }

    async def test_first_user_becomes_admin_second_is_user(self, client, store):
        r1 = client.post("/auth/register", json={"email": "a@b.com", "password": "pw12345678"})
        assert r1.status_code == 201, r1.text
        assert r1.json()["user"]["role"] == "admin"
        r2 = client.post("/auth/register", json={"email": "c@d.com", "password": "pw12345678"})
        assert r2.status_code == 201, r2.text
        assert r2.json()["user"]["role"] == "user"
        assert (await store.get_user_by_email("a@b.com")).role == "admin"
        assert (await store.get_user_by_email("c@d.com")).role == "user"

    def test_duplicate_email_rejected_409(self, client):
        r1 = client.post("/auth/register", json={"email": "a@b.com", "password": "pw12345678"})
        assert r1.status_code == 201
        r2 = client.post("/auth/register", json={"email": "A@b.com", "password": "pw12345678"})
        assert r2.status_code == 409  # email normalization catches the re-register

    def test_register_rejects_short_password(self, client):
        r = client.post("/auth/register", json={"email": "a@b.com", "password": "short"})
        assert r.status_code == 422  # Pydantic validation

    def test_register_rejects_bad_email(self, client):
        r = client.post("/auth/register", json={"email": "notanemail", "password": "pw12345678"})
        assert r.status_code == 400

    def test_login_happy_path(self, client):
        client.post("/auth/register", json={"email": "a@b.com", "password": "pw12345678"})
        r = client.post("/auth/login", json={"email": "a@b.com", "password": "pw12345678"})
        assert r.status_code == 200
        assert "token" in r.json()

    def test_login_bad_password_and_unknown_user_same_status(self, client):
        client.post("/auth/register", json={"email": "a@b.com", "password": "pw12345678"})
        r1 = client.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})
        r2 = client.post("/auth/login", json={"email": "x@y.com", "password": "pw12345678"})
        assert r1.status_code == 401
        assert r2.status_code == 401
        # Same message, too — avoids leaking whether the email exists.
        assert r1.json() == r2.json()

    def test_logout_invalidates_token(self, client):
        r = client.post("/auth/register", json={"email": "a@b.com", "password": "pw12345678"})
        tok = r.json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        assert client.get("/auth/status", headers=H).json()["authenticated"] is True
        client.post("/auth/logout", headers=H)
        assert client.get("/auth/status", headers=H).json()["authenticated"] is False


# ---------------- settings router ----------------

class TestSettingsRouter:
    def test_requires_auth(self, client):
        assert client.get("/settings/api-keys").status_code == 401

    async def test_put_stores_encrypted(self, client, auth_headers, store):
        r = client.put(
            "/settings/api-keys/anthropic",
            json={"api_key": "sk-ant-plaintext"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        # Response must not contain the plaintext key.
        assert "sk-ant-plaintext" not in r.text
        # DB holds ciphertext, not plaintext.
        rec = await store.get_api_key(user_id=1, provider="anthropic")
        assert rec.encrypted_key != "sk-ant-plaintext"
        assert encryption.decrypt(rec.encrypted_key) == "sk-ant-plaintext"

    def test_get_never_returns_plaintext(self, client, auth_headers):
        client.put(
            "/settings/api-keys/anthropic",
            json={"api_key": "sk-ant-plaintext-xyz"},
            headers=auth_headers,
        )
        r = client.get("/settings/api-keys", headers=auth_headers)
        assert "sk-ant-plaintext-xyz" not in r.text
        ant = next(item for item in r.json() if item["provider"] == "anthropic")
        assert ant["has_key"] is True

    async def test_put_null_deletes(self, client, auth_headers, store):
        client.put(
            "/settings/api-keys/anthropic",
            json={"api_key": "sk"},
            headers=auth_headers,
        )
        r = client.put(
            "/settings/api-keys/anthropic",
            json={"api_key": None},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["has_key"] is False
        assert await store.user_has_api_key(user_id=1, provider="anthropic") is False

    def test_unknown_provider_404(self, client, auth_headers):
        r = client.put(
            "/settings/api-keys/bogus",
            json={"api_key": "x"},
            headers=auth_headers,
        )
        assert r.status_code == 404


# ---------------- providers router availability ----------------

class TestProvidersAvailability:
    def test_reflects_user_key(self, client, auth_headers):
        # No env var, no user key: all unavailable.
        r = client.get("/chat/providers", headers=auth_headers)
        assert all(p["available"] is False for p in r.json())
        # After storing an anthropic key, anthropic becomes available (user-scoped).
        client.put(
            "/settings/api-keys/anthropic",
            json={"api_key": "sk"},
            headers=auth_headers,
        )
        r = client.get("/chat/providers", headers=auth_headers)
        by_name = {p["name"]: p["available"] for p in r.json()}
        assert by_name["anthropic"] is True


# ---------------- rate limiting ----------------

@pytest.mark.usefixtures("no_login_throttle")
class TestRateLimit:
    def test_register_rate_limit_returns_429(self, client):
        # 5 attempts is the configured ceiling for register.
        for i in range(5):
            r = client.post(
                "/auth/register",
                json={"email": f"user{i}@e.com", "password": "pw12345678"},
            )
            # First succeeds (201), subsequent ones succeed too (open signup).
            # We only care the limiter allows up to 5 before flipping to 429.
            assert r.status_code != 429, f"attempt {i} got 429 prematurely"
        r = client.post(
            "/auth/register",
            json={"email": "user6@e.com", "password": "pw12345678"},
        )
        assert r.status_code == 429
        assert "Retry-After" in r.headers

    def test_login_rate_limit_returns_429(self, client):
        client.post("/auth/register", json={"email": "a@b.com", "password": "pw12345678"})
        # 10 is the login ceiling; burn through with wrong passwords.
        for _ in range(10):
            r = client.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})
            assert r.status_code != 429
        r = client.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})
        assert r.status_code == 429


# ---------------- multi-user isolation ----------------

@pytest.fixture
def two_users(client):
    """Register user A and user B; return (headers_a, headers_b, ids)."""
    a = client.post("/auth/register", json={"email": "a@e.com", "password": "pw12345678"})
    assert a.status_code == 201, a.text
    b = client.post("/auth/register", json={"email": "b@e.com", "password": "pw12345678"})
    assert b.status_code == 201, b.text
    return (
        {"Authorization": f"Bearer {a.json()['token']}"},
        {"Authorization": f"Bearer {b.json()['token']}"},
        {"a": a.json()["user"]["id"], "b": b.json()["user"]["id"]},
    )


class TestMultiUserIsolation:
    async def test_api_keys_scoped_per_user(self, client, two_users, store):
        h_a, h_b, _ = two_users
        client.put("/settings/api-keys/anthropic", json={"api_key": "a-key"}, headers=h_a)
        client.put("/settings/api-keys/anthropic", json={"api_key": "b-key"}, headers=h_b)
        a_list = client.get("/settings/api-keys", headers=h_a).json()
        b_list = client.get("/settings/api-keys", headers=h_b).json()
        assert next(x for x in a_list if x["provider"] == "anthropic")["has_key"] is True
        assert next(x for x in b_list if x["provider"] == "anthropic")["has_key"] is True
        # Both rows exist, distinct ciphertexts.
        a_rec = await store.get_api_key(user_id=1, provider="anthropic")
        b_rec = await store.get_api_key(user_id=2, provider="anthropic")
        assert a_rec.encrypted_key != b_rec.encrypted_key
        assert encryption.decrypt(a_rec.encrypted_key) == "a-key"
        assert encryption.decrypt(b_rec.encrypted_key) == "b-key"

    async def test_store_scoping_for_sessions_and_exports(self, store):
        """Direct-store check (no router): user_id filters block cross-user access."""
        u_a = await store.create_user(email="a@e.com", password_hash="h")
        u_b = await store.create_user(email="b@e.com", password_hash="h")
        s_a = await store.get_or_create_session(provider="anthropic", model="m", user_id=u_a.id)
        assert await store.get_session(s_a.id, user_id=u_a.id) is not None
        assert await store.get_session(s_a.id, user_id=u_b.id) is None, "User B must not see A's session"
        assert await store.delete_session(s_a.id, user_id=u_b.id) is False, "User B must not delete A's session"
        # A's session still alive.
        assert await store.get_session(s_a.id, user_id=u_a.id) is not None

    async def test_scoped_mutators_block_cross_user(self, store):
        """Mutating a session/export via the wrong user_id is a no-op."""
        u_a = await store.create_user(email="a@e.com", password_hash="h")
        u_b = await store.create_user(email="b@e.com", password_hash="h")
        s_a = await store.get_or_create_session(provider="anthropic", model="m", user_id=u_a.id)
        # set_session_pinned scoped to B returns None; session remains unpinned.
        assert await store.set_session_pinned(s_a.id, True, user_id=u_b.id) is None
        assert (await store.get_session(s_a.id, user_id=u_a.id)).pinned_at is None


# ---------------- IDOR via HTTP ----------------

class TestIDOR:
    @pytest.fixture
    def full_client(self, store):
        """App with chat + conversations + csvs routers mounted, not just auth."""
        os.environ.pop("ANTHROPIC_API_KEY", None)
        app = build_test_app(runtime_store=store)
        app.include_router(auth_router)
        app.include_router(settings_router)
        app.include_router(conversations_router)
        app.include_router(csvs_router)
        with managed_test_client(app) as client:
            yield client

    async def test_cross_user_conversation_returns_404(self, full_client, store):
        # User A owns a session in the DB; user B tries to read/delete it.
        u_a = await store.create_user(email="a@e.com", password_hash=hash_password("pw12345678"))
        await store.get_or_create_session(
            session_id="aaa-session", provider="anthropic", model="m", user_id=u_a.id
        )
        r_b = full_client.post("/auth/register", json={"email": "b@e.com", "password": "pw12345678"})
        h_b = {"Authorization": f"Bearer {r_b.json()['token']}"}
        # B can't fetch A's transcript.
        r = full_client.get("/chat/conversations/aaa-session/transcript", headers=h_b)
        assert r.status_code == 404
        # B can't delete A's session.
        r = full_client.delete("/chat/conversations/aaa-session", headers=h_b)
        assert r.status_code == 404
        # A's session still in the DB.
        assert await store.get_session("aaa-session", user_id=u_a.id) is not None

    async def test_cross_user_rename_csv_returns_404(self, full_client, store):
        from backend.data import ExportRecord  # noqa: F401
        u_a = await store.create_user(email="a@e.com", password_hash=hash_password("pw"))
        rec = await store.register_export(
            filename="x.csv", title="A's CSV", sql="SELECT 1",
            row_count=0, columns=[], file_size=0,
            source_session_id=None, source_tool_run_id=None,
        )
        # register_export only assigns user_id if source_session exists; set manually for A.
        from sqlalchemy import update as sa_update
        from backend.data.models import ExportRecord as _ExportRecord
        async with store._async_session() as session:
            await session.execute(
                sa_update(_ExportRecord)
                .where(_ExportRecord.id == rec.id)
                .values(user_id=u_a.id)
            )
            await session.commit()
        r_b = full_client.post("/auth/register", json={"email": "b@e.com", "password": "pw12345678"})
        h_b = {"Authorization": f"Bearer {r_b.json()['token']}"}
        # B tries to rename A's CSV.
        r = full_client.patch(
            f"/chat/exports/{rec.id}",
            json={"title": "hijacked"},
            headers=h_b,
        )
        assert r.status_code == 404
        # Title unchanged in the DB.
        assert (await store.get_export(rec.id, user_id=u_a.id)).title == "A's CSV"


# ---------------- orphan-row helper ----------------

class TestOrphanRows:
    async def test_count_orphan_rows_zero_on_fresh_db(self, store):
        assert await store.count_orphan_rows() == {"sessions": 0, "exports": 0}

    async def test_count_orphan_rows_detects_nulls(self, store):
        # Insert a session without a user_id to simulate pre-auth orphan data.
        from backend.data.models import SessionRecord as _SessionRecord
        now = datetime.now(timezone.utc).isoformat()
        async with store._async_session() as session:
            session.add(_SessionRecord(
                id="orphan-1",
                created_at=now,
                updated_at=now,
                context_window=0,
            ))
            await session.commit()
        assert (await store.count_orphan_rows())["sessions"] == 1

    async def test_ensure_admin_exists_promotes_oldest(self, store):
        u1 = await store.create_user(email="a@e.com", password_hash="h")
        u2 = await store.create_user(email="b@e.com", password_hash="h")
        # Both default to role='user' when created directly via the store.
        assert u1.role == "user" and u2.role == "user"
        promoted = await store.ensure_admin_exists()
        assert promoted == u1.id
        # Idempotent: second call no-ops.
        assert await store.ensure_admin_exists() is None


# ---------------- invite code gate ----------------

class TestInviteCodeGate:
    """Optional REGISTRATION_INVITE_CODE gate on /auth/register."""

    def test_register_without_gate_works(self, client):
        # Default: no gate set, registration is open.
        r = client.post("/auth/register", json={"email": "a@e.com", "password": "pw12345678"})
        assert r.status_code == 201

    def test_status_reflects_gate(self, client, monkeypatch):
        monkeypatch.setattr(auth_service_module, "REGISTRATION_INVITE_CODE", "SECRET-123")
        r = client.get("/auth/status")
        assert r.json()["invite_required"] is True

    def test_register_without_code_rejected_when_gate_set(self, client, monkeypatch):
        monkeypatch.setattr(auth_service_module, "REGISTRATION_INVITE_CODE", "SECRET-123")
        r = client.post("/auth/register", json={"email": "a@e.com", "password": "pw12345678"})
        assert r.status_code == 403

    def test_register_with_wrong_code_rejected(self, client, monkeypatch):
        monkeypatch.setattr(auth_service_module, "REGISTRATION_INVITE_CODE", "SECRET-123")
        r = client.post(
            "/auth/register",
            json={"email": "a@e.com", "password": "pw12345678", "invite_code": "wrong"},
        )
        assert r.status_code == 403

    def test_register_with_correct_code_succeeds(self, client, monkeypatch):
        monkeypatch.setattr(auth_service_module, "REGISTRATION_INVITE_CODE", "SECRET-123")
        r = client.post(
            "/auth/register",
            json={"email": "a@e.com", "password": "pw12345678", "invite_code": "SECRET-123"},
        )
        assert r.status_code == 201, r.text


# ---------------- account management ----------------

class TestAccountManagement:
    def test_change_password_happy_path(self, client):
        r = client.post("/auth/register", json={"email": "a@e.com", "password": "oldpw1234"})
        token = r.json()["token"]
        H = {"Authorization": f"Bearer {token}"}
        # Rotate.
        r = client.put(
            "/auth/password",
            json={"current_password": "oldpw1234", "new_password": "newpw98765"},
            headers=H,
        )
        assert r.status_code == 200, r.text
        # Old password no longer works.
        r = client.post("/auth/login", json={"email": "a@e.com", "password": "oldpw1234"})
        assert r.status_code == 401
        # New password does.
        r = client.post("/auth/login", json={"email": "a@e.com", "password": "newpw98765"})
        assert r.status_code == 200

    async def test_change_password_wrong_current_401(self, client, store):
        r = client.post("/auth/register", json={"email": "a@e.com", "password": "oldpw1234"})
        token = r.json()["token"]
        old_hash = (await store.get_user_by_email("a@e.com")).password_hash
        r = client.put(
            "/auth/password",
            json={"current_password": "wrong-current", "new_password": "newpw98765"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
        # Hash unchanged.
        assert (await store.get_user_by_email("a@e.com")).password_hash == old_hash

    def test_change_password_short_new_422(self, client):
        r = client.post("/auth/register", json={"email": "a@e.com", "password": "oldpw1234"})
        token = r.json()["token"]
        r = client.put(
            "/auth/password",
            json={"current_password": "oldpw1234", "new_password": "short"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

    def test_change_password_invalidates_other_sessions(self, client):
        # Register, then log in a second time to get a second token.
        client.post("/auth/register", json={"email": "a@e.com", "password": "oldpw1234"})
        tok_b = client.post("/auth/login", json={"email": "a@e.com", "password": "oldpw1234"}).json()["token"]
        tok_a = client.post("/auth/login", json={"email": "a@e.com", "password": "oldpw1234"}).json()["token"]
        # Session A initiates the password change.
        r = client.put(
            "/auth/password",
            json={"current_password": "oldpw1234", "new_password": "newpw98765"},
            headers={"Authorization": f"Bearer {tok_a}"},
        )
        assert r.status_code == 200
        # Session A's token still validates.
        assert client.get("/auth/status", headers={"Authorization": f"Bearer {tok_a}"}).json()["authenticated"] is True
        # Session B's token is revoked.
        assert client.get("/auth/status", headers={"Authorization": f"Bearer {tok_b}"}).json()["authenticated"] is False

    async def test_delete_account_happy_path(self, client, store):
        r = client.post("/auth/register", json={"email": "a@e.com", "password": "hunter22"})
        token = r.json()["token"]
        H = {"Authorization": f"Bearer {token}"}
        # Seed some state: an API key + a conversation row so the cascade has work to do.
        client.put("/settings/api-keys/anthropic", json={"api_key": "sk-x"}, headers=H)
        user = await store.get_user_by_email("a@e.com")
        await store.get_or_create_session(provider="anthropic", model="m", user_id=user.id)
        # Delete.
        r = client.request(
            "DELETE",
            "/auth/me",
            json={"password": "hunter22"},
            headers=H,
        )
        assert r.status_code == 200, r.text
        # User row gone, cascades ran.
        assert await store.get_user_by_email("a@e.com") is None
        assert await store.user_has_api_key(user_id=user.id, provider="anthropic") is False
        assert await store.get_auth_session(token) is None
        assert await store.list_sessions(user_id=user.id) == []
        # Status endpoint on the dead token says unauthenticated.
        assert client.get("/auth/status", headers=H).json()["authenticated"] is False

    async def test_delete_account_wrong_password_401(self, client, store):
        r = client.post("/auth/register", json={"email": "a@e.com", "password": "hunter22"})
        token = r.json()["token"]
        r = client.request(
            "DELETE",
            "/auth/me",
            json={"password": "wrong"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
        # User still exists.
        assert await store.get_user_by_email("a@e.com") is not None

    async def test_delete_user_store_cascade(self, store):
        """Direct-store check of the cascade; no HTTP."""
        from datetime import datetime, timedelta, timezone
        u = await store.create_user(email="a@e.com", password_hash="h")
        await store.upsert_api_key(user_id=u.id, provider="anthropic", encrypted_key="ct")
        await store.create_auth_session(
            token="tok-x", user_id=u.id,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        )
        s = await store.get_or_create_session(provider="anthropic", model="m", user_id=u.id)
        # Cascade.
        filenames = await store.delete_user(u.id)
        assert filenames == []
        assert await store.get_user_by_id(u.id) is None
        assert await store.get_api_key(user_id=u.id, provider="anthropic") is None
        assert await store.get_auth_session("tok-x") is None
        assert await store.get_session(s.id, user_id=u.id) is None
