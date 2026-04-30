"""HTTP-level tests for the DB helper chat streaming endpoint.

Mirrors the test pattern in `test_database_routes.py`: monkeypatch the
provider client so the test doesn't depend on a real LLM, register a
fresh user, and exercise the SSE stream end-to-end.
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

from backend.api.routes.auth import router as auth_router
from backend.api.routes.database import router as database_router
from backend.application import auth as auth_service_module
from backend.data import RuntimeStore
from backend.domain.auth import encryption
from backend.domain.providers.types import (
    Message,
    MessageResponse,
    StopReason,
    TextEvent,
    ToolUseEvent,
)
from tests.app_factory import build_test_app, managed_test_client


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encryption.reset_cache()
    yield
    encryption.reset_cache()


@pytest.fixture(autouse=True)
def _clear_invite_code(monkeypatch):
    monkeypatch.setattr(auth_service_module, "REGISTRATION_INVITE_CODE", None)


@pytest.fixture
def store(tmp_path):
    return RuntimeStore(tmp_path / "r.sqlite3")


@pytest.fixture
def client(store, monkeypatch):
    """TestClient with auth + database routes mounted and a stub LLM client."""
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # makes provider 'available'

    app = build_test_app(runtime_store=store)
    app.include_router(auth_router)
    app.include_router(database_router)
    with managed_test_client(app) as c:
        yield c


def _register_and_get_csrf(client) -> str:
    r = client.post(
        "/auth/register", json={"email": "a@b.com", "password": "pw12345678"}
    )
    assert r.status_code == 201, r.text
    csrf = client.cookies.get("csrf_token")
    assert csrf
    return csrf


def _parse_sse(text: str) -> list[dict]:
    """Pull JSON event objects out of an SSE response body."""
    events = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[len("data:"):].strip()
        if not body:
            continue
        try:
            events.append(json.loads(body))
        except json.JSONDecodeError:
            pass
    return events


class _StubLLM:
    """Minimal stand-in for `BaseLLMClient` that yields scripted events."""

    def __init__(self, scripts):
        self.model = "stub"
        self.last_stop_reason = None
        self.last_usage = None
        self._scripts = scripts
        self.calls: list[list[Message]] = []

    async def stream_message(  # type: ignore[override]
        self, messages, tools=None, system=None, tool_choice=None
    ) -> AsyncIterator:
        self.calls.append(list(messages))
        if not self._scripts:
            return
        events = self._scripts.pop(0)
        for ev in events:
            yield ev
        self.last_stop_reason = StopReason.END_TURN

    async def create_message(self, *_a, **_kw) -> MessageResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def test_helper_chat_requires_auth(client):
    r = client.post(
        "/database/helper-chat/stream",
        json={"messages": [{"role": "user", "text": "hi"}]},
    )
    assert r.status_code == 401


def test_helper_chat_requires_csrf(client):
    _register_and_get_csrf(client)
    r = client.post(
        "/database/helper-chat/stream",
        json={"messages": [{"role": "user", "text": "hi"}]},
    )
    assert r.status_code == 403


def test_helper_chat_streams_text_response(client, monkeypatch):
    csrf = _register_and_get_csrf(client)

    stub = _StubLLM(scripts=[[TextEvent(text="Hello "), TextEvent(text="world")]])
    monkeypatch.setattr(
        "backend.application.oauth.provider_credentials.create_client",
        lambda **_: stub,
    )

    r = client.post(
        "/database/helper-chat/stream",
        json={
            "messages": [{"role": "user", "text": "hi"}],
            "provider": "anthropic",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    text_chunks = [e for e in events if e.get("type") == "text"]
    assert "".join(e["text"] for e in text_chunks) == "Hello world"
    assert events[-1] == {"type": "done"}


def test_helper_chat_inlines_tool_result_content(client, monkeypatch):
    """Tool result content rides on the SSE event so the slim renderer
    has it without re-fetching from the DB (there is no DB for this)."""
    csrf = _register_and_get_csrf(client)

    stub = _StubLLM(scripts=[
        [ToolUseEvent(id="tool_a", name="execute_sql", input={"sql": "SELECT 1"})],
        [TextEvent(text="Got it.")],
    ])
    monkeypatch.setattr(
        "backend.application.oauth.provider_credentials.create_client",
        lambda **_: stub,
    )

    async def fake_execute_tool(name, input_data, ctx=None):
        return json.dumps({"columns": ["a"], "rows": [{"a": 1}], "row_count": 1})

    monkeypatch.setattr(
        "backend.domain.agent.stateless.execute_tool", fake_execute_tool
    )

    r = client.post(
        "/database/helper-chat/stream",
        json={
            "messages": [{"role": "user", "text": "run a select"}],
            "provider": "anthropic",
        },
        headers={"X-CSRF-Token": csrf},
    )
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    # Order: tool_call → tool_result → text → done.
    assert types[: -1] == ["tool_call", "tool_result", "text"]
    assert events[1]["type"] == "tool_result"
    parsed = json.loads(events[1]["content"])
    assert parsed["row_count"] == 1


def test_helper_chat_writes_no_sessions(client, store, monkeypatch):
    """Stateless: after a successful turn, no `sessions` row should exist."""
    csrf = _register_and_get_csrf(client)

    stub = _StubLLM(scripts=[[TextEvent(text="hi")]])
    monkeypatch.setattr(
        "backend.application.oauth.provider_credentials.create_client",
        lambda **_: stub,
    )

    import asyncio

    sessions_before = asyncio.get_event_loop().run_until_complete(
        store.list_sessions(user_id=1)
    )
    r = client.post(
        "/database/helper-chat/stream",
        json={
            "messages": [{"role": "user", "text": "hi"}],
            "provider": "anthropic",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    sessions_after = asyncio.get_event_loop().run_until_complete(
        store.list_sessions(user_id=1)
    )
    assert len(sessions_after) == len(sessions_before)


def test_helper_chat_configuration_error_emits_sse_error(client, monkeypatch):
    """Unknown provider → service raises HelperChatConfigurationError →
    SSE frame with code=configuration."""
    csrf = _register_and_get_csrf(client)

    r = client.post(
        "/database/helper-chat/stream",
        json={
            "messages": [{"role": "user", "text": "hi"}],
            "provider": "made-up-provider",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    err = next(e for e in events if e.get("type") == "error")
    assert err.get("code") == "configuration"
    assert events[-1] == {"type": "done"}


def test_helper_chat_assistant_with_tool_calls_round_trips(client, monkeypatch):
    """Frontend sends back a previous assistant turn with tool_calls and the
    matching tool_result. The route must rehydrate that into the wire
    history without crashing pydantic."""
    csrf = _register_and_get_csrf(client)

    stub = _StubLLM(scripts=[[TextEvent(text="ok")]])
    monkeypatch.setattr(
        "backend.application.oauth.provider_credentials.create_client",
        lambda **_: stub,
    )

    r = client.post(
        "/database/helper-chat/stream",
        json={
            "messages": [
                {"role": "user", "text": "run a select"},
                {
                    "role": "assistant",
                    "text": "Querying...",
                    "tool_calls": [
                        {"id": "t1", "name": "execute_sql", "input": {"sql": "SELECT 1"}}
                    ],
                },
                {"role": "tool_result", "tool_use_id": "t1", "content": '{"row_count": 1}'},
                {"role": "user", "text": "great, what do you see?"},
            ],
            "provider": "anthropic",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    # The full converted history reached the stub: 4 messages with the
    # right roles in the right order.
    assert len(stub.calls) == 1
    sent = stub.calls[0]
    roles = [m.role for m in sent]
    assert roles == ["user", "assistant", "tool_result", "user"]
    # And the assistant message round-tripped its tool call.
    assistant_msg = sent[1]
    assert assistant_msg.tool_calls is not None
    assert assistant_msg.tool_calls[0].id == "t1"
    assert assistant_msg.tool_calls[0].name == "execute_sql"
