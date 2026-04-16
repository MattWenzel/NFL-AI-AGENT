"""CSV export download endpoint and cleanup utilities."""

import logging
import re
import time

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


def cleanup_old_exports(max_age_seconds: int = 3600) -> int:
    """Remove CSV files older than max_age_seconds from the exports directory.

    Returns the number of files removed.
    """
    if not EXPORTS_DIR.exists():
        return 0

    now = time.time()
    removed = 0
    for f in EXPORTS_DIR.glob("*.csv"):
        try:
            if now - f.stat().st_mtime > max_age_seconds:
                f.unlink()
                removed += 1
        except OSError as e:
            logger.warning("Could not remove export %s: %s", f.name, e)
    return removed
