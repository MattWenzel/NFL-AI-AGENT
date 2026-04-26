"""GET /chat/providers — enumerate configured LLM providers.

`available` is true when either (a) the server has the provider's env-var
key set (server-configured fallback) or (b) the authenticated user has stored
their own key via /settings/api-keys.
"""

from fastapi import APIRouter, Depends

from backend.domain.auth.types import AuthenticatedUser
from backend.api.dependencies import get_current_user, get_store
from backend.api.schemas.providers import ProviderResponse
from backend.data import RuntimeStore
from backend.domain.providers import list_providers, provider_is_available

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/providers", response_model=list[ProviderResponse])
async def get_providers(
    user: AuthenticatedUser = Depends(get_current_user),
    store: RuntimeStore = Depends(get_store),
) -> list[ProviderResponse]:
    """List available LLM providers and their configuration for the current user."""
    existing_keys = {rec.provider for rec in await store.list_api_keys(user.id)}
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
        for info in list_providers()
    ]
