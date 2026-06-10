"""POST /chat/stream — drive a single chat turn over SSE.

The endpoint delegates orchestration to `ChatService`, which owns
provider selection, session creation, and runtime invocation. The
route's job is purely transport: it emits each runtime event as SSE
and uses a heartbeat ping so reverse proxies don't drop long-running
connections.

The service is resolved via `backend.api.dependencies`. Event-to-wire
translation lives in the API package, alongside its Pydantic wire schemas.
"""

import logging
from contextlib import aclosing

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.domain.auth.types import AuthenticatedUser
from backend.server.csrf import verify_csrf
from backend.api.dependencies import (
    get_chat_service,
    get_current_user,
    get_process_state,
    get_store,
)
from backend.data import RuntimeStore
from backend.server.process_state import AppProcessState
from backend.server.rate_limit import ConcurrencyLimiter
from backend.api.schemas.chat import CancelChatRequest, ChatRequest
from backend.application.chat import (
    ChatConfigurationError,
    ChatNotFoundError,
    ChatService,
    close_client,
)
from backend.server.sse import (
    DetachHandoff,
    done_payload,
    error_to_sse_payload,
    event_to_sse_payload,
    sse_line,
    sse_response,
    stream_sse_events,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_csrf)])


async def _acquire_stream_slot(stream_gate: ConcurrencyLimiter, user_id: int) -> None:
    await stream_gate.acquire(
        user_id,
        detail=(
            f"Too many concurrent chat streams (max "
            f"{stream_gate.max_active} per user). "
            "Wait for an in-flight reply to finish."
        ),
    )


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
    cancel_registry = process_state.chat_cancel_events

    async def event_generator():
        slot_acquired = False
        prepared = None
        cancel_event = None
        session_id = None
        handoff = None

        async def release_resources():
            if cancel_event is not None and session_id is not None:
                cancel_registry.close(session_id, cancel_event)
            if prepared is not None:
                await close_client(prepared.client)
            if slot_acquired:
                await stream_gate.release(user.id)

        try:
            try:
                # Volume cap first (records the attempt), then the
                # concurrency slot. Both surface as the same SSE error.
                process_state.chat_request_limiter.check_key(f"user:{user.id}")
                await _acquire_stream_slot(stream_gate, user.id)
                slot_acquired = True
            except HTTPException as exc:
                yield sse_line(error_to_sse_payload(
                    code="rate_limited", status=exc.status_code, message=exc.detail,
                ))
                yield sse_line(done_payload())
                return

            try:
                prepared = await service.prepare_chat(
                    conversation_id=body.conversation_id,
                    provider=body.provider,
                    model=body.model,
                    user=user,
                )
            except ChatNotFoundError as exc:
                yield sse_line(error_to_sse_payload(
                    code="not_found", status=status.HTTP_404_NOT_FOUND, message=str(exc),
                ))
                yield sse_line(done_payload())
                return
            except ChatConfigurationError as exc:
                yield sse_line(error_to_sse_payload(
                    code="configuration", status=status.HTTP_503_SERVICE_UNAVAILABLE, message=str(exc),
                ))
                yield sse_line(done_payload())
                return

            session = prepared.session
            session_id = session.id
            logger.debug("chat/stream  session=%s  msg=%s", session.id, body.message[:100])

            # Cancellation handle for POST /chat/cancel (the Stop button).
            # Registered before streaming starts so a cancel can't race the
            # first event.
            cancel_event = cancel_registry.open(session.id)
            # Finish-on-disconnect: a dropped tab hands the turn to a
            # background drain so the answer persists; the drain runs
            # release_resources() when it finishes.
            handoff = DetachHandoff(release_resources)

            source = service.stream_events(
                prepared,
                message=body.message,
                tool_choice=body.tool_choice,
            )
            # aclosing: if the client closes this response generator early,
            # the inner transport generator's `finally` (detach or cleanup)
            # must run *now*, not at GC time.
            async with aclosing(stream_sse_events(
                request,
                source,
                event_to_sse_payload,
                first_payloads=[{"type": "conversation_id", "id": session.id}],
                log_label=f"chat/stream session={session.id}",
                detach=handoff,
                cancel_event=cancel_event,
                draining=lambda: process_state.draining,
            )) as lines:
                async for line in lines:
                    yield line
        finally:
            if handoff is not None and handoff.detached:
                # The background drain owns cleanup now (client close, slot
                # release, cancel-event unregistration).
                pass
            else:
                await release_resources()

    return sse_response(event_generator())


@router.post("/cancel")
async def chat_cancel(
    body: CancelChatRequest,
    store: RuntimeStore = Depends(get_store),
    process_state: AppProcessState = Depends(get_process_state),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Stop the in-flight stream for a conversation.

    The Stop button calls this *before* aborting its fetch — without it,
    finish-on-disconnect would keep the turn running in the background
    and keep spending the user's tokens. Idempotent: cancelling a
    conversation with no active stream returns `{"cancelled": false}`.
    """
    if await store.get_session(body.conversation_id, user_id=user.id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    cancelled = process_state.chat_cancel_events.cancel(body.conversation_id)
    return {"cancelled": cancelled}
