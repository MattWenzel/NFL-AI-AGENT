from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import ValidationError

from backend.domain.agent.events import TextDeltaEvent
from backend.domain.auth import encryption
from backend.domain.auth.types import AuthenticatedUser
from backend.api.dependencies import get_chat_service, get_current_user
from backend.server.process_state import AppProcessState
from backend.data import RuntimeStore, SessionRecord
from backend.api.routes import chat as chat_router
from backend.api.routes.chat import router as chat_router_module
from backend.application.chat import (
    ChatConfigurationError,
    ChatNotFoundError,
    ChatService,
    ChatServiceError,
)
from backend.application.chat import PreparedChat
from tests.app_factory import build_test_app, managed_test_client


def _make_service():
    """Build a ChatService with None-typed deps — the tests below
    monkeypatch the methods they exercise, so the injected collaborators are
    never actually called."""
    return ChatService.__new__(ChatService)


@pytest.mark.asyncio
async def test_chat_message_returns_error_for_runtime_failure_and_closes_client(monkeypatch):
    user = AuthenticatedUser(id=1, email="t@e.com")
    service = _make_service()

    async def fake_run_message(
        self,
        *,
        message,
        conversation_id,
        provider,
        model,
        tool_choice,
        user,
    ):
        raise ChatServiceError("Detected repeated tool loop on search_players with identical input")

    monkeypatch.setattr(ChatService, "run_message", fake_run_message)

    with pytest.raises(HTTPException) as exc:
        await chat_router.chat_message(
            chat_router.ChatRequest(message="hi"),
            service=service,
            user=user,
        )

    assert exc.value.status_code == 500
    assert "repeated tool loop" in exc.value.detail.lower()


def test_chat_request_accepts_valid_tool_choice_values():
    """Every ToolChoice literal the frontend can emit must survive Pydantic."""
    for value in ("auto", "required", "none"):
        req = chat_router.ChatRequest(message="hi", tool_choice=value)
        assert req.tool_choice == value
    # Default / omitted must land as None (the "let the server decide" case).
    assert chat_router.ChatRequest(message="hi").tool_choice is None
    assert chat_router.ChatRequest(message="hi", tool_choice=None).tool_choice is None


def test_chat_request_rejects_bogus_tool_choice():
    with pytest.raises(ValidationError):
        chat_router.ChatRequest(message="hi", tool_choice="force")


@pytest.fixture
def _encryption_key(monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encryption.reset_cache()
    yield
    encryption.reset_cache()


async def test_chat_message_route_resolves_service_through_depends_chain(tmp_path, monkeypatch, _encryption_key):
    """Integration: a POST to /chat/message must resolve get_chat_service
    through FastAPI's Depends chain with the authenticated user and return
    the service's response. Proves runtime + repositories + refresh_locks
    all plumb correctly without the route constructing the service inline."""
    store = RuntimeStore(tmp_path / "r.sqlite3")
    user = await store.create_user(email="a@e.com", password_hash="h")

    captured: dict = {}

    async def fake_run_message(
        self,
        *,
        message,
        conversation_id,
        provider,
        model,
        tool_choice,
        user,
    ):
        captured["service_class"] = type(self).__name__
        captured["caller_id"] = user.id
        captured["message"] = message
        # Prove the injected dependencies reached the service.
        assert isinstance(self, ChatService)
        assert self.store is not None
        assert self.credentials is not None
        assert self.runtime is not None
        return {
            "conversation_id": "session-x",
            "response": "wired correctly",
            "tool_calls": [],
            "truncated": False,
        }

    monkeypatch.setattr(ChatService, "run_message", fake_run_message)

    app = build_test_app(runtime_store=store)
    app.include_router(chat_router_module)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=user.id, email=user.email)

    with managed_test_client(app) as client:
        r = client.post("/chat/message", json={"message": "hello"})
        assert r.status_code == 200, r.text
        assert r.json()["response"] == "wired correctly"

    assert captured["service_class"] == "ChatService"
    assert captured["caller_id"] == user.id
    assert captured["message"] == "hello"


@pytest.mark.asyncio
async def test_chat_message_forwards_tool_choice_to_service(monkeypatch):
    """The route delegates non-streaming orchestration to ChatService."""
    user = AuthenticatedUser(id=1, email="t@e.com")
    service = _make_service()

    captured: dict = {}

    async def fake_run_message(
        self,
        *,
        message,
        conversation_id,
        provider,
        model,
        tool_choice,
        user,
    ):
        captured["tool_choice"] = tool_choice
        return {
            "conversation_id": "s1",
            "response": "ok",
            "tool_calls": [],
            "truncated": False,
        }

    monkeypatch.setattr(ChatService, "run_message", fake_run_message)

    await chat_router.chat_message(
        chat_router.ChatRequest(message="hi", tool_choice="required"),
        service=service,
        user=user,
    )

    assert captured["tool_choice"] == "required"


# ---------------------------------------------------------------------------
# /chat/stream — resource-acquisition-inside-generator invariants
# ---------------------------------------------------------------------------
#
# These tests guard the invariant that the stream slot and LLM client are
# only acquired once the event generator is iterated. Without that guarantee,
# a client disconnect between `return StreamingResponse(...)` and Starlette's
# first body-send call would leak both — the generator's `finally` block
# never fires on an un-iterated generator.


class _CountingStreamGate:
    """Test double for ConcurrencyLimiter that tallies acquire/release calls."""

    def __init__(self, *, reject: bool = False, max_active: int = 3):
        self.active = 0
        self.acquires = 0
        self.releases = 0
        self.reject = reject
        self.max_active = max_active

    async def acquire(self, key, *, detail: str) -> None:
        if self.reject:
            raise HTTPException(status_code=429, detail=detail)
        self.active += 1
        self.acquires += 1

    async def release(self, key) -> None:
        self.active -= 1
        self.releases += 1


class _TrackingClient:
    def __init__(self):
        self.model = "stub"
        self.closed = False

    async def aclose(self):
        self.closed = True


class _StubRequest:
    def __init__(self, *, disconnected: bool = False):
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected


def _stub_session() -> SessionRecord:
    return SessionRecord(
        id="session-stream",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        provider="anthropic",
        model="stub",
        title=None,
        context_window=1000,
        user_id=1,
    )


async def _drain_body(response) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(chunks)


@pytest.mark.asyncio
async def test_chat_stream_acquires_no_resources_before_generator_is_iterated(monkeypatch):
    """If Starlette never starts iterating the StreamingResponse body, the
    generator's `finally` will never fire. Anything acquired before the first
    `yield` would leak. Guarantee: before iteration, zero acquires."""
    gate = _CountingStreamGate()
    service = _make_service()

    async def fake_prepare(self, *, conversation_id, provider, model, user):
        raise AssertionError("prepare_chat must not be called before iteration")

    monkeypatch.setattr(ChatService, "prepare_chat", fake_prepare)

    response = await chat_router.chat_stream(
        _StubRequest(),
        chat_router.ChatRequest(message="hi"),
        service=service,
        process_state=AppProcessState(chat_stream_limiter=gate),
        user=AuthenticatedUser(id=1, email="t@e.com"),
    )

    assert gate.acquires == 0
    assert gate.active == 0

    # Mimic Starlette discarding the iterator without starting it.
    await response.body_iterator.aclose()

    assert gate.acquires == 0
    assert gate.active == 0


@pytest.mark.asyncio
async def test_chat_stream_releases_slot_and_client_on_normal_completion(monkeypatch):
    """Normal success path: slot acquires on first yield, releases on close;
    client opens in prepare_chat, closes in the generator's finally."""
    gate = _CountingStreamGate()
    service = _make_service()
    stub_client = _TrackingClient()

    async def fake_prepare(self, *, conversation_id, provider, model, user):
        return PreparedChat(
            client=stub_client, provider_name="anthropic", session=_stub_session()
        )

    def fake_stream_events(self, prepared, *, message, tool_choice):
        async def _empty_events():
            if False:
                yield  # makes this an async generator

        return _empty_events()

    monkeypatch.setattr(ChatService, "prepare_chat", fake_prepare)
    monkeypatch.setattr(ChatService, "stream_events", fake_stream_events)

    response = await chat_router.chat_stream(
        _StubRequest(),
        chat_router.ChatRequest(message="hi"),
        service=service,
        process_state=AppProcessState(chat_stream_limiter=gate),
        user=AuthenticatedUser(id=1, email="t@e.com"),
    )

    body = await _drain_body(response)

    assert '"type": "conversation_id"' in body
    assert '"type": "done"' in body
    assert gate.acquires == 1
    assert gate.releases == 1
    assert gate.active == 0
    assert stub_client.closed


@pytest.mark.asyncio
async def test_chat_stream_emits_not_found_error_as_sse_and_releases_slot(monkeypatch):
    """ChatNotFoundError from prepare_chat used to raise HTTPException 404.
    Now that prep happens inside the generator, it must surface as an SSE
    error event — with the slot still released in the generator's finally."""
    gate = _CountingStreamGate()
    service = _make_service()

    async def fake_prepare(self, *, conversation_id, provider, model, user):
        raise ChatNotFoundError("Conversation not found")

    monkeypatch.setattr(ChatService, "prepare_chat", fake_prepare)

    response = await chat_router.chat_stream(
        _StubRequest(),
        chat_router.ChatRequest(message="hi", conversation_id="missing"),
        service=service,
        process_state=AppProcessState(chat_stream_limiter=gate),
        user=AuthenticatedUser(id=1, email="t@e.com"),
    )

    body = await _drain_body(response)

    # One error SSE + one done SSE
    error_lines = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ") and '"type": "error"' in line
    ]
    assert len(error_lines) == 1
    assert error_lines[0]["code"] == "not_found"
    assert error_lines[0]["status"] == 404
    assert '"type": "done"' in body

    # Slot acquired (since rate check passed) and then released.
    assert gate.acquires == 1
    assert gate.releases == 1
    assert gate.active == 0


@pytest.mark.asyncio
async def test_chat_stream_emits_configuration_error_as_sse_and_releases_slot(monkeypatch):
    """Same contract as the not_found case: 503 → SSE error event, slot released."""
    gate = _CountingStreamGate()
    service = _make_service()

    async def fake_prepare(self, *, conversation_id, provider, model, user):
        raise ChatConfigurationError("No API key for Anthropic — add one in Settings.")

    monkeypatch.setattr(ChatService, "prepare_chat", fake_prepare)

    response = await chat_router.chat_stream(
        _StubRequest(),
        chat_router.ChatRequest(message="hi"),
        service=service,
        process_state=AppProcessState(chat_stream_limiter=gate),
        user=AuthenticatedUser(id=1, email="t@e.com"),
    )

    body = await _drain_body(response)
    error_lines = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ") and '"type": "error"' in line
    ]
    assert len(error_lines) == 1
    assert error_lines[0]["code"] == "configuration"
    assert error_lines[0]["status"] == 503
    assert gate.releases == 1


@pytest.mark.asyncio
async def test_chat_stream_emits_rate_limit_as_sse_without_holding_resources(monkeypatch):
    """429 from the stream gate used to surface as HTTPException. Now it
    becomes an SSE error event, and no slot/client is held afterward."""
    gate = _CountingStreamGate(reject=True)
    service = _make_service()

    async def fake_prepare(self, *, conversation_id, provider, model, user):
        raise AssertionError("prepare_chat must not be called when the gate rejects")

    monkeypatch.setattr(ChatService, "prepare_chat", fake_prepare)

    response = await chat_router.chat_stream(
        _StubRequest(),
        chat_router.ChatRequest(message="hi"),
        service=service,
        process_state=AppProcessState(chat_stream_limiter=gate),
        user=AuthenticatedUser(id=1, email="t@e.com"),
    )

    body = await _drain_body(response)
    error_lines = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ") and '"type": "error"' in line
    ]
    assert len(error_lines) == 1
    assert error_lines[0]["code"] == "rate_limited"
    assert error_lines[0]["status"] == 429
    # Gate rejected: no net acquire, nothing to release.
    assert gate.acquires == 0
    assert gate.releases == 0
    assert gate.active == 0


@pytest.mark.asyncio
async def test_chat_stream_cleans_up_runtime_source_and_client_on_early_close(monkeypatch):
    """If the client disconnects mid-stream (Starlette aborts iteration), the
    generator's finally must close the runtime source (which releases the
    session lock and reconciles pending tool runs) and close the LLM client."""
    gate = _CountingStreamGate()
    service = _make_service()
    stub_client = _TrackingClient()
    source_closed = {"called": False}

    async def fake_prepare(self, *, conversation_id, provider, model, user):
        return PreparedChat(
            client=stub_client, provider_name="anthropic", session=_stub_session()
        )

    def fake_stream_events(self, prepared, *, message, tool_choice):
        async def _slow_events():
            try:
                # Hand control back once so the outer generator yields the
                # conversation_id event, then block until cancelled. That
                # models a runtime mid-turn when the client disconnects.
                yield TextDeltaEvent(
                    session_id=prepared.session.id,
                    turn_id="turn-stream",
                    text="hello",
                    iterations=1,
                )
                import asyncio as _asyncio
                await _asyncio.Event().wait()
            finally:
                source_closed["called"] = True

        return _slow_events()

    monkeypatch.setattr(ChatService, "prepare_chat", fake_prepare)
    monkeypatch.setattr(ChatService, "stream_events", fake_stream_events)

    response = await chat_router.chat_stream(
        _StubRequest(),
        chat_router.ChatRequest(message="hi"),
        service=service,
        process_state=AppProcessState(chat_stream_limiter=gate),
        user=AuthenticatedUser(id=1, email="t@e.com"),
    )

    # Pull the first chunk (conversation_id SSE) so the generator is past
    # acquisition and into the streaming loop, then close abruptly.
    it = response.body_iterator
    first = await it.__anext__()
    assert '"type": "conversation_id"' in first

    # Pull the first streamed runtime event so the `async for event in source`
    # inside the producer task has actually entered source's body — without
    # this, aclose() on an async generator that was never started is a no-op
    # and source's finally never runs.
    second = await it.__anext__()
    assert '"type": "text"' in second

    await it.aclose()

    assert source_closed["called"], "runtime source.aclose() did not fire"
    assert stub_client.closed, "LLM client was not closed on early abort"
    assert gate.releases == 1
    assert gate.active == 0
