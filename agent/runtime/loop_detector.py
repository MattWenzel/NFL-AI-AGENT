"""Doom-loop detector: abort if the model calls the same tool with the
same input too many times in a row.

Scans the last DOOM_LOOP_WINDOW tool runs from the store plus the tool
calls queued for the current turn. If the tail DOOM_LOOP_MATCH
fingerprints are identical (tool name + input JSON), raise
`RuntimeLoopError` so the caller can surface a clear error instead of
burning through the iteration budget.
"""

from infra.persistence.runtime_store import RuntimeStore

from agent.runtime.events import RuntimeLoopError


DOOM_LOOP_WINDOW = 6
DOOM_LOOP_MATCH = 3


def raise_if_doom_loop(store: RuntimeStore, session_id: str, tool_runs) -> None:
    """Raise RuntimeLoopError if the last DOOM_LOOP_MATCH tool runs are identical."""
    recent = store.get_recent_tool_runs(session_id, limit=DOOM_LOOP_WINDOW)
    fingerprints = [(r.tool_name, r.input_json) for r in reversed(recent)]
    fingerprints.extend((r.tool_name, r.input_json) for r in tool_runs)
    if len(fingerprints) < DOOM_LOOP_MATCH:
        return
    tail = fingerprints[-DOOM_LOOP_MATCH:]
    if all(fp == tail[0] for fp in tail):
        raise RuntimeLoopError(
            f"Detected repeated tool loop on {tail[0][0]} with identical input"
        )
