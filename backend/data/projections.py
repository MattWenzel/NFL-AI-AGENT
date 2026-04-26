"""Composite/projection types returned by storage read helpers.

These aren't tables — they're the shapes `RuntimeStore` produces when it
needs to return more (or less) than a single row. They live next to the
tables because they reference SQLModel record types directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from backend.data.models import (
    AssistantPartRecord,
    CompactionSummaryRecord,
    SessionRecord,
    ToolRunRecord,
    TurnRecord,
)


@dataclass(frozen=True)
class SessionListEntry:
    """Typed session-list projection returned by storage read helpers."""

    id: str
    turn_count: int
    title: str
    provider: str | None
    model: str | None
    updated_at: str | None
    pinned_at: str | None
    source_csv_id: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SessionListEntry":
        return cls(
            id=str(row["id"]),
            turn_count=int(row["turn_count"]),
            title=str(row["title"]),
            provider=row.get("provider"),
            model=row.get("model"),
            updated_at=row.get("updated_at"),
            pinned_at=row.get("pinned_at"),
            source_csv_id=row.get("source_csv_id"),
        )


@dataclass
class SessionTranscript:
    """Composite container returned by `get_transcript` — not a table."""

    session: SessionRecord
    turns: list[TurnRecord]
    parts_by_turn: dict[str, list[AssistantPartRecord]]
    tool_runs_by_turn: dict[str, list[ToolRunRecord]]
    summaries: list[CompactionSummaryRecord]
