"""Settings endpoints for per-user API key storage."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from auth.primitives import AuthenticatedUser
from server.dependencies import get_current_user, get_settings_service
from server.schemas.settings import ApiKeyStatus, ApiKeyUpdate
from server.services.settings import (
    SettingsApplicationService,
    SettingsNotFoundError,
    SettingsServiceError,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/api-keys", response_model=list[ApiKeyStatus])
async def list_api_key_status(
    user: AuthenticatedUser = Depends(get_current_user),
    service: SettingsApplicationService = Depends(get_settings_service),
) -> list[ApiKeyStatus]:
    return await service.list_api_key_status(user.id)


@router.put("/api-keys/{provider}", response_model=ApiKeyStatus)
async def update_api_key(
    provider: str,
    payload: ApiKeyUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SettingsApplicationService = Depends(get_settings_service),
) -> ApiKeyStatus:
    try:
        return await service.update_api_key(
            user_id=user.id,
            provider=provider,
            api_key=payload.api_key,
        )
    except SettingsNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SettingsServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
