"""Tests for the stateless agent loop in `backend/domain/agent/stateless.py`.

The loop is the core primitive behind the Database browser's helper chat:
takes a fresh message list, drives provider streaming + tool dispatch,
yields runtime events without writing anything to the runtime store.

We don't exercise the real provider here — a stub `BaseLLMClient`
yields scripted events so we can pin down the loop's behavior on text,
tool calls, multi-iteration loops, and tool whitelisting.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest

from backend.domain.agent.events import (
    RuntimeErrorEvent,
    TextDeltaEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolPendingEvent,
)
from backend.domain.agent.stateless import (
    ALLOWED_HELPER_TOOLS,
    run_stateless_turn,
)
from backend.domain.providers.base import BaseLLMClient
from backend.domain.providers.errors import LLMError
from backend.domain.providers.types import (
    Message,
    MessageResponse,
    StopReason,
    TextEvent,
    ToolChoice,
    ToolDefinition,
    ToolUseEvent,
)


class _StubClient(BaseLLMClient):
    """LLM client that replays canned per-iteration event scripts.

    Each entry in `scripts` is the events for one call to `stream_message`.
    The first call drains scripts[0], the second drains scripts[1], etc.
    """

    def __init__(self, scripts: list[list]):
        super().__init__(model="stub-model")
        self._scripts = scripts
        self.calls: list[dict] = []

    @property
    def provider_name(self) -> str:
        return "stub"

    def _translate_error(self, exc: Exception) -> LLMError:
        return LLMError(str(exc))

    async def create_message(self, *_args, **_kwargs) -> MessageResponse:
        raise NotImplementedError

    async def stream_message(  # type: ignore[override]
        self,
        messages,
        tools=None,
        system=None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator:
        self.calls.append({
            "messages": list(messages),
            "tool_choice": tool_choice,
        })
        if not self._scripts:
            return
        events = self._scripts.pop(0)

        async def _gen():
            for ev in events:
                yield ev

        async for ev in _gen():
            yield ev
        self.last_stop_reason = StopReason.END_TURN


def _tool_def(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description="", input_schema={})


@pytest.mark.asyncio
async def test_text_only_turn_terminates_after_one_iteration():
    client = _StubClient(scripts=[[TextEvent(text="Hello "), TextEvent(text="world")]])
    events = []
    async for ev in run_stateless_turn(
        messages=[Message(role="user", text="hi")],
        client=client,
        tools=[_tool_def("execute_sql")],
        system="prompt",
    ):
        events.append(ev)

    text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
    assert [e.text for e in text_events] == ["Hello ", "world"]
    # Loop ends after one provider call when no tool calls fired.
    assert len(client.calls) == 1
    # No errors, no pending events for tools.
    assert not any(isinstance(e, ToolPendingEvent) for e in events)
    assert not any(isinstance(e, RuntimeErrorEvent) for e in events)


@pytest.mark.asyncio
async def test_tool_call_then_followup_text(monkeypatch):
    """Iter 1: model calls execute_sql. Iter 2: model emits final text."""
    captured_calls: list[tuple[str, dict]] = []

    async def fake_execute_tool(name, input_data, ctx=None):
        captured_calls.append((name, input_data))
        return json.dumps({"columns": ["a"], "rows": [{"a": 1}], "row_count": 1})

    monkeypatch.setattr(
        "backend.domain.agent.stateless.execute_tool", fake_execute_tool
    )

    client = _StubClient(scripts=[
        [ToolUseEvent(id="toolu_1", name="execute_sql", input={"sql": "SELECT 1 AS a"})],
        [TextEvent(text="That returned 1 row with value 1.")],
    ])
    events = []
    async for ev in run_stateless_turn(
        messages=[Message(role="user", text="run select 1")],
        client=client,
        tools=[_tool_def("execute_sql")],
        system="prompt",
    ):
        events.append(ev)

    assert captured_calls == [("execute_sql", {"sql": "SELECT 1 AS a"})]
    pending = [e for e in events if isinstance(e, ToolPendingEvent)]
    completed = [e for e in events if isinstance(e, ToolCompletedEvent)]
    text = [e for e in events if isinstance(e, TextDeltaEvent)]
    assert len(pending) == 1 and pending[0].name == "execute_sql"
    assert len(completed) == 1 and completed[0].tool_run_id == "toolu_1"
    assert "1 row" in "".join(t.text for t in text)
    # Two iterations: once for the tool call, once for the followup text.
    assert len(client.calls) == 2
    # Iteration 2 should see the user msg + assistant w/ tool_calls + tool_result.
    iter2_msgs = client.calls[1]["messages"]
    roles = [m.role for m in iter2_msgs]
    assert roles == ["user", "assistant", "tool_result"]


@pytest.mark.asyncio
async def test_tool_returning_error_yields_tool_failed(monkeypatch):
    async def fake_execute_tool(name, input_data, ctx=None):
        return json.dumps({"error": "Only SELECT and WITH allowed"})

    monkeypatch.setattr(
        "backend.domain.agent.stateless.execute_tool", fake_execute_tool
    )

    client = _StubClient(scripts=[
        [ToolUseEvent(id="t", name="execute_sql", input={"sql": "DELETE FROM x"})],
        [TextEvent(text="That query isn't allowed.")],
    ])
    events = []
    async for ev in run_stateless_turn(
        messages=[Message(role="user", text="delete")],
        client=client,
        tools=[_tool_def("execute_sql")],
        system="prompt",
    ):
        events.append(ev)

    failed = [e for e in events if isinstance(e, ToolFailedEvent)]
    assert len(failed) == 1
    assert failed[0].error and "SELECT" in failed[0].error
    # Result content is preserved on the event so the slim renderer can show it.
    assert failed[0].result and "Only SELECT" in failed[0].result


@pytest.mark.asyncio
async def test_max_iterations_terminates_with_runtime_error(monkeypatch):
    """Model that keeps calling tools forever should be capped."""
    async def fake_execute_tool(name, input_data, ctx=None):
        return json.dumps({"ok": True})

    monkeypatch.setattr(
        "backend.domain.agent.stateless.execute_tool", fake_execute_tool
    )

    # Every iteration emits a tool call; the loop never gets a "done" turn.
    scripts = [
        [ToolUseEvent(id=f"t{i}", name="execute_sql", input={"sql": "SELECT 1"})]
        for i in range(20)
    ]
    client = _StubClient(scripts=scripts)
    events = []
    async for ev in run_stateless_turn(
        messages=[Message(role="user", text="loop")],
        client=client,
        tools=[_tool_def("execute_sql")],
        system="prompt",
        max_iterations=3,
    ):
        events.append(ev)

    # Three iterations, three tool dispatches, then the cap triggers.
    pending = [e for e in events if isinstance(e, ToolPendingEvent)]
    completed = [e for e in events if isinstance(e, ToolCompletedEvent)]
    errors = [e for e in events if isinstance(e, RuntimeErrorEvent)]
    assert len(pending) == 3
    assert len(completed) == 3
    assert len(errors) == 1
    assert "tool iterations" in errors[0].error.lower()


@pytest.mark.asyncio
async def test_non_whitelisted_tool_rejected(monkeypatch):
    """A misbehaving model that emits set_table should get a ToolFailedEvent
    without the handler ever being invoked."""
    handler_called = []

    async def fake_execute_tool(name, input_data, ctx=None):
        handler_called.append(name)
        return json.dumps({"ok": True})

    monkeypatch.setattr(
        "backend.domain.agent.stateless.execute_tool", fake_execute_tool
    )

    client = _StubClient(scripts=[
        [ToolUseEvent(id="bad", name="set_table", input={"sql": "SELECT 1"})],
        [TextEvent(text="OK")],
    ])
    events = []
    async for ev in run_stateless_turn(
        messages=[Message(role="user", text="x")],
        client=client,
        tools=[_tool_def("set_table")],
        system="prompt",
    ):
        events.append(ev)

    assert handler_called == []  # the rejection short-circuited dispatch
    failed = [e for e in events if isinstance(e, ToolFailedEvent)]
    assert len(failed) == 1
    assert failed[0].name == "set_table"
    assert "not available" in (failed[0].error or "")


@pytest.mark.asyncio
async def test_allowed_helper_tools_set_is_correct():
    # Sanity-check the whitelist constant — anything outside this set
    # would let the helper write to the DB or generate Reports.
    # `run_in_editor` is included: it's a remote-control affordance whose
    # handler does no I/O server-side (the rows are fetched by the
    # browser, through the same sandbox).
    assert ALLOWED_HELPER_TOOLS == frozenset({
        "get_schema", "get_guide", "execute_sql",
        "search_players", "get_player_info",
        "run_in_editor",
    })


@pytest.mark.asyncio
async def test_provider_error_is_surfaced_as_runtime_error_event(monkeypatch):
    class _ExplodingClient(_StubClient):
        async def stream_message(self, *a, **kw):  # type: ignore[override]
            raise LLMError("rate limited")
            # silence "unreachable" linter via an unused yield
            yield  # pragma: no cover

    client = _ExplodingClient(scripts=[])
    events = []
    async for ev in run_stateless_turn(
        messages=[Message(role="user", text="hi")],
        client=client,
        tools=[_tool_def("execute_sql")],
        system="prompt",
    ):
        events.append(ev)
    errors = [e for e in events if isinstance(e, RuntimeErrorEvent)]
    assert len(errors) == 1
    assert "rate limited" in errors[0].error
