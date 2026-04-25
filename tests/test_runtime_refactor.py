import asyncio
import json
from pathlib import Path

import pytest

from backend.providers.base import BaseLLMClient
from backend.providers.types import MessageResponse, StopReason, TextEvent, ToolDefinition, ToolUseEvent, Usage
from backend.agent.runtime import ChatRuntime
from backend.storage import RuntimeStore


class StubClient(BaseLLMClient):
    """Replays pre-canned MessageResponses by converting them into stream events."""

    def __init__(self, responses):
        super().__init__("stub-model")
        self._responses = responses
        self.calls = 0
        self.tool_choice_calls: list = []

    @property
    def provider_name(self) -> str:
        return "stub"

    def _translate_error(self, exc: Exception):
        return exc

    async def create_message(self, messages, tools=None, system=None) -> MessageResponse:
        raise AssertionError("runtime is streaming-only; create_message should not be called")

    async def stream_message(self, messages, tools=None, system=None, tool_choice=None):
        self.tool_choice_calls.append(tool_choice)
        response = self._responses[self.calls]
        self.calls += 1
        for item in response.content:
            yield item
        self._set_last_usage(response.usage)


class StreamingStubClient(BaseLLMClient):
    def __init__(self):
        super().__init__("stream-stub")

    @property
    def provider_name(self) -> str:
        return "stub"

    def _translate_error(self, exc: Exception):
        return exc

    async def create_message(self, messages, tools=None, system=None) -> MessageResponse:
        raise AssertionError("runtime is streaming-only; create_message should not be called")

    async def stream_message(self, messages, tools=None, system=None, tool_choice=None):
        yield TextEvent(text="partial")
        await asyncio.sleep(60)


def _make_store(tmp_path: Path) -> RuntimeStore:
    return RuntimeStore(tmp_path / "runtime.sqlite3")


def _make_runtime(store: RuntimeStore) -> ChatRuntime:
    return ChatRuntime(store)


@pytest.mark.asyncio
async def test_runtime_persists_turns_and_tool_runs(tmp_path: Path, monkeypatch):
    store = _make_store(tmp_path)
    runtime = _make_runtime(store)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=200)
    tools = [ToolDefinition.from_dict(d) for d in [{"name": "search_players", "description": "", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}}]]
    async def fake_execute(name, input_data, ctx=None):
        return {
            "status": "completed",
            "tool": name,
            "content": json.dumps({"data": [{"display_name": "Patrick Mahomes"}]}),
            "error": None,
            "hint": None,
            "duration_ms": 1,
        }
    monkeypatch.setattr("backend.agent.runtime.execute_tool_structured", fake_execute)
    client = StubClient(
        [
            MessageResponse(
                content=[ToolUseEvent(id="ignored", name="search_players", input={"name": "Mahomes"})],
                stop_reason=StopReason.TOOL_USE,
                usage=Usage(input_tokens=40, output_tokens=5),
            ),
            MessageResponse(
                content=[TextEvent(text="Patrick Mahomes plays for KC.")],
                stop_reason=StopReason.END_TURN,
                usage=Usage(input_tokens=45, output_tokens=12),
            ),
        ]
    )

    events = []
    async for event in runtime.run_session(
        session,
        "Who is Patrick Mahomes?",
        client,
        tools=tools,
        provider_name="anthropic",
    ):
        events.append(event.type)

    transcript = await store.get_transcript(session.id)
    assert [turn.role for turn in transcript.turns] == ["user", "assistant", "assistant"]
    tool_runs = [run for runs in transcript.tool_runs_by_turn.values() for run in runs]
    assert len(tool_runs) == 1
    assert tool_runs[0].tool_name == "search_players"
    assert tool_runs[0].status == "completed"
    assert "tool_completed" in events
    assert transcript.turns[-1].text == "Patrick Mahomes plays for KC."
    assert transcript.turns[-1].input_tokens == 45
    assert transcript.turns[-1].output_tokens == 12


@pytest.mark.asyncio
async def test_runtime_forwards_tool_choice_to_client(tmp_path: Path, monkeypatch):
    """run_session(tool_choice="required") reaches the client on iteration 1,
    and resets to None on subsequent iterations so the follow-up call after a
    tool result doesn't re-force another tool call."""
    store = _make_store(tmp_path)
    runtime = _make_runtime(store)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=200)
    tools = [ToolDefinition.from_dict({"name": "search_players", "description": "", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}})]

    async def fake_execute(name, input_data, ctx=None):
        return {"status": "completed", "tool": name, "content": "{}", "error": None, "hint": None, "duration_ms": 1}

    monkeypatch.setattr("backend.agent.runtime.execute_tool_structured", fake_execute)
    client = StubClient([
        MessageResponse(
            content=[ToolUseEvent(id="x", name="search_players", input={"name": "M"})],
            stop_reason=StopReason.TOOL_USE,
            usage=Usage(input_tokens=10, output_tokens=2),
        ),
        MessageResponse(
            content=[TextEvent(text="done")],
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=12, output_tokens=3),
        ),
    ])

    async for _ in runtime.run_session(
        session, "find M", client,
        tools=tools, provider_name="anthropic",
        tool_choice="required",
    ):
        pass

    assert client.tool_choice_calls == ["required", None]


@pytest.mark.asyncio
async def test_runtime_store_compacts_old_turns(tmp_path: Path):
    store = _make_store(tmp_path)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
    for i in range(10):
        await store.create_turn(session.id, "user", text=f"user question {i} " * 20)
        await store.create_turn(session.id, "assistant", text=f"assistant answer {i} " * 20)

    from backend.agent.compaction import compact_if_needed
    await compact_if_needed(store, session)

    transcript = await store.get_transcript(session.id)
    compacted_turns = [turn for turn in transcript.turns if turn.compacted]
    summary_turns = [turn for turn in transcript.turns if turn.role == "summary"]
    assert compacted_turns
    assert summary_turns
    assert summary_turns[-1].text.startswith("Earlier conversation summary:")


@pytest.mark.asyncio
async def test_runtime_uses_stored_token_usage_for_compaction(tmp_path: Path):
    store = _make_store(tmp_path)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=150)
    for i in range(8):
        await store.create_turn(session.id, "user", text=f"user {i}")
        assistant = await store.create_turn(session.id, "assistant", text=f"assistant {i}")
        await store.update_turn(assistant.id, input_tokens=120, output_tokens=60)

    from backend.agent.compaction import compact_if_needed
    await compact_if_needed(store, session)
    transcript = await store.get_transcript(session.id)
    assert any(turn.compacted for turn in transcript.turns)
    assert any(turn.role == "summary" for turn in transcript.turns)


async def test_build_model_messages_omits_compacted_turns(tmp_path: Path):
    store = _make_store(tmp_path)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
    old_user = await store.create_turn(session.id, "user", text="old user")
    old_assistant = await store.create_turn(session.id, "assistant", text="old assistant")
    recent_user = await store.create_turn(session.id, "user", text="recent user")
    await store.record_compaction(session.id, "summary text", [old_user.id, old_assistant.id])

    from backend.agent.message_builder import build_model_messages
    messages = build_model_messages(await store.get_transcript(session.id))
    texts = [msg.text for msg in messages if msg.text]
    assert "old user" not in texts
    assert "old assistant" not in texts
    assert "recent user" in texts
    # Summary is wrapped in <prior_conversation_summary> so the model treats it
    # as reference context, not something to mimic. See runtime_store.py:883.
    assert any("<prior_conversation_summary>" in text and "summary text" in text for text in texts)


async def test_build_model_messages_preserves_tool_pairing_after_compaction(tmp_path: Path):
    """After compaction, build_model_messages must still emit the recent
    assistant tool-call immediately followed by its matching tool_result,
    with the summary as the first message. A broken ordering here would
    make Anthropic/OpenAI reject the request ("tool_use_id without
    matching tool_use block")."""
    store = _make_store(tmp_path)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
    # Old turns: will get compacted.
    old_user = await store.create_turn(session.id, "user", text="old user")
    old_assistant = await store.create_turn(session.id, "assistant", text="old assistant")

    # Recent turn: assistant calls a tool, then has a result. Neither is compacted.
    recent_user = await store.create_turn(session.id, "user", text="what about Mahomes")
    recent_assistant = await store.create_turn(session.id, "assistant", text="looking up")
    tool_run = await store.create_tool_run(
        session.id,
        recent_assistant.id,
        "search_players",
        {"name": "Mahomes"},
        status="completed",
    )
    await store.update_tool_run(tool_run.id, result='{"data":[{"name":"Mahomes"}]}', status="completed")
    await store.add_part(
        session.id,
        recent_assistant.id,
        "tool_call",
        json.dumps({"name": "search_players", "input": {"name": "Mahomes"}}),
        name="search_players",
        tool_run_id=tool_run.id,
    )

    await store.record_compaction(session.id, "summary of old context", [old_user.id, old_assistant.id])

    from backend.agent.message_builder import build_model_messages
    transcript = await store.get_transcript(session.id)
    messages = build_model_messages(transcript)

    # First: summary wrapped as an assistant prefix.
    assert messages[0].role == "assistant"
    assert "<prior_conversation_summary>" in messages[0].text
    assert "summary of old context" in messages[0].text

    # Compacted turns must not appear.
    all_text = " ".join(m.text or "" for m in messages)
    assert "old user" not in all_text
    assert "old assistant" not in all_text

    # Recent assistant turn must carry the tool_call, and the matching
    # tool_result must come directly after. No untethered tool_result blocks.
    assistant_tool_messages = [
        (i, m) for i, m in enumerate(messages)
        if m.role == "assistant" and m.tool_calls
    ]
    assert len(assistant_tool_messages) == 1, "expected exactly one assistant turn with a tool call"
    assistant_idx, assistant_msg = assistant_tool_messages[0]
    assert assistant_msg.tool_calls[0].name == "search_players"
    assert assistant_msg.tool_calls[0].id == tool_run.id
    assert messages[assistant_idx + 1].role == "tool_result"
    assert messages[assistant_idx + 1].tool_use_id == tool_run.id


async def test_build_model_messages_emits_summary_before_trailing_user_turn(tmp_path: Path):
    """Regression: a compaction fired *during* a user turn must not leave the
    summary as the last assistant message. Anthropic rejects that sequence
    with "conversation must end with a user message" (400).
    """
    store = _make_store(tmp_path)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
    old_user = await store.create_turn(session.id, "user", text="old user")
    old_assistant = await store.create_turn(session.id, "assistant", text="old assistant")
    # User turn created BEFORE compaction fires (matches the runtime loop).
    pending_user = await store.create_turn(session.id, "user", text="what if we include rushing tds also")
    # Compaction runs after the user turn → summary turn gets a later created_at.
    await store.record_compaction(session.id, "summary of old context", [old_user.id, old_assistant.id])

    from backend.agent.message_builder import build_model_messages
    messages = build_model_messages(await store.get_transcript(session.id))
    assert messages, "expected at least one message"
    assert messages[-1].role == "user", "last message must be the user turn, not the summary"
    assert messages[-1].text == "what if we include rushing tds also"
    # Summary is emitted first (as an assistant-role prefix).
    assert messages[0].role == "assistant"
    assert "<prior_conversation_summary>" in messages[0].text
    assert "summary of old context" in messages[0].text


@pytest.mark.asyncio
async def test_doom_loop_detection_stops_repeated_tool_calls(tmp_path: Path, monkeypatch):
    store = _make_store(tmp_path)
    runtime = _make_runtime(store)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=200)
    tools = [ToolDefinition.from_dict(d) for d in [{"name": "search_players", "description": "", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}}]]
    async def fake_execute(name, input_data, ctx=None):
        return {
            "status": "completed",
            "tool": name,
            "content": json.dumps({"data": []}),
            "error": None,
            "hint": None,
            "duration_ms": 1,
        }
    monkeypatch.setattr("backend.agent.runtime.execute_tool_structured", fake_execute)
    repeated = MessageResponse(
        content=[ToolUseEvent(id="ignored", name="search_players", input={"name": "Josh Allen"})],
        stop_reason=StopReason.TOOL_USE,
    )
    client = StubClient([repeated, repeated, repeated])

    events = []
    async for event in runtime.run_session(
        session,
        "Find Josh Allen",
        client,
        tools=tools,
        provider_name="anthropic",
    ):
        events.append(event)

    runtime_errors = [event for event in events if event.type == "runtime_error"]
    assert runtime_errors
    assert "repeated tool loop" in runtime_errors[-1].error.lower()


@pytest.mark.asyncio
async def test_runtime_marks_streaming_turn_interrupted_when_consumer_stops_early(tmp_path: Path):
    store = _make_store(tmp_path)
    runtime = _make_runtime(store)
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=200)
    client = StreamingStubClient()

    stream = runtime.run_session(
        session,
        "Start streaming",
        client,
        tools=[],
        provider_name="anthropic",
    )

    events = []
    async for event in stream:
        events.append(event.type)
        if event.type == "text_delta":
            break
    await stream.aclose()

    transcript = await store.get_transcript(session.id)
    assistant_turns = [turn for turn in transcript.turns if turn.role == "assistant"]
    assert events[:2] == ["turn_started", "assistant_started"]
    assert "text_delta" in events
    assert assistant_turns
    assert assistant_turns[-1].status == "interrupted"
    assert "interrupted" in (assistant_turns[-1].error or "").lower()
