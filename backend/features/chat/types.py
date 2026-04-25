"""Internal DTOs for the chat process."""

from __future__ import annotations

from dataclasses import dataclass

from backend.providers.base import BaseLLMClient
from backend.storage import SessionRecord


@dataclass
class PreparedChat:
    client: BaseLLMClient
    provider_name: str
    session: SessionRecord


@dataclass
class ToolCallLogEntry:
    tool_run_id: str
    tool: str
    input: dict
    result_preview: str = ""
