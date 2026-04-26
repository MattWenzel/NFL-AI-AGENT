"""CSV library and download endpoints."""

from fastapi import APIRouter, Depends, HTTPException
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
from backend.application.exports import ExportNotFoundError, ExportServiceError
from backend.application.exports import ExportService
from backend.data import ExportRecord
from backend.domain.auth.types import AuthenticatedUser

router = APIRouter(prefix="/chat/exports", tags=["csv-library"], dependencies=[Depends(verify_csrf)])
download_router = APIRouter(tags=["exports"])


def _export_info(record: ExportRecord) -> ExportInfo:
    return ExportInfo(
        id=record.id,
        filename=record.filename,
        title=record.title,
        row_count=record.row_count,
        columns=list(record.columns),
        file_size=record.file_size,
        created_at=record.created_at,
        updated_at=record.updated_at,
        download_url=f"/exports/{record.filename}",
        source_session_id=record.source_session_id,
    )


def _export_detail(
    record: ExportRecord,
    preview_rows: list[dict],
    preview_truncated: bool,
) -> ExportDetail:
    info = _export_info(record)
    return ExportDetail(
        id=info.id,
        filename=info.filename,
        title=info.title,
        row_count=info.row_count,
        columns=info.columns,
        file_size=info.file_size,
        created_at=info.created_at,
        updated_at=info.updated_at,
        download_url=info.download_url,
        source_session_id=info.source_session_id,
        sql=record.sql,
        preview_rows=preview_rows,
        preview_truncated=preview_truncated,
    )


@router.get("", response_model=list[ExportInfo])
async def list_csvs(
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    records = await service.list_exports(user.id)
    return [_export_info(record) for record in records]


@router.get("/{export_id}", response_model=ExportDetail)
async def get_csv_detail(
    export_id: str,
    service: ExportService = Depends(get_export_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        record, preview_rows, preview_truncated = await service.get_export_detail(export_id, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _export_detail(record, preview_rows, preview_truncated)


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
        raise HTTPException(status_code=404, detail=str(exc))
    return _export_info(record)


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
        conversation_id = await service.create_session_from_export(
            export_id,
            user_id=user.id,
            provider_name=body.provider,
            model=body.model,
        )
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
        raise HTTPException(status_code=404, detail=str(exc))
    except ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return FileResponse(path=file_path, filename=filename, media_type="text/csv")
