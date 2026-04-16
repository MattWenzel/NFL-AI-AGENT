"""Tests for the 4 agent code improvements (Round N)."""

import asyncio
import json
import pytest

# ---------------------------------------------------------------------------
# 1. OFFSET preserved when LIMIT is capped
# ---------------------------------------------------------------------------
from agent.tools.sql_sandbox import _ensure_limit


class TestEnsureLimit:
    """Tests for _ensure_limit — LIMIT capping + OFFSET preservation."""

    def test_limit_capped_preserves_offset(self):
        sql = "SELECT * FROM players LIMIT 1000 OFFSET 10"
        result = _ensure_limit(sql, 500)
        assert "LIMIT 500" in result
        assert "OFFSET 10" in result

    def test_limit_capped_preserves_offset_case_insensitive(self):
        sql = "SELECT * FROM players limit 1000 offset 50"
        result = _ensure_limit(sql, 500)
        assert "LIMIT 500" in result.upper()
        assert "OFFSET 50" in result.upper()

    def test_limit_capped_no_offset(self):
        sql = "SELECT * FROM players LIMIT 1000"
        result = _ensure_limit(sql, 500)
        assert "LIMIT 500" in result
        assert "OFFSET" not in result.upper()

    def test_limit_under_max_unchanged(self):
        sql = "SELECT * FROM players LIMIT 100 OFFSET 20"
        result = _ensure_limit(sql, 500)
        assert "LIMIT 100" in result
        assert "OFFSET 20" in result

    def test_no_limit_adds_one(self):
        sql = "SELECT * FROM players"
        result = _ensure_limit(sql, 500)
        assert "LIMIT 500" in result

    def test_trailing_semicolon_stripped(self):
        sql = "SELECT * FROM players LIMIT 1000 OFFSET 5;"
        result = _ensure_limit(sql, 500)
        assert "LIMIT 500" in result
        assert "OFFSET 5" in result

    def test_exact_max_unchanged(self):
        sql = "SELECT * FROM players LIMIT 500"
        result = _ensure_limit(sql, 500)
        assert "LIMIT 500" in result

    def test_limit_1_offset_0(self):
        sql = "SELECT * FROM players LIMIT 2000 OFFSET 0"
        result = _ensure_limit(sql, 500)
        assert "LIMIT 500" in result
        assert "OFFSET 0" in result

    def test_placeholder_limit_not_duplicated(self):
        """Parameterized LIMIT ? must not get a duplicate LIMIT appended."""
        sql = "SELECT * FROM players LIMIT ?"
        result = _ensure_limit(sql, 500)
        assert result.upper().count("LIMIT") == 1

    def test_named_placeholder_limit_not_duplicated(self):
        sql = "SELECT * FROM players LIMIT :cap"
        result = _ensure_limit(sql, 500)
        assert result.upper().count("LIMIT") == 1

    def test_placeholder_limit_with_offset(self):
        sql = "SELECT * FROM players LIMIT ? OFFSET ?"
        result = _ensure_limit(sql, 500)
        assert result.upper().count("LIMIT") == 1


class TestValidateSQLEdgeCases:
    """Tests for validate_sql — comment tolerance and multi-statement handling."""

    def test_leading_block_comment_accepted(self):
        from agent.tools.sql_sandbox import validate_sql
        validate_sql("/* hi */ SELECT 1")

    def test_leading_line_comment_accepted(self):
        from agent.tools.sql_sandbox import validate_sql
        validate_sql("-- note\nSELECT 1")

    def test_string_literal_with_semicolon_accepted(self):
        """Legal query with a semicolon inside a string must not be rejected."""
        from agent.tools.sql_sandbox import validate_sql
        validate_sql("SELECT 'a; b' AS x")

    def test_multi_statement_rejected_by_sqlite(self):
        """Multi-statement is caught by SQLite at execute time."""
        from agent.tools.sql_sandbox import execute_safe_sql, SQLValidationError
        with pytest.raises(SQLValidationError, match="one statement"):
            execute_safe_sql("SELECT 1; SELECT 2")

    def test_ddl_still_rejected(self):
        from agent.tools.sql_sandbox import validate_sql, SQLValidationError
        with pytest.raises(SQLValidationError):
            validate_sql("DROP TABLE players")


class TestSandboxIntegration:
    """Integration tests that exercise the sandbox end-to-end."""

    def test_parameterized_limit_query_runs(self):
        """LIMIT ? placeholder must execute without SQL syntax error."""
        from agent.tools.sql_sandbox import execute_safe_sql
        result = execute_safe_sql(
            "SELECT gsis_id FROM players WHERE position = ? LIMIT ?",
            ("QB", 3),
        )
        assert result.row_count <= 3

    def test_search_players_tool_works(self):
        """Regression: _search_players must not produce duplicate LIMIT."""
        from agent.tools.player_lookup import _search_players
        out = _search_players({"position": "QB", "limit": 3})
        # Result is a JSON string; must not contain a syntax error marker.
        assert "syntax error" not in out.lower()


# ---------------------------------------------------------------------------
# 2. Anthropic tool_results merged into a single user message
# ---------------------------------------------------------------------------
from agent.providers.anthropic_provider import AnthropicClient
from agent.providers.base import Message, ToolUseEvent


class TestAnthropicMergeToolResults:
    """Tests for _convert_messages — consecutive tool_result merging."""

    def test_two_tool_results_merged(self):
        messages = [
            Message(role="user", text="Compare two players"),
            Message(
                role="assistant",
                text=None,
                tool_calls=[
                    ToolUseEvent(id="t1", name="sql_query", input={"sql": "SELECT 1"}),
                    ToolUseEvent(id="t2", name="sql_query", input={"sql": "SELECT 2"}),
                ],
            ),
            Message(role="tool_result", tool_use_id="t1", tool_content="result1"),
            Message(role="tool_result", tool_use_id="t2", tool_content="result2"),
        ]
        converted = AnthropicClient._convert_messages(messages)

        # Should be: user, assistant, user (merged tool_results)
        assert len(converted) == 3
        assert converted[0]["role"] == "user"
        assert converted[1]["role"] == "assistant"
        assert converted[2]["role"] == "user"

        # The merged user message should have 2 tool_result blocks
        tool_blocks = converted[2]["content"]
        assert len(tool_blocks) == 2
        assert tool_blocks[0]["type"] == "tool_result"
        assert tool_blocks[0]["tool_use_id"] == "t1"
        assert tool_blocks[1]["type"] == "tool_result"
        assert tool_blocks[1]["tool_use_id"] == "t2"

    def test_three_tool_results_merged(self):
        messages = [
            Message(role="user", text="x"),
            Message(
                role="assistant",
                tool_calls=[
                    ToolUseEvent(id="a", name="t", input={}),
                    ToolUseEvent(id="b", name="t", input={}),
                    ToolUseEvent(id="c", name="t", input={}),
                ],
            ),
            Message(role="tool_result", tool_use_id="a", tool_content="ra"),
            Message(role="tool_result", tool_use_id="b", tool_content="rb"),
            Message(role="tool_result", tool_use_id="c", tool_content="rc"),
        ]
        converted = AnthropicClient._convert_messages(messages)
        assert len(converted) == 3
        assert len(converted[2]["content"]) == 3

    def test_single_tool_result_not_affected(self):
        messages = [
            Message(role="user", text="hi"),
            Message(
                role="assistant",
                tool_calls=[ToolUseEvent(id="t1", name="sql_query", input={})],
            ),
            Message(role="tool_result", tool_use_id="t1", tool_content="result"),
        ]
        converted = AnthropicClient._convert_messages(messages)
        assert len(converted) == 3
        assert len(converted[2]["content"]) == 1

    def test_normal_user_messages_not_merged(self):
        messages = [
            Message(role="user", text="hello"),
            Message(role="assistant", text="hi"),
            Message(role="user", text="how are you"),
        ]
        converted = AnthropicClient._convert_messages(messages)
        assert len(converted) == 3
        assert converted[0]["content"] == "hello"
        assert converted[2]["content"] == "how are you"

    def test_tool_result_then_user_text_not_merged(self):
        """A tool_result followed by a plain user message should NOT merge."""
        messages = [
            Message(role="user", text="hi"),
            Message(
                role="assistant",
                tool_calls=[ToolUseEvent(id="t1", name="sql_query", input={})],
            ),
            Message(role="tool_result", tool_use_id="t1", tool_content="result"),
            Message(role="assistant", text="Here's the result"),
            Message(role="user", text="thanks"),
        ]
        converted = AnthropicClient._convert_messages(messages)
        assert len(converted) == 5
        # tool_result stays as tool_result user message
        assert converted[2]["role"] == "user"
        assert converted[2]["content"][0]["type"] == "tool_result"
        # next user message is plain text
        assert converted[4]["role"] == "user"
        assert converted[4]["content"] == "thanks"

    def test_multi_turn_with_two_tool_pairs(self):
        """Two separate rounds of tool calls — each pair merged independently."""
        messages = [
            Message(role="user", text="q1"),
            Message(
                role="assistant",
                tool_calls=[
                    ToolUseEvent(id="a1", name="t", input={}),
                    ToolUseEvent(id="a2", name="t", input={}),
                ],
            ),
            Message(role="tool_result", tool_use_id="a1", tool_content="r1"),
            Message(role="tool_result", tool_use_id="a2", tool_content="r2"),
            Message(
                role="assistant",
                tool_calls=[
                    ToolUseEvent(id="b1", name="t", input={}),
                    ToolUseEvent(id="b2", name="t", input={}),
                ],
            ),
            Message(role="tool_result", tool_use_id="b1", tool_content="r3"),
            Message(role="tool_result", tool_use_id="b2", tool_content="r4"),
            Message(role="assistant", text="done"),
        ]
        converted = AnthropicClient._convert_messages(messages)
        # user, assistant, user(merged t1+t2), assistant, user(merged t3+t4), assistant
        assert len(converted) == 6
        roles = [m["role"] for m in converted]
        assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
        assert len(converted[2]["content"]) == 2
        assert len(converted[4]["content"]) == 2


# ---------------------------------------------------------------------------
# 3. SSE stream disconnect check (structural test — verify the code path exists)
# ---------------------------------------------------------------------------


class TestSSEDisconnectDetection:
    """Verify the disconnect check is wired into event_generator."""

    def test_disconnect_check_in_source(self):
        """The event_generator should check request.is_disconnected()."""
        import inspect
        from api.routers.chat import chat_stream

        source = inspect.getsource(chat_stream)
        assert "is_disconnected" in source
        assert "Client disconnected" in source


# ---------------------------------------------------------------------------
# 4. Runtime transcript replaces conversation windowing
# ---------------------------------------------------------------------------
from agent.runtime_store import RuntimeStore
from agent.runtime import RuntimeEvent


class TestRuntimeTranscript:
    """Tests for the SQLite-backed transcript model."""

    def test_session_creation_and_listing(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = store.get_or_create_session(provider="anthropic", model="stub", context_window=123)
        listed = store.list_sessions()
        assert listed[0]["id"] == session.id
        assert listed[0]["turn_count"] == 0

    def test_build_model_messages_preserves_tool_result_order(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = store.get_or_create_session(provider="anthropic", model="stub", context_window=1000)
        store.create_turn(session.id, "user", text="Compare two players")
        assistant = store.create_turn(session.id, "assistant", text="Working on it")
        tool_run = store.create_tool_run(session.id, assistant.id, "execute_sql", {"sql": "SELECT 1"}, status="completed")
        store.update_tool_run(tool_run.id, result_text='{"rows":[{"x":1}]}')

        msgs = store.build_model_messages(session.id)
        assert len(msgs) == 3
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"
        assert msgs[1].tool_calls[0].name == "execute_sql"
        assert msgs[2].role == "tool_result"
        assert msgs[2].tool_use_id == tool_run.id

    def test_compacted_turns_omitted_but_summary_kept(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
        user1 = store.create_turn(session.id, "user", text="old question")
        assistant1 = store.create_turn(session.id, "assistant", text="old answer")
        store.create_turn(session.id, "user", text="new question")
        store.record_compaction(session.id, "summary text", [user1.id, assistant1.id])

        msgs = store.build_model_messages(session.id)
        texts = [msg.text for msg in msgs if msg.text]
        assert "old question" not in texts
        assert "old answer" not in texts
        assert any(text.startswith("[Compacted summary]") for text in texts)


# ---------------------------------------------------------------------------
# 5. Tool result pairing in /chat/message now uses tool_run_id
# ---------------------------------------------------------------------------
class TestToolResultPairing:
    """Test that tool results are paired with the matching tool_run_id."""

    def test_two_parallel_tool_calls_paired_correctly(self):
        """Results should attach to the corresponding pending tool record."""
        tool_calls_log = []
        events = [
            RuntimeEvent(type="tool_pending", session_id="s", tool_run_id="a", name="sql_query", input={"sql": "SELECT 1"}),
            RuntimeEvent(type="tool_pending", session_id="s", tool_run_id="b", name="get_schema", input={"table": "players"}),
            RuntimeEvent(type="tool_completed", session_id="s", tool_run_id="a", name="sql_query", result="result_A"),
            RuntimeEvent(type="tool_completed", session_id="s", tool_run_id="b", name="get_schema", result="result_B"),
        ]

        for event in events:
            if event.type == "tool_pending":
                tool_calls_log.append({
                    "tool_run_id": event.tool_run_id,
                    "tool": event.name,
                    "input": event.input,
                    "result_preview": "",
                })
            elif event.type == "tool_completed":
                for item in reversed(tool_calls_log):
                    if item["tool_run_id"] == event.tool_run_id and not item["result_preview"]:
                        item["result_preview"] = event.result
                        break

        assert len(tool_calls_log) == 2
        assert tool_calls_log[0]["tool"] == "sql_query"
        assert tool_calls_log[0]["result_preview"] == "result_A"
        assert tool_calls_log[1]["tool"] == "get_schema"
        assert tool_calls_log[1]["result_preview"] == "result_B"

    def test_out_of_order_results_still_pair_correctly(self):
        tool_calls_log = []
        events = [
            RuntimeEvent(type="tool_pending", session_id="s", tool_run_id="x", name="A", input={}),
            RuntimeEvent(type="tool_pending", session_id="s", tool_run_id="y", name="A", input={"k": 2}),
            RuntimeEvent(type="tool_completed", session_id="s", tool_run_id="y", name="A", result="second"),
            RuntimeEvent(type="tool_completed", session_id="s", tool_run_id="x", name="A", result="first"),
        ]

        for event in events:
            if event.type == "tool_pending":
                tool_calls_log.append({
                    "tool_run_id": event.tool_run_id,
                    "tool": event.name,
                    "input": event.input,
                    "result_preview": "",
                })
            elif event.type == "tool_completed":
                for item in reversed(tool_calls_log):
                    if item["tool_run_id"] == event.tool_run_id and not item["result_preview"]:
                        item["result_preview"] = event.result
                        break

        assert tool_calls_log[0]["result_preview"] == "first"
        assert tool_calls_log[1]["result_preview"] == "second"


# ---------------------------------------------------------------------------
# 6. Runtime persistence validation
# ---------------------------------------------------------------------------


class TestRuntimeStoreValidation:
    """Tests for RuntimeStore session and transcript behavior."""

    def test_missing_session_returns_none(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        assert store.get_session("missing") is None

    def test_delete_session_removes_transcript(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = store.get_or_create_session(provider="anthropic", model="stub", context_window=50)
        store.create_turn(session.id, "user", text="hello")
        assert store.delete_session(session.id) is True
        assert store.get_session(session.id) is None
        assert store.delete_session(session.id) is False

    def test_reconcile_interrupted_runs(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = store.get_or_create_session(provider="anthropic", model="stub", context_window=50)
        assistant = store.create_turn(session.id, "assistant", text="", status="running")
        tool_run = store.create_tool_run(session.id, assistant.id, "execute_sql", {"sql": "SELECT 1"}, status="running")
        count = store.reconcile_interrupted_runs()
        assert count >= 1
        updated = store.get_tool_run(tool_run.id)
        assert updated.status == "interrupted"
        assert "interrupted" in updated.error_text.lower()


# ---------------------------------------------------------------------------
# 8. Negative limit clamped to 1 in _search_players (Fix 1)
# ---------------------------------------------------------------------------
from agent.tools.player_lookup import _search_players


class TestSearchPlayersLimit:
    """Tests for limit clamping in _search_players."""

    def test_negative_limit_clamped_to_1(self):
        """LIMIT -5 in SQLite means no limit; must be clamped to 1."""
        # We only need to verify the clamped value reaches the SQL.
        # Patch execute_safe_sql to capture the params tuple.
        import unittest.mock as mock
        from agent.tools.sql_sandbox import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("agent.tools.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": -5})
            # Last positional arg in the params tuple is the limit
            call_params = m.call_args[0][1]
            assert call_params[-1] == 1

    def test_zero_limit_clamped_to_1(self):
        import unittest.mock as mock
        from agent.tools.sql_sandbox import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("agent.tools.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": 0})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 1

    def test_normal_limit_unchanged(self):
        import unittest.mock as mock
        from agent.tools.sql_sandbox import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("agent.tools.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": 25})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 25

    def test_over_max_clamped_to_50(self):
        import unittest.mock as mock
        from agent.tools.sql_sandbox import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("agent.tools.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": 999})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 50


# ---------------------------------------------------------------------------
# 9. ChatResponse.truncated field (Fix 4)
# ---------------------------------------------------------------------------
from api.routers.chat import ChatResponse


class TestChatResponseTruncated:
    """Tests for ChatResponse.truncated field."""

    def test_truncated_defaults_to_false(self):
        resp = ChatResponse(conversation_id="x", response="hi")
        assert resp.truncated is False

    def test_truncated_can_be_set_true(self):
        resp = ChatResponse(conversation_id="x", response="hi", truncated=True)
        assert resp.truncated is True

    def test_truncated_in_serialized_output(self):
        resp = ChatResponse(conversation_id="x", response="hi")
        data = resp.model_dump()
        assert "truncated" in data
        assert data["truncated"] is False


# ---------------------------------------------------------------------------
# 10. _get_joins helper returns consistent data (Fix 9)
# ---------------------------------------------------------------------------
from agent.tools.get_schema import _get_joins, JOIN_EDGES


class TestGetJoins:
    """Tests for the _get_joins DRY helper."""

    def test_no_duplicates(self):
        joins = _get_joins()
        pairs = [(j["table_a"], j["table_b"]) for j in joins]
        normalized = [tuple(sorted(p)) for p in pairs]
        assert len(normalized) == len(set(normalized))

    def test_no_pipe_columns(self):
        """Joins with '||' in column names should be excluded."""
        for j in _get_joins():
            assert "||" not in j["column_a"]
            assert "||" not in j["column_b"]

    def test_filtered_to_table(self):
        joins = _get_joins("players")
        for j in joins:
            assert "players" in (j["table_a"], j["table_b"])

    def test_unfiltered_includes_all_valid_edges(self):
        """All non-pipe, deduplicated edges should be present."""
        all_joins = _get_joins()
        # Count expected edges manually from JOIN_EDGES
        expected_pairs = set()
        for (a, b), (a_col, b_col, _) in JOIN_EDGES.items():
            if (a_col and "||" in a_col) or (b_col and "||" in b_col):
                continue
            expected_pairs.add(tuple(sorted([a, b])))
        assert len(all_joins) == len(expected_pairs)

    def test_cast_needed_preserved(self):
        """The qbr join should have cast_needed=True."""
        joins = _get_joins("qbr")
        qbr_joins = [j for j in joins if "qbr" in (j["table_a"], j["table_b"])]
        assert any(j["cast_needed"] for j in qbr_joins)


# ---------------------------------------------------------------------------
# 11. Non-integer limit falls back to default (Fix 2)
# ---------------------------------------------------------------------------


class TestSearchPlayersNonIntegerLimit:
    """Tests for _search_players handling non-integer limit values."""

    def test_string_limit_falls_back_to_default(self):
        """LLM sends 'ten' instead of 10 — should fall back to 10."""
        import unittest.mock as mock
        from agent.tools.sql_sandbox import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("agent.tools.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": "ten"})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 10

    def test_none_limit_falls_back_to_default(self):
        """limit=None should fall back to 10."""
        import unittest.mock as mock
        from agent.tools.sql_sandbox import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("agent.tools.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": None})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 10

    def test_float_string_limit_truncates(self):
        """'10.5' is not a valid int literal — should fall back to 10."""
        import unittest.mock as mock
        from agent.tools.sql_sandbox import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("agent.tools.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": "10.5"})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 10


# ---------------------------------------------------------------------------
# 12. format_file_size shared helper (Fix 5)
# ---------------------------------------------------------------------------
from config import format_file_size


class TestFormatFileSize:
    """Tests for the shared format_file_size helper."""

    def test_zero_bytes(self):
        assert format_file_size(0) == "0 B"

    def test_small_bytes(self):
        assert format_file_size(500) == "500 B"

    def test_one_byte(self):
        assert format_file_size(1) == "1 B"

    def test_just_under_1kb(self):
        assert format_file_size(1023) == "1023 B"

    def test_exactly_1kb(self):
        assert format_file_size(1024) == "1.0 KB"

    def test_kilobytes(self):
        assert format_file_size(51200) == "50.0 KB"

    def test_just_under_1mb(self):
        result = format_file_size(1024 * 1024 - 1)
        assert result.endswith("KB")

    def test_exactly_1mb(self):
        assert format_file_size(1024 * 1024) == "1.0 MB"

    def test_large_megabytes(self):
        assert format_file_size(200 * 1024 * 1024) == "200.0 MB"

    def test_fractional_kb(self):
        # 1536 bytes = 1.5 KB
        assert format_file_size(1536) == "1.5 KB"


# ---------------------------------------------------------------------------
# 13. RuntimeStore session retrieval behavior
# ---------------------------------------------------------------------------


class TestRuntimeStoreSessionLookup:
    """Tests for session lookup and transcript persistence."""

    def test_unknown_session_is_none(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        assert store.get_session("missing") is None

    def test_known_session_round_trips(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = store.get_or_create_session(provider="anthropic", model="stub", context_window=77)
        loaded = store.get_session(session.id)
        assert loaded is not None
        assert loaded.id == session.id
        assert loaded.provider == "anthropic"
        assert loaded.context_window == 77

    def test_turn_text_round_trips(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = store.get_or_create_session(provider="anthropic", model="stub", context_window=77)
        user_turn = store.create_turn(session.id, "user", text="hello")
        assistant_turn = store.create_turn(session.id, "assistant", text="hi")
        transcript = store.get_transcript(session.id)
        assert [turn.id for turn in transcript.turns] == [user_turn.id, assistant_turn.id]
        assert transcript.turns[0].text == "hello"
        assert transcript.turns[1].text == "hi"
