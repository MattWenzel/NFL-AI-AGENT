"""Settings process service errors."""

from __future__ import annotations


class SettingsServiceError(Exception):
    pass


class SettingsNotFoundError(SettingsServiceError):
    pass
