"""Unit tests for the agent.oauth package."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent.oauth.codex_auth import (
    CODEX_CLIENT_ID,
    CODEX_REDIRECT_URI,
    CODEX_TOKEN_URL,
    CodexAuth,
    CodexAuthError,
)
from agent.oauth.jwt_decode import (
    JWTDecodeError,
    chatgpt_account_id_from_access_token,
    decode_jwt_payload,
    email_from_id_token,
)
from agent.oauth.pkce import generate_pkce
from agent.oauth.token_store import TokenRecord, TokenStore


# ---------- PKCE ----------

class TestPKCE:
    def test_generated_values_have_no_padding_and_correct_lengths(self):
        pkce = generate_pkce()
        for value in (pkce.verifier, pkce.challenge, pkce.state):
            assert "=" not in value
            # url-safe alphabet only
            assert all(c.isalnum() or c in "-_" for c in value)
        # verifier ~ 86 chars (64 random bytes → 88 b64 chars → minus padding ≈ 86)
        assert 80 <= len(pkce.verifier) <= 90
        # sha256 digest → 44 chars → minus padding = 43
        assert len(pkce.challenge) == 43

    def test_challenge_matches_verifier(self):
        pkce = generate_pkce()
        recomputed = base64.urlsafe_b64encode(
            hashlib.sha256(pkce.verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert recomputed == pkce.challenge

    def test_successive_calls_produce_different_values(self):
        a = generate_pkce()
        b = generate_pkce()
        assert a.verifier != b.verifier
        assert a.state != b.state


# ---------- JWT decode ----------

def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"{header}.{body}.sig"


class TestJWTDecode:
    def test_decode_payload_roundtrip(self):
        claims = {"email": "user@example.com", "nested": {"a": 1}}
        token = _fake_jwt(claims)
        assert decode_jwt_payload(token) == claims

    def test_email_from_id_token(self):
        token = _fake_jwt({"email": "user@example.com"})
        assert email_from_id_token(token) == "user@example.com"

    def test_email_missing_returns_none(self):
        token = _fake_jwt({"sub": "abc"})
        assert email_from_id_token(token) is None

    def test_chatgpt_account_id_extracted(self):
        token = _fake_jwt({
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}
        })
        assert chatgpt_account_id_from_access_token(token) == "acct_123"

    def test_chatgpt_account_id_missing_claim_raises(self):
        token = _fake_jwt({"sub": "no-auth-claim"})
        with pytest.raises(JWTDecodeError):
            chatgpt_account_id_from_access_token(token)

    def test_malformed_token_raises(self):
        with pytest.raises(JWTDecodeError):
            decode_jwt_payload("not.a.jwt")  # payload segment isn't valid base64-json


# ---------- Token store ----------

class TestTokenStore:
    def test_roundtrip_single_account(self, tmp_path: Path):
        store = TokenStore(tmp_path / "codex_auth.json")
        rec = TokenRecord(
            access_token="at",
            refresh_token="rt",
            expires_at_ms=123,
            id_token="id",
            email="u@x.com",
        )
        store.save(rec)
        loaded = store.get()
        assert loaded is not None
        assert loaded.access_token == "at"
        assert loaded.email == "u@x.com"

    def test_save_requires_email(self, tmp_path: Path):
        store = TokenStore(tmp_path / "codex_auth.json")
        rec = TokenRecord(access_token="a", refresh_token="r", expires_at_ms=1)
        with pytest.raises(ValueError):
            store.save(rec)

    def test_second_account_does_not_change_default_unless_requested(self, tmp_path: Path):
        store = TokenStore(tmp_path / "codex_auth.json")
        rec_a = TokenRecord("a", "ra", 1, email="a@x.com")
        rec_b = TokenRecord("b", "rb", 2, email="b@x.com")
        store.save(rec_a)
        store.save(rec_b, make_default=False)
        assert store.get().email == "a@x.com"
        assert store.get("b@x.com").access_token == "b"

    def test_delete_removes_and_updates_default(self, tmp_path: Path):
        store = TokenStore(tmp_path / "codex_auth.json")
        rec_a = TokenRecord("a", "ra", 1, email="a@x.com")
        rec_b = TokenRecord("b", "rb", 2, email="b@x.com")
        store.save(rec_a)
        store.save(rec_b, make_default=False)
        assert store.delete("a@x.com") is True
        assert store.get() is not None
        assert store.get().email == "b@x.com"

    def test_missing_file_returns_none(self, tmp_path: Path):
        store = TokenStore(tmp_path / "missing.json")
        assert store.get() is None
        assert store.has_record() is False

    def test_corrupt_file_treated_as_empty(self, tmp_path: Path):
        path = tmp_path / "codex_auth.json"
        path.write_text("{not json")
        store = TokenStore(path)
        assert store.get() is None


# ---------- CodexAuth orchestrator ----------

class _MockTransport(httpx.AsyncBaseTransport):
    """Captures requests and returns scripted responses."""

    def __init__(self, responses: list[httpx.Response]):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        return self._responses.pop(0)


def _codex_auth_with_mock(tmp_path: Path, responses: list[httpx.Response]) -> tuple[CodexAuth, _MockTransport]:
    transport = _MockTransport(responses)
    http = httpx.AsyncClient(transport=transport)
    store = TokenStore(tmp_path / "codex_auth.json")
    return CodexAuth(store, http_client=http), transport


def _token_response(
    *,
    account_id: str = "acct_1",
    email: str = "u@x.com",
    expires_in: int = 3600,
    access_token_suffix: str = "A",
    refresh_token: str = "new-refresh",
) -> httpx.Response:
    access_payload = {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    id_payload = {"email": email}
    body: dict[str, Any] = {
        "access_token": _fake_jwt(access_payload) + access_token_suffix,
        "id_token": _fake_jwt(id_payload),
        "expires_in": expires_in,
        "token_type": "Bearer",
    }
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    return httpx.Response(200, json=body)


class TestCodexAuth:
    def test_authorize_url_has_required_params(self, tmp_path):
        auth = CodexAuth(TokenStore(tmp_path / "codex_auth.json"))
        req = auth.build_authorize_request()
        assert CODEX_CLIENT_ID in req.url
        assert "code_challenge_method=S256" in req.url
        assert "codex_cli_simplified_flow=true" in req.url
        # The callback URI is URL-encoded in the query string.
        assert CODEX_REDIRECT_URI.replace(":", "%3A").replace("/", "%2F") in req.url

    @pytest.mark.asyncio
    async def test_exchange_code_persists_record(self, tmp_path):
        auth, transport = _codex_auth_with_mock(tmp_path, [_token_response()])
        record = await auth.exchange_code("auth-code", "verifier")
        assert record.email == "u@x.com"
        assert record.refresh_token == "new-refresh"
        assert auth.store.get().email == "u@x.com"
        # Request hit the token URL with form body.
        assert len(transport.requests) == 1
        assert transport.requests[0].url == httpx.URL(CODEX_TOKEN_URL)
        await auth.aclose()

    @pytest.mark.asyncio
    async def test_exchange_code_requires_email_for_initial_login(self, tmp_path):
        auth, _transport = _codex_auth_with_mock(
            tmp_path,
            [httpx.Response(200, json={
                "access_token": _fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_1"}}),
                "expires_in": 3600,
                "token_type": "Bearer",
            })],
        )
        with pytest.raises(CodexAuthError, match="usable email address"):
            await auth.exchange_code("auth-code", "verifier")
        assert auth.store.get() is None
        await auth.aclose()

    @pytest.mark.asyncio
    async def test_get_valid_token_refreshes_near_expiry(self, tmp_path):
        auth, transport = _codex_auth_with_mock(tmp_path, [_token_response(access_token_suffix="Z")])
        expired = TokenRecord(
            access_token=_fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "old"}}) + "X",
            refresh_token="old-refresh",
            expires_at_ms=int(time.time() * 1000),  # already expired
            id_token=_fake_jwt({"email": "u@x.com"}),
            email="u@x.com",
        )
        auth.store.save(expired)
        token, account_id = await auth.get_valid_token()
        assert token.endswith("Z")  # came from the mocked refresh response
        assert account_id == "acct_1"
        assert len(transport.requests) == 1
        await auth.aclose()

    @pytest.mark.asyncio
    async def test_get_valid_token_skips_refresh_when_fresh(self, tmp_path):
        auth, transport = _codex_auth_with_mock(tmp_path, [])  # no responses needed
        fresh = TokenRecord(
            access_token=_fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "fresh"}}),
            refresh_token="rt",
            expires_at_ms=int(time.time() * 1000) + 10 * 60 * 1000,  # +10 min
            id_token=None,
            email="u@x.com",
        )
        auth.store.save(fresh)
        token, account_id = await auth.get_valid_token()
        assert account_id == "fresh"
        assert token == fresh.access_token
        assert transport.requests == []
        await auth.aclose()

    @pytest.mark.asyncio
    async def test_refresh_keeps_old_refresh_token_if_response_omits_it(self, tmp_path):
        auth, transport = _codex_auth_with_mock(
            tmp_path, [_token_response(refresh_token=None)]  # response omits refresh_token
        )
        original = TokenRecord(
            access_token=_fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "id"}}),
            refresh_token="keep-me",
            expires_at_ms=0,
            id_token=None,
            email="u@x.com",
        )
        auth.store.save(original)
        await auth.get_valid_token()
        stored = auth.store.get()
        assert stored.refresh_token == "keep-me"
        await auth.aclose()

    @pytest.mark.asyncio
    async def test_get_valid_token_raises_when_not_authenticated(self, tmp_path):
        auth, _ = _codex_auth_with_mock(tmp_path, [])
        with pytest.raises(CodexAuthError):
            await auth.get_valid_token()
        await auth.aclose()

    @pytest.mark.asyncio
    async def test_token_endpoint_error_is_wrapped(self, tmp_path):
        auth, _ = _codex_auth_with_mock(
            tmp_path, [httpx.Response(400, json={"error": "invalid_grant"})]
        )
        with pytest.raises(CodexAuthError):
            await auth.exchange_code("bad-code", "v")
        await auth.aclose()
