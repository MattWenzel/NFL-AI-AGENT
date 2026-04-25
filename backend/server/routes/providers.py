"""GET /chat/providers — enumerate configured LLM providers.

`available` is true when either (a) the server has the provider's env-var
key set (server-configured fallback) or (b) the authenticated user has stored
their own key via /settings/api-keys.
"""

from fastapi import APIRouter, Depends

from backend.lib.auth.types import AuthenticatedUser
from backend.server.dependencies import get_current_user, get_provider_service
from backend.features.providers import ProviderResponse
from backend.features.providers import ProviderService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/providers", response_model=list[ProviderResponse])
async def get_providers(
    user: AuthenticatedUser = Depends(get_current_user),
    service: ProviderService = Depends(get_provider_service),
):
    """List available LLM providers and their configuration for the current user."""
    return await service.list_provider_availability(user.id)
