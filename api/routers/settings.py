"""Settings endpoints — per-user API key storage.

Keys are stored encrypted (Fernet / AES-128) via `infra.encryption`. Plaintext
never leaves these handlers: GET returns only status + updated_at, never the key.

OAuth-backed providers (credential_shape="codex_oauth") route through
`api/routers/oauth_codex.py` instead of accepting a pasted key. The GET
surfaces their linked-email + expiry so the UI can render the correct
status; the PUT rejects with a pointer to the OAuth flow.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import AuthenticatedUser, get_current_user
from api.dependencies import get_store
from api.schemas import ApiKeyStatus, ApiKeyUpdate
from infra import codex_oauth, encryption
from infra.persistence.runtime_store import RuntimeStore, UserApiKeyRecord
from infra.providers import ProviderInfo, get_provider, list_providers

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


def _build_status(info: ProviderInfo, rec: UserApiKeyRecord | None) -> ApiKeyStatus:
    """Shape an ApiKeyStatus for the Settings UI.

    For OAuth providers we decrypt the stored blob and surface the linked
    email + expiry so the UI can render "Linked: user@example.com". Raw
    tokens never leave this handler.
    """
    if info.credential_shape == "codex_oauth" and rec is not None:
        email: str | None = None
        expires_at: int | None = None
        try:
            plaintext = encryption.decrypt(rec.encrypted_key)
            bundle = codex_oauth.bundle_from_json(plaintext)
            email = bundle.email
            expires_at = bundle.expires_at
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            # Ciphertext corruption or blob shape drift. Still report
            # has_key=True so the UI offers a Disconnect button rather
            # than a Connect button (which would stack a second blob).
            logger.warning(
                "Could not decode Codex bundle for user=%d: %s", rec.user_id, exc
            )
        return ApiKeyStatus(
            provider=info.name,
            display_name=info.display_name,
            has_key=True,
            updated_at=rec.updated_at,
            credential_shape=info.credential_shape,
            email=email,
            expires_at=expires_at,
        )
    return ApiKeyStatus(
        provider=info.name,
        display_name=info.display_name,
        has_key=rec is not None,
        updated_at=rec.updated_at if rec else None,
        credential_shape=info.credential_shape,
    )


@router.get("/api-keys", response_model=list[ApiKeyStatus])
def list_api_key_status(
    user: AuthenticatedUser = Depends(get_current_user),
    store: RuntimeStore = Depends(get_store),
) -> list[ApiKeyStatus]:
    """Return per-provider key status. Never returns plaintext."""
    existing = {rec.provider: rec for rec in store.list_api_keys(user.id)}
    return [_build_status(info, existing.get(info.name)) for info in list_providers()]


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

    if info.credential_shape == "codex_oauth" and raw:
        # OAuth providers can't accept a pasted key — there's no raw-key
        # form for Codex. Empty/null is still allowed so the UI can
        # disconnect through this same endpoint.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Codex uses OAuth — use POST /settings/oauth/codex/start to connect.",
        )

    if raw is None or raw == "":
        # Delete the key
        store.delete_api_key(user_id=user.id, provider=provider)
        logger.info("Deleted %s API key for user %d", provider, user.id)
        return _build_status(info, None)

    ciphertext = encryption.encrypt(raw)
    rec = store.upsert_api_key(user_id=user.id, provider=provider, encrypted_key=ciphertext)
    logger.info("Saved %s API key for user %d", provider, user.id)
    return _build_status(info, rec)
