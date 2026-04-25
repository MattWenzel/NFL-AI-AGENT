"""Tests for LLM-backed compaction summarization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.providers.base import BaseLLMClient
from backend.providers.errors import LLMError
from backend.providers.types import (
    MessageResponse,
    StopReason,
    TextEvent,
    Usage,
)
from backend.storage import RuntimeStore, ToolRunRecord, TurnRecord

from backend.agent.compaction import compact_if_needed
from backend.agent.compaction.summarizer import (
    SUMMARIZER_INPUT_BUDGET_TOKENS,
    _build_summarizer_input,
    summarize_for_compaction,
)


class RecordingStubClient(BaseLLMClient):
    """Captures the messages/system/model passed to create_message and replays canned text."""

    def __init__(self, response_text: str = "- goal: explore Mahomes stats\n- finding: 4839 yards in 2022"):
        super().__init__("parent-model")
        self._response_text = response_text
        self.seen_model: str | None = None
        self.seen_system: str | None = None
        self.seen_messages = None

    @property
    def provider_name(self) -> str:
        return "stub"

    def _translate_error(self, exc: Exception):
        return LLMError(str(exc))

    async def create_message(self, messages, tools=None, system=None, model=None) -> MessageResponse:
        self.seen_messages = messages
        self.seen_system = system
        self.seen_model = model
        return MessageResponse(
            content=[TextEvent(text=self._response_text)],
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=100, output_tokens=20),
        )

    async def stream_message(self, messages, tools=None, system=None):
        raise AssertionError("summarizer should not stream")


class FailingStubClient(RecordingStubClient):
    async def create_message(self, messages, tools=None, system=None, model=None) -> MessageResponse:
        raise LLMError("simulated provider outage")


def _make_turns_and_tools():
    """Fabricate a small window of turns + tool runs."""
    turns = [
        TurnRecord(
            id="t1", session_id="s1", role="user", status="completed",
            text="How many yards did Mahomes throw for in 2022?",
            created_at="2024-01-01", updated_at="2024-01-01",
        ),
        TurnRecord(
            id="t2", session_id="s1", role="assistant", status="completed",
            text="Let me check the database.",
            created_at="2024-01-01", updated_at="2024-01-01",
            input_tokens=50, output_tokens=10,
        ),
    ]
    tool_runs_by_turn = {
        "t2": [
            ToolRunRecord(
                id="tr1", session_id="s1", turn_id="t2",
                tool_name="execute_sql",
                input={"sql": "SELECT SUM(passing_yards) FROM game_stats WHERE player_id='00-0033873' AND season=2022"},
                status="completed",
                result=json.dumps({"rows": [{"total": 4839}], "total": 1}),
                error=None, hint=None, duration_ms=25, compacted=False,
                created_at="2024-01-01", updated_at="2024-01-01",
            ),
        ],
    }
    return turns, tool_runs_by_turn


@pytest.mark.asyncio
async def test_summarize_for_compaction_uses_model_override():
    client = RecordingStubClient()
    turns, tool_runs = _make_turns_and_tools()
    summary = await summarize_for_compaction(
        client,
        summarizer_model="haiku-summarizer",
        source_turns=turns,
        tool_runs_by_turn=tool_runs,
    )
    assert "Mahomes" in summary or "4839" in summary
    assert client.seen_model == "haiku-summarizer"
    assert client.seen_system is not None
    # The flattened user message should contain the tool result so the summarizer
    # can preserve the actual finding, not just the tool name.
    flattened = client.seen_messages[0].text
    assert "4839" in flattened


@pytest.mark.asyncio
async def test_summarize_for_compaction_raises_on_empty_response():
    client = RecordingStubClient(response_text="   ")
    turns, tool_runs = _make_turns_and_tools()
    with pytest.raises(RuntimeError, match="empty"):
        await summarize_for_compaction(
            client,
            summarizer_model=None,
            source_turns=turns,
            tool_runs_by_turn=tool_runs,
        )


def test_build_summarizer_input_trims_oversize_tool_results():
    """When concatenated input exceeds the budget, tool results are truncated."""
    huge_result = "x" * 400_000  # well over 60K tokens at 4 chars/token
    turn = TurnRecord(
        id="t1", session_id="s1", role="assistant", status="completed",
        text="", created_at="", updated_at="",
    )
    tool_run = ToolRunRecord(
        id="tr1", session_id="s1", turn_id="t1",
        tool_name="execute_sql",
        input={"sql": "select *"},
        status="completed",
        result=huge_result,
        error=None, hint=None, duration_ms=1, compacted=False,
        created_at="", updated_at="",
    )
    flattened = _build_summarizer_input([turn], {"t1": [tool_run]})
    # Should be far smaller than the raw result after truncation
    assert len(flattened) < 100_000
    assert "truncated" in flattened.lower()


@pytest.mark.asyncio
async def test_compact_falls_back_to_heuristic_when_llm_fails(tmp_path: Path):
    """A failing summarizer client must not block compaction."""
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
    for i in range(10):
        await store.create_turn(session.id, "user", text=f"user question {i} " * 20)
        await store.create_turn(session.id, "assistant", text=f"assistant answer {i} " * 20)

    client = FailingStubClient()
    info = await compact_if_needed(store, session, client)
    assert info is not None
    assert info["summary_source"] == "heuristic"
    transcript = await store.get_transcript(session.id)
    summary_turn = next(t for t in transcript.turns if t.role == "summary")
    assert summary_turn.text.startswith("Earlier conversation summary:")


@pytest.mark.asyncio
async def test_compact_uses_llm_summary_when_client_succeeds(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
    for i in range(10):
        await store.create_turn(session.id, "user", text=f"user question {i} " * 20)
        await store.create_turn(session.id, "assistant", text=f"assistant answer {i} " * 20)

    client = RecordingStubClient(response_text="MEMO: explored passing yards by season")
    info = await compact_if_needed(store, session, client)
    assert info is not None
    assert info["summary_source"] == "llm"
    # Anthropic provider declares haiku as its summarizer; we pinned that above.
    assert client.seen_model == "claude-haiku-4-5-20251001"
    transcript = await store.get_transcript(session.id)
    summary_turn = next(t for t in transcript.turns if t.role == "summary")
    assert "MEMO" in summary_turn.text
