"""Unit tests for the per-user-turn doom-loop detector.

The detector is scoped to a single user message: callers pass the
list of tool_runs emitted across the inner iterations of one turn. A
follow-up turn that re-runs the same query ("try again", "rerun that")
starts with a fresh empty list and must not inherit the prior turn's
fingerprints.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.events import RuntimeLoopError
from agent.turn import DOOM_LOOP_MATCH, raise_if_doom_loop


@dataclass
class FakeToolRun:
    """Stand-in for ToolRunRecord — the detector only reads two fields."""
    tool_name: str
    input: dict


def _sql(q: str) -> FakeToolRun:
    return FakeToolRun(tool_name="execute_sql", input={"sql": q})


def test_fires_on_three_identical_calls():
    runs = [_sql("SELECT 1"), _sql("SELECT 1"), _sql("SELECT 1")]
    with pytest.raises(RuntimeLoopError, match="execute_sql"):
        raise_if_doom_loop(runs)


def test_does_not_fire_on_two_identical_calls():
    """Regression: two identical calls in a single turn must not trip the
    tail-of-3 check (bug was double-counting via store + local list)."""
    runs = [_sql("SELECT 1"), _sql("SELECT 1")]
    raise_if_doom_loop(runs)


def test_ignores_different_inputs():
    runs = [_sql(f"SELECT {i}") for i in range(5)]
    raise_if_doom_loop(runs)


def test_ignores_different_tools():
    runs = [
        FakeToolRun("search_players", {"name": "M"}),
        _sql("SELECT 1"),
        _sql("SELECT 1"),
    ]
    raise_if_doom_loop(runs)


def test_empty_list_does_nothing():
    raise_if_doom_loop([])


def test_fires_even_with_non_identical_earlier_calls():
    """Three identical at the tail trip it, even if the head was mixed."""
    runs = [
        FakeToolRun("search_players", {"name": "M"}),
        _sql("SELECT 1"),
        _sql("SELECT 2"),
        _sql("SELECT 2"),
        _sql("SELECT 2"),
    ]
    with pytest.raises(RuntimeLoopError):
        raise_if_doom_loop(runs)


def test_match_threshold_exposed():
    """Changing DOOM_LOOP_MATCH would change behavior — surface it."""
    assert DOOM_LOOP_MATCH == 3
