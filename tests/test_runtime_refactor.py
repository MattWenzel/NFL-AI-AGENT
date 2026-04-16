import asyncio
import json
from pathlib import Path

import pytest

from infra.providers.base import BaseLLMClient, MessageResponse, StopReason, TextEvent, ToolDefinition, ToolUseEvent, Usage
from agent.runtime import ChatRuntime
from infra.persistence.runtime_store import RuntimeStore


class StubClient(BaseLLMClient):
    """Replays pre-canned MessageResponses by converting them into stream events."""

    def __init__(self, responses):
        super().__init__("stub-model")
        self._responses = responses
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "stub"

    def _translate_error(self, exc: Exception):
        return exc

    async def create_message(self, messages, tools=None, system=None) -> MessageResponse:
        raise AssertionError("runtime is streaming-only; create_message should not be called")

    async def stream_message(self, messages, tools=None, system=None):
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

    async def stream_message(self, messages, tools=None, system=None):
        yield TextEvent(text="partial")
        await asyncio.sleep(60)


def _make_store(tmp_path: Path) -> RuntimeStore:
    return RuntimeStore(tmp_path / "runtime.sqlite3")


@pytest.mark.asyncio
async def test_runtime_persists_turns_and_tool_runs(tmp_path: Path, monkeypatch):
    store = _make_store(tmp_path)
    runtime = ChatRuntime(store)
    session = store.get_or_create_session(provider="anthropic", model="stub", context_window=200)
    tools = [ToolDefinition.from_dict(d) for d in [{"name": "search_players", "description": "", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}}]]
    async def fake_execute(name, input_data):
        return {
            "status": "completed",
            "tool": name,
            "content": json.dumps({"data": [{"display_name": "Patrick Mahomes"}]}),
            "error": None,
            "hint": None,
            "duration_ms": 1,
        }
    monkeypatch.setattr("agent.runtime.loop.execute_tool_structured", fake_execute)
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

    transcript = store.get_transcript(session.id)
    assert [turn.role for turn in transcript.turns] == ["user", "assistant", "assistant"]
    tool_runs = [run for runs in transcript.tool_runs_by_turn.values() for run in runs]
    assert len(tool_runs) == 1
    assert tool_runs[0].tool_name == "search_players"
    assert tool_runs[0].status == "completed"
    assert "tool_completed" in events
    assert transcript.turns[-1].text == "Patrick Mahomes plays for KC."
    assert transcript.turns[-1].input_tokens == 45
    assert transcript.turns[-1].output_tokens == 12


def test_runtime_store_compacts_old_turns(tmp_path: Path):
    store = _make_store(tmp_path)
    session = store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
    for i in range(10):
        store.create_turn(session.id, "user", text=f"user question {i} " * 20)
        store.create_turn(session.id, "assistant", text=f"assistant answer {i} " * 20)

    from agent.runtime.compaction import compact_if_needed
    compact_if_needed(store, session)

    transcript = store.get_transcript(session.id)
    compacted_turns = [turn for turn in transcript.turns if turn.compacted]
    summary_turns = [turn for turn in transcript.turns if turn.role == "summary"]
    assert compacted_turns
    assert summary_turns
    assert summary_turns[-1].text.startswith("Earlier conversation summary:")


def test_runtime_uses_stored_token_usage_for_compaction(tmp_path: Path):
    store = _make_store(tmp_path)
    session = store.get_or_create_session(provider="anthropic", model="stub", context_window=150)
    for i in range(8):
        store.create_turn(session.id, "user", text=f"user {i}")
        assistant = store.create_turn(session.id, "assistant", text=f"assistant {i}")
        store.update_turn(assistant.id, input_tokens=120, output_tokens=60)

    from agent.runtime.compaction import compact_if_needed
    compact_if_needed(store, session)
    transcript = store.get_transcript(session.id)
    assert any(turn.compacted for turn in transcript.turns)
    assert any(turn.role == "summary" for turn in transcript.turns)


def test_build_model_messages_omits_compacted_turns(tmp_path: Path):
    store = _make_store(tmp_path)
    session = store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
    old_user = store.create_turn(session.id, "user", text="old user")
    old_assistant = store.create_turn(session.id, "assistant", text="old assistant")
    recent_user = store.create_turn(session.id, "user", text="recent user")
    store.record_compaction(session.id, "summary text", [old_user.id, old_assistant.id])

    messages = store.build_model_messages(session.id)
    texts = [msg.text for msg in messages if msg.text]
    assert "old user" not in texts
    assert "old assistant" not in texts
    assert "recent user" in texts
    assert any(text.startswith("[Compacted summary]") for text in texts)


@pytest.mark.asyncio
async def test_doom_loop_detection_stops_repeated_tool_calls(tmp_path: Path, monkeypatch):
    store = _make_store(tmp_path)
    runtime = ChatRuntime(store)
    session = store.get_or_create_session(provider="anthropic", model="stub", context_window=200)
    tools = [ToolDefinition.from_dict(d) for d in [{"name": "search_players", "description": "", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}}]]
    async def fake_execute(name, input_data):
        return {
            "status": "completed",
            "tool": name,
            "content": json.dumps({"data": []}),
            "error": None,
            "hint": None,
            "duration_ms": 1,
        }
    monkeypatch.setattr("agent.runtime.loop.execute_tool_structured", fake_execute)
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
    runtime = ChatRuntime(store)
    session = store.get_or_create_session(provider="anthropic", model="stub", context_window=200)
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

    transcript = store.get_transcript(session.id)
    assistant_turns = [turn for turn in transcript.turns if turn.role == "assistant"]
    assert events[:2] == ["turn_started", "assistant_started"]
    assert "text_delta" in events
    assert assistant_turns
    assert assistant_turns[-1].status == "interrupted"
    assert "interrupted" in (assistant_turns[-1].error or "").lower()
