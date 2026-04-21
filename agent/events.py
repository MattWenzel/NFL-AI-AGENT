"""Runtime event types surfaced by the chat loop.

A single `RuntimeEvent` dataclass carries every signal the loop emits:
assistant text deltas, tool calls, tool completions/failures, compaction
notices, runtime errors, and turn lifecycle events. The `type` field
discriminates between them so downstream transports
can branch on one field.
"""

from dataclasses import dataclass


@dataclass
class RuntimeEvent:
    type: str
    session_id: str
    turn_id: str | None = None
    text: str | None = None
    tool_run_id: str | None = None
    name: str | None = None
    input: dict | None = None
    result: str | None = None
    error: str | None = None
    iterations: int | None = None
    status: str | None = None
    meta: dict | None = None
    # Retry-specific fields surfaced by the `retrying` event so the UI can
    # render "retrying after rate limit (attempt 2/4, ~4s)" instead of a
    # silent stall.
    attempt: int | None = None
    delay_seconds: float | None = None


class RuntimeLoopError(Exception):
    """Raised when the runtime detects an unrecoverable loop condition."""
