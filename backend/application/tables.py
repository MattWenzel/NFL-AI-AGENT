"""Table-view chat feature: list / open / save / delete.

A "table chat" is a `SessionRecord` with `kind == "table_chat"`. It owns
a single live `TableStateRecord` keyed by `session_id`. The chat half
runs through the existing `/chat/stream` pipeline; this service owns
everything else — creating the conversation, fetching the current
table, and turning the live table into a saved CSV in the Reports
library.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from backend.config import EXPORTS_DIR, format_file_size
from backend.data import (
    ExportRecord,
    RuntimeStore,
    SessionListEntry,
    SessionRecord,
    SessionTranscript,
    TableStateRecord,
)
from backend.domain.providers import get_default_provider, get_provider

logger = logging.getLogger(__name__)


class TableChatServiceError(Exception):
    """Base class for table-chat service failures."""


class TableChatNotFoundError(TableChatServiceError):
    """Conversation does not exist or is not a table-chat for the caller."""


class TableNotReadyError(TableChatServiceError):
    """Save was requested before any rows were generated."""


def _sanitize_filename(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized[:80] or "table"


@dataclass
class TableChatTranscript:
    """Composite returned by `get_table_chat`: transcript + live table."""

    session: SessionRecord
    transcript: SessionTranscript
    table: TableStateRecord | None


@dataclass
class TableChatService:
    store: RuntimeStore
    exports_dir: Path = EXPORTS_DIR

    async def list_table_chats(self, user_id: int) -> list[SessionListEntry]:
        return await self.store.list_sessions(user_id=user_id, kind="table_chat")

    async def create_table_chat(
        self,
        *,
        user_id: int,
        provider: str | None = None,
        model: str | None = None,
        title: str | None = None,
    ) -> SessionRecord:
        resolved_provider = provider or get_default_provider()
        try:
            info = get_provider(resolved_provider)
        except KeyError as exc:
            raise TableChatServiceError(str(exc)) from exc
        resolved_model = model or info.default_model
        session = await self.store.get_or_create_session(
            provider=resolved_provider,
            model=resolved_model,
            context_window=info.effective_context_window,
            user_id=user_id,
            kind="table_chat",
        )
        if title:
            await self.store.update_session(session.id, title=title.strip())
            session.title = title.strip()
        return session

    async def get_table_chat(
        self, conversation_id: str, user_id: int
    ) -> TableChatTranscript:
        session = await self.store.get_session(conversation_id, user_id=user_id)
        if session is None or session.kind != "table_chat":
            raise TableChatNotFoundError("Table chat not found")
        try:
            transcript = await self.store.get_transcript(conversation_id)
        except KeyError as exc:
            raise TableChatNotFoundError("Table chat not found") from exc
        table = await self.store.get_table_state(conversation_id)
        return TableChatTranscript(session=session, transcript=transcript, table=table)

    async def update_table_chat(
        self,
        conversation_id: str,
        *,
        user_id: int,
        title: str | None,
        pinned: bool | None,
    ) -> SessionListEntry:
        session = await self.store.get_session(conversation_id, user_id=user_id)
        if session is None or session.kind != "table_chat":
            raise TableChatNotFoundError("Table chat not found")
        if title is not None:
            await self.store.update_session(conversation_id, title=title.strip())
        if pinned is not None:
            await self.store.set_session_pinned(
                conversation_id, pinned, user_id=user_id
            )
        entry = await self.store.get_session_list_entry(
            conversation_id, user_id=user_id
        )
        if entry is None:
            raise TableChatNotFoundError("Table chat not found")
        return entry

    async def delete_table_chat(self, conversation_id: str, user_id: int) -> None:
        session = await self.store.get_session(conversation_id, user_id=user_id)
        if session is None or session.kind != "table_chat":
            raise TableChatNotFoundError("Table chat not found")
        if not await self.store.delete_session(conversation_id, user_id=user_id):
            raise TableChatNotFoundError("Table chat not found")

    async def save_to_reports(
        self,
        conversation_id: str,
        *,
        user_id: int,
        title: str | None = None,
    ) -> ExportRecord:
        """Snapshot the live table into a CSV + ExportRecord.

        Reuses the existing exports plumbing (`store.register_export`) so
        the result lands in the same Reports library and can be opened by
        `CsvViewer`. The rows are already in memory in `table_states`, so
        we don't re-execute the SQL — that's the whole point of the live
        table.
        """
        session = await self.store.get_session(conversation_id, user_id=user_id)
        if session is None or session.kind != "table_chat":
            raise TableChatNotFoundError("Table chat not found")
        table = await self.store.get_table_state(conversation_id)
        if table is None or not table.columns or not table.rows:
            raise TableNotReadyError(
                "There is no table to save yet — ask the agent to build one first."
            )

        resolved_title = (title or session.title or "table").strip() or "table"
        sanitized = _sanitize_filename(resolved_title)
        timestamp = int(time.time())
        filename = f"{sanitized}_{timestamp}.csv"

        self.exports_dir.mkdir(parents=True, exist_ok=True)
        csv_path = self.exports_dir / filename

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(table.columns))
        writer.writeheader()
        # Coerce row values through the writer — the rows came from
        # `set_table` which stored them as JSON, so values may be int /
        # float / str / None. csv.DictWriter handles each correctly.
        writer.writerows(table.rows)
        csv_path.write_text(buf.getvalue(), encoding="utf-8")

        file_size = csv_path.stat().st_size
        try:
            record = await self.store.register_export(
                filename=filename,
                title=resolved_title,
                sql=table.last_sql or "",
                row_count=table.row_count,
                columns=list(table.columns),
                file_size=file_size,
                source_session_id=conversation_id,
                source_tool_run_id=None,
            )
        except Exception:
            logger.exception("Failed to register table-chat export %s; unlinking", filename)
            try:
                csv_path.unlink()
            except OSError:
                pass
            raise

        # Display-only convenience for logs.
        logger.info(
            "Saved table chat %s to %s (%s, %d rows)",
            conversation_id,
            filename,
            format_file_size(file_size),
            table.row_count,
        )
        return record
