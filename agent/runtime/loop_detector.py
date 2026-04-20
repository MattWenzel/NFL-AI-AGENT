"""Doom-loop detector: abort if the agent calls the same tool with the
same input too many times while reasoning about one user message.

Scope is one user turn — the list the runtime accumulates across inner
iterations of `run_session` and resets when the next user message
arrives. If the tail DOOM_LOOP_MATCH fingerprints are identical (tool
name + input JSON), raise `RuntimeLoopError` so the caller can surface
a clear error instead of burning through the iteration budget.

Repeating a query in a follow-up user turn is legitimate ("try again",
"rerun that") and must not trip the detector.
"""

from agent.runtime.events import RuntimeLoopError


DOOM_LOOP_MATCH = 3


def raise_if_doom_loop(tool_runs) -> None:
    """Raise RuntimeLoopError if the tail DOOM_LOOP_MATCH tool runs are identical.

    `tool_runs` is the list accumulated across inner iterations of the
    current user turn — not a session-wide history. Callers pass a fresh
    list per user message.
    """
    if len(tool_runs) < DOOM_LOOP_MATCH:
        return
    tail = [(r.tool_name, r.input_json) for r in tool_runs[-DOOM_LOOP_MATCH:]]
    if all(fp == tail[0] for fp in tail):
        raise RuntimeLoopError(
            f"Detected repeated tool loop on {tail[0][0]} with identical input"
        )
