"""Provider process: schemas and application service."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from backend.lib.db import RuntimeStore
from backend.lib.providers import list_providers, provider_is_available


class ProviderResponse(BaseModel):
    name: str
    display_name: str
    models: list[str]
    default_model: str
    available: bool
    context_window: int
    supports_streaming: bool
    supports_tools: bool


@dataclass
class ProviderService:
    store: RuntimeStore

    async def list_provider_availability(self, user_id: int) -> list[ProviderResponse]:
        infos = list_providers()
        existing_keys = {rec.provider for rec in await self.store.list_api_keys(user_id)}
        return [
            ProviderResponse(
                name=info.name,
                display_name=info.display_name,
                models=info.models,
                default_model=info.default_model,
                available=provider_is_available(info) or info.name in existing_keys,
                context_window=info.context_window,
                supports_streaming=info.supports_streaming,
                supports_tools=info.supports_tools,
            )
            for info in infos
        ]
