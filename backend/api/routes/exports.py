"""CSV library and download endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from backend.server.csrf import verify_csrf
from backend.api.dependencies import get_current_user, get_export_service
from backend.api.schemas.exports import (
    ExportDetail,
    ExportInfo,
    ExportUpdate,
    NewSessionFromExportRequest,
    NewSessionFromExportResponse,
)
from backend.api.schemas.common import OkResponse
from backend.application.exports import ExportNotFoundError, ExportServiceError
from backend.application.exports import ExportService
from backend.domain.auth.types import AuthenticatedUser

router = APIRouter(prefix="/chat/exports", tags=["csv-library"], dependencies=[Depends(verify_csrf)])
download_router = APIRouter(tags=["exports"])


@router.get("", response_model=list[ExportInfo])
async def list_csvs(
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    records = await service.list_exports(user.id)
    return [ExportInfo.from_record(record) for record in records]


@router.get("/{export_id}", response_model=ExportDetail)
async def get_csv_detail(
    export_id: str,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        record, preview_rows, preview_truncated = await service.get_export_detail(export_id, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ExportDetail.from_record(
        record, preview_rows=preview_rows, preview_truncated=preview_truncated
    )


@router.patch("/{export_id}", response_model=ExportInfo)
async def rename_csv(
    export_id: str,
    body: ExportUpdate,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        record = await service.rename_export(export_id, body.title, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ExportInfo.from_record(record)


@router.delete("/{export_id}", response_model=OkResponse)
async def delete_csv(
    export_id: str,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OkResponse:
    try:
        await service.delete_export(export_id, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return OkResponse()


@router.post("/{export_id}/new-session", response_model=NewSessionFromExportResponse)
async def new_session_from_csv(
    export_id: str,
    body: NewSessionFromExportRequest,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        conversation_id = await service.create_session_from_export(
            export_id,
            user_id=user.id,
            provider_name=body.provider,
            model=body.model,
        )
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ExportServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return NewSessionFromExportResponse(conversation_id=conversation_id)


@download_router.get("/exports/{filename}")
async def download_export(
    filename: str,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        file_path = await service.resolve_download_path(filename, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ExportServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return FileResponse(path=file_path, filename=filename, media_type="text/csv")
