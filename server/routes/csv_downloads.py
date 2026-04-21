"""CSV export download endpoint."""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from auth.primitives import AuthenticatedUser, get_current_user
from config import EXPORTS_DIR
from server.repository_dependencies import get_store
from server.services.exports import ExportApplicationService, ExportNotFoundError
from storage import RuntimeStore

router = APIRouter(tags=["exports"])

_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-]+\.csv$")


@router.get("/exports/{filename}")
async def download_export(
    filename: str,
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = (EXPORTS_DIR / filename).resolve()
    if not str(file_path).startswith(str(EXPORTS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")

    service = ExportApplicationService(store)
    try:
        await service.get_download_record(filename, user.id)
    except ExportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(path=file_path, filename=filename, media_type="text/csv")
