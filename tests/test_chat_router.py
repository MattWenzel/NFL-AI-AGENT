from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.domain.agent.events import TextDeltaEvent
from backend.domain.auth.types import AuthenticatedUser
from backend.server.process_state import AppProcessState
from backend.data import SessionRecord
from backend.api.routes import chat as chat_router
from backend.application.chat import (
    ChatConfigurationError,
    ChatNotFoundError,
    ChatService,
)
from backend.application.chat import PreparedChat


def _make_service():
    """Build a ChatService with None-typed deps — the tests below
    monkeypatch the methods they exercise, so the injected collaborators are
    never actually called."""
    return ChatService.__new__(ChatService)


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

    def fake_stream_events(self, prepared, *, message, tool_choice, **_extra):
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


async def _wait_for(predicate, *, timeout: float = 2.0) -> None:
    """Poll an async-loop-friendly predicate until true or timeout."""
    import asyncio as _asyncio
    deadline = _asyncio.get_event_loop().time() + timeout
    while not predicate():
        if _asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met within timeout")
        await _asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_chat_stream_finishes_turn_in_background_on_early_close(monkeypatch):
    """Finish-on-disconnect: when the client drops mid-stream, the turn keeps
    running on a detached drain task so the answer persists; the LLM client,
    stream slot, and runtime source are released only after the turn ends."""
    import asyncio as _asyncio

    gate = _CountingStreamGate()
    service = _make_service()
    stub_client = _TrackingClient()
    source_closed = {"called": False}
    release_turn = _asyncio.Event()

    async def fake_prepare(self, *, conversation_id, provider, model, user):
        return PreparedChat(
            client=stub_client, provider_name="anthropic", session=_stub_session()
        )

    def fake_stream_events(self, prepared, *, message, tool_choice, **_extra):
        async def _slow_events():
            try:
                yield TextDeltaEvent(
                    session_id=prepared.session.id,
                    turn_id="turn-stream",
                    text="hello",
                    iterations=1,
                )
                # Models a turn still mid-flight when the client drops; the
                # test releases this to simulate the turn completing.
                await release_turn.wait()
                yield TextDeltaEvent(
                    session_id=prepared.session.id,
                    turn_id="turn-stream",
                    text=" world",
                    iterations=1,
                )
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

    it = response.body_iterator
    first = await it.__anext__()
    assert '"type": "conversation_id"' in first
    second = await it.__anext__()
    assert '"type": "text"' in second

    # Client disconnects abruptly mid-turn.
    await it.aclose()

    # The turn is still running in the background — nothing released yet.
    assert not source_closed["called"]
    assert not stub_client.closed
    assert gate.releases == 0

    # Turn completes → drain task closes the source, client, and slot.
    release_turn.set()
    await _wait_for(lambda: gate.releases == 1)
    assert source_closed["called"], "runtime source did not finish/close after drain"
    assert stub_client.closed, "LLM client was not closed after drain"
    assert gate.active == 0


@pytest.mark.asyncio
async def test_chat_cancel_stops_detached_turn(monkeypatch):
    """POST /chat/cancel must stop a turn that survived a disconnect —
    otherwise Stop would silently keep spending the user's tokens."""
    import asyncio as _asyncio

    gate = _CountingStreamGate()
    service = _make_service()
    stub_client = _TrackingClient()
    process_state = AppProcessState(chat_stream_limiter=gate)
    source_closed = {"called": False}

    async def fake_prepare(self, *, conversation_id, provider, model, user):
        return PreparedChat(
            client=stub_client, provider_name="anthropic", session=_stub_session()
        )

    def fake_stream_events(self, prepared, *, message, tool_choice, **_extra):
        async def _hung_events():
            try:
                yield TextDeltaEvent(
                    session_id=prepared.session.id,
                    turn_id="turn-stream",
                    text="hello",
                    iterations=1,
                )
                await _asyncio.Event().wait()  # hangs until cancelled
            finally:
                source_closed["called"] = True

        return _hung_events()

    monkeypatch.setattr(ChatService, "prepare_chat", fake_prepare)
    monkeypatch.setattr(ChatService, "stream_events", fake_stream_events)

    response = await chat_router.chat_stream(
        _StubRequest(),
        chat_router.ChatRequest(message="hi"),
        service=service,
        process_state=process_state,
        user=AuthenticatedUser(id=1, email="t@e.com"),
    )

    it = response.body_iterator
    await it.__anext__()  # conversation_id
    await it.__anext__()  # first text event
    await it.aclose()  # disconnect → detach

    assert gate.releases == 0  # still draining in background

    # The Stop button's signal: cancel the session's active stream.
    assert process_state.chat_cancel_events.cancel("session-stream") is True

    await _wait_for(lambda: gate.releases == 1)
    assert source_closed["called"]
    assert stub_client.closed
    assert gate.active == 0
    # Registry entry cleaned up — a second cancel finds nothing.
    assert process_state.chat_cancel_events.cancel("session-stream") is False


# ---------------------------------------------------------------------------
# POST /chat/cancel — ownership + idempotence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_cancel_route_scopes_to_owner_and_is_idempotent(tmp_path):
    from backend.data import RuntimeStore

    store = RuntimeStore(tmp_path / "r.sqlite3")
    owner = await store.create_user(email="owner@e.com", password_hash="h")
    other = await store.create_user(email="other@e.com", password_hash="h")
    session = await store.get_or_create_session(
        provider="anthropic", model="stub", context_window=100, user_id=owner.id
    )
    process_state = AppProcessState()

    # Someone else's conversation → 404, indistinguishable from unknown id.
    with pytest.raises(HTTPException) as exc:
        await chat_router.chat_cancel(
            chat_router.CancelChatRequest(conversation_id=session.id),
            store=store,
            process_state=process_state,
            user=AuthenticatedUser(id=other.id, email=other.email),
        )
    assert exc.value.status_code == 404

    # Owner, no active stream → cancelled: false (idempotent no-op).
    result = await chat_router.chat_cancel(
        chat_router.CancelChatRequest(conversation_id=session.id),
        store=store,
        process_state=process_state,
        user=AuthenticatedUser(id=owner.id, email=owner.email),
    )
    assert result == {"cancelled": False}

    # Active stream registered → cancelled: true and the event fires.
    event = process_state.chat_cancel_events.open(session.id)
    result = await chat_router.chat_cancel(
        chat_router.CancelChatRequest(conversation_id=session.id),
        store=store,
        process_state=process_state,
        user=AuthenticatedUser(id=owner.id, email=owner.email),
    )
    assert result == {"cancelled": True}
    assert event.is_set()
