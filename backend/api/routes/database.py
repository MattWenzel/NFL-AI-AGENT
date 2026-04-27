"""Database browser endpoints.

A no-LLM-roundtrip surface for the nflverse DuckDB: list tables, run an
ad-hoc SELECT, and convert any result into a new `table_chat` session
(so the user can continue with the agent in the existing Reports view).

Read-only by construction — the SQL goes through the same sandbox the
agent uses, which only allows SELECT/WITH and rejects multi-statement.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.dependencies import get_current_user, get_database_service
from backend.api.schemas.database import (
    QueryRequest,
    QueryResponse,
    SaveAsReportRequest,
    SaveAsReportResponse,
    TableInfo,
)
from backend.application.database import DatabaseQueryError, DatabaseService
from backend.domain.auth.types import AuthenticatedUser
from backend.server.csrf import verify_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/database", tags=["database"], dependencies=[Depends(verify_csrf)])


@router.get("/tables", response_model=list[TableInfo])
async def list_database_tables(
    service: DatabaseService = Depends(get_database_service),
    _user: AuthenticatedUser = Depends(get_current_user),
) -> list[TableInfo]:
    return [TableInfo(**row) for row in service.list_browseable_tables()]


@router.post("/query", response_model=QueryResponse)
async def run_database_query(
    body: QueryRequest,
    service: DatabaseService = Depends(get_database_service),
    _user: AuthenticatedUser = Depends(get_current_user),
) -> QueryResponse:
    try:
        result = await service.run_query(body.sql)
    except DatabaseQueryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return QueryResponse(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
    )


@router.post(
    "/save-as-report",
    response_model=SaveAsReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_query_as_report(
    body: SaveAsReportRequest,
    service: DatabaseService = Depends(get_database_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> SaveAsReportResponse:
    if not body.columns or not body.rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run a query that returned at least one row before saving.",
        )
    session = await service.save_query_as_report(
        sql=body.sql,
        columns=body.columns,
        rows=body.rows,
        row_count=body.row_count,
        truncated=body.truncated,
        title=body.title,
        user_id=user.id,
    )
    return SaveAsReportResponse(conversation_id=session.id)
