"""CSV library: list, preview, rename, delete, and seed new chats."""

from fastapi import APIRouter, Depends, HTTPException

from auth.primitives import AuthenticatedUser, get_current_user
from server.repository_dependencies import get_conversation_repository, get_export_repository
from server.repositories import ConversationRepository, ExportRepository
from server.schemas.exports import (
    ExportDetail,
    ExportInfo,
    ExportUpdate,
    NewSessionFromExportRequest,
    NewSessionFromExportResponse,
)
from server.services.exports import (
    ExportApplicationService,
    ExportNotFoundError,
    ExportServiceError,
)

router = APIRouter(prefix="/chat/exports", tags=["csv-library"])


@router.get("", response_model=list[ExportInfo])
async def list_csvs(
    exports: ExportRepository = Depends(get_export_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = ExportApplicationService(exports, conversations)
    return await service.list_exports(user.id)


@router.get("/{export_id}", response_model=ExportDetail)
async def get_csv_detail(
    export_id: str,
    exports: ExportRepository = Depends(get_export_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = ExportApplicationService(exports, conversations)
    try:
        return await service.get_export_detail(export_id, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/{export_id}", response_model=ExportInfo)
async def rename_csv(
    export_id: str,
    body: ExportUpdate,
    exports: ExportRepository = Depends(get_export_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = ExportApplicationService(exports, conversations)
    try:
        return await service.rename_export(export_id, body.title, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{export_id}")
async def delete_csv(
    export_id: str,
    exports: ExportRepository = Depends(get_export_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = ExportApplicationService(exports, conversations)
    try:
        await service.delete_export(export_id, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "deleted"}


@router.post("/{export_id}/new-session", response_model=NewSessionFromExportResponse)
async def new_session_from_csv(
    export_id: str,
    body: NewSessionFromExportRequest,
    exports: ExportRepository = Depends(get_export_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = ExportApplicationService(exports, conversations)
    try:
        return await service.create_session_from_export(
            export_id,
            user_id=user.id,
            provider_name=body.provider,
            model=body.model,
        )
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
