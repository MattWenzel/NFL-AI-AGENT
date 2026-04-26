"""GET /chat/providers — enumerate configured LLM providers.

`available` is true when either (a) the server has the provider's env-var
key set (server-configured fallback) or (b) the authenticated user has stored
their own key via /settings/api-keys.
"""

from fastapi import APIRouter, Depends

from backend.domain.auth.types import AuthenticatedUser
from backend.api.dependencies import get_current_user, get_settings_service
from backend.api.schemas.providers import ProviderResponse
from backend.application.settings import SettingsService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/providers", response_model=list[ProviderResponse])
async def get_providers(
    user: AuthenticatedUser = Depends(get_current_user),
    service: SettingsService = Depends(get_settings_service),
) -> list[ProviderResponse]:
    """List available LLM providers and their configuration for the current user."""
    return await service.list_provider_status(user.id)
