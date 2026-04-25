"""Tool registry and execution dispatch for the NFL stats agent.

Public surface:
    TOOLS                    — typed list[ToolDefinition] (preferred import)
    TOOL_DEFINITIONS         — raw Anthropic-format dicts
    execute_tool             — async tool runner returning a JSON string
    execute_tool_structured  — async tool runner returning a normalized envelope

Tool implementations live in sibling modules; see `registry.py` for the
dispatch table.
"""

from core.tools.definitions import TOOL_DEFINITIONS, TOOLS
from core.tools.registry import execute_tool, execute_tool_structured

__all__ = [
    "TOOL_DEFINITIONS",
    "TOOLS",
    "execute_tool",
    "execute_tool_structured",
]
