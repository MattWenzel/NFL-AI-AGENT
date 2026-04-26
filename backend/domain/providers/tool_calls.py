"""Shared helpers for parsing provider tool-call payloads."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from backend.domain.providers.types import ToolUseEvent


def build_tool_use_event(
    *,
    tool_id: str,
    tool_name: str,
    arguments: str,
    logger: logging.Logger,
    context: str,
) -> ToolUseEvent:
    try:
        tool_input = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        logger.warning("%s: invalid JSON arguments %r", context, arguments[:200])
        tool_input = {}
    return ToolUseEvent(id=tool_id, name=tool_name, input=tool_input)


def emit_accumulated_tool_calls(
    tool_calls_acc: Mapping[int, Mapping[str, str]],
    *,
    logger: logging.Logger,
    context: str,
) -> list[ToolUseEvent]:
    events: list[ToolUseEvent] = []
    for idx in sorted(tool_calls_acc.keys()):
        acc = tool_calls_acc[idx]
        tool_id = acc.get("id", "")
        tool_name = acc.get("name", "")
        if tool_id and tool_name:
            events.append(
                build_tool_use_event(
                    tool_id=tool_id,
                    tool_name=tool_name,
                    arguments=acc.get("arguments", ""),
                    logger=logger,
                    context=context,
                )
            )
        else:
            logger.warning(
                "%s: dropping incomplete tool call at index %d: id=%r name=%r",
                context,
                idx,
                tool_id,
                tool_name,
            )
    return events
