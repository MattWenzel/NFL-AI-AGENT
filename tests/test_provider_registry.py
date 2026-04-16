"""Tests for ProviderInfo.auth_type, create_client branching, and availability."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from agent.providers import (
    LLMError,
    ProviderInfo,
    create_client,
    get_provider,
    list_providers,
    provider_is_available,
)
from agent.providers.codex_provider import CodexClient
from agent.oauth.token_store import TokenRecord, TokenStore


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


@pytest.fixture
def isolated_codex_auth_path(monkeypatch, tmp_path: Path) -> Path:
    """Point CODEX_AUTH_PATH at a tmp file for the duration of the test."""
    path = tmp_path / "codex_auth.json"
    # The factory and provider_is_available both import from config at call time;
    # patching the config module attribute is sufficient.
    import config
    monkeypatch.setattr(config, "CODEX_AUTH_PATH", path)
    return path


class TestProviderRegistry:
    def test_codex_is_registered_with_oauth_auth_type(self):
        info = get_provider("codex")
        assert info.auth_type == "oauth"
        assert info.client_class is CodexClient
        assert info.default_model == "gpt-5.1-codex"

    def test_other_providers_default_to_api_key(self):
        for info in list_providers():
            if info.name != "codex":
                assert info.auth_type == "api_key"


class TestAvailability:
    def test_codex_unavailable_without_token(self, isolated_codex_auth_path):
        info = get_provider("codex")
        assert provider_is_available(info) is False

    def test_codex_available_once_token_saved(self, isolated_codex_auth_path):
        store = TokenStore(isolated_codex_auth_path)
        record = TokenRecord(
            access_token=_fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "a"}}),
            refresh_token="rt",
            expires_at_ms=int(time.time() * 1000) + 3_600_000,
            id_token=_fake_jwt({"email": "u@x.com"}),
            email="u@x.com",
        )
        store.save(record)
        info = get_provider("codex")
        assert provider_is_available(info) is True

    def test_api_key_provider_availability_tracks_env(self, monkeypatch):
        info = get_provider("anthropic")
        monkeypatch.delenv(info.env_key, raising=False)
        assert provider_is_available(info) is False
        monkeypatch.setenv(info.env_key, "sk-anything")
        assert provider_is_available(info) is True


class TestCreateClient:
    def test_oauth_provider_errors_without_token(self, isolated_codex_auth_path):
        with pytest.raises(LLMError, match="not authenticated"):
            create_client(provider="codex")

    def test_oauth_provider_constructs_when_authenticated(self, isolated_codex_auth_path):
        store = TokenStore(isolated_codex_auth_path)
        record = TokenRecord(
            access_token=_fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "a"}}),
            refresh_token="rt",
            expires_at_ms=int(time.time() * 1000) + 3_600_000,
            id_token=_fake_jwt({"email": "u@x.com"}),
            email="u@x.com",
        )
        store.save(record)
        client = create_client(provider="codex")
        assert isinstance(client, CodexClient)
        assert client.model == "gpt-5.1-codex"
        assert client.provider_name == "codex"

    def test_api_key_provider_still_errors_without_key(self, monkeypatch):
        info = get_provider("anthropic")
        monkeypatch.delenv(info.env_key, raising=False)
        with pytest.raises(LLMError, match="API key not set"):
            create_client(provider="anthropic")

    def test_unknown_provider_raises(self):
        with pytest.raises(KeyError):
            create_client(provider="not-a-provider")


class TestProviderInfoDefaults:
    def test_auth_type_defaults_to_api_key(self):
        info = ProviderInfo(
            name="x", display_name="X", env_key="X_KEY", default_model="m",
        )
        assert info.auth_type == "api_key"


class TestProvidersEndpointExposesAuthType:
    def test_chat_providers_includes_auth_type(self, isolated_codex_auth_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routers import chat as chat_router

        app = FastAPI()
        app.include_router(chat_router.router)
        with TestClient(app) as client:
            resp = client.get("/chat/providers")
            assert resp.status_code == 200
            body = resp.json()
        codex = next(p for p in body if p["name"] == "codex")
        assert codex["auth_type"] == "oauth"
        assert codex["available"] is False  # isolated path → no token
        anthropic = next(p for p in body if p["name"] == "anthropic")
        assert anthropic["auth_type"] == "api_key"
