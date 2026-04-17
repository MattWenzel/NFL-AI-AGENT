"""CSV library: list, preview, rename, delete, and seed new chats.

The download endpoint lives in `exports.py` (path `/exports/{filename}`)
for back-compat with the tool's own `download_url` field; this router
adds the library surface on top — CRUD keyed by `export_id`, and a
`/new-session` endpoint that opens a chat pre-seeded with the CSV's
context.
"""

import csv
import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_store
from api.schemas import (
    ExportDetail,
    ExportInfo,
    ExportUpdate,
    NewSessionFromExportRequest,
    NewSessionFromExportResponse,
)
from config import EXPORTS_DIR
from infra.persistence.runtime_store import ExportRecord, RuntimeStore
from infra.providers import get_default_provider, get_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/exports", tags=["csv-library"])

PREVIEW_ROW_LIMIT = 50


def _columns(record: ExportRecord) -> list[str]:
    try:
        cols = json.loads(record.columns_json)
        if isinstance(cols, list):
            return [str(c) for c in cols]
    except json.JSONDecodeError:
        logger.warning("Malformed columns_json for export %s", record.id)
    return []


def _to_info(record: ExportRecord) -> ExportInfo:
    return ExportInfo(
        id=record.id,
        filename=record.filename,
        title=record.title,
        row_count=record.row_count,
        columns=_columns(record),
        file_size=record.file_size,
        created_at=record.created_at,
        updated_at=record.updated_at,
        download_url=f"/exports/{record.filename}",
        source_session_id=record.source_session_id,
    )


@router.get("", response_model=list[ExportInfo])
async def list_csvs(store: RuntimeStore = Depends(get_store)):
    return [_to_info(r) for r in store.list_exports()]


@router.get("/{export_id}", response_model=ExportDetail)
async def get_csv_detail(export_id: str, store: RuntimeStore = Depends(get_store)):
    record = store.get_export(export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="CSV not found")

    preview_rows: list[dict] = []
    preview_truncated = False
    csv_path = EXPORTS_DIR / record.filename
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as fp:
                reader = csv.DictReader(fp)
                for idx, row in enumerate(reader):
                    if idx >= PREVIEW_ROW_LIMIT:
                        preview_truncated = True
                        break
                    preview_rows.append(row)
        except OSError as exc:
            logger.warning("Could not read CSV preview for %s: %s", record.filename, exc)
    else:
        # File is missing but registry row survives — reflect that honestly.
        logger.warning("CSV file missing on disk: %s", record.filename)

    info = _to_info(record)
    return ExportDetail(
        **info.model_dump(),
        sql=record.sql,
        preview_rows=preview_rows,
        preview_truncated=preview_truncated,
    )


@router.patch("/{export_id}", response_model=ExportInfo)
async def rename_csv(
    export_id: str,
    body: ExportUpdate,
    store: RuntimeStore = Depends(get_store),
):
    updated = store.update_export_title(export_id, body.title.strip())
    if updated is None:
        raise HTTPException(status_code=404, detail="CSV not found")
    return _to_info(updated)


@router.delete("/{export_id}")
async def delete_csv(export_id: str, store: RuntimeStore = Depends(get_store)):
    """Remove the registry row and unlink the on-disk file.

    Does not cascade to conversations seeded from this CSV — their
    seeded summary turn remains in the transcript, but the source_csv_id
    back-reference will no longer resolve (UI renders a 'CSV deleted'
    chip).
    """
    record = store.delete_export(export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="CSV not found")
    csv_path = EXPORTS_DIR / record.filename
    try:
        csv_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not unlink CSV file %s: %s", record.filename, exc)
    return {"status": "deleted"}


@router.post("/{export_id}/new-session", response_model=NewSessionFromExportResponse)
async def new_session_from_csv(
    export_id: str,
    body: NewSessionFromExportRequest,
    store: RuntimeStore = Depends(get_store),
):
    """Create a new conversation seeded with this CSV's context.

    Returns the new conversation_id. The client then opens the transcript
    and sends the first user message; the seeded summary turn is already
    in place so the LLM sees the CSV context from turn one.
    """
    record = store.get_export(export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="CSV not found")

    provider_name = body.provider or get_default_provider()
    try:
        info = get_provider(provider_name)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    model = body.model or info.default_model

    session = store.get_or_create_session(
        provider=provider_name,
        model=model,
        context_window=info.effective_context_window,
    )
    session.title = record.title
    store.update_session(session)
    store.set_session_source_csv(session.id, export_id)

    columns = _columns(record)
    summary_text = (
        f"The user has opened a saved CSV for this conversation.\n"
        f"- Title: {record.title}\n"
        f"- File: {record.filename}\n"
        f"- Row count: {record.row_count}\n"
        f"- Columns: {', '.join(columns) if columns else '(none recorded)'}\n"
        f"- Generated by this SQL:\n```sql\n{record.sql}\n```\n"
        f"Use this context for follow-up questions. You can reference the data "
        f"by re-running the SQL or variants of it; you do not have the CSV bytes directly."
    )
    store.seed_summary(session.id, summary_text)
    return NewSessionFromExportResponse(conversation_id=session.id)
