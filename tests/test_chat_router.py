from __future__ import annotations

import pytest
from fastapi import HTTPException

from agent.runtime import RuntimeEvent
from infra.persistence.runtime_store import SessionRecord
from api.routers import chat as chat_router


class _ClosableClient:
    def __init__(self):
        self.model = "stub-model"
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _RuntimeWithError:
    """Stub runtime: prepare_session returns a fixed SessionRecord, run_session
    yields a single runtime_error event. Lets us exercise /chat/message's
    error-path cleanup without touching the real store or LLM client."""

    def prepare_session(self, client, provider_name, conversation_id=None):
        return SessionRecord(
            id="session-1",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            provider=provider_name,
            model=client.model,
            title=None,
            context_window=1000,
        )

    async def run_session(self, *args, **kwargs):
        yield RuntimeEvent(
            type="runtime_error",
            session_id="session-1",
            error="Detected repeated tool loop on search_players with identical input",
        )


@pytest.mark.asyncio
async def test_chat_message_returns_error_for_runtime_failure_and_closes_client(monkeypatch):
    client = _ClosableClient()
    runtime = _RuntimeWithError()

    monkeypatch.setattr(chat_router, "create_client_for_request", lambda provider=None, model=None: client)
    monkeypatch.setattr(chat_router, "get_default_provider", lambda: "anthropic")

    with pytest.raises(HTTPException) as exc:
        await chat_router.chat_message(chat_router.ChatRequest(message="hi"), runtime=runtime)

    assert exc.value.status_code == 500
    assert "repeated tool loop" in exc.value.detail.lower()
    assert client.closed is True
