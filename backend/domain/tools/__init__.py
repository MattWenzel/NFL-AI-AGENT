"""Tool catalog and execution dispatch for the NFL stats agent.

Public surface:
    TOOLS                    — typed list[Tool] (schema + handler per tool)
    execute_tool             — async tool runner returning a JSON string
    execute_tool_structured  — async tool runner returning a normalized envelope

Each tool lives in its own sibling module (`execute_sql.py`,
`set_table.py`, …) exporting `TOOL = Tool(...)`; `registry.py` assembles
the catalog and owns dispatch.
"""

from backend.domain.tools.registry import TOOLS, execute_tool, execute_tool_structured

__all__ = [
    "TOOLS",
    "execute_tool",
    "execute_tool_structured",
]
