"""CSV export process service errors."""

from __future__ import annotations


class ExportServiceError(Exception):
    pass


class ExportNotFoundError(ExportServiceError):
    pass
