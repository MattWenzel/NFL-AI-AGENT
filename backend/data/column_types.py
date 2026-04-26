"""Custom SQLAlchemy types shared by the ORM models.

`TolerantJSONList` preserves the current log-and-recover behavior for
`compaction_summaries.source_turn_ids` — a single corrupt row must not
wedge `get_transcript` and therefore the whole session.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)


class TolerantJSONList(TypeDecorator):
    """Store a `list[str]` as a JSON string; fall back to [] on malformed data.

    Behavior matches the pre-ORM `row_to_summary` mapper: malformed JSON is
    logged at WARNING and replaced with [] so the transcript assembles.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return "[]"
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            logger.warning("Malformed JSON in tolerant list column: %s", exc)
            return []
        if not isinstance(parsed, list):
            logger.warning(
                "Tolerant list column holds non-list JSON (type=%s)",
                type(parsed).__name__,
            )
            return []
        return parsed


class ToolInputJSON(TypeDecorator):
    """Store a `dict` as a JSON string with canonical ordering.

    Behavior matches the pre-ORM `safe_load_tool_input`: malformed JSON or
    non-object values return `{}` with a WARNING. One corrupted row must not
    wedge compaction, message build, or tool execution for the whole session.

    Canonical ordering (sort_keys=True) on write is load-bearing for the
    doom-loop detector: `raise_if_doom_loop` fingerprints consecutive tool
    calls on the stored JSON string, so two equivalent dicts with different
    iteration orders must serialize identically.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return "{}"
        return json.dumps(value, sort_keys=True)

    def process_result_value(self, value, dialect):
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            logger.warning("Malformed tool input JSON: %s", exc)
            return {}
        if not isinstance(parsed, dict):
            logger.warning(
                "Tool input JSON is not an object (type=%s)",
                type(parsed).__name__,
            )
            return {}
        return parsed
