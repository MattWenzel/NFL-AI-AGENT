"""GET /chat/providers — enumerate configured LLM providers.

`available` is true when either (a) the server has the provider's env-var
key set (CLI/admin fallback) or (b) the authenticated user has stored
their own key via /settings/api-keys.
"""

from fastapi import APIRouter, Depends

from api.auth import AuthenticatedUser, get_current_user
from api.dependencies import get_store
from api.schemas import ProviderResponse
from infra.persistence.runtime_store import RuntimeStore
from infra.providers import list_providers, provider_is_available

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/providers", response_model=list[ProviderResponse])
async def get_providers(
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List available LLM providers and their configuration for the current user."""
    return [
        ProviderResponse(
            name=info.name,
            display_name=info.display_name,
            models=info.models,
            default_model=info.default_model,
            available=(
                provider_is_available(info)
                or store.user_has_api_key(user_id=user.id, provider=info.name)
            ),
            context_window=info.context_window,
            supports_streaming=info.supports_streaming,
            supports_tools=info.supports_tools,
        )
        for info in list_providers()
    ]
