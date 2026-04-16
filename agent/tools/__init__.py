"""Tool registry and execution dispatch for the NFL stats agent.

Public surface:
    TOOLS                    — typed list[ToolDefinition] (preferred import)
    TOOL_DEFINITIONS         — raw Anthropic-format dicts
    execute_tool             — async tool runner returning a JSON string
    execute_tool_structured  — async tool runner returning a normalized envelope

Tool implementations live in per-tool modules; see `dispatch.py` for the
dispatch table.
"""

from agent.tools.definitions import TOOL_DEFINITIONS, TOOLS
from agent.tools.dispatch import execute_tool, execute_tool_structured

__all__ = [
    "TOOL_DEFINITIONS",
    "TOOLS",
    "execute_tool",
    "execute_tool_structured",
]
