"""Tests for the `chat_cli.py login` subcommand's login_flow function."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

import chat_cli
from agent.oauth import codex_auth as codex_auth_mod
from agent.oauth import login_server as login_server_mod
from agent.oauth.login_server import CallbackResult, LoopbackCaptureError
from agent.oauth.token_store import TokenStore


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


@pytest.fixture
def isolated_codex_auth_path(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "codex_auth.json"
    import config
    monkeypatch.setattr(config, "CODEX_AUTH_PATH", path)
    monkeypatch.setattr(chat_cli, "CODEX_AUTH_PATH", path)
    return path


@pytest.fixture
def silent_browser(monkeypatch):
    monkeypatch.setattr(chat_cli.webbrowser, "open", lambda url: None)


def _mock_token_exchange(monkeypatch, email: str = "cli@x.com"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "access_token": _fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "a"}}),
            "refresh_token": "rt-cli",
            "expires_in": 3600,
            "id_token": _fake_jwt({"email": email}),
            "token_type": "Bearer",
        })
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _client_stub(self):
        return mock_client

    monkeypatch.setattr(codex_auth_mod.CodexAuth, "_client", _client_stub)


class TestLoginFlow:
    @pytest.mark.asyncio
    async def test_login_succeeds_and_saves_token(
        self, monkeypatch, silent_browser, isolated_codex_auth_path
    ):
        # Stub loopback to return a matching state after the authorize_request builds one.
        captured_state = {}

        async def fake_loopback(*args, **kwargs):
            # The chat_cli.login_flow builds the request BEFORE calling loopback, so
            # the verifier has already been generated. We need to echo whatever state
            # chat_cli generated — but chat_cli doesn't expose it. Use the most recent
            # TokenStore-less path: intercept generate_pkce.
            return CallbackResult(code="auth-code", state=captured_state["state"])

        # Intercept build_authorize_request to capture the state the CLI will expect.
        real_build = codex_auth_mod.CodexAuth.build_authorize_request

        def capturing_build(self):
            req = real_build(self)
            captured_state["state"] = req.state
            return req

        monkeypatch.setattr(codex_auth_mod.CodexAuth, "build_authorize_request", capturing_build)
        monkeypatch.setattr(chat_cli, "run_loopback_capture", fake_loopback)
        _mock_token_exchange(monkeypatch)

        rc = await chat_cli.login_flow("codex")
        assert rc == 0
        record = TokenStore(isolated_codex_auth_path).get()
        assert record is not None
        assert record.email == "cli@x.com"
        assert record.refresh_token == "rt-cli"

    @pytest.mark.asyncio
    async def test_login_rejects_state_mismatch(
        self, monkeypatch, silent_browser, isolated_codex_auth_path
    ):
        async def fake_loopback(*args, **kwargs):
            return CallbackResult(code="c", state="WRONG")

        monkeypatch.setattr(chat_cli, "run_loopback_capture", fake_loopback)
        rc = await chat_cli.login_flow("codex")
        assert rc == 1
        assert TokenStore(isolated_codex_auth_path).get() is None

    @pytest.mark.asyncio
    async def test_login_rejects_non_oauth_provider(
        self, monkeypatch, silent_browser, isolated_codex_auth_path, capsys
    ):
        rc = await chat_cli.login_flow("anthropic")
        assert rc == 1
        out = capsys.readouterr().out
        assert "API key" in out

    @pytest.mark.asyncio
    async def test_login_handles_loopback_timeout(
        self, monkeypatch, silent_browser, isolated_codex_auth_path
    ):
        async def fake_loopback(*args, **kwargs):
            raise LoopbackCaptureError("Timed out")

        monkeypatch.setattr(chat_cli, "run_loopback_capture", fake_loopback)
        rc = await chat_cli.login_flow("codex")
        assert rc == 1
