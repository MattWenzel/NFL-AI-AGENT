"""Service-level tests for the Database browser feature.

The HTTP layer is exercised in test_database_routes.py; these tests
hit DatabaseService directly with a tmp_path RuntimeStore and a
monkeypatched sandbox so we don't depend on the real nflverse DuckDB.
"""

from __future__ import annotations

import pytest

from backend.application.database import DatabaseQueryError, DatabaseService
from backend.application.tables import TableChatService
from backend.data import RuntimeStore
from backend.domain.tools.sandbox.runner import SQLResult, SQLValidationError


@pytest.fixture
def service(tmp_path):
    store = RuntimeStore(tmp_path / "r.sqlite3")
    table_chat_service = TableChatService(store, exports_dir=tmp_path / "exports")
    return DatabaseService(store=store, table_chat_service=table_chat_service)


@pytest.mark.asyncio
async def test_run_query_passes_sql_through_sandbox(service, monkeypatch):
    captured = {}

    def fake_run(sql):
        captured["sql"] = sql
        return SQLResult(columns=["a"], rows=[{"a": 1}], row_count=1, truncated=False)

    monkeypatch.setattr(
        "backend.application.database.execute_safe_sql", fake_run
    )

    result = await service.run_query("SELECT 1 AS a")
    assert captured["sql"] == "SELECT 1 AS a"
    assert result.columns == ["a"]
    assert result.rows == [{"a": 1}]
    assert result.row_count == 1


@pytest.mark.asyncio
async def test_run_query_translates_validation_error(service, monkeypatch):
    def fake_run(sql):
        raise SQLValidationError("Only SELECT and WITH (CTE) statements are allowed")

    monkeypatch.setattr(
        "backend.application.database.execute_safe_sql", fake_run
    )

    with pytest.raises(DatabaseQueryError) as exc:
        await service.run_query("DELETE FROM x")
    assert "SELECT and WITH" in str(exc.value)


@pytest.mark.asyncio
async def test_save_query_as_report_seeds_table_chat_session(service):
    user = await service.store.create_user(email="t@e.com", password_hash="h")

    session = await service.save_query_as_report(
        sql="SELECT player FROM players LIMIT 2",
        columns=["player", "team"],
        rows=[{"player": "Mahomes", "team": "KC"}, {"player": "Allen", "team": "BUF"}],
        row_count=2,
        truncated=False,
        title="QBs",
        user_id=user.id,
    )

    assert session.kind == "table_chat"
    assert session.title == "QBs"

    state = await service.store.get_table_state(session.id)
    assert state is not None
    assert state.columns == ["player", "team"]
    assert state.rows == [
        {"player": "Mahomes", "team": "KC"},
        {"player": "Allen", "team": "BUF"},
    ]
    assert state.last_sql == "SELECT player FROM players LIMIT 2"
    assert state.row_count == 2
    assert state.truncated is False
    # Brand-new session — no auto-lock at this stage; the user can refine
    # via the agent and lock when they're done.
    assert state.locked is False


def test_list_browseable_tables_projects_schema_response(service, monkeypatch):
    monkeypatch.setattr(
        "backend.application.database.build_schema_response",
        lambda: {
            "tables": {
                "players": {
                    "alias": "p",
                    "columns": [
                        {"name": "player_gsis_id", "type": "VARCHAR"},
                        {"name": "display_name", "type": "VARCHAR"},
                    ],
                    "column_count": 2,
                },
                "combine": {
                    "alias": None,
                    "columns": [{"name": "season", "type": "INTEGER"}],
                    "column_count": 1,
                },
            },
            "joins": [{"a": "x"}],
            "aliases": {"p": "players"},
            "total_tables": 2,
        },
    )

    rows = service.list_browseable_tables()
    # Sorted alphabetically by table name.
    assert [r["name"] for r in rows] == ["combine", "players"]
    # Aliases / joins / column_count are dropped.
    assert rows[1] == {
        "name": "players",
        "columns": [
            {"name": "player_gsis_id", "type": "VARCHAR"},
            {"name": "display_name", "type": "VARCHAR"},
        ],
    }
