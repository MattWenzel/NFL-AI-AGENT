"""Tool input validation and error hint injection.

Keeps tool errors actionable:
- `validate_tool_input` rejects malformed input before dispatch using
  full JSON Schema semantics (enum, pattern, array, number, boolean,
  nested objects) via the `jsonschema` library — catches invalid enum
  values and wrong array element types that the previous type-only
  checker let through.
- `inject_hint` post-processes tool JSON errors to append a short
  remediation hint for known patterns (ambiguous column, missing
  table, timeout, etc.).
"""

import json

from jsonschema import Draft202012Validator

from backend.domain.tools.definitions import TOOL_DEFINITIONS


def _get_tool_definition(name: str) -> dict | None:
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return tool
    return None


# Cache one validator per tool. Building the validator parses the schema
# and builds the checker graph — doing it per-call adds measurable
# overhead on a hot path that runs before every tool invocation.
_VALIDATOR_CACHE: dict[str, Draft202012Validator] = {}


def _validator_for(name: str) -> Draft202012Validator | None:
    cached = _VALIDATOR_CACHE.get(name)
    if cached is not None:
        return cached
    tool = _get_tool_definition(name)
    if tool is None:
        return None
    schema = tool.get("input_schema", {})
    if not schema:
        return None
    validator = Draft202012Validator(schema)
    _VALIDATOR_CACHE[name] = validator
    return validator


def _strip_codex_nulls(tool: dict, input_data: dict) -> dict:
    """Drop `null` values for optional properties at any nesting depth.

    Codex strict-mode schemas force every property into `required` and
    make optionals nullable, so Codex sends `{"topic": null, "extra": null}`
    for unused optionals. The validator sees null-vs-string as a type
    mismatch; handlers use `.get()` + falsy checks so absent and
    explicit-null are identical to them. Walk the input alongside the
    schema and drop nulls that map to not-originally-required properties
    at every level — none of our 7 tools have nested object inputs
    today, but the recursion future-proofs the validator.
    """
    schema = tool.get("input_schema") or {}
    return _strip_nulls_recursive(input_data, schema)


def _strip_nulls_recursive(value, schema):
    if not isinstance(schema, dict):
        return value
    schema_type = schema.get("type")
    if isinstance(value, dict) and (schema_type == "object" or "properties" in schema):
        required = set(schema.get("required") or [])
        properties = schema.get("properties") or {}
        cleaned: dict = {}
        for key, sub_value in value.items():
            sub_schema = properties.get(key)
            if sub_value is None and key in properties and key not in required:
                continue
            if sub_schema is not None:
                cleaned[key] = _strip_nulls_recursive(sub_value, sub_schema)
            else:
                cleaned[key] = sub_value
        return cleaned
    if isinstance(value, list) and (schema_type == "array" or "items" in schema):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [_strip_nulls_recursive(item, item_schema) for item in value]
    return value


def validate_tool_input(name: str, input_data: dict) -> str | None:
    """Return an error string if input_data violates the tool's schema, else None.

    Uses `jsonschema.Draft202012Validator` so we catch enum mismatches,
    pattern violations, array element types, and number/boolean types —
    things the previous hand-rolled checker missed. The error text names
    the offending property path so the hint injector can point at it.
    """
    tool = _get_tool_definition(name)
    if tool is None:
        return f"Unknown tool: {name}"
    if not isinstance(input_data, dict):
        return f"{name} expects an object input"

    validator = _validator_for(name)
    if validator is None:
        return None

    cleaned = _strip_codex_nulls(tool, input_data)
    # First reported error wins — the model gets one clear message to
    # retry against instead of a noisy multi-line dump.
    errors = sorted(validator.iter_errors(cleaned), key=lambda e: e.path)
    if not errors:
        return None
    err = errors[0]
    path = ".".join(str(p) for p in err.absolute_path)
    if path:
        return f"Invalid input for {name}: {path}: {err.message}"
    return f"Invalid input for {name}: {err.message}"


_ERROR_HINTS = [
    ("ambiguous", "Hint: Prefix columns with table name (e.g., season_stats.player_gsis_id)."),
    ("not found in table", "Hint: Call get_schema(table_name) to see available columns before retrying."),
    ("no such column", "Hint: Call get_schema(table_name) to see available columns before retrying."),
    ("not found in any queried table", "Hint: Call get_schema(table_name) to see available columns."),
    ("timed out", "Hint: Add WHERE filters (season, team, or player). For snap_counts, always filter by season."),
    ("no join path", "Hint: players has player_gsis_id / player_pfr_id / player_espn_id — join directly instead of going through player_ids."),
    ("no such table", "Hint: Valid tables: players, player_ids, games, stadiums, officials, team_game_stats, team_season_stats, game_stats, season_stats, weekly_rosters, snap_counts, ngs_stats, pfr_advanced, pfr_advanced_weekly, qbr, draft_picks, combine, injuries, contracts, contracts_cap_breakdown, v_depth_charts (preferred depth-chart view), depth_charts, depth_charts_2025, play_by_play, pbp_participation, ftn_charting."),
    ("invalid table", "Hint: Valid tables: players, player_ids, games, stadiums, officials, team_game_stats, team_season_stats, game_stats, season_stats, weekly_rosters, snap_counts, ngs_stats, pfr_advanced, pfr_advanced_weekly, qbr, draft_picks, combine, injuries, contracts, contracts_cap_breakdown, v_depth_charts (preferred depth-chart view), depth_charts, depth_charts_2025, play_by_play, pbp_participation, ftn_charting."),
]


def _classify_error(error_text: str) -> str | None:
    """Return an actionable hint for a known error pattern, or None."""
    lower = error_text.lower()
    for pattern, hint in _ERROR_HINTS:
        if pattern in lower:
            return hint
    return None


def inject_hint(result_str: str) -> str:
    """If result_str is a JSON error, append a hint key when a known pattern matches."""
    try:
        data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str
    if not isinstance(data, dict):
        return result_str
    error_text = data.get("error") or data.get("detail")
    if not error_text or not isinstance(error_text, str):
        return result_str
    hint = _classify_error(error_text)
    if hint:
        data["hint"] = hint
        return json.dumps(data)
    return result_str
