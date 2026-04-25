from __future__ import annotations

import asyncio

import pytest

from server.services.conversations import ConversationService
from storage import RuntimeStore


@pytest.fixture
def store(tmp_path):
    return RuntimeStore(tmp_path / "runtime.sqlite3")


async def _create_user_and_session(store: RuntimeStore):
    user = await store.create_user(
        email="test@example.com",
        password_hash="hashed",
    )
    session = await store.get_or_create_session(
        provider="anthropic",
        model="claude-test",
        context_window=1234,
        user_id=user.id,
    )
    return user, session


@pytest.mark.asyncio
async def test_session_patch_updates_do_not_clobber_other_fields(store: RuntimeStore):
    user, session = await _create_user_and_session(store)
    await store.update_session(session.id, title="Initial title")
    await store.set_session_pinned(session.id, True, user_id=user.id)

    # Mirrors the provider/model patch runtime.run_session issues per user turn —
    # guards against the regression where a partial update clobbers unrelated
    # fields like title or pinned_at.
    await store.update_session(session.id, provider="openai", model="gpt-test")

    updated = await store.get_session(session.id, user_id=user.id)
    assert updated is not None
    assert updated.title == "Initial title"
    assert updated.provider == "openai"
    assert updated.model == "gpt-test"
    assert updated.pinned_at is not None


@pytest.mark.asyncio
async def test_list_sessions_returns_typed_entries(store: RuntimeStore):
    user, session = await _create_user_and_session(store)
    await store.update_session(session.id, title="Typed sessions")

    rows = await store.list_sessions(user_id=user.id)
    entry = await store.get_session_list_entry(session.id, user_id=user.id)

    assert len(rows) == 1
    assert rows[0].id == session.id
    assert rows[0].title == "Typed sessions"
    assert rows[0].turn_count == 0
    assert entry is not None
    assert entry.id == session.id


@pytest.mark.asyncio
async def test_parallel_part_inserts_keep_unique_monotonic_order(store: RuntimeStore):
    user, session = await _create_user_and_session(store)
    turn = await store.create_turn(session.id, "assistant", status="running")

    async def add_tool_result_part(index: int) -> None:
        await store.add_part(
            session.id,
            turn.id,
            "tool_result",
            f"result {index}",
            name=f"tool_{index}",
        )

    await asyncio.gather(*(add_tool_result_part(i) for i in range(20)))

    transcript = await store.get_transcript(session.id)
    parts = transcript.parts_by_turn[turn.id]
    order_indices = [part.order_index for part in parts]

    assert len(parts) == 20
    assert len(set(order_indices)) == 20
    assert order_indices == list(range(20))


@pytest.mark.asyncio
async def test_transcript_response_hides_storage_only_fields(store: RuntimeStore):
    user, session = await _create_user_and_session(store)
    user_turn = await store.create_turn(session.id, "user", text="hello")
    assistant_turn = await store.create_turn(session.id, "assistant", status="running")
    tool_run = await store.create_tool_run(
        session.id,
        assistant_turn.id,
        "lookup",
        {"player": "Mahomes"},
    )
    await store.add_part(
        session.id,
        assistant_turn.id,
        "tool_call",
        '{"player":"Mahomes"}',
        name="lookup",
        tool_run_id=tool_run.id,
    )
    await store.record_compaction(session.id, "summary text", [user_turn.id])

    service = ConversationService(store)
    response = await service.get_transcript(session.id, user.id)
    payload = response.model_dump()

    assert all("session_id" not in turn for turn in payload["turns"])
    assert all("session_id" not in part for part in payload["parts"])
    assert all("session_id" not in run for run in payload["tool_runs"])
    assert all("session_id" not in summary for summary in payload["summaries"])
    assert all("raw_input_text" not in run for run in payload["tool_runs"])
