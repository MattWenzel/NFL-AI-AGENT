"""Data types for the tools subsystem."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SQLResult:
    """Result of a sandboxed SQL query."""
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool
