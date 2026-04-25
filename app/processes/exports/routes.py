"""CSV library: list, preview, rename, delete, and seed new chats."""

from fastapi import APIRouter, Depends, HTTPException

from core.auth.types import AuthenticatedUser
from app.bootstrap.csrf import verify_csrf
from app.bootstrap.dependencies import get_current_user, get_export_service
from app.processes.exports.schemas import (
    ExportDetail,
    ExportInfo,
    ExportUpdate,
    NewSessionFromExportRequest,
    NewSessionFromExportResponse,
)
from app.processes.exports.errors import ExportNotFoundError, ExportServiceError
from app.processes.exports.service import ExportService

router = APIRouter(prefix="/chat/exports", tags=["csv-library"], dependencies=[Depends(verify_csrf)])


@router.get("", response_model=list[ExportInfo])
async def list_csvs(
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    return await service.list_exports(user.id)


@router.get("/{export_id}", response_model=ExportDetail)
async def get_csv_detail(
    export_id: str,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        return await service.get_export_detail(export_id, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/{export_id}", response_model=ExportInfo)
async def rename_csv(
    export_id: str,
    body: ExportUpdate,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        return await service.rename_export(export_id, body.title, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{export_id}")
async def delete_csv(
    export_id: str,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        await service.delete_export(export_id, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "deleted"}


@router.post("/{export_id}/new-session", response_model=NewSessionFromExportResponse)
async def new_session_from_csv(
    export_id: str,
    body: NewSessionFromExportRequest,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
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
