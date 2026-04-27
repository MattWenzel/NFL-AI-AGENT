"""HTTP-level tests for the Database browser endpoints.

The sandbox is monkeypatched so these tests don't depend on the real
nflverse DuckDB being present — we're exercising routing, auth, CSRF,
and request/response shape, not the SQL engine itself.
"""

from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

from backend.api.routes.auth import router as auth_router
from backend.api.routes.database import router as database_router
from backend.application import auth as auth_service_module
from backend.data import RuntimeStore
from backend.domain.auth import encryption
from backend.domain.tools.sandbox.runner import SQLResult, SQLValidationError
from tests.app_factory import build_test_app, managed_test_client


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encryption.reset_cache()
    yield
    encryption.reset_cache()


@pytest.fixture(autouse=True)
def _clear_invite_code(monkeypatch):
    monkeypatch.setattr(auth_service_module, "REGISTRATION_INVITE_CODE", None)


@pytest.fixture
def store(tmp_path):
    return RuntimeStore(tmp_path / "r.sqlite3")


@pytest.fixture
def client(store):
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    app = build_test_app(runtime_store=store)
    app.include_router(auth_router)
    app.include_router(database_router)
    with managed_test_client(app) as client:
        yield client


def _register_and_get_csrf(client) -> str:
    r = client.post(
        "/auth/register", json={"email": "a@b.com", "password": "pw12345678"}
    )
    assert r.status_code == 201, r.text
    csrf = client.cookies.get("csrf_token")
    assert csrf
    return csrf


def test_query_requires_auth(client):
    # No registration → no session cookie → 401.
    r = client.post("/database/query", json={"sql": "SELECT 1"})
    assert r.status_code == 401


def test_query_requires_csrf(client):
    _register_and_get_csrf(client)
    r = client.post("/database/query", json={"sql": "SELECT 1"})
    # Session cookie present but no X-CSRF-Token header.
    assert r.status_code == 403


def test_query_returns_columns_and_rows(client, monkeypatch):
    csrf = _register_and_get_csrf(client)

    def fake_run(sql):
        assert sql == "SELECT 1 AS a, 2 AS b"
        return SQLResult(
            columns=["a", "b"],
            rows=[{"a": 1, "b": 2}],
            row_count=1,
            truncated=False,
        )

    monkeypatch.setattr(
        "backend.application.database.execute_safe_sql", fake_run
    )

    r = client.post(
        "/database/query",
        json={"sql": "SELECT 1 AS a, 2 AS b"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["a", "b"]
    assert body["rows"] == [{"a": 1, "b": 2}]
    assert body["row_count"] == 1
    assert body["truncated"] is False


def test_query_400_on_validation_error(client, monkeypatch):
    csrf = _register_and_get_csrf(client)

    def fake_run(sql):
        raise SQLValidationError("Only SELECT and WITH (CTE) statements are allowed")

    monkeypatch.setattr(
        "backend.application.database.execute_safe_sql", fake_run
    )

    r = client.post(
        "/database/query",
        json={"sql": "DELETE FROM x"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "SELECT" in r.json()["detail"]


def test_list_tables_projects_schema(client, monkeypatch):
    csrf = _register_and_get_csrf(client)

    monkeypatch.setattr(
        "backend.application.database.build_schema_response",
        lambda: {
            "tables": {
                "players": {
                    "alias": "p",
                    "columns": [{"name": "player_gsis_id", "type": "VARCHAR"}],
                    "column_count": 1,
                }
            },
            "joins": [],
            "aliases": {},
            "total_tables": 1,
        },
    )

    r = client.get("/database/tables", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json() == [
        {
            "name": "players",
            "columns": [{"name": "player_gsis_id", "type": "VARCHAR"}],
        }
    ]


def test_save_as_report_creates_table_chat_session(client, store):
    csrf = _register_and_get_csrf(client)

    r = client.post(
        "/database/save-as-report",
        json={
            "sql": "SELECT player FROM players LIMIT 1",
            "columns": ["player"],
            "rows": [{"player": "Mahomes"}],
            "row_count": 1,
            "truncated": False,
            "title": "QB sample",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text
    conversation_id = r.json()["conversation_id"]
    assert conversation_id

    import asyncio

    session = asyncio.get_event_loop().run_until_complete(
        store.get_session(conversation_id, user_id=1)
    )
    assert session is not None
    assert session.kind == "table_chat"
    assert session.title == "QB sample"

    state = asyncio.get_event_loop().run_until_complete(
        store.get_table_state(conversation_id)
    )
    assert state is not None
    assert state.columns == ["player"]
    assert state.rows == [{"player": "Mahomes"}]
    assert state.last_sql == "SELECT player FROM players LIMIT 1"


def test_save_as_report_rejects_empty_rows(client):
    csrf = _register_and_get_csrf(client)

    r = client.post(
        "/database/save-as-report",
        json={
            "sql": "SELECT 1",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "row" in r.json()["detail"].lower()
