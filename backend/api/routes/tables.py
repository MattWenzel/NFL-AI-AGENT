"""Table-view chat endpoints.

Distinct from `/chat/conversations` because table chats have an extra
piece of state (the live `TableStateRecord` plus its `locked` flag) and
a save-to-reports action that doesn't apply to regular chats.
Streaming still goes through the existing `/chat/stream` route — the
request body shape is identical to a regular chat now that the explore /
edit-table mode picker has been replaced by the lock flag.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.dependencies import (
    get_current_user,
    get_table_chat_service,
)
from backend.api.schemas.common import OkResponse
from backend.api.schemas.conversations import ConversationInfo, ConversationUpdate
from backend.api.schemas.exports import ExportInfo
from backend.api.schemas.tables import (
    TableChatCreate,
    TableChatResponse,
    TableChatSaveRequest,
    TableLockUpdate,
    TableRunSqlRequest,
    TableState,
)
from backend.application.sql_execution import SQLExecutionError
from backend.application.tables import (
    TableChatNotFoundError,
    TableChatService,
    TableLockedError,
    TableNotReadyError,
)
from backend.domain.auth.types import AuthenticatedUser
from backend.server.csrf import verify_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/tables", tags=["tables"], dependencies=[Depends(verify_csrf)])


@router.post("", response_model=ConversationInfo, status_code=status.HTTP_201_CREATED)
async def create_table_chat(
    body: TableChatCreate,
    service: TableChatService = Depends(get_table_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ConversationInfo:
    session = await service.create_table_chat(
        user_id=user.id,
        provider=body.provider,
        model=body.model,
        title=body.title,
    )
    entry = await service.get_session_list_entry(session.id, user_id=user.id)
    if entry is None:  # pragma: no cover — race-only
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not load created table chat")
    return ConversationInfo.from_row(entry)


@router.get("", response_model=list[ConversationInfo])
async def list_table_chats(
    service: TableChatService = Depends(get_table_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[ConversationInfo]:
    rows = await service.list_table_chats(user.id)
    return [ConversationInfo.from_row(item) for item in rows]


@router.get("/{conversation_id}", response_model=TableChatResponse)
async def get_table_chat(
    conversation_id: str,
    service: TableChatService = Depends(get_table_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> TableChatResponse:
    try:
        payload = await service.get_table_chat(conversation_id, user.id)
    except TableChatNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return TableChatResponse.from_transcript(conversation_id, payload)


@router.patch("/{conversation_id}", response_model=ConversationInfo)
async def update_table_chat(
    conversation_id: str,
    body: ConversationUpdate,
    service: TableChatService = Depends(get_table_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ConversationInfo:
    if body.title is None and body.pinned is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide title and/or pinned")
    try:
        entry = await service.update_table_chat(
            conversation_id,
            user_id=user.id,
            title=body.title,
            pinned=body.pinned,
        )
    except TableChatNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ConversationInfo.from_row(entry)


@router.put("/{conversation_id}/lock", response_model=OkResponse)
async def set_table_lock(
    conversation_id: str,
    body: TableLockUpdate,
    service: TableChatService = Depends(get_table_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OkResponse:
    try:
        await service.set_table_locked(
            conversation_id, user_id=user.id, locked=body.locked
        )
    except TableChatNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except TableNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return OkResponse()


@router.put("/{conversation_id}/sql", response_model=TableState)
async def run_table_sql(
    conversation_id: str,
    body: TableRunSqlRequest,
    service: TableChatService = Depends(get_table_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> TableState:
    """Run user-edited SQL and replace the live table state."""
    try:
        record = await service.run_and_persist_sql(
            conversation_id, user_id=user.id, sql=body.sql
        )
    except TableChatNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except TableLockedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except SQLExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return TableState.from_record(record)


@router.delete("/{conversation_id}", response_model=OkResponse)
async def delete_table_chat(
    conversation_id: str,
    service: TableChatService = Depends(get_table_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OkResponse:
    try:
        await service.delete_table_chat(conversation_id, user.id)
    except TableChatNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return OkResponse()


@router.post(
    "/{conversation_id}/save",
    response_model=ExportInfo,
    status_code=status.HTTP_201_CREATED,
)
async def save_table_chat_to_reports(
    conversation_id: str,
    body: TableChatSaveRequest,
    service: TableChatService = Depends(get_table_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> ExportInfo:
    try:
        record = await service.save_to_reports(
            conversation_id,
            user_id=user.id,
            title=body.title,
        )
    except TableChatNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except TableNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ExportInfo.from_record(record)
