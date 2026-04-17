"""CSV export download endpoint.

Files are registered in the `exports` table when created by the
`create_csv_export` tool and persist until explicitly deleted via the
`/chat/exports/{id}` endpoints. No TTL cleanup; the library is the
source of truth for which exports the user can see.
"""

import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import EXPORTS_DIR

logger = logging.getLogger(__name__)

router = APIRouter(tags=["exports"])

# Only allow safe filenames: alphanumeric, hyphens, underscores, dots
_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-]+\.csv$")


@router.get("/exports/{filename}")
def download_export(filename: str):
    """Download a CSV export file."""
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = (EXPORTS_DIR / filename).resolve()

    # Path traversal check
    if not str(file_path).startswith(str(EXPORTS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="text/csv",
    )
