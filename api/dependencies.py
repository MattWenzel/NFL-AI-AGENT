"""FastAPI dependencies and HTTP-level helpers shared by chat routers.

- `get_store` / `get_runtime`: resolve the `RuntimeStore` and
  `ChatRuntime` attached to `app.state` by the lifespan. Use via
  `Depends(get_store)`.
- `create_client_for_request`: translate provider/model request params
  into a live `BaseLLMClient`, raising the right HTTP status when the
  provider is unknown or its API key is missing.
- `close_client`: swallow close-errors so one failing client doesn't
  crash request teardown.
"""

import logging

from fastapi import HTTPException, Request

from agent.runtime import ChatRuntime
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


def create_client_for_request(provider: str | None = None, model: str | None = None) -> BaseLLMClient:
    """Create an LLM client for a request, raising HTTPException on provider errors."""
    provider_name = provider or get_default_provider()
    try:
        info = get_provider(provider_name)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not provider_is_available(info):
        detail = f"{info.env_key} not configured — {info.display_name} provider unavailable"
        raise HTTPException(status_code=503, detail=detail)

    try:
        return create_client(provider=provider_name, model=model)
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e))


async def close_client(client: BaseLLMClient) -> None:
    try:
        await client.aclose()
    except Exception:
        logger.exception("Failed to close provider client")
