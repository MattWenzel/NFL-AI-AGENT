"""Codex OAuth token resolution with refresh locking.

Exposes `resolve_access_token(store, user_id, provider)` — the single entry
point used by HTTP-level credential resolution. Encapsulates:

- Reading and decrypting the stored token bundle (tolerant of tampered
  ciphertext, malformed JSON, or missing records — all collapse to None
  so callers treat the state as "not connected").
- Near-expiry refresh via OpenAI's token endpoint, under a per-user lock
  so two concurrent requests don't race and leave the loser's refresh
  token stale.
- Persisting the refreshed bundle back to the store.

Raises `HTTPException(503)` if the refresh call itself fails — the user
IS connected, they just need to reconnect. Returns None for the "never
connected / unreadable" cases so the caller can surface a distinct
configure-a-key error.

In-process only — the refresh lock dict is per-worker. Fine on the
single-worker deploy (same constraint as the in-memory rate limiter);
if this ever needs multiple workers, swap for a DB-level lock.
"""

from __future__ import annotations

import asyncio
import json
import logging

from auth import codex_oauth, encryption
from server.process_state import InMemoryPerUserLockRegistry
from storage import RuntimeStore

logger = logging.getLogger(__name__)

class CodexCredentialError(Exception):
    """Raised when a stored Codex connection exists but refresh fails."""


_refresh_locks = InMemoryPerUserLockRegistry()


def _load_bundle(
    store: RuntimeStore, user_id: int, provider_name: str
) -> codex_oauth.TokenBundle | None:
    """Read and decrypt the stored Codex bundle, returning None on any
    "effectively missing" state (no record, tampered ciphertext, malformed
    JSON)."""
    rec = store.get_api_key(user_id=user_id, provider=provider_name)
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
    store: RuntimeStore, user_id: int, provider_name: str
) -> str | None:
    """Return a valid bearer access token for the user's Codex connection.

    Refreshes under a per-user lock if the bundle is within 30s of expiry.
    Returns None when the user is not connected; raises HTTPException(503)
    when refresh fails.
    """
    bundle = _load_bundle(store, user_id, provider_name)
    if bundle is None:
        return None
    if not codex_oauth.is_near_expiry(bundle):
        return bundle.access_token
    # Refresh path: serialize across concurrent requests for this user and
    # re-read under the lock — a concurrent request may have already
    # refreshed and persisted while we were waiting.
    async with _refresh_locks.for_user(user_id):
        bundle = _load_bundle(store, user_id, provider_name)
        if bundle is None:
            return None
        if not codex_oauth.is_near_expiry(bundle):
            return bundle.access_token
        try:
            bundle = await codex_oauth.refresh_access_token(bundle.refresh_token)
        except codex_oauth.CodexOAuthError as exc:
            logger.warning("Codex token refresh failed for user=%d: %s", user_id, exc)
            raise CodexCredentialError("ChatGPT session expired — reconnect in Settings.") from exc
        store.upsert_api_key(
            user_id=user_id,
            provider=provider_name,
            encrypted_key=encryption.encrypt(codex_oauth.bundle_to_json(bundle)),
        )
        logger.info("Refreshed Codex token for user=%d", user_id)
        return bundle.access_token
