"""FastAPI dependencies and HTTP-level helpers shared by chat routers.

- `get_store` / `get_runtime`: resolve the `RuntimeStore` and
  `ChatRuntime` attached to `app.state` by the lifespan. Use via
  `Depends(get_store)`.
- `resolve_user_credential`: look up (and if needed refresh) the
  authenticated user's stored credential for a provider. Returns the
  string that the client constructor wants — an API key for the
  classic providers, a bearer access_token for Codex OAuth.
- `create_client_for_request`: translate provider/model request params
  into a live `BaseLLMClient`, raising the right HTTP status when the
  provider is unknown or its key is missing.
- `close_client`: swallow close-errors so one failing client doesn't
  crash request teardown.
"""

import json
import logging

from fastapi import HTTPException, Request

from agent.runtime import ChatRuntime
from infra import codex_oauth, encryption
from infra.persistence.runtime_store import RuntimeStore
from infra.providers import (
    BaseLLMClient,
    LLMError,
    create_client,
    get_default_provider,
    get_provider,
    provider_is_available,
)

logger = logging.getLogger(__name__)

CODEX_PROVIDER = "openai-codex"


def get_store(request: Request) -> RuntimeStore:
    store = getattr(request.app.state, "runtime_store", None)
    if store is None:
        raise RuntimeError(
            "runtime_store not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return store


def get_runtime(request: Request) -> ChatRuntime:
    runtime = getattr(request.app.state, "chat_runtime", None)
    if runtime is None:
        raise RuntimeError(
            "chat_runtime not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return runtime


async def resolve_user_credential(
    store: RuntimeStore, user_id: int, provider_name: str
) -> str | None:
    """Return the live credential string for the given user+provider.

    For classic API-key providers this is the decrypted stored key. For
    `openai-codex` this is a bearer access token, refreshed via OpenAI's
    OAuth token endpoint when within 30s of expiry. Returns None if the
    user has no stored credential or if their stored blob is unreadable
    (tampered ciphertext, schema drift, etc.) — callers can then fall
    back to the env var or surface the configure-a-key error.
    """
    try:
        info = get_provider(provider_name)
    except KeyError:
        return None
    if info.credential_shape == "codex_oauth":
        return await _resolve_codex_access_token(store, user_id, provider_name)
    return _resolve_api_key(store, user_id, provider_name)


def _resolve_api_key(
    store: RuntimeStore, user_id: int, provider_name: str
) -> str | None:
    rec = store.get_api_key(user_id=user_id, provider=provider_name)
    if rec is None:
        return None
    try:
        return encryption.decrypt(rec.encrypted_key)
    except ValueError:
        # Tampered/wrong-key-era ciphertext. Treat as missing so callers
        # can fall back to the env var or surface the configure-a-key error.
        logger.error(
            "Failed to decrypt stored API key for user=%d provider=%s", user_id, provider_name
        )
        return None


async def _resolve_codex_access_token(
    store: RuntimeStore, user_id: int, provider_name: str
) -> str | None:
    rec = store.get_api_key(user_id=user_id, provider=provider_name)
    if rec is None:
        return None
    try:
        blob_json = encryption.decrypt(rec.encrypted_key)
    except ValueError:
        logger.error(
            "Failed to decrypt stored Codex bundle for user=%d", user_id
        )
        return None
    try:
        bundle = codex_oauth.bundle_from_json(blob_json)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Malformed Codex bundle for user=%d: %s", user_id, exc)
        return None
    if codex_oauth.is_near_expiry(bundle):
        try:
            bundle = await codex_oauth.refresh_access_token(bundle.refresh_token)
        except codex_oauth.CodexOAuthError as exc:
            logger.warning(
                "Codex token refresh failed for user=%d: %s", user_id, exc
            )
            return None
        store.upsert_api_key(
            user_id=user_id,
            provider=provider_name,
            encrypted_key=encryption.encrypt(codex_oauth.bundle_to_json(bundle)),
        )
        logger.info("Refreshed Codex token for user=%d", user_id)
    return bundle.access_token


def create_client_for_request(
    provider: str | None = None,
    model: str | None = None,
    *,
    api_key: str | None = None,
) -> BaseLLMClient:
    """Create an LLM client for a request, raising HTTPException on provider errors.

    Callers pass the already-resolved per-user credential as `api_key` — see
    `resolve_user_credential`. For OAuth providers this is a live access
    token; for classic providers it's the decrypted API key. Env-var
    fallback still kicks in for classic providers if the user has none
    stored.
    """
    provider_name = provider or get_default_provider()
    try:
        info = get_provider(provider_name)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not api_key and not provider_is_available(info):
        if info.credential_shape == "codex_oauth":
            detail = f"{info.display_name} not connected — click Connect ChatGPT in Settings."
        else:
            detail = f"No API key for {info.display_name} — add one in Settings."
        raise HTTPException(status_code=503, detail=detail)

    try:
        return create_client(provider=provider_name, model=model, api_key=api_key)
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e))


async def close_client(client: BaseLLMClient) -> None:
    try:
        await client.aclose()
    except Exception:
        logger.exception("Failed to close provider client")
