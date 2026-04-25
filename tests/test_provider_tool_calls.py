from __future__ import annotations

import logging

from backend.lib.providers.tool_calls import build_tool_use_event, emit_accumulated_tool_calls


def test_build_tool_use_event_parses_valid_json():
    event = build_tool_use_event(
        tool_id="call_1",
        tool_name="lookup",
        arguments='{"player":"Mahomes"}',
        logger=logging.getLogger("test"),
        context="test",
    )

    assert event.id == "call_1"
    assert event.name == "lookup"
    assert event.input == {"player": "Mahomes"}


def test_emit_accumulated_tool_calls_tolerates_invalid_and_incomplete_json(caplog):
    caplog.set_level(logging.WARNING)

    events = emit_accumulated_tool_calls(
        {
            0: {"id": "call_1", "name": "lookup", "arguments": "{bad json"},
            1: {"id": "call_2", "name": "", "arguments": "{}"},
        },
        logger=logging.getLogger("test"),
        context="provider stream",
    )

    assert len(events) == 1
    assert events[0].id == "call_1"
    assert events[0].input == {}
    assert "invalid JSON arguments" in caplog.text
    assert "dropping incomplete tool call" in caplog.text
