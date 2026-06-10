"""Cost-abuse protections: shared-key gating, per-key rate limits, export cap.

These guard the operator's wallet on a public deployment — the env-var
LLM keys must not be spendable by arbitrary signups, request volume on
LLM/SQL endpoints must be bounded per user, and the CSV library must not
grow without bound.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import backend.data.repositories.exports as exports_repo
import backend.domain.providers.registry as registry_module
from backend.application.oauth.provider_credentials import (
    CredentialServiceError,
    ProviderCredentialService,
)
from backend.data import ExportLimitExceededError, RuntimeStore
from backend.domain.providers import env_fallback_allowed
from backend.server.rate_limit import RateLimiter


class TestEnvFallbackPolicy:
    def test_default_admin_only(self, monkeypatch):
        monkeypatch.setattr(registry_module, "SHARED_PROVIDER_KEYS", "admin")
        assert env_fallback_allowed("admin") is True
        assert env_fallback_allowed("user") is False

    def test_all_opens_fallback_to_everyone(self, monkeypatch):
        monkeypatch.setattr(registry_module, "SHARED_PROVIDER_KEYS", "all")
        assert env_fallback_allowed("user") is True

    def test_none_denies_even_admin(self, monkeypatch):
        monkeypatch.setattr(registry_module, "SHARED_PROVIDER_KEYS", "none")
        assert env_fallback_allowed("admin") is False


class _NoKeysStore:
    """Stub store: the user has no stored API key for any provider."""

    async def get_api_key(self, *, user_id, provider):
        return None


class TestResolveProviderClientGate:
    """A signed-up non-admin must NOT be able to spend on the server's
    env-var key — that's the operator's bill."""

    @pytest.fixture(autouse=True)
    def _env_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
        monkeypatch.setattr(registry_module, "SHARED_PROVIDER_KEYS", "admin")

    @pytest.mark.asyncio
    async def test_regular_user_without_own_key_is_rejected(self):
        service = ProviderCredentialService(_NoKeysStore(), None)
        with pytest.raises(CredentialServiceError, match="add one in Settings"):
            await service.resolve_provider_client(
                user_id=2, role="user", provider="anthropic", model=None
            )

    @pytest.mark.asyncio
    async def test_admin_falls_back_to_env_key(self):
        service = ProviderCredentialService(_NoKeysStore(), None)
        resolved = await service.resolve_provider_client(
            user_id=1, role="admin", provider="anthropic", model=None
        )
        try:
            assert resolved.provider_name == "anthropic"
        finally:
            await resolved.client.aclose()


class TestRateLimiterCheckKey:
    def test_over_quota_raises_429(self):
        limiter = RateLimiter(max_attempts=2, window_seconds=60)
        limiter.check_key("user:1")
        limiter.check_key("user:1")
        with pytest.raises(HTTPException) as exc:
            limiter.check_key("user:1")
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers

    def test_keys_are_independent(self):
        limiter = RateLimiter(max_attempts=1, window_seconds=60)
        limiter.check_key("user:1")
        limiter.check_key("user:2")  # different identity, separate bucket


class TestExportCap:
    @pytest.mark.asyncio
    async def test_register_export_rejects_past_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(exports_repo, "MAX_EXPORTS_PER_USER", 1)
        store = RuntimeStore(tmp_path / "r.sqlite3")
        user = await store.create_user(email="t@e.com", password_hash="h")
        session = await store.get_or_create_session(
            provider="anthropic",
            model="claude-sonnet-4-6",
            context_window=100_000,
            user_id=user.id,
        )

        async def register(filename):
            return await store.register_export(
                filename=filename,
                title="t",
                sql="SELECT 1",
                row_count=1,
                columns=["a"],
                file_size=10,
                source_session_id=session.id,
                source_tool_run_id=None,
            )

        await register("a.csv")
        with pytest.raises(ExportLimitExceededError):
            await register("b.csv")

        # Freeing a slot lets the user export again.
        exports = await store.list_exports(user_id=user.id)
        await store.delete_export(exports[0].id, user_id=user.id)
        await register("c.csv")
