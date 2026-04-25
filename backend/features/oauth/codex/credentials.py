"""Codex OAuth token resolution with refresh locking.

Service-layer orchestration over storage, decryption, token refresh, and
per-user coordination locks.
"""

from __future__ import annotations

import json
import logging

from backend.lib.auth import codex_oauth, encryption
from backend.lib.auth.errors import CodexOAuthError
from backend.lib.auth.types import TokenBundle
from backend.lib.storage import RuntimeStore
from backend.runtime_state import PerUserLockRegistry

logger = logging.getLogger(__name__)


class CodexCredentialError(Exception):
    """Raised when a stored Codex connection exists but refresh fails."""


async def _load_bundle(
    store: RuntimeStore, user_id: int, provider_name: str
) -> TokenBundle | None:
    rec = await store.get_api_key(user_id=user_id, provider=provider_name)
    if rec is None:
        return None
    try:
        blob_json = encryption.decrypt(rec.encrypted_key)
    except ValueError:
        logger.error("Failed to decrypt stored Codex bundle for user=%d", user_id)
        return None
    try:
        return codex_oauth.bundle_from_json(blob_json)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Malformed Codex bundle for user=%d: %s", user_id, exc)
        return None


async def resolve_access_token(
    store: RuntimeStore,
    user_id: int,
    provider_name: str,
    *,
    refresh_locks: PerUserLockRegistry,
) -> str | None:
    bundle = await _load_bundle(store, user_id, provider_name)
    if bundle is None:
        return None
    if not codex_oauth.is_near_expiry(bundle):
        return bundle.access_token
    async with refresh_locks.for_user(user_id):
        bundle = await _load_bundle(store, user_id, provider_name)
        if bundle is None:
            return None
        if not codex_oauth.is_near_expiry(bundle):
            return bundle.access_token
        try:
            bundle = await codex_oauth.refresh_access_token(bundle.refresh_token)
        except CodexOAuthError as exc:
            logger.warning("Codex token refresh failed for user=%d: %s", user_id, exc)
            raise CodexCredentialError("ChatGPT session expired — reconnect in Settings.") from exc
        await store.upsert_api_key(
            user_id=user_id,
            provider=provider_name,
            encrypted_key=encryption.encrypt(codex_oauth.bundle_to_json(bundle)),
        )
        logger.info("Refreshed Codex token for user=%d", user_id)
        return bundle.access_token
