"""Tests for the Codex (ChatGPT) OAuth provider.

Covers three layers:

- `auth/codex_oauth.py` — JWT decode, expiry computation, bundle
  serialization, device-code polling (happy/retry/timeout), refresh token
  rotation.
- `provider/codex.py` — strict-schema transformation, SSE
  parser, stop-reason derivation, and the `[DONE]`-before-`response.done`
  fallback that ensures emitted tool calls still surface `TOOL_USE`.
- `backend/server/routes/settings.py` — start/status/cancel endpoints, cross-user
  404 guard, rate-limit backstop.

Network is stubbed via `httpx.MockTransport`; the background OAuth task is
replaced with a hanging stub and the module's in-memory pending dict is
poked directly to simulate terminal states. Avoids any real OpenAI call.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from backend.lib.auth.types import AuthenticatedUser
from backend.server.dependencies import get_current_user
from tests.app_factory import build_test_app, managed_test_client
from backend.server.routes.settings import router as settings_router
from backend.lib.auth import codex_oauth, encryption
from backend.lib.auth.errors import CodexOAuthError, DeviceCodeExpired
from backend.lib.auth.types import TokenBundle
from backend.lib.auth.codex_oauth import (
    _compute_expires_at,
    _decode_jwt_payload,
    bundle_from_json,
    bundle_to_json,
    decode_account_id,
    decode_email,
    is_near_expiry,
    poll_device_code,
    refresh_access_token,
)
from backend.services.oauth.codex.service import CodexOAuthService
from backend.lib.db import RuntimeStore
from backend.lib.providers.types import StopReason
from backend.lib.providers.clients.codex import OpenAICodexClient


# ---------------- helpers ----------------


def _make_jwt(payload: dict) -> str:
    """Build a JWT-shaped token string. Signature segment is fake — the
    code under test never verifies signatures."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


def _account_jwt(*, exp: int | None = None) -> str:
    payload: dict = {"https://api.openai.com/auth": {"chatgpt_account_id": "acc-xyz"}}
    if exp is not None:
        payload["exp"] = exp
    return _make_jwt(payload)


# ---------------- JWT decode ----------------


class TestJwtDecode:
    def test_decode_payload_happy(self):
        tok = _make_jwt({"sub": "user-1", "email": "a@b.com"})
        assert _decode_jwt_payload(tok) == {"sub": "user-1", "email": "a@b.com"}

    def test_decode_payload_wrong_segments(self):
        with pytest.raises(CodexOAuthError):
            _decode_jwt_payload("just.two")

    def test_decode_account_id_happy(self):
        assert decode_account_id(_account_jwt()) == "acc-xyz"

    def test_decode_account_id_missing_claim(self):
        tok = _make_jwt({"sub": "user-1"})
        with pytest.raises(CodexOAuthError):
            decode_account_id(tok)

    def test_decode_email_happy(self):
        assert decode_email(_make_jwt({"email": "bob@example.com"})) == "bob@example.com"

    def test_decode_email_missing_returns_none(self):
        assert decode_email(_make_jwt({"sub": "nobody"})) is None

    def test_decode_email_tolerates_garbage(self):
        assert decode_email("not-a-jwt") is None


# ---------------- expiry computation ----------------


class TestComputeExpiresAt:
    def test_prefers_jwt_exp_when_present(self):
        future = int(time.time()) + 3600
        tok = _make_jwt({"exp": future})
        assert _compute_expires_at(tok, 60) == future * 1000

    def test_falls_back_to_expires_in_when_exp_missing(self):
        tok = _make_jwt({"sub": "x"})
        before = int(time.time() * 1000)
        got = _compute_expires_at(tok, 900)
        after = int(time.time() * 1000)
        assert before + 900_000 <= got <= after + 900_000

    def test_default_to_55_minutes_when_both_missing(self):
        tok = _make_jwt({"sub": "x"})
        before = int(time.time() * 1000)
        got = _compute_expires_at(tok, None)
        after = int(time.time() * 1000)
        assert before + 55 * 60_000 <= got <= after + 55 * 60_000


# ---------------- bundle serialization ----------------


class TestBundleSerialization:
    def test_round_trip(self):
        b = TokenBundle(
            access_token="acc", refresh_token="ref", expires_at=123456789, email="a@b.com",
        )
        assert bundle_from_json(bundle_to_json(b)) == b

    def test_round_trip_without_email(self):
        b = TokenBundle(access_token="acc", refresh_token="ref", expires_at=1, email=None)
        assert bundle_from_json(bundle_to_json(b)) == b

    def test_missing_required_field_raises(self):
        with pytest.raises(KeyError):
            bundle_from_json(json.dumps({"access_token": "x"}))


# ---------------- is_near_expiry ----------------


class TestIsNearExpiry:
    def test_far_future_is_fresh(self):
        b = TokenBundle("a", "r", int(time.time() * 1000) + 60 * 60_000)
        assert not is_near_expiry(b)

    def test_inside_skew_is_near(self):
        b = TokenBundle("a", "r", int(time.time() * 1000) + 10_000)
        assert is_near_expiry(b)

    def test_already_expired_is_near(self):
        b = TokenBundle("a", "r", int(time.time() * 1000) - 60_000)
        assert is_near_expiry(b)


# ---------------- poll_device_code ----------------


@pytest.mark.asyncio
class TestPollDeviceCode:
    async def test_happy_path_first_poll(self):
        async def handler(request):
            return httpx.Response(200, json={
                "authorization_code": "AUTH",
                "code_verifier": "VERIFIER",
                "code_challenge": "CHAL",
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
            result = await poll_device_code("dev-1", "CODE", client=cli)
        assert result.authorization_code == "AUTH"
        assert result.code_verifier == "VERIFIER"

    async def test_403_retries_then_succeeds(self):
        calls = {"n": 0}

        async def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(403, text="pending")
            return httpx.Response(200, json={
                "authorization_code": "AUTH",
                "code_verifier": "VERIFIER",
            })

        slept: list = []

        async def fake_sleep(s):
            slept.append(s)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
            result = await poll_device_code(
                "dev-1", "CODE",
                interval_seconds=1,
                client=cli,
                sleep=fake_sleep,
            )
        assert result.authorization_code == "AUTH"
        assert calls["n"] == 2
        assert slept == [1]

    async def test_timeout_raises_device_code_expired(self):
        async def handler(request):
            return httpx.Response(403, text="pending")

        async def fake_sleep(s):
            pass

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
            with pytest.raises(DeviceCodeExpired):
                await poll_device_code(
                    "dev-1", "CODE",
                    interval_seconds=1,
                    max_wait_seconds=0,
                    client=cli,
                    sleep=fake_sleep,
                )

    async def test_non_retry_error_raises(self):
        async def handler(request):
            return httpx.Response(500, text="boom")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
            with pytest.raises(CodexOAuthError):
                await poll_device_code("dev-1", "CODE", client=cli)


# ---------------- refresh_access_token ----------------


@pytest.mark.asyncio
class TestRefreshAccessToken:
    async def test_happy_with_rotation(self):
        async def handler(request):
            return httpx.Response(200, json={
                "access_token": _account_jwt(exp=int(time.time()) + 3600),
                "refresh_token": "NEW_REFRESH",
                "id_token": _make_jwt({"email": "new@e.com"}),
                "expires_in": 3600,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
            bundle = await refresh_access_token("OLD_REFRESH", client=cli)
        assert bundle.refresh_token == "NEW_REFRESH"
        assert bundle.email == "new@e.com"

    async def test_keeps_prior_refresh_token_when_omitted(self):
        """OpenAI may decline to rotate — caller must fall back to the old token."""
        async def handler(request):
            return httpx.Response(200, json={
                "access_token": _account_jwt(),
                "id_token": _make_jwt({"email": "same@e.com"}),
                "expires_in": 3600,
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
            bundle = await refresh_access_token("PRIOR", client=cli)
        assert bundle.refresh_token == "PRIOR"

    async def test_http_error_raises(self):
        async def handler(request):
            return httpx.Response(401, text="invalid_refresh")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cli:
            with pytest.raises(CodexOAuthError):
                await refresh_access_token("BAD", client=cli)


# ---------------- strict schema ----------------


class TestStrictSchema:
    def test_adds_additional_properties_false(self):
        schema = {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        }
        strict = OpenAICodexClient._make_strict_schema(schema)
        assert strict["additionalProperties"] is False

    def test_optional_property_becomes_nullable_and_required(self):
        schema = {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["q"],
        }
        strict = OpenAICodexClient._make_strict_schema(schema)
        assert set(strict["required"]) == {"q", "limit"}
        assert strict["properties"]["limit"]["type"] == ["integer", "null"]
        assert strict["properties"]["q"]["type"] == "string"

    def test_recurses_into_nested_objects(self):
        schema = {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {"team": {"type": "string"}},
                    "required": ["team"],
                },
            },
            "required": ["filter"],
        }
        strict = OpenAICodexClient._make_strict_schema(schema)
        assert strict["properties"]["filter"]["additionalProperties"] is False

    def test_fallback_anyof_for_typeless_optional_property(self):
        schema = {
            "type": "object",
            "properties": {"enum_val": {"enum": ["a", "b"]}},
            "required": [],
        }
        strict = OpenAICodexClient._make_strict_schema(schema)
        assert "anyOf" in strict["properties"]["enum_val"]


# ---------------- derive_stop_reason ----------------


class TestDeriveStopReason:
    def test_empty_calls_is_end_turn(self):
        assert OpenAICodexClient._derive_stop_reason({}) == StopReason.END_TURN

    def test_emitted_call_is_tool_use(self):
        calls = {"c1": {"name": "x", "arguments": "{}", "emitted": True}}
        assert OpenAICodexClient._derive_stop_reason(calls) == StopReason.TOOL_USE

    def test_unemitted_call_is_end_turn(self):
        calls = {"c1": {"name": "x", "arguments": "{}", "emitted": False}}
        assert OpenAICodexClient._derive_stop_reason(calls) == StopReason.END_TURN


# ---------------- _iter_sse ----------------


class _FakeStreamResponse:
    """Minimal async-iterator response emulating httpx.Response for SSE."""

    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def aiter_lines(self):
        async def gen():
            for line in self._lines:
                yield line

        return gen()


@pytest.mark.asyncio
class TestIterSse:
    async def test_yields_events_and_stops_on_done(self):
        resp = _FakeStreamResponse([
            'data: {"type":"a"}',
            "",
            'data: {"type":"b"}',
            "",
            "data: [DONE]",
            "",
            'data: {"type":"never"}',
            "",
        ])
        collected = [e async for e in OpenAICodexClient._iter_sse(resp)]
        assert collected == [{"type": "a"}, {"type": "b"}]

    async def test_ignores_comment_lines(self):
        resp = _FakeStreamResponse([": ping", 'data: {"type":"a"}', ""])
        collected = [e async for e in OpenAICodexClient._iter_sse(resp)]
        assert collected == [{"type": "a"}]


# ---------------- _run_stream fallback (NIT 3) ----------------


class _StreamContextManager:
    """Minimal async context manager that yields the given response."""

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_run_stream_derives_tool_use_when_done_precedes_response_done():
    """NIT 3: Codex may terminate on `[DONE]` without emitting
    `response.done`. If tool calls were emitted, stop_reason must still
    surface as TOOL_USE so the runtime loops back to execute the tool.
    """
    events = [
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "id": "i1", "call_id": "c1", "name": "execute_sql"},
        },
        {
            "type": "response.function_call_arguments.done",
            "call_id": "c1",
            "arguments": '{"q":"SELECT 1"}',
        },
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "i1",
                "call_id": "c1",
                "name": "execute_sql",
                "arguments": '{"q":"SELECT 1"}',
            },
        },
    ]
    lines: list[str] = []
    for e in events:
        lines.append(f"data: {json.dumps(e)}")
        lines.append("")
    # Stream terminates on [DONE] WITHOUT any response.done/response.completed.
    lines.append("data: [DONE]")
    lines.append("")

    resp = _FakeStreamResponse(lines)

    client = OpenAICodexClient(
        model="gpt-5.3-codex",
        api_key=_account_jwt(exp=int(time.time()) + 3600),
    )
    try:
        client._http.stream = lambda *a, **kw: _StreamContextManager(resp)
        collected = [
            ev async for ev in client._run_stream(messages=[], tools=None, system=None)
        ]
    finally:
        await client.aclose()

    # Exactly one ToolUseEvent (emitted on function_call_arguments.done).
    assert len(collected) == 1
    assert collected[0].name == "execute_sql"
    # Fallback: stop_reason is TOOL_USE, not END_TURN.
    assert client.last_stop_reason == StopReason.TOOL_USE


@pytest.mark.asyncio
async def test_run_stream_reports_end_turn_when_no_tools_emitted():
    """Baseline: pure-text stream ending on [DONE] with no tool calls → END_TURN."""
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        "",
        "data: [DONE]",
        "",
    ]
    resp = _FakeStreamResponse(lines)
    client = OpenAICodexClient(
        model="gpt-5.3-codex",
        api_key=_account_jwt(exp=int(time.time()) + 3600),
    )
    try:
        client._http.stream = lambda *a, **kw: _StreamContextManager(resp)
        text_chunks = []
        async for ev in client._run_stream(messages=[], tools=None, system=None):
            if hasattr(ev, "text"):
                text_chunks.append(ev.text)
    finally:
        await client.aclose()

    assert "".join(text_chunks) == "hi"
    assert client.last_stop_reason == StopReason.END_TURN


# ---------------- /settings/oauth/codex router ----------------


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encryption.reset_cache()
    yield
    encryption.reset_cache()


@pytest.fixture(autouse=True)
def _reset_codex_module_state():
    yield


@pytest.fixture
def store(tmp_path):
    return RuntimeStore(tmp_path / "r.sqlite3")


def _make_app(store: RuntimeStore, user_id: int, email: str):
    app = build_test_app(runtime_store=store)
    app.include_router(settings_router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=user_id, email=email)
    return app


def _patch_device_flow(monkeypatch, user_code="USER-CODE"):
    """Stub out the OpenAI device-code call and the background poller."""
    from backend.lib.auth.types import DeviceCodeStart
    start = DeviceCodeStart(
        device_auth_id="dev-1",
        user_code=user_code,
        interval=5,
        verification_url="https://auth.openai.com/codex/device",
    )

    async def fake_request(*args, **kwargs):
        return start

    async def fake_run_flow(self, pending_id):
        # Hang forever so tests can drive terminal transitions by hand.
        await asyncio.sleep(300)

    monkeypatch.setattr(codex_oauth, "request_device_code", fake_request)
    monkeypatch.setattr(CodexOAuthService, "run_device_flow", fake_run_flow)


class TestCodexRouter:
    async def test_start_returns_user_code_and_pending_id(self, store, monkeypatch):
        user = await store.create_user(email="a@e.com", password_hash="h")
        _patch_device_flow(monkeypatch)
        with managed_test_client(_make_app(store, user.id, user.email)) as client:
            r = client.post("/settings/oauth/codex/start")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["user_code"] == "USER-CODE"
            assert body["verification_url"] == "https://auth.openai.com/codex/device"
            assert body["expires_in"] == 15 * 60
            assert client.app.state.process_state.codex_pending_flows.get(body["pending_id"]) is not None

    async def test_status_reports_pending_then_terminal_and_evicts(self, store, monkeypatch):
        user = await store.create_user(email="a@e.com", password_hash="h")
        _patch_device_flow(monkeypatch)
        with managed_test_client(_make_app(store, user.id, user.email)) as client:
            r = client.post("/settings/oauth/codex/start")
            pending_id = r.json()["pending_id"]

            r1 = client.get(f"/settings/oauth/codex/status?pending_id={pending_id}")
            assert r1.status_code == 200
            assert r1.json()["status"] == "pending"

            client.app.state.process_state.codex_pending_flows.get(pending_id).status = "complete"
            client.app.state.process_state.codex_pending_flows.get(pending_id).email = "user@e.com"

            r2 = client.get(f"/settings/oauth/codex/status?pending_id={pending_id}")
            assert r2.status_code == 200
            assert r2.json()["status"] == "complete"
            assert r2.json()["email"] == "user@e.com"
            assert client.app.state.process_state.codex_pending_flows.get(pending_id) is None

            r3 = client.get(f"/settings/oauth/codex/status?pending_id={pending_id}")
            assert r3.status_code == 404

    async def test_status_cross_user_returns_404(self, store, monkeypatch):
        u1 = await store.create_user(email="a@e.com", password_hash="h")
        u2 = await store.create_user(email="b@e.com", password_hash="h")
        _patch_device_flow(monkeypatch)

        with managed_test_client(_make_app(store, u1.id, u1.email)) as c1:
            pending_id = c1.post("/settings/oauth/codex/start").json()["pending_id"]
            with managed_test_client(_make_app(store, u2.id, u2.email)) as c2:
                r = c2.get(f"/settings/oauth/codex/status?pending_id={pending_id}")
                assert r.status_code == 404
            r1 = c1.get(f"/settings/oauth/codex/status?pending_id={pending_id}")
            assert r1.status_code == 200

    async def test_cancel_removes_record(self, store, monkeypatch):
        user = await store.create_user(email="a@e.com", password_hash="h")
        _patch_device_flow(monkeypatch)
        with managed_test_client(_make_app(store, user.id, user.email)) as client:
            pending_id = client.post("/settings/oauth/codex/start").json()["pending_id"]
            assert client.app.state.process_state.codex_pending_flows.get(pending_id) is not None

            r = client.delete(f"/settings/oauth/codex/cancel?pending_id={pending_id}")
            assert r.status_code == 200
            assert r.json() == {"ok": True}
            assert client.app.state.process_state.codex_pending_flows.get(pending_id) is None

            r2 = client.get(f"/settings/oauth/codex/status?pending_id={pending_id}")
            assert r2.status_code == 404

    async def test_cancel_cross_user_returns_404(self, store, monkeypatch):
        u1 = await store.create_user(email="a@e.com", password_hash="h")
        u2 = await store.create_user(email="b@e.com", password_hash="h")
        _patch_device_flow(monkeypatch)

        with managed_test_client(_make_app(store, u1.id, u1.email)) as c1:
            pending_id = c1.post("/settings/oauth/codex/start").json()["pending_id"]
            with managed_test_client(_make_app(store, u2.id, u2.email)) as c2:
                r = c2.delete(f"/settings/oauth/codex/cancel?pending_id={pending_id}")
                assert r.status_code == 404
            assert c1.app.state.process_state.codex_pending_flows.get(pending_id) is not None

    async def test_start_rate_limit_kicks_in(self, store, monkeypatch):
        user = await store.create_user(email="a@e.com", password_hash="h")
        _patch_device_flow(monkeypatch)
        with managed_test_client(_make_app(store, user.id, user.email)) as client:
            for i in range(5):
                assert client.post("/settings/oauth/codex/start").status_code == 200, f"attempt {i}"
            assert client.post("/settings/oauth/codex/start").status_code == 429
