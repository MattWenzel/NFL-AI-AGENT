"""Database browser endpoints.

A no-LLM-roundtrip surface for the nflverse DuckDB: list tables, run an
ad-hoc SELECT, and convert any result into a new `table_chat` session
(so the user can continue with the agent in the existing Reports view).

Read-only by construction — the SQL goes through the same sandbox the
agent uses, which only allows SELECT/WITH and rejects multi-statement.

The `/helper-chat/stream` endpoint mirrors `/chat/stream` for the
ephemeral helper-chat surface in the Database tab: per-user concurrency
slot, prepared LLM client, producer/consumer SSE loop with heartbeats,
and identical SSE error/done framing on every failure mode.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend.api.dependencies import (
    get_current_user,
    get_database_service,
    get_db_helper_chat_service,
    get_process_state,
)
from backend.api.schemas.database import (
    DbHelperChatRequest,
    QueryRequest,
    QueryResponse,
    SaveAsReportRequest,
    SaveAsReportResponse,
    SaveSqlAsReportRequest,
    TableInfo,
)
from backend.application.chat import close_client
from backend.application.database import DatabaseQueryError, DatabaseService
from backend.application.db_helper_chat import (
    DbHelperChatService,
    HelperChatConfigurationError,
)
from backend.domain.agent.events import (
    RetryingEvent,
    RuntimeErrorEvent,
    TextDeltaEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolPendingEvent,
)
from backend.domain.auth.types import AuthenticatedUser
from backend.domain.providers.errors import LLMError
from backend.server.csrf import verify_csrf
from backend.server.process_state import AppProcessState
from backend.server.rate_limit import ConcurrencyLimiter
from backend.server.sse import done_payload, error_to_sse_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/database", tags=["database"], dependencies=[Depends(verify_csrf)])

# Idle interval after which the helper-chat stream emits an SSE keepalive
# comment so reverse proxies don't drop the connection during long tool
# calls. Mirrors `chat.SSE_HEARTBEAT_SECONDS`.
HELPER_SSE_HEARTBEAT_SECONDS = 15

_PRODUCER_DONE = object()


async def _acquire_helper_slot(stream_gate: ConcurrencyLimiter, user_id: int) -> None:
    await stream_gate.acquire(
        user_id,
        detail=(
            f"Too many concurrent LLM streams (max "
            f"{stream_gate.max_active} per user). "
            "Wait for an in-flight reply to finish."
        ),
    )


def _helper_event_to_sse(event) -> dict | None:
    """Helper-chat SSE serializer.

    Distinct from `event_to_sse_payload` because the helper has no DB to
    fetch tool results from — the result content needs to ride along on
    the SSE event itself for the slim message renderer to display it
    inline. The browser keeps the full message history (including tool
    calls and their results) and posts it back next turn.
    """
    if isinstance(event, TextDeltaEvent):
        return {"type": "text", "text": event.text}
    if isinstance(event, ToolPendingEvent):
        return {
            "type": "tool_call",
            "tool_run_id": event.tool_run_id,
            "name": event.name,
            "input": event.input,
        }
    if isinstance(event, ToolCompletedEvent):
        return {
            "type": "tool_result",
            "tool_run_id": event.tool_run_id,
            "name": event.name,
            "content": event.result or "",
        }
    if isinstance(event, ToolFailedEvent):
        return {
            "type": "tool_failed",
            "tool_run_id": event.tool_run_id,
            "name": event.name,
            "content": event.result or "",
            "message": event.error or f"{event.name} failed",
        }
    if isinstance(event, RetryingEvent):
        return {
            "type": "retrying",
            "attempt": event.attempt,
            "delay_seconds": event.delay_seconds,
            "message": event.error or "Retrying after transient error",
        }
    if isinstance(event, RuntimeErrorEvent):
        return {"type": "error", "message": event.error or "Runtime error"}
    return None


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


@router.post(
    "/save-sql-as-report",
    response_model=SaveAsReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_sql_as_report(
    body: SaveSqlAsReportRequest,
    service: DatabaseService = Depends(get_database_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> SaveAsReportResponse:
    """Run user-provided SQL and seed a brand-new Report with the result."""
    try:
        session = await service.save_sql_as_report(
            sql=body.sql,
            title=body.title,
            user_id=user.id,
        )
    except DatabaseQueryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return SaveAsReportResponse(conversation_id=session.id)


@router.post("/helper-chat/stream")
async def db_helper_chat_stream(
    request: Request,
    body: DbHelperChatRequest,
    service: DbHelperChatService = Depends(get_db_helper_chat_service),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Stream a turn of the Database browser's helper chat.

    The full message history rides on the request body (the browser owns
    it). No conversation id, no DB writes — refresh wipes the chat by
    design.

    Acquisition + cleanup mirrors `/chat/stream`: the per-user
    concurrency slot, LLM client, and runtime source are all created
    inside the generator (so an aborted response doesn't leak any of
    them) and released in the generator's `finally`. The shared
    `chat_stream_limiter` covers both surfaces — a single user can't
    exceed the LLM stream cap by mixing chat and helper-chat calls.
    """

    stream_gate = process_state.chat_stream_limiter

    def _sse_line(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    raw_messages = [m.model_dump() for m in body.messages]

    async def event_generator():
        slot_acquired = False
        prepared = None
        source = None
        try:
            try:
                await _acquire_helper_slot(stream_gate, user.id)
                slot_acquired = True
            except HTTPException as exc:
                yield _sse_line(error_to_sse_payload(
                    code="rate_limited",
                    status=exc.status_code,
                    message=exc.detail,
                ))
                yield _sse_line(done_payload())
                return

            try:
                prepared = await service.prepare(
                    provider=body.provider,
                    model=body.model,
                    user=user,
                )
            except HelperChatConfigurationError as exc:
                yield _sse_line(error_to_sse_payload(
                    code="configuration",
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    message=str(exc),
                ))
                yield _sse_line(done_payload())
                return

            queue: asyncio.Queue = asyncio.Queue()
            source = service.stream_events(
                prepared,
                messages=raw_messages,
                tool_choice=body.tool_choice,
            )

            async def producer():
                try:
                    async for event in source:
                        await queue.put(event)
                except BaseException as exc:
                    await queue.put(exc)
                finally:
                    await queue.put(_PRODUCER_DONE)

            producer_task = asyncio.create_task(producer())

            try:
                while True:
                    if await request.is_disconnected():
                        logger.info("Client disconnected, stopping helper-chat stream")
                        break
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=HELPER_SSE_HEARTBEAT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if item is _PRODUCER_DONE:
                        break
                    if isinstance(item, LLMError):
                        yield _sse_line(error_to_sse_payload(message=str(item)))
                        break
                    if isinstance(item, BaseException):
                        raise item
                    payload = _helper_event_to_sse(item)
                    if payload is not None:
                        yield _sse_line(payload)
                yield _sse_line(done_payload())
            except Exception:
                logger.exception("Unexpected error in helper-chat stream")
                yield _sse_line(error_to_sse_payload(
                    message="Internal error — check server logs"
                ))
                yield _sse_line(done_payload())
            finally:
                if not producer_task.done():
                    producer_task.cancel()
                try:
                    await producer_task
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await source.aclose()
                except Exception:
                    logger.exception("Failed to close helper-chat source")
        finally:
            if prepared is not None:
                await close_client(prepared.client)
            if slot_acquired:
                await stream_gate.release(user.id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
