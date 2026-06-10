"""Guide loader tool: return topic-specific markdown reference docs."""

import json

from backend.domain.providers.types import Tool
from backend.domain.tools.guide_registry import GUIDES_DIR, GUIDE_TOPICS


def _load_all() -> dict[str, str]:
    out = {}
    for topic in GUIDE_TOPICS:
        path = GUIDES_DIR / f"{topic}.md"
        out[topic] = path.read_text() if path.exists() else ""
    return out


_GUIDES = _load_all()


def _load_guide(input_data: dict, ctx: dict | None = None) -> str:
    topic = input_data.get("topic", "")
    if topic not in GUIDE_TOPICS:
        return json.dumps({
            "error": f"Unknown topic: {topic!r}. Available topics: {list(GUIDE_TOPICS)}",
        })
    content = _GUIDES.get(topic) or ""
    if not content:
        return json.dumps({
            "error": f"Guide {topic!r} is missing or empty on disk at {GUIDES_DIR / (topic + '.md')}",
        })
    return json.dumps({"topic": topic, "content": content})


TOOL = Tool(
    name="get_guide",
    description=(
        "Load a topic-specific guide for writing database queries. Each guide has "
        "column references, gotchas, and ready-to-copy SQL templates. Call BEFORE "
        "writing SQL for the topic. Topics: fantasy (scoring rules + kicker formula), "
        "player_stats (season/game stats + snap_counts + NGS + PFR + QBR column catalogs), "
        "play_by_play (372-col PBP reference + query patterns), "
        "drives (drive-level aggregations, TOP parsing, scoring-drive rates), "
        "postseason (playoff encoding across tables, Super Bowl/WC/DIV/CON queries), "
        "player_profile (IDs, draft, combine, depth charts), "
        "games (schedules + game-level joins + betting lines)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": list(GUIDE_TOPICS),
                "description": "Which guide to load.",
            },
        },
        "required": ["topic"],
    },
    handler=_load_guide,
)
