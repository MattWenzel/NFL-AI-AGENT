"""Shared helpers for tool implementations: result truncation."""

import json

TOOL_RESULT_MAX_CHARS = 8000


def truncate_text(text: str) -> str:
    """Truncate plain-text tool result to max chars (fallback for non-structured data)."""
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    return text[:TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"


def truncate_rows(rows: list[dict], key: str = "rows", **extra) -> str:
    """Serialize rows to JSON, dropping trailing rows if over the char limit.

    Produces valid JSON even when truncation is needed, unlike character-level
    truncation which can break mid-object.  Extra keyword arguments (e.g.
    columns, row_count) are included in the output dict.
    """
    total = extra.pop("total", len(rows))
    output = {**extra, key: rows, "total": total}
    serialized = json.dumps(output)
    if len(serialized) <= TOOL_RESULT_MAX_CHARS:
        return serialized

    # Binary search for the max number of rows that fit
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = {**extra, key: rows[:mid], "total": total, "note": f"Showing {mid} of {total} rows (truncated to fit)"}
        if len(json.dumps(candidate)) <= TOOL_RESULT_MAX_CHARS:
            lo = mid
        else:
            hi = mid - 1

    if lo == 0:
        # Single row too large — character-truncate the first row so the LLM
        # gets enough context to refine its query (e.g. select fewer columns).
        first = json.dumps(rows[0])[:TOOL_RESULT_MAX_CHARS - 200]
        return json.dumps({**extra, "total": total, "note": "First row too large to fit — showing truncated preview", "preview": first})

    truncated = {**extra, key: rows[:lo], "total": total, "note": f"Showing {lo} of {total} rows (truncated to fit)"}
    return json.dumps(truncated)
