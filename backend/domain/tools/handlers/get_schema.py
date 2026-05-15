"""Schema discovery tool: the agent-facing wrapper.

The actual introspection + projection logic lives in
`backend.domain.tools.sandbox.schema`. This module is just the tool
handler that translates the canonical schema response into a JSON string
for the agent.
"""

import json
import logging

from backend.domain.tools.sandbox.schema import (
    build_schema_response,
    build_table_schema,
)

logger = logging.getLogger(__name__)


def _get_schema(input_data: dict, ctx: dict | None = None) -> str:
    table_name = input_data.get("table_name", "")
    if table_name:
        result = build_table_schema(table_name)
        if result is None:
            return json.dumps({"error": f"Unknown table: {table_name}"})
    else:
        result = build_schema_response()
    # Schema responses are structured JSON; character-level truncation corrupts
    # them. Return the full payload — callers treat schema as reference data.
    return json.dumps(result)
