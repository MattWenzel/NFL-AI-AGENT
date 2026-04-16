"""Schema discovery endpoints."""

from fastapi import APIRouter, HTTPException

from api.schema_registry import (
    build_schema_response,
    build_table_schema,
    resolve_table_name,
)

router = APIRouter(tags=["schema"])


@router.get("/schema")
def get_schema():
    """
    Returns the full database schema: all 13 tables, columns, types,
    join relationships, and table aliases.

    Use this to discover what's available before building queries.
    """
    return build_schema_response()


@router.get("/schema/{table_name}")
def get_table_schema(table_name: str):
    """
    Returns schema for a single table including columns, types,
    and join relationships.

    Accepts table name or alias (e.g., 'players' or 'p').
    """
    resolved = resolve_table_name(table_name)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")
    schema = build_table_schema(resolved)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")
    return schema
