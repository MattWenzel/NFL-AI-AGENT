"""POST /chat/message + POST /chat/stream — drive a single chat turn.

Both endpoints delegate orchestration to `ChatService`, which
owns provider selection, session creation, and runtime invocation. The
route's job is purely transport: `/message` buffers the service's event
stream into one JSON response; `/stream` emits each event as SSE and
uses a heartbeat ping so reverse proxies don't drop long-running
connections.

The service is resolved via `get_chat_service` from
`app.bootstrap.dependencies`. Event-to-wire translation lives in this
process package, alongside its Pydantic wire schemas.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.lib.tools import TOOLS
from backend.lib.auth.types import AuthenticatedUser
from backend.server.csrf import verify_csrf
from backend.server.dependencies import get_chat_service, get_current_user, get_process_state
from backend.server.process_state import AppProcessState
from backend.server.rate_limit import ConcurrencyLimiter
from backend.services.chat.schemas import ChatRequest, ChatResponse
from backend.services.chat.service import (
    ChatConfigurationError,
    ChatNotFoundError,
    ChatService,
    ChatServiceError,
    close_client,
)
from backend.server.sse import event_to_sse_payload
from backend.lib.providers.errors import LLMError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_csrf)])

# Idle interval after which /chat/stream emits an SSE comment keepalive so
# reverse proxies (nginx 60s default, Cloudflare 100s) don't drop the
# connection while a long-running tool call is in flight.
SSE_HEARTBEAT_SECONDS = 15

async def _acquire_stream_slot(stream_gate: ConcurrencyLimiter, user_id: int) -> None:
    await stream_gate.acquire(
        user_id,
        detail=(
            f"Too many concurrent chat streams (max "
            f"{stream_gate.max_active} per user). "
            "Wait for an in-flight reply to finish."
        ),
    )


async def _release_stream_slot(stream_gate: ConcurrencyLimiter, user_id: int) -> None:
    await stream_gate.release(user_id)


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    body: ChatRequest,
    service: ChatService = Depends(get_chat_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Send a message and get a complete response."""
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
    service: ChatService = Depends(get_chat_service),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Send a message and stream the response via SSE.

    Resource acquisition (stream slot, LLM client) happens *inside* the event
    generator, not in the route body. If we reserved the slot or opened the
    client before returning the `StreamingResponse` and Starlette never began
    iterating the body (client aborted between response-start and first body
    send, middleware failure, etc.), the generator's `finally` would never
    run and both would leak. With all acquisition deferred to the generator,
    an un-iterated generator holds nothing.

    Acquisition failures that used to surface as HTTP errors (429 on stream
    cap, 404/503 from `prepare_chat`) now emit a structured SSE error event
    followed by `{"type": "done"}`. HTTP status is always 200 once the
    response is returned — the frontend already handles `{"type": "error"}`
    payloads via the same code path as runtime errors.
    """

    stream_gate = process_state.chat_stream_limiter

    async def event_generator():
        slot_acquired = False
        prepared = None
        try:
            try:
                await _acquire_stream_slot(stream_gate, user.id)
                slot_acquired = True
            except HTTPException as exc:
                yield f"data: {json.dumps({'type': 'error', 'code': 'rate_limited', 'status': exc.status_code, 'message': exc.detail})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            try:
                prepared = await service.prepare_chat(body, user)
            except ChatNotFoundError as exc:
                yield f"data: {json.dumps({'type': 'error', 'code': 'not_found', 'status': 404, 'message': str(exc)})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
            except ChatConfigurationError as exc:
                yield f"data: {json.dumps({'type': 'error', 'code': 'configuration', 'status': 503, 'message': str(exc)})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            session = prepared.session
            logger.debug("chat/stream  session=%s  msg=%s", session.id, body.message[:100])

            # Producer/consumer split. The runtime drains into a queue on its
            # own task; the SSE loop reads from the queue with a heartbeat
            # timeout. This keeps `wait_for` from cancelling the producer (and
            # any in-flight tool call it is awaiting) every heartbeat tick.
            queue: asyncio.Queue = asyncio.Queue()
            source = service.stream_events(prepared, body, tools=TOOLS)

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
        finally:
            if prepared is not None:
                await close_client(prepared.client)
            if slot_acquired:
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
