"""POST /chat/message + POST /chat/stream — drive a single chat turn.

Both endpoints drive the same `ChatRuntime.run_session` generator. The
difference is only in how events are surfaced: `/message` buffers
everything into one JSON response; `/stream` emits each event as SSE
and uses a heartbeat ping so reverse proxies don't drop long-running
connections.

Provider selection, session creation, and runtime/store resolution
come from `api.dependencies`. Event-to-wire translation lives in
`api.sse`. Pydantic shapes live in `api.schemas`.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent.runtime import ChatRuntime, TOOLS
from api.auth import AuthenticatedUser, get_current_user
from api.dependencies import (
    close_client,
    create_client_for_request,
    get_runtime,
    get_store,
)
from api.schemas import ChatRequest, ChatResponse
from api.sse import event_to_sse_payload
from infra import encryption
from infra.persistence.runtime_store import RuntimeStore
from infra.providers import BaseLLMClient, LLMError, get_default_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Idle interval after which /chat/stream emits an SSE comment keepalive so
# reverse proxies (nginx 60s default, Cloudflare 100s) don't drop the
# connection while a long-running tool call is in flight.
SSE_HEARTBEAT_SECONDS = 15


def _resolve_user_api_key(
    store: RuntimeStore, user_id: int, provider_name: str
) -> str | None:
    """Look up and decrypt the user's stored key for the given provider, or None."""
    rec = store.get_api_key(user_id=user_id, provider=provider_name)
    if rec is None:
        return None
    try:
        return encryption.decrypt(rec.encrypted_key)
    except ValueError:
        # Tampered/wrong-key-era ciphertext. Treat as missing so the caller can fall back
        # to env var or surface the configure-a-key error.
        logger.error("Failed to decrypt stored API key for user=%d provider=%s", user_id, provider_name)
        return None


def _prepare_chat(
    body: ChatRequest,
    runtime: ChatRuntime,
    store: RuntimeStore,
    user: AuthenticatedUser,
) -> tuple[BaseLLMClient, str, "SessionRecord"]:
    """Resolve provider/client/session for a chat request.

    The caller owns closing the returned client. Used by /message and
    /stream so both endpoints agree on how body params map to a live
    session.
    """
    provider_name = body.provider or get_default_provider()
    user_key = _resolve_user_api_key(store, user.id, provider_name)
    client = create_client_for_request(body.provider, body.model, api_key=user_key)
    session = runtime.prepare_session(
        client, provider_name, body.conversation_id, user_id=user.id
    )
    return client, provider_name, session


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    body: ChatRequest,
    runtime: ChatRuntime = Depends(get_runtime),
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Send a message and get a complete response."""
    client, provider_name, session = _prepare_chat(body, runtime, store, user)

    logger.debug("chat/message  session=%s  msg=%s", session.id, body.message[:100])

    response_text = ""
    tool_calls_log = []
    hit_limit = False
    runtime_error = None

    try:
        async for event in runtime.run_session(
            session,
            body.message,
            client,
            tools=TOOLS,
            provider_name=provider_name,
        ):
            if event.type == "text_delta" and event.text:
                response_text += event.text
            elif event.type == "tool_pending":
                tool_calls_log.append({
                    "tool_run_id": event.tool_run_id,
                    "tool": event.name,
                    "input": event.input,
                    "result_preview": "",
                })
            elif event.type in {"tool_completed", "tool_failed"} and tool_calls_log:
                preview = event.result[:500] if event.result and len(event.result) > 500 else event.result or event.error or ""
                for item in reversed(tool_calls_log):
                    if item["tool_run_id"] == event.tool_run_id and not item["result_preview"]:
                        item["result_preview"] = preview
                        break
            elif event.type == "runtime_error":
                runtime_error = event.error or "Runtime error"
                hit_limit = bool(runtime_error.startswith("Reached maximum tool iterations"))
        if runtime_error and not hit_limit:
            raise HTTPException(status_code=500, detail=runtime_error)
    except LLMError as e:
        logger.warning("LLM error in chat/message: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected runtime error in chat/message")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await close_client(client)

    logger.debug("chat/message done  session=%s", session.id)

    return ChatResponse(
        conversation_id=session.id,
        response=response_text,
        tool_calls=[
            {
                "tool": item["tool"],
                "input": item["input"],
                "result_preview": item["result_preview"],
            }
            for item in tool_calls_log
        ],
        truncated=hit_limit,
    )


_PRODUCER_DONE = object()  # sentinel put on the queue when the producer finishes


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    runtime: ChatRuntime = Depends(get_runtime),
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Send a message and stream the response via SSE."""
    client, provider_name, session = _prepare_chat(body, runtime, store, user)

    async def event_generator():
        logger.debug("chat/stream  session=%s  msg=%s", session.id, body.message[:100])

        # Producer/consumer split. The runtime drains into a queue on its
        # own task; the SSE loop reads from the queue with a heartbeat
        # timeout. This keeps `wait_for` from cancelling the producer (and
        # any in-flight tool call it is awaiting) every heartbeat tick.
        queue: asyncio.Queue = asyncio.Queue()
        source = runtime.run_session(
            session,
            body.message,
            client,
            tools=TOOLS,
            provider_name=provider_name,
        )

        async def producer():
            try:
                async for event in source:
                    await queue.put(event)
            except BaseException as exc:  # incl. CancelledError for disconnects
                await queue.put(exc)
            finally:
                await queue.put(_PRODUCER_DONE)

        producer_task = asyncio.create_task(producer())

        try:
            yield f"data: {json.dumps({'type': 'conversation_id', 'id': session.id})}\n\n"
            while True:
                if await request.is_disconnected():
                    logger.info("Client disconnected, stopping stream  session=%s", session.id)
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Keepalive comment — proxies reset their idle timer.
                    # The producer keeps running; only the queue-read was cancelled.
                    yield ": ping\n\n"
                    continue
                if item is _PRODUCER_DONE:
                    break
                if isinstance(item, LLMError):
                    raise item
                if isinstance(item, BaseException):
                    # Re-raise non-LLM errors so the outer handler can log them.
                    raise item
                payload = event_to_sse_payload(item)
                if payload is not None:
                    yield f"data: {json.dumps(payload)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except LLMError as e:
            logger.warning("LLM error in chat/stream: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception:
            logger.exception("Unexpected error in chat/stream")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Internal error — check server logs'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        finally:
            # Stop the producer and close the runtime generator so its
            # `finally` block runs now (releases the session lock,
            # reconciles pending tool runs) rather than waiting on GC.
            if not producer_task.done():
                producer_task.cancel()
            try:
                await producer_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await source.aclose()
            except Exception:
                logger.exception("Failed to close runtime source  session=%s", session.id)
            await close_client(client)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
