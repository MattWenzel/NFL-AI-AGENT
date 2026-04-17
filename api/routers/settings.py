"""Settings endpoints — per-user API key storage.

Keys are stored encrypted (Fernet / AES-128) via `infra.encryption`. Plaintext
never leaves these handlers: GET returns only status + updated_at, never the key.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import AuthenticatedUser, get_current_user
from api.dependencies import get_store
from api.schemas import ApiKeyStatus, ApiKeyUpdate
from infra import encryption
from infra.persistence.runtime_store import RuntimeStore
from infra.providers import get_provider, list_providers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _validate_provider(provider: str) -> None:
    try:
        get_provider(provider)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown provider '{provider}'",
        )


@router.get("/api-keys", response_model=list[ApiKeyStatus])
def list_api_key_status(
    user: AuthenticatedUser = Depends(get_current_user),
    store: RuntimeStore = Depends(get_store),
) -> list[ApiKeyStatus]:
    """Return per-provider key status. Never returns plaintext."""
    existing = {rec.provider: rec for rec in store.list_api_keys(user.id)}
    out: list[ApiKeyStatus] = []
    for info in list_providers():
        rec = existing.get(info.name)
        out.append(
            ApiKeyStatus(
                provider=info.name,
                display_name=info.display_name,
                has_key=rec is not None,
                updated_at=rec.updated_at if rec else None,
            )
        )
    return out


@router.put("/api-keys/{provider}", response_model=ApiKeyStatus)
def update_api_key(
    provider: str,
    payload: ApiKeyUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
    store: RuntimeStore = Depends(get_store),
) -> ApiKeyStatus:
    _validate_provider(provider)
    info = get_provider(provider)

    raw = (payload.api_key or "").strip() if payload.api_key is not None else None

    if raw is None or raw == "":
        # Delete the key
        store.delete_api_key(user_id=user.id, provider=provider)
        logger.info("Deleted %s API key for user %d", provider, user.id)
        return ApiKeyStatus(
            provider=provider,
            display_name=info.display_name,
            has_key=False,
            updated_at=None,
        )

    ciphertext = encryption.encrypt(raw)
    rec = store.upsert_api_key(user_id=user.id, provider=provider, encrypted_key=ciphertext)
    logger.info("Saved %s API key for user %d", provider, user.id)
    return ApiKeyStatus(
        provider=provider,
        display_name=info.display_name,
        has_key=True,
        updated_at=rec.updated_at,
    )
