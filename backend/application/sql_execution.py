"""Async facade over the read-only SQL sandbox.

The sandbox in `backend/domain/tools/sandbox/runner.py` exposes
synchronous executors (`execute_safe_sql`, `execute_export_sql`,
`execute_table_sql`) used directly by the agent's tool handlers, which
already run inside `asyncio.to_thread`. HTTP routes and other async
contexts need the same execution but with two extra concerns:

  - run the sync sandbox on a worker thread so the event loop is free
  - translate the sandbox's validation/runtime errors into a single
    user-facing service error

Previously both `DatabaseService.run_query` and
`TableChatService.run_and_persist_sql` re-implemented that thread-wrap
+ error-translation pattern with their own service-specific error
types. This module concentrates the policy so there's one place to add
audit logging, per-user budgets, or caching when the time comes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from backend.domain.tools.sandbox.runner import (
    SQLResult,
    SQLValidationError,
    execute_safe_sql,
)


class SQLExecutionError(Exception):
    """SQL failed validation or execution in the read-only sandbox.

    The message carries the sandbox's validation/error string verbatim
    — safe to display to the user (the sandbox already redacts internals
    and produces structured "Query timed out" / "Only SELECT and WITH"
    style messages).
    """


@dataclass
class SQLExecutionService:
    """Async wrapper around the synchronous SQL sandbox.

    Stateless — instances are cheap to construct per-request via the
    FastAPI Depends factory. Composed by `DatabaseService` and
    `TableChatService` so both share the same execution policy.
    """

    async def run(self, sql: str) -> SQLResult:
        """Run `sql` through the 500-row / 30s read-only sandbox.

        Raises `SQLExecutionError` on validation failure or runtime
        error from DuckDB. Callers surface this as HTTP 400.
        """
        try:
            return await asyncio.to_thread(execute_safe_sql, sql)
        except SQLValidationError as exc:
            raise SQLExecutionError(str(exc)) from exc
