"""Smoke tests for the table-view chat feature.

Covers the parts most likely to break under future edits:
- migration 0007 actually runs (`kind` column present, `table_states` table created)
- the `set_table` tool handler clamps rows, persists via the ctx callback,
  and refuses to run without a callback
- the `TableChatService` end-to-end: create → upsert table state → save to
  Reports → ExportRecord exists and contains the rows that were written
- ChatService whitelists tools by mode (no `set_table` outside edit mode,
  only `set_table` in edit mode)
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from backend.application.chat import ChatService
from backend.application.tables import (
    TableChatNotFoundError,
    TableChatService,
    TableNotReadyError,
)
from backend.data import RuntimeStore, SessionRecord
from backend.domain.tools.handlers.set_table import _set_table


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------

def test_migration_0007_adds_kind_and_creates_table_states(tmp_path):
    store = RuntimeStore(tmp_path / "r.sqlite3")
    with store._sync_engine.connect() as conn:
        version = conn.execute(text("PRAGMA user_version")).scalar()
        sessions_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(sessions)"))}
        ts_exists = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='table_states'"
        )).first() is not None
    # Bump in lockstep with `MIGRATIONS` length in backend/data/migrations.py.
    assert version == 8
    assert "kind" in sessions_cols
    assert "source_session_id" in sessions_cols
    assert ts_exists


# --------------------------------------------------------------------------
# set_table tool handler
# --------------------------------------------------------------------------

class _FakeSqlResult:
    def __init__(self, columns, rows, truncated=False):
        self.columns = columns
        self.rows = rows
        self.row_count = len(rows)
        self.truncated = truncated


def test_set_table_clamps_to_table_max_rows(monkeypatch):
    captured = {}

    def fake_run(sql, max_rows):
        captured["max_rows"] = max_rows
        return _FakeSqlResult(["a"], [{"a": 1}])

    monkeypatch.setattr(
        "backend.domain.tools.handlers.set_table.execute_table_sql", fake_run
    )

    persisted = {}

    def persist(*, columns, rows, sql, row_count, truncated):
        persisted["columns"] = columns
        persisted["rows"] = rows
        persisted["sql"] = sql
        persisted["row_count"] = row_count
        persisted["truncated"] = truncated

    out = _set_table(
        {"sql": "SELECT 1 AS a"},
        {"persist_table": persist, "table_max_rows": 50},
    )
    payload = json.loads(out)
    assert payload["status"] == "success"
    assert payload["row_count"] == 1
    assert payload["columns"] == ["a"]
    assert captured["max_rows"] == 50
    assert persisted["columns"] == ["a"]
    assert persisted["rows"] == [{"a": 1}]
    assert persisted["sql"] == "SELECT 1 AS a"


def test_set_table_refuses_without_persist_callback():
    out = _set_table({"sql": "SELECT 1 AS a"}, ctx=None)
    assert json.loads(out)["error"].startswith("set_table is only available")


def test_set_table_rejects_blank_sql():
    out = _set_table({"sql": "   "}, {"persist_table": lambda **_: None})
    assert "Missing required `sql`" in json.loads(out)["error"]


# --------------------------------------------------------------------------
# TableChatService end-to-end
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_table_chat_service_save_creates_export_record(tmp_path, monkeypatch):
    # Skip provider availability checks — tests don't go through the real LLM.
    from backend.domain.providers import types as provider_types  # noqa: F401

    store = RuntimeStore(tmp_path / "r.sqlite3")
    user = await store.create_user(email="t@e.com", password_hash="h")
    service = TableChatService(store, exports_dir=tmp_path / "exports")

    session = await service.create_table_chat(user_id=user.id, title="QB leaders")
    assert session.kind == "table_chat"

    # Simulate the agent's set_table tool persisting to table_states.
    await store.upsert_table_state(
        session.id,
        columns=["player", "yards"],
        rows=[{"player": "Mahomes", "yards": 4839}, {"player": "Allen", "yards": 4544}],
        last_sql="SELECT player, yards FROM s",
        row_count=2,
        truncated=False,
    )

    # save_to_reports should write a CSV and register an ExportRecord.
    record = await service.save_to_reports(session.id, user_id=user.id)
    assert record.title == "QB leaders"
    assert record.row_count == 2
    assert record.columns == ["player", "yards"]
    assert (tmp_path / "exports" / record.filename).exists()

    csv_text = (tmp_path / "exports" / record.filename).read_text()
    assert "player,yards" in csv_text.splitlines()[0]
    assert any("Mahomes" in line for line in csv_text.splitlines())


@pytest.mark.asyncio
async def test_table_chat_service_save_without_table_raises(tmp_path):
    store = RuntimeStore(tmp_path / "r.sqlite3")
    user = await store.create_user(email="t@e.com", password_hash="h")
    service = TableChatService(store, exports_dir=tmp_path / "exports")
    session = await service.create_table_chat(user_id=user.id)
    with pytest.raises(TableNotReadyError):
        await service.save_to_reports(session.id, user_id=user.id)


@pytest.mark.asyncio
async def test_table_chat_service_rejects_regular_chats(tmp_path):
    store = RuntimeStore(tmp_path / "r.sqlite3")
    user = await store.create_user(email="t@e.com", password_hash="h")
    service = TableChatService(store, exports_dir=tmp_path / "exports")
    # Regular chat (kind defaults to 'chat'), not a table chat.
    plain = await store.get_or_create_session(user_id=user.id, kind="chat")
    with pytest.raises(TableChatNotFoundError):
        await service.get_table_chat(plain.id, user_id=user.id)
    with pytest.raises(TableChatNotFoundError):
        await service.save_to_reports(plain.id, user_id=user.id)


# --------------------------------------------------------------------------
# ChatService tool whitelist by table_mode
# --------------------------------------------------------------------------

def _stub_session(kind: str) -> SessionRecord:
    return SessionRecord(
        id="s1",
        created_at="2026-04-26",
        updated_at="2026-04-26",
        provider="anthropic",
        model="claude",
        title=None,
        context_window=200_000,
        pinned_at=None,
        source_csv_id=None,
        user_id=1,
        kind=kind,
    )


def _tool_names(tools):
    return {t.name for t in tools}


def test_chat_service_tool_whitelist_for_regular_chat():
    sess = _stub_session("chat")
    mode = ChatService._resolve_table_mode(sess, requested_mode=None)
    assert mode is None
    tools = ChatService._tools_for_mode(mode)
    assert "set_table" not in _tool_names(tools)
    assert "execute_sql" in _tool_names(tools)


def test_chat_service_table_explore_excludes_set_table():
    sess = _stub_session("table_chat")
    mode = ChatService._resolve_table_mode(sess, requested_mode="explore")
    assert mode == "explore"
    tools = ChatService._tools_for_mode(mode)
    assert "set_table" not in _tool_names(tools)
    assert "execute_sql" in _tool_names(tools)


def test_chat_service_table_edit_only_set_table():
    sess = _stub_session("table_chat")
    mode = ChatService._resolve_table_mode(sess, requested_mode="edit_table")
    assert mode == "edit_table"
    tools = ChatService._tools_for_mode(mode)
    assert _tool_names(tools) == {"set_table"}


def test_chat_service_defaults_table_chat_to_explore():
    """When the request omits table_mode but the session is a table_chat,
    fall back to 'explore' so the dangerous set_table tool isn't exposed."""
    sess = _stub_session("table_chat")
    mode = ChatService._resolve_table_mode(sess, requested_mode=None)
    assert mode == "explore"


def test_chat_service_ignores_table_mode_on_regular_chats():
    sess = _stub_session("chat")
    # A misbehaving client shouldn't be able to opt a regular chat into
    # set_table by faking table_mode.
    mode = ChatService._resolve_table_mode(sess, requested_mode="edit_table")
    assert mode is None
    tools = ChatService._tools_for_mode(mode)
    assert "set_table" not in _tool_names(tools)
