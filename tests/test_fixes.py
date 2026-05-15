"""Tests for the 4 agent code improvements (Round N)."""

import asyncio
import json
import pytest


def _skip_if_stats_db_locked():
    """Skip a test when the stats DuckDB file isn't openable.

    DuckDB refuses cross-process file access even in read-only mode, so
    if a concurrent NFLVERSE build script holds the lock, every test that
    opens the real DB hard-fails. Integration tests that legitimately need
    the DB should call this first — locked-DB becomes a skip with a clear
    reason instead of a flaky failure.
    """
    import duckdb
    from backend.config import DB_PATH
    try:
        duckdb.connect(str(DB_PATH), read_only=True).close()
    except Exception as exc:
        pytest.skip(f"stats DB unavailable ({exc.__class__.__name__}) — likely a concurrent build")


# ---------------------------------------------------------------------------
# 1. OFFSET preserved when LIMIT is capped
# ---------------------------------------------------------------------------
from backend.domain.tools.sandbox.runner import _ensure_limit


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
        from backend.domain.tools.sandbox import validate_sql
        validate_sql("/* hi */ SELECT 1")

    def test_leading_line_comment_accepted(self):
        from backend.domain.tools.sandbox import validate_sql
        validate_sql("-- note\nSELECT 1")

    def test_string_literal_with_semicolon_accepted(self):
        """Legal query with a semicolon inside a string must not be rejected."""
        from backend.domain.tools.sandbox import validate_sql
        validate_sql("SELECT 'a; b' AS x")

    def test_multi_statement_rejected_by_sqlite(self):
        """Multi-statement is caught by SQLite at execute time."""
        from backend.domain.tools.sandbox.runner import SQLValidationError
        from backend.domain.tools.sandbox import execute_safe_sql
        with pytest.raises(SQLValidationError, match="one statement"):
            execute_safe_sql("SELECT 1; SELECT 2")

    def test_ddl_still_rejected(self):
        from backend.domain.tools.sandbox.runner import SQLValidationError
        from backend.domain.tools.sandbox import validate_sql
        with pytest.raises(SQLValidationError):
            validate_sql("DROP TABLE players")


class TestSandboxIntegration:
    """Integration tests that exercise the sandbox end-to-end."""

    def test_parameterized_limit_query_runs(self):
        """LIMIT ? placeholder must execute without SQL syntax error."""
        _skip_if_stats_db_locked()
        from backend.domain.tools.sandbox import execute_safe_sql
        result = execute_safe_sql(
            "SELECT player_gsis_id FROM players WHERE position = ? LIMIT ?",
            ("QB", 3),
        )
        assert result.row_count <= 3

    def test_search_players_tool_works(self):
        """Regression: _search_players must not produce duplicate LIMIT."""
        _skip_if_stats_db_locked()
        from backend.domain.tools.handlers.player_lookup import _search_players
        out = _search_players({"position": "QB", "limit": 3})
        # Result is a JSON string; must not contain a syntax error marker.
        assert "syntax error" not in out.lower()


class TestClampLimitParam:
    """Tests for _clamp_limit_param — bind-time row-cap enforcement."""

    def test_clamps_above_max(self):
        from backend.domain.tools.sandbox.runner import _clamp_limit_param
        # SQL: one placeholder before the LIMIT, LIMIT itself is `?`.
        out = _clamp_limit_param(
            "SELECT * FROM players WHERE position = ? LIMIT ?",
            ("QB", 999_999),
            max_rows=500,
        )
        assert out == ("QB", 500)

    def test_leaves_values_under_max(self):
        from backend.domain.tools.sandbox.runner import _clamp_limit_param
        out = _clamp_limit_param(
            "SELECT * FROM players WHERE position = ? LIMIT ?",
            ("QB", 5),
            max_rows=500,
        )
        assert out == ("QB", 5)

    def test_no_limit_clause_passthrough(self):
        from backend.domain.tools.sandbox.runner import _clamp_limit_param
        out = _clamp_limit_param(
            "SELECT * FROM players WHERE position = ?",
            ("QB",),
            max_rows=500,
        )
        assert out == ("QB",)

    def test_numeric_limit_not_clamped_here(self):
        """Numeric LIMITs are clamped in _ensure_limit, not here."""
        from backend.domain.tools.sandbox.runner import _clamp_limit_param
        out = _clamp_limit_param(
            "SELECT * FROM players LIMIT 999999",
            (),
            max_rows=500,
        )
        assert out == ()

    def test_live_integration_clamps_oversized_bound(self):
        """End-to-end: a caller passing LIMIT ? with 999_999 gets 500 rows max."""
        _skip_if_stats_db_locked()
        from backend.domain.tools.sandbox import execute_safe_sql
        r = execute_safe_sql(
            "SELECT player_gsis_id FROM players WHERE position = ? LIMIT ?",
            ("QB", 999_999),
        )
        assert r.row_count <= 500


# ---------------------------------------------------------------------------
# 2. Anthropic tool_results merged into a single user message
# ---------------------------------------------------------------------------
from backend.domain.providers.clients.anthropic import AnthropicClient
from backend.domain.providers.types import Message, ToolUseEvent


class TestAnthropicPromptCaching:
    """Regression guard: the cache_control markers must survive refactors.

    Anthropic's prefix-match cache is invisible — stripping the marker is a
    silent ~10x cost regression with no test failure unless we assert the
    markers are still in the built kwargs.
    """

    def _client(self):
        return AnthropicClient(model="claude-sonnet-4-6", api_key="test-key")

    def test_system_prompt_has_cache_control(self):
        kwargs = self._client()._build_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            system="You are a helpful assistant.",
        )
        assert isinstance(kwargs["system"], list)
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_last_message_block_has_cache_control(self):
        kwargs = self._client()._build_kwargs(
            messages=[
                {"role": "user", "content": "turn 1"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "turn 2"},
            ],
            tools=None,
            system=None,
        )
        last_content = kwargs["messages"][-1]["content"]
        assert isinstance(last_content, list)
        assert last_content[-1]["cache_control"] == {"type": "ephemeral"}
        # Earlier messages must not be mutated with extra markers.
        earlier = kwargs["messages"][0]
        if isinstance(earlier["content"], list):
            for block in earlier["content"]:
                assert "cache_control" not in block

    def test_no_system_no_system_kwarg(self):
        kwargs = self._client()._build_kwargs(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            system=None,
        )
        assert "system" not in kwargs


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
        from backend.api.routes.chat import chat_stream

        source = inspect.getsource(chat_stream)
        assert "is_disconnected" in source
        assert "Client disconnected" in source


# ---------------------------------------------------------------------------
# 4. Runtime transcript replaces conversation windowing
# ---------------------------------------------------------------------------
from backend.data import RuntimeStore
from backend.domain.agent.events import ToolCompletedEvent, ToolPendingEvent


class TestRuntimeTranscript:
    """Tests for the SQLite-backed transcript model."""

    async def test_session_creation_and_listing(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=123)
        listed = await store.list_sessions()
        assert listed[0].id == session.id
        assert listed[0].turn_count == 0

    async def test_build_model_messages_preserves_tool_result_order(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=1000)
        await store.create_turn(session.id, "user", text="Compare two players")
        assistant = await store.create_turn(session.id, "assistant", text="Working on it")
        tool_run = await store.create_tool_run(session.id, assistant.id, "execute_sql", {"sql": "SELECT 1"}, status="completed")
        await store.update_tool_run(tool_run.id, result='{"rows":[{"x":1}]}')

        from backend.domain.agent.message_builder import build_model_messages
        msgs = build_model_messages(await store.get_transcript(session.id))
        assert len(msgs) == 3
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"
        assert msgs[1].tool_calls[0].name == "execute_sql"
        assert msgs[2].role == "tool_result"
        assert msgs[2].tool_use_id == tool_run.id

    async def test_compacted_turns_omitted_but_summary_kept(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=10)
        user1 = await store.create_turn(session.id, "user", text="old question")
        assistant1 = await store.create_turn(session.id, "assistant", text="old answer")
        await store.create_turn(session.id, "user", text="new question")
        await store.record_compaction(session.id, "summary text", [user1.id, assistant1.id])

        from backend.domain.agent.message_builder import build_model_messages
        msgs = build_model_messages(await store.get_transcript(session.id))
        texts = [msg.text for msg in msgs if msg.text]
        assert "old question" not in texts
        assert "old answer" not in texts
        # Summary is wrapped in <prior_conversation_summary> so the model treats
        # it as reference context, not something to mimic. See runtime_store.py:883.
        assert any("<prior_conversation_summary>" in text and "summary text" in text for text in texts)


# ---------------------------------------------------------------------------
# 5. Tool result pairing in /chat/message now uses tool_run_id
# ---------------------------------------------------------------------------
class TestCsvExportRegistration:
    """Regression guard: the `register_export` ctx callback bridges sync→async correctly.

    `store.register_export` is an async coroutine; the tool handler runs in
    `asyncio.to_thread`. Without `run_coroutine_threadsafe`, the lambda just
    returns a coroutine object, the DB row never gets written, and the CSV
    stays orphaned on disk. This test exercises the full Turn →
    _execute_one_tool path and asserts the library row lands.
    """

    def test_csv_export_registers_library_row(self, tmp_path, monkeypatch):
        import asyncio
        import json
        from backend.data import RuntimeStore
        from backend.domain.agent.turn import Turn
        from backend.domain.tools.sandbox.runner import SQLResult

        # Point exports at a tmp dir so we don't pollute the repo.
        tmp_exports = tmp_path / "exports"
        monkeypatch.setattr("backend.domain.tools.handlers.create_csv_export.EXPORTS_DIR", tmp_exports)

        # Mock the DuckDB-reading SQL path so this test doesn't depend on
        # the stats DB being available or unlocked. The test is about the
        # register_export bridge — it shouldn't flake on concurrent DB builds.
        def fake_execute_export_sql(sql):
            return SQLResult(
                columns=["n", "name"],
                rows=[{"n": 1, "name": "alice"}, {"n": 2, "name": "bob"}],
                row_count=2,
                truncated=False,
            )
        monkeypatch.setattr("backend.domain.tools.handlers.create_csv_export.execute_export_sql", fake_execute_export_sql)

        async def _run():
            store = RuntimeStore(tmp_path / "runtime.sqlite3")
            session = await store.get_or_create_session(
                None, provider="anthropic", model="claude-sonnet-4-6",
                context_window=200_000, user_id=None,
            )

            async def execute_tool(name, input_data, *, ctx=None):
                # Run the real handler via the real to_thread bridge — that's
                # the code path we need to exercise to catch the sync→async
                # register_export bug.
                from backend.domain.tools.handlers.create_csv_export import _create_csv_export
                result_str = await asyncio.to_thread(_create_csv_export, input_data, ctx)
                result = json.loads(result_str)
                return {
                    "status": "error" if "error" in result else "completed",
                    "content": result_str,
                    "error": result.get("error"),
                    "hint": None,
                    "duration_ms": 0,
                }

            turn = Turn(
                store=store,
                session=session,
                execute_tool=execute_tool,
            )
            turn.begin_iteration()
            await turn.open_assistant_turn()

            from backend.domain.providers.types import ToolUseEvent
            await turn.record_tool_call(ToolUseEvent(
                id="t1", name="create_csv_export",
                input={"sql": "ignored by the mock", "filename": "regression_test_export"},
            ))
            results = await turn.execute_tools()

            assert results[0].is_completed, f"tool errored: {results[0].error}"

            # The critical assertion — a library row landed. Without the
            # sync→async bridge fix in Turn._execute_one_tool, the async
            # register_export coroutine is silently dropped and this row
            # never gets written.
            exports = await store.list_exports()
            assert len(exports) == 1, (
                "CSV export did not register in the library. "
                "Check the sync→async bridge in Turn._execute_one_tool's ctx."
            )
            assert exports[0].title == "regression_test_export"
            assert exports[0].row_count == 2

        asyncio.run(_run())


class TestToolResultPairing:
    """Test that tool results are paired with the matching tool_run_id."""

    def test_two_parallel_tool_calls_paired_correctly(self):
        """Results should attach to the corresponding pending tool record."""
        tool_calls_log = []
        events = [
            ToolPendingEvent(session_id="s", turn_id="t", tool_run_id="a", name="sql_query", input={"sql": "SELECT 1"}, iterations=1),
            ToolPendingEvent(session_id="s", turn_id="t", tool_run_id="b", name="get_schema", input={"table": "players"}, iterations=1),
            ToolCompletedEvent(session_id="s", turn_id="t", tool_run_id="a", name="sql_query", result="result_A", iterations=1),
            ToolCompletedEvent(session_id="s", turn_id="t", tool_run_id="b", name="get_schema", result="result_B", iterations=1),
        ]

        for event in events:
            if isinstance(event, ToolPendingEvent):
                tool_calls_log.append({
                    "tool_run_id": event.tool_run_id,
                    "tool": event.name,
                    "input": event.input,
                    "result_preview": "",
                })
            elif isinstance(event, ToolCompletedEvent):
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
            ToolPendingEvent(session_id="s", turn_id="t", tool_run_id="x", name="A", input={}, iterations=1),
            ToolPendingEvent(session_id="s", turn_id="t", tool_run_id="y", name="A", input={"k": 2}, iterations=1),
            ToolCompletedEvent(session_id="s", turn_id="t", tool_run_id="y", name="A", result="second", iterations=1),
            ToolCompletedEvent(session_id="s", turn_id="t", tool_run_id="x", name="A", result="first", iterations=1),
        ]

        for event in events:
            if isinstance(event, ToolPendingEvent):
                tool_calls_log.append({
                    "tool_run_id": event.tool_run_id,
                    "tool": event.name,
                    "input": event.input,
                    "result_preview": "",
                })
            elif isinstance(event, ToolCompletedEvent):
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

    async def test_missing_session_returns_none(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        assert await store.get_session("missing") is None

    async def test_delete_session_removes_transcript(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=50)
        await store.create_turn(session.id, "user", text="hello")
        assert await store.delete_session(session.id) is True
        assert await store.get_session(session.id) is None
        assert await store.delete_session(session.id) is False

    async def test_reconcile_interrupted_runs(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=50)
        assistant = await store.create_turn(session.id, "assistant", text="", status="running")
        tool_run = await store.create_tool_run(session.id, assistant.id, "execute_sql", {"sql": "SELECT 1"}, status="running")
        count = await store.reconcile_interrupted_runs()
        assert count >= 1
        updated = await store.get_tool_run(tool_run.id)
        assert updated.status == "interrupted"
        assert "interrupted" in updated.error.lower()


# ---------------------------------------------------------------------------
# 8. Negative limit clamped to 1 in _search_players (Fix 1)
# ---------------------------------------------------------------------------
from backend.domain.tools.handlers.player_lookup import _search_players


class TestSearchPlayersLimit:
    """Tests for limit clamping in _search_players."""

    def test_negative_limit_clamped_to_1(self):
        """LIMIT -5 in SQLite means no limit; must be clamped to 1."""
        # We only need to verify the clamped value reaches the SQL.
        # Patch execute_safe_sql to capture the params tuple.
        import unittest.mock as mock
        from backend.domain.tools.sandbox.runner import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("backend.domain.tools.handlers.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": -5})
            # Last positional arg in the params tuple is the limit
            call_params = m.call_args[0][1]
            assert call_params[-1] == 1

    def test_zero_limit_clamped_to_1(self):
        import unittest.mock as mock
        from backend.domain.tools.sandbox.runner import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("backend.domain.tools.handlers.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": 0})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 1

    def test_normal_limit_unchanged(self):
        import unittest.mock as mock
        from backend.domain.tools.sandbox.runner import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("backend.domain.tools.handlers.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": 25})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 25

    def test_over_max_clamped_to_50(self):
        import unittest.mock as mock
        from backend.domain.tools.sandbox.runner import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("backend.domain.tools.handlers.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": 999})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 50


# ---------------------------------------------------------------------------
# 9. ChatResponse.truncated field (Fix 4)
# ---------------------------------------------------------------------------
from backend.api.routes.chat import ChatResponse


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
from backend.domain.tools.sandbox.schema import _get_joins
from backend.domain.tools.sandbox.schema_metadata import JOIN_EDGES


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

    def test_no_cast_needed_after_id_normalization(self):
        """After the 2026-04-23 ID normalization, every join lines up on a
        single VARCHAR column — no edge should need a CAST anymore.

        Kept as a regression guard: if a future schema change reintroduces a
        type mismatch, surface it here before it becomes an LLM-visible pitfall.
        """
        from backend.domain.tools.sandbox.schema_metadata import JOIN_EDGES
        cast_edges = [(a, b) for (a, b), (_, _, cast) in JOIN_EDGES.items() if cast]
        assert cast_edges == [], f"Unexpected CAST-required edges: {cast_edges}"


# ---------------------------------------------------------------------------
# 11. Non-integer limit falls back to default (Fix 2)
# ---------------------------------------------------------------------------


class TestSearchPlayersNonIntegerLimit:
    """Tests for _search_players handling non-integer limit values."""

    def test_string_limit_falls_back_to_default(self):
        """LLM sends 'ten' instead of 10 — should fall back to 10."""
        import unittest.mock as mock
        from backend.domain.tools.sandbox.runner import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("backend.domain.tools.handlers.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": "ten"})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 10

    def test_none_limit_falls_back_to_default(self):
        """limit=None should fall back to 10."""
        import unittest.mock as mock
        from backend.domain.tools.sandbox.runner import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("backend.domain.tools.handlers.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": None})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 10

    def test_float_string_limit_truncates(self):
        """'10.5' is not a valid int literal — should fall back to 10."""
        import unittest.mock as mock
        from backend.domain.tools.sandbox.runner import SQLResult

        dummy = SQLResult(rows=[], columns=[], row_count=0, truncated=False)
        with mock.patch("backend.domain.tools.handlers.player_lookup.execute_safe_sql", return_value=dummy) as m:
            _search_players({"name": "Test", "limit": "10.5"})
            call_params = m.call_args[0][1]
            assert call_params[-1] == 10


# ---------------------------------------------------------------------------
# 12. format_file_size shared helper (Fix 5)
# ---------------------------------------------------------------------------
from backend.config import format_file_size


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

    async def test_unknown_session_is_none(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        assert await store.get_session("missing") is None

    async def test_known_session_round_trips(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=77)
        loaded = await store.get_session(session.id)
        assert loaded is not None
        assert loaded.id == session.id
        assert loaded.provider == "anthropic"
        assert loaded.context_window == 77

    async def test_turn_text_round_trips(self, tmp_path):
        store = RuntimeStore(tmp_path / "runtime.sqlite3")
        session = await store.get_or_create_session(provider="anthropic", model="stub", context_window=77)
        user_turn = await store.create_turn(session.id, "user", text="hello")
        assistant_turn = await store.create_turn(session.id, "assistant", text="hi")
        transcript = await store.get_transcript(session.id)
        assert [turn.id for turn in transcript.turns] == [user_turn.id, assistant_turn.id]
        assert transcript.turns[0].text == "hello"
        assert transcript.turns[1].text == "hi"
