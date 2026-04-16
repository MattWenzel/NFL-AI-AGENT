from __future__ import annotations

import pytest
from fastapi import HTTPException

from agent.providers import ProviderInfo
from agent.runtime import RuntimeEvent
from agent.runtime_store import SessionRecord
from api.routers import chat as chat_router


class _ClosableClient:
    def __init__(self):
        self.model = "stub-model"
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _RuntimeWithError:
    async def run_session(self, *args, **kwargs):
        yield RuntimeEvent(
            type="runtime_error",
            session_id="session-1",
            error="Detected repeated tool loop on search_players with identical input",
        )


class _StoreStub:
    def get_or_create_session(self, *args, **kwargs):
        return SessionRecord(
            id="session-1",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            provider="anthropic",
            model="stub-model",
            title=None,
            context_window=1000,
        )


@pytest.mark.asyncio
async def test_chat_message_returns_error_for_runtime_failure_and_closes_client(monkeypatch):
    client = _ClosableClient()
    store = _StoreStub()
    runtime = _RuntimeWithError()
    provider = ProviderInfo(
        name="anthropic",
        display_name="Anthropic",
        env_key="ANTHROPIC_API_KEY",
        default_model="stub-model",
    )

    monkeypatch.setattr(chat_router, "_create_client_for_request", lambda provider_name=None, model=None: client)
    monkeypatch.setattr(chat_router, "_get_store", lambda request=None: store)
    monkeypatch.setattr(chat_router, "_get_runtime", lambda request=None: runtime)
    monkeypatch.setattr(chat_router, "get_default_provider", lambda: "anthropic")
    monkeypatch.setattr(chat_router, "get_provider", lambda name: provider)

    with pytest.raises(HTTPException) as exc:
        await chat_router.chat_message(None, chat_router.ChatRequest(message="hi"))

    assert exc.value.status_code == 500
    assert "repeated tool loop" in exc.value.detail.lower()
    assert client.closed is True
