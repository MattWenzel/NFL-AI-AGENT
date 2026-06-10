"""Shared types for the chat runtime.

Event variants live in `agent/events.py`; `RuntimeLoopError` is defined
in `agent/turn.py` next to the doom-loop detector that raises it. This
module holds the contracts and value types consumed by `Turn` and
`ChatRuntime` — the tool-execution seam and its structured result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


class ToolExecutor(Protocol):
    """Callable that runs one tool invocation on behalf of the runtime.

    Supplied by the caller of `Turn` (in production: the tool registry's
    `execute_tool_structured`). Kept as a Protocol so tests can inject a
    lightweight stub without depending on the real registry.
    """

    async def __call__(
        self, tool_name: str, tool_input: dict, *, ctx: dict | None = None
    ) -> dict: ...


@dataclass(frozen=True)
class ToolExecutionResult:
    """Normalized outcome of a single tool run, as seen by the runtime."""
    status: Literal["completed", "error"]
    content: str
    error: str | None = None
    duration_ms: int | None = None

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"
