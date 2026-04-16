"""GET /chat/providers — enumerate configured LLM providers."""

from fastapi import APIRouter

from api.schemas import ProviderResponse
from infra.providers import list_providers, provider_is_available

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/providers", response_model=list[ProviderResponse])
async def get_providers():
    """List available LLM providers and their configuration."""
    return [
        ProviderResponse(
            name=info.name,
            display_name=info.display_name,
            models=info.models,
            default_model=info.default_model,
            available=provider_is_available(info),
            context_window=info.context_window,
            supports_streaming=info.supports_streaming,
            supports_tools=info.supports_tools,
        )
        for info in list_providers()
    ]
