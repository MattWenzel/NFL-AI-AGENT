"""POST /chat/message + POST /chat/stream — drive a single chat turn.

Both endpoints delegate orchestration to `ChatService`, which
owns provider selection, session creation, and runtime invocation. The
route's job is purely transport: `/message` buffers the service's event
stream into one JSON response; `/stream` emits each event as SSE and
uses a heartbeat ping so reverse proxies don't drop long-running
connections.

The service is resolved via `backend.api.dependencies`. Event-to-wire
translation lives in the API package, alongside its Pydantic wire schemas.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend.domain.auth.types import AuthenticatedUser
from backend.server.csrf import verify_csrf
from backend.api.dependencies import get_chat_service, get_current_user, get_process_state
from backend.server.process_state import AppProcessState
from backend.server.rate_limit import ConcurrencyLimiter
from backend.api.schemas.chat import ChatRequest, ChatResponse
from backend.application.chat import (
    ChatConfigurationError,
    ChatNotFoundError,
    ChatService,
    ChatServiceError,
    close_client,
)
from backend.server.sse import done_payload, error_to_sse_payload, event_to_sse_payload
from backend.domain.providers.errors import LLMError

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
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Send a message and get a complete response."""
    process_state.chat_request_limiter.check_key(f"user:{user.id}")
    try:
        response = await service.run_message(
            message=body.message,
            conversation_id=body.conversation_id,
            provider=body.provider,
            model=body.model,
            tool_choice=body.tool_choice,
            user=user,
        )
    except ChatNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ChatConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except ChatServiceError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except LLMError as e:
        logger.warning("LLM error in chat/message: %s", e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected runtime error in chat/message")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    logger.debug("chat/message done  session=%s", response.get("conversation_id"))
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

    def _sse_line(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    async def event_generator():
        slot_acquired = False
        prepared = None
        try:
            try:
                # Volume cap first (records the attempt), then the
                # concurrency slot. Both surface as the same SSE error.
                process_state.chat_request_limiter.check_key(f"user:{user.id}")
                await _acquire_stream_slot(stream_gate, user.id)
                slot_acquired = True
            except HTTPException as exc:
                yield _sse_line(error_to_sse_payload(
                    code="rate_limited", status=exc.status_code, message=exc.detail,
                ))
                yield _sse_line(done_payload())
                return

            try:
                prepared = await service.prepare_chat(
                    conversation_id=body.conversation_id,
                    provider=body.provider,
                    model=body.model,
                    user=user,
                )
            except ChatNotFoundError as exc:
                yield _sse_line(error_to_sse_payload(
                    code="not_found", status=status.HTTP_404_NOT_FOUND, message=str(exc),
                ))
                yield _sse_line(done_payload())
                return
            except ChatConfigurationError as exc:
                yield _sse_line(error_to_sse_payload(
                    code="configuration", status=status.HTTP_503_SERVICE_UNAVAILABLE, message=str(exc),
                ))
                yield _sse_line(done_payload())
                return

            session = prepared.session
            logger.debug("chat/stream  session=%s  msg=%s", session.id, body.message[:100])

            # Producer/consumer split. The runtime drains into a queue on its
            # own task; the SSE loop reads from the queue with a heartbeat
            # timeout. This keeps `wait_for` from cancelling the producer (and
            # any in-flight tool call it is awaiting) every heartbeat tick.
            queue: asyncio.Queue = asyncio.Queue()
            source = service.stream_events(
                prepared,
                message=body.message,
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
                yield _sse_line({"type": "conversation_id", "id": session.id})
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
                        yield _sse_line(payload)
                yield _sse_line(done_payload())
            except LLMError as e:
                logger.warning("LLM error in chat/stream: %s", e)
                yield _sse_line(error_to_sse_payload(message=str(e)))
                yield _sse_line(done_payload())
            except Exception:
                logger.exception("Unexpected error in chat/stream")
                yield _sse_line(error_to_sse_payload(message="Internal error — check server logs"))
                yield _sse_line(done_payload())
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
