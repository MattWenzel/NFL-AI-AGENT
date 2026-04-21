"""CSV export download endpoint."""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from auth.primitives import AuthenticatedUser, get_current_user
from config import EXPORTS_DIR
from server.dependencies import get_conversation_repository, get_export_repository
from server.repositories import ConversationRepository, ExportRepository
from server.services.exports import ExportApplicationService, ExportNotFoundError

router = APIRouter(tags=["exports"])

_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-]+\.csv$")


@router.get("/exports/{filename}")
async def download_export(
    filename: str,
    exports: ExportRepository = Depends(get_export_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = (EXPORTS_DIR / filename).resolve()
    if not str(file_path).startswith(str(EXPORTS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")

    service = ExportApplicationService(exports, conversations)
    try:
        await service.get_download_record(filename, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(path=file_path, filename=filename, media_type="text/csv")
