"""Database browser: ad-hoc SQL execution + save-as-Report.

The browser tab runs SELECT/WITH queries through the same sandbox the
agent uses (`execute_safe_sql`) and can convert any result into a new
`kind=table_chat` session so the user can keep working with it through
the agent.

Both methods are thin compositions over existing infrastructure — see
`backend/domain/tools/sandbox/runner.py` for the sandbox and
`backend/application/tables.py` for the table-chat session lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from backend.application.tables import TableChatService
from backend.data import RuntimeStore, SessionRecord
from backend.domain.tools.handlers.get_schema import build_schema_response
from backend.domain.tools.sandbox.runner import (
    SQLResult,
    SQLValidationError,
    execute_safe_sql,
)

logger = logging.getLogger(__name__)


class DatabaseQueryError(Exception):
    """Sandbox refused or failed to run the query.

    The message is the validation/error string the sandbox produced, suitable
    for showing to the user verbatim.
    """


@dataclass
class DatabaseService:
    store: RuntimeStore
    table_chat_service: TableChatService

    def list_browseable_tables(self) -> list[dict]:
        """Project the agent's schema response down to what the picker needs.

        Drops aliases, joins, and the global table count — the UI only needs
        each table's name and its columns (name + type) for the dropdown
        and the auto-generated `SELECT *`.
        """
        payload = build_schema_response()
        tables = []
        for name, info in sorted(payload.get("tables", {}).items()):
            columns = [
                {"name": col["name"], "type": col["type"]}
                for col in info.get("columns", [])
            ]
            tables.append({"name": name, "columns": columns})
        return tables

    async def run_query(self, sql: str) -> SQLResult:
        """Run `sql` through the read-only sandbox.

        The sandbox is sync; thread-offloaded so the FastAPI event loop
        isn't blocked while DuckDB pages through results.
        """
        try:
            return await asyncio.to_thread(execute_safe_sql, sql)
        except SQLValidationError as exc:
            raise DatabaseQueryError(str(exc)) from exc

    async def save_query_as_report(
        self,
        *,
        sql: str,
        columns: list[str],
        rows: list[dict],
        row_count: int,
        truncated: bool,
        title: str | None,
        user_id: int,
    ) -> SessionRecord:
        """Mint a `kind=table_chat` session seeded with the rows.

        The user lands in `TableChatView` immediately — the existing
        save-to-CSV flow inside that view handles persisting to the
        Reports library if/when the user is done iterating.
        """
        session = await self.table_chat_service.create_table_chat(
            user_id=user_id,
            title=title,
        )
        await self.store.upsert_table_state(
            session.id,
            columns=list(columns),
            rows=list(rows),
            last_sql=sql,
            row_count=row_count,
            truncated=truncated,
        )
        logger.info(
            "Created table chat %s from database browser (%d rows)",
            session.id,
            row_count,
        )
        return session
