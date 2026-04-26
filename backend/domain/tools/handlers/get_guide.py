"""Guide loader tool: return topic-specific markdown reference docs."""

import json

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
