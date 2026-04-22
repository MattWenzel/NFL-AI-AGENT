"""GET /chat/providers — enumerate configured LLM providers.

`available` is true when either (a) the server has the provider's env-var
key set (server-configured fallback) or (b) the authenticated user has stored
their own key via /settings/api-keys.
"""

from fastapi import APIRouter, Depends

from auth.primitives import AuthenticatedUser
from server.dependencies import get_current_user, get_settings_service
from server.schemas.providers import ProviderResponse
from server.services.settings import SettingsApplicationService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/providers", response_model=list[ProviderResponse])
async def get_providers(
    user: AuthenticatedUser = Depends(get_current_user),
    service: SettingsApplicationService = Depends(get_settings_service),
):
    """List available LLM providers and their configuration for the current user."""
    return await service.list_provider_availability(user.id)
