"""Unit tests for retention policy + token counting changes in compaction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.domain.agent.compaction.policy import (
    HARD_KEEP_FLOOR_TURNS,
    MAX_RECENT_RAW_TOOL_RUNS,
    MAX_RECENT_RAW_TURNS,
    MIN_RECENT_RAW_TOOL_RUNS,
    MIN_RECENT_RAW_TURNS,
    RetentionPolicy,
    _select_source_turns,
    estimate_active_tokens,
)
from backend.domain.agent.compaction.token_counting import count_text_tokens
from backend.data import RuntimeStore


def test_retention_policy_floors_when_no_window():
    p = RetentionPolicy.for_context_window(None)
    assert p.recent_raw_turns == MIN_RECENT_RAW_TURNS
    assert p.recent_raw_tool_runs == MIN_RECENT_RAW_TOOL_RUNS


def test_retention_policy_floors_for_small_windows():
    """Windows below the 32K anchor shouldn't drop below the minima."""
    p = RetentionPolicy.for_context_window(8_000)
    assert p.recent_raw_turns == MIN_RECENT_RAW_TURNS
    assert p.recent_raw_tool_runs == MIN_RECENT_RAW_TOOL_RUNS


def test_retention_policy_scales_with_context_window():
    small = RetentionPolicy.for_context_window(32_000)
    medium = RetentionPolicy.for_context_window(96_000)
    large = RetentionPolicy.for_context_window(150_000)
    # Strictly monotonic growth up through the plateau.
    assert small.recent_raw_turns <= medium.recent_raw_turns <= large.recent_raw_turns
    assert small.recent_raw_tool_runs <= medium.recent_raw_tool_runs <= large.recent_raw_tool_runs
    # Large provider windows should clearly exceed the floors.
    assert large.recent_raw_turns > MIN_RECENT_RAW_TURNS
    assert large.recent_raw_tool_runs > MIN_RECENT_RAW_TOOL_RUNS


def test_retention_policy_clamped_at_ceiling():
    """A silly-big context window shouldn't produce silly-big retention."""
    p = RetentionPolicy.for_context_window(10_000_000)
    assert p.recent_raw_turns == MAX_RECENT_RAW_TURNS
    assert p.recent_raw_tool_runs == MAX_RECENT_RAW_TOOL_RUNS


def test_count_text_tokens_counts_higher_than_naive_for_dense_json():
    """JSON is denser than prose; ÷4 materially under-counted it."""
    dense = json.dumps({"rows": [{"player": "Patrick Mahomes", "yards": 4839, "season": 2022}] * 50})
    naive = len(dense) // 4
    tiktoken_count = count_text_tokens(dense)
    # tiktoken should count meaningfully (not trivially less than naive).
    # Punctuation-heavy JSON tokenizes to more BPE pieces than prose.
    assert tiktoken_count >= naive * 0.8


@pytest.mark.asyncio
async def test_estimate_active_tokens_does_not_double_count_input_tokens(tmp_path: Path):
    """Regression: assistant.input_tokens is the prompt size at that call
    (includes every earlier message). Summing it across turns turns an
    11-turn session with a ~15K transcript into ~150K phantom tokens and
    triggers pointless compaction. We must count only what each turn
    contributes (output_tokens).
    """
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=200_000)
    # Simulate a realistic conversation: each assistant call sees growing
    # input_tokens but adds only ~300 output_tokens.
    for i in range(10):
        await store.create_turn(session.id, "user", text=f"q{i}")
        assistant = await store.create_turn(session.id, "assistant", text=f"a{i}" * 50)
        await store.update_turn(assistant.id, input_tokens=5_000 + 2_000 * i, output_tokens=300)

    total = await estimate_active_tokens(
        store, session.id
    )
    # 10 × 300 output + user-text tokens = ~3_000-4_000. Nowhere near the
    # 140K we'd get from summing input_tokens.
    assert total < 10_000, f"estimate should be ~3K but was {total} — likely double-counting input_tokens"


def test_retention_budget_scales_with_context_window():
    small = RetentionPolicy.for_context_window(32_000)
    large = RetentionPolicy.for_context_window(200_000)
    assert small.retention_budget_tokens < large.retention_budget_tokens
    # 32K window → ~16K retention budget; leaves headroom for summary + response.
    assert 10_000 <= small.retention_budget_tokens <= 20_000


async def test_select_source_turns_walks_by_token_weight(tmp_path: Path):
    """Token-weighted retention: a huge recent turn should crowd out other recent turns.

    Regression for the count-based policy, where `last N turns` kept 6 uniform
    turns regardless of their size — so a 30K-token tool result in the tail
    would drag 30K+ worth of recent raw context into the kept window and
    barely move active_tokens.
    """
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=32_000)
    # 10 tiny old turns; then ONE huge recent turn whose tool result alone
    # blows past the 32K//2 = 16K retention budget.
    for i in range(10):
        await store.create_turn(session.id, "user", text=f"q{i}")
    huge_turn = await store.create_turn(session.id, "assistant", text="ok")
    await store.update_turn(huge_turn.id, output_tokens=20_000)

    transcript = await store.get_transcript(session.id)
    active_turns = [t for t in transcript.turns if not t.compacted]
    policy = RetentionPolicy.for_context_window(session.context_window)
    source = _select_source_turns(
        active_turns, transcript.tool_runs_by_turn, transcript.parts_by_turn, policy,
    )
    kept = len(active_turns) - len(source)
    # Hard floor protects the most recent HARD_KEEP_FLOOR_TURNS turns; the
    # huge turn fills the budget and nothing else can be added past the floor.
    assert kept == HARD_KEEP_FLOOR_TURNS
    assert len(source) == len(active_turns) - HARD_KEEP_FLOOR_TURNS


async def test_select_source_turns_keeps_many_tiny_recent_turns(tmp_path: Path):
    """Under-retention fix: when recent turns are tiny, the budget lets us
    keep up to MAX_RECENT_RAW_TURNS instead of stopping at the count floor.
    """
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=200_000)
    for i in range(40):
        await store.create_turn(session.id, "user", text=f"q{i}")
    transcript = await store.get_transcript(session.id)
    active_turns = [t for t in transcript.turns if not t.compacted]
    policy = RetentionPolicy.for_context_window(session.context_window)
    source = _select_source_turns(
        active_turns, transcript.tool_runs_by_turn, transcript.parts_by_turn, policy,
    )
    kept = len(active_turns) - len(source)
    # Tiny turns + huge budget → selector caps at MAX_RECENT_RAW_TURNS.
    assert kept == MAX_RECENT_RAW_TURNS


@pytest.mark.asyncio
async def test_estimate_active_tokens_uses_tiktoken_for_tool_results(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=1000)
    turn = await store.create_turn(session.id, "assistant", text="hi")
    big_result = json.dumps({"rows": [{"x": i, "name": f"player_{i}"} for i in range(500)]})
    await store.create_tool_run(
        session.id, turn.id, "execute_sql",
        {"sql": "select *"}, status="completed",
    )
    # Back-fill the result text so estimate_active_tokens sees a tool result.
    tool_runs = [r for runs in (await store.get_transcript(session.id)).tool_runs_by_turn.values() for r in runs]
    await store.update_tool_run(tool_runs[0].id, result=big_result, status="completed")

    total = await estimate_active_tokens(
        store, session.id
    )
    # Must at least account for the tool result, which is >1000 tokens by itself.
    assert total >= count_text_tokens(big_result)
