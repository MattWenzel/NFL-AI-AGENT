"""POST /chat/message + POST /chat/stream — drive a single chat turn.

Both endpoints drive the same `ChatRuntime.run_session` generator. The
difference is only in how events are surfaced: `/message` buffers
everything into one JSON response; `/stream` emits each event as SSE
and uses a heartbeat ping so reverse proxies don't drop long-running
connections.

Provider selection, session creation, and runtime/store resolution
come from `server.dependencies`. Event-to-wire translation lives in
`server.sse`. Pydantic shapes live in `server.schemas`.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from agent.runtime import ChatRuntime
from tools import TOOLS
from auth.primitives import AuthenticatedUser, get_current_user
from server.dependencies import (
    get_conversation_repository,
    get_runtime,
    get_user_repository,
)
from server.process_state import (
    ChatStreamGate,
    PerUserLockRegistry,
    get_chat_stream_gate,
    get_codex_refresh_locks,
)
from server.repositories import ConversationRepository, UserRepository
from server.schemas.chat import ChatRequest, ChatResponse
from server.services.chat import (
    ChatApplicationService,
    ChatConfigurationError,
    ChatNotFoundError,
    ChatServiceError,
    close_client,
)
from server.sse import event_to_sse_payload
from provider import BaseLLMClient, LLMError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Idle interval after which /chat/stream emits an SSE comment keepalive so
# reverse proxies (nginx 60s default, Cloudflare 100s) don't drop the
# connection while a long-running tool call is in flight.
SSE_HEARTBEAT_SECONDS = 15

MAX_CONCURRENT_STREAMS_PER_USER = 3


async def _acquire_stream_slot(stream_gate: ChatStreamGate, user_id: int) -> None:
    await stream_gate.acquire(
        user_id,
        detail=(
            f"Too many concurrent chat streams (max "
            f"{MAX_CONCURRENT_STREAMS_PER_USER} per user). "
            "Wait for an in-flight reply to finish."
        ),
    )


async def _release_stream_slot(stream_gate: ChatStreamGate, user_id: int) -> None:
    await stream_gate.release(user_id)


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    body: ChatRequest,
    runtime: ChatRuntime = Depends(get_runtime),
    users: UserRepository = Depends(get_user_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    refresh_locks: PerUserLockRegistry = Depends(get_codex_refresh_locks),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Send a message and get a complete response."""
    service = ChatApplicationService(
        runtime,
        users,
        conversations,
        refresh_locks=refresh_locks,
    )

    try:
        response = await service.run_message(body, user, tools=TOOLS)
    except ChatNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ChatConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ChatServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except LLMError as e:
        logger.warning("LLM error in chat/message: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected runtime error in chat/message")
        raise HTTPException(status_code=500, detail=str(e))

    logger.debug("chat/message done  session=%s", response.conversation_id)
    return response


_PRODUCER_DONE = object()  # sentinel put on the queue when the producer finishes


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    runtime: ChatRuntime = Depends(get_runtime),
    users: UserRepository = Depends(get_user_repository),
    conversations: ConversationRepository = Depends(get_conversation_repository),
    stream_gate: ChatStreamGate = Depends(get_chat_stream_gate),
    refresh_locks: PerUserLockRegistry = Depends(get_codex_refresh_locks),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Send a message and stream the response via SSE."""
    # Reserve a slot before we touch any provider state. If the cap is
    # hit the request fails with 429 *before* a session is prepared, so
    # we don't churn an LLM client for a rejected request.
    await _acquire_stream_slot(stream_gate, user.id)
    service = ChatApplicationService(
        runtime,
        users,
        conversations,
        refresh_locks=refresh_locks,
    )
    try:
        prepared = await service.prepare_chat(body, user)
        client = prepared.client
        provider_name = prepared.provider_name
        session = prepared.session
    except ChatNotFoundError as exc:
        await _release_stream_slot(stream_gate, user.id)
        raise HTTPException(status_code=404, detail=str(exc))
    except ChatConfigurationError as exc:
        await _release_stream_slot(stream_gate, user.id)
        raise HTTPException(status_code=503, detail=str(exc))
    except BaseException:
        await _release_stream_slot(stream_gate, user.id)
        raise

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
            tool_choice=body.tool_choice,
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
            await _release_stream_slot(stream_gate, user.id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
