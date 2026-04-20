"""CSV export download endpoint.

Files are registered in the `exports` table when created by the
`create_csv_export` tool and persist until explicitly deleted via the
`/chat/exports/{id}` endpoints. No TTL cleanup; the library is the
source of truth for which exports the user can see.

Requires auth + scopes the download to the caller's user_id so users can
only download their own exports. The frontend fetches the bytes via
fetch() with the Authorization header and offers the download as a blob.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from auth.primitives import AuthenticatedUser, get_current_user
from server.dependencies import get_store
from config import EXPORTS_DIR
from storage import RuntimeStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["exports"])

# Only allow safe filenames: alphanumeric, hyphens, underscores, dots
_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-]+\.csv$")


@router.get("/exports/{filename}")
def download_export(
    filename: str,
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Download a CSV export file owned by the caller."""
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = (EXPORTS_DIR / filename).resolve()

    # Path traversal check
    if not str(file_path).startswith(str(EXPORTS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Ownership check — same 404 whether it doesn't exist or is owned by another user.
    record = store.get_export_by_filename(filename, user_id=user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="Export not found")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="text/csv",
    )
