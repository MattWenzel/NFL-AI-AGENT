"""Tests for the /auth/codex router state machine without spinning up TestClient."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from agent.oauth.login_server import CallbackResult, LoopbackCaptureError
from agent.oauth.token_store import TokenRecord, TokenStore
from api.routers import auth as auth_router


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


def _fresh_record(email: str = "u@x.com") -> TokenRecord:
    return TokenRecord(
        access_token=_fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "a"}}),
        refresh_token="rt",
        expires_at_ms=int(time.time() * 1000) + 3_600_000,
        id_token=_fake_jwt({"email": email}),
        email=email,
    )


@pytest.fixture
def isolated_store(monkeypatch, tmp_path: Path):
    path = tmp_path / "codex_auth.json"
    import config
    monkeypatch.setattr(config, "CODEX_AUTH_PATH", path)
    auth_router._reset_state_for_tests()
    return path


class TestStatus:
    @pytest.mark.asyncio
    async def test_unauthenticated_when_store_empty(self, isolated_store):
        resp = await auth_router.get_status()
        assert resp.model_dump() == {
            "authenticated": False,
            "email": None,
            "expires_at_ms": None,
            "login_in_progress": False,
            "error": None,
        }

    @pytest.mark.asyncio
    async def test_authenticated_when_record_saved(self, isolated_store):
        TokenStore(isolated_store).save(_fresh_record())
        resp = await auth_router.get_status()
        body = resp.model_dump()
        assert body["authenticated"] is True
        assert body["email"] == "u@x.com"


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_clears_store(self, isolated_store):
        TokenStore(isolated_store).save(_fresh_record())
        resp = await auth_router.logout()
        assert resp.model_dump() == {"status": "logged_out"}
        assert TokenStore(isolated_store).get() is None

    @pytest.mark.asyncio
    async def test_logout_when_not_authenticated_returns_404(self, isolated_store):
        with pytest.raises(HTTPException) as exc:
            await auth_router.logout()
        assert exc.value.status_code == 404


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_returns_authorize_url_and_state(self, monkeypatch, isolated_store):
        async def fake_loopback(*args, **kwargs):
            await asyncio.sleep(60)
            return CallbackResult(code="never", state="never")

        monkeypatch.setattr(auth_router, "run_loopback_capture", fake_loopback)

        resp = await auth_router.start_login()
        assert resp.authorize_url.startswith("https://auth.openai.com/oauth/authorize?")
        assert "state=" in resp.authorize_url
        assert resp.state

        status = await auth_router.get_status()
        assert status.authenticated is False
        assert status.login_in_progress is True

        await auth_router.cancel_active_login()

    @pytest.mark.asyncio
    async def test_successful_callback_persists_token(self, monkeypatch, isolated_store):
        captured_state = {}

        async def fake_loopback(*args, **kwargs):
            for _ in range(50):
                if auth_router._active_login is not None:
                    captured_state["value"] = auth_router._active_login.state
                    return CallbackResult(code="auth-code", state=captured_state["value"])
                await asyncio.sleep(0.01)
            raise LoopbackCaptureError("active login never registered")

        monkeypatch.setattr(auth_router, "run_loopback_capture", fake_loopback)

        def make_token_response(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "access_token": _fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_1"}}),
                "refresh_token": "new-refresh",
                "expires_in": 3600,
                "id_token": _fake_jwt({"email": "logged-in@x.com"}),
                "token_type": "Bearer",
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(make_token_response))

        async def _client_stub(self):
            return mock_client

        from agent.oauth import codex_auth as codex_auth_mod
        monkeypatch.setattr(codex_auth_mod.CodexAuth, "_client", _client_stub)

        await auth_router.start_login()
        for _ in range(200):
            status = await auth_router.get_status()
            if status.authenticated:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail(f"Login did not complete. Last status: {status.model_dump()}")

        assert status.email == "logged-in@x.com"
        assert TokenStore(isolated_store).get().refresh_token == "new-refresh"

    @pytest.mark.asyncio
    async def test_state_mismatch_records_error(self, monkeypatch, isolated_store):
        async def fake_loopback(*args, **kwargs):
            return CallbackResult(code="c", state="WRONG-STATE")

        monkeypatch.setattr(auth_router, "run_loopback_capture", fake_loopback)

        await auth_router.start_login()
        for _ in range(100):
            status = await auth_router.get_status()
            if status.error:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail(f"Expected error in status, got: {status.model_dump()}")

        assert "State mismatch" in status.error
        assert status.authenticated is False

    @pytest.mark.asyncio
    async def test_second_login_cancels_the_first(self, monkeypatch, isolated_store):
        cancelled = []
        started = asyncio.Event()

        async def fake_loopback(*args, **kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return CallbackResult(code="c", state="s")

        monkeypatch.setattr(auth_router, "run_loopback_capture", fake_loopback)

        first = await auth_router.start_login()
        await started.wait()
        second = await auth_router.start_login()
        assert first.state != second.state
        assert len(cancelled) >= 1

        await auth_router.cancel_active_login()

    @pytest.mark.asyncio
    async def test_cancel_active_login_clears_inflight_task(self, monkeypatch, isolated_store):
        cancelled = []
        started = asyncio.Event()

        async def fake_loopback(*args, **kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        monkeypatch.setattr(auth_router, "run_loopback_capture", fake_loopback)

        await auth_router.start_login()
        await started.wait()
        await auth_router.cancel_active_login()

        assert cancelled == [True]
        assert auth_router._active_login is None
