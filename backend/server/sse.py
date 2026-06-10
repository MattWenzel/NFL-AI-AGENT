"""Server-sent-event transport + payload serialization for chat streaming.

Two layers live here:

- **Serialization** — `event_to_sse_payload` translates a domain
  `RuntimeEvent` into the wire dict the browser receives. Returning
  `None` means "suppress this event". Events that drive server-side loop
  state (turn lifecycle / followup iteration) are deliberately unmapped.
  Any event variant without an explicit arm trips the catch-all warning
  so new variants can't be silently dropped after a refactor.

- **Transport** — `stream_sse_events` is the producer/consumer +
  heartbeat loop shared by `/chat/stream` and
  `/database/helper-chat/stream`. The runtime drains into a queue on its
  own task; the SSE loop reads from the queue with a heartbeat timeout
  so reverse proxies (nginx 60s, Cloudflare 100s) don't drop idle
  connections, and so the heartbeat's `wait_for` never cancels the
  producer (and any in-flight tool call it is awaiting). Routes keep
  ownership of acquisition (rate-limit slot, LLM client) and release it
  in their own `finally` around this helper.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, Callable, Iterable

from fastapi import Request
from fastapi.responses import StreamingResponse

from backend.domain.providers.errors import LLMError
from backend.domain.agent.events import (
    AssistantRequiresFollowupEvent,
    AssistantStartedEvent,
    CompactionStartedEvent,
    ReportCreatedEvent,
    RetryingEvent,
    RuntimeErrorEvent,
    RuntimeEvent,
    TableUpdatedEvent,
    TextDeltaEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolPendingEvent,
    TurnFinishedEvent,
    TurnStartedEvent,
)

logger = logging.getLogger(__name__)

# Idle interval after which the stream emits an SSE comment keepalive.
SSE_HEARTBEAT_SECONDS = 15

# Queue-read granularity. Smaller than the heartbeat so disconnects and
# process draining are noticed within a couple of seconds, not only when
# an event or heartbeat tick arrives.
_POLL_SECONDS = 2.0

_PRODUCER_DONE = object()  # sentinel put on the queue when the producer finishes

# Strong references to detached drain tasks (finish-on-disconnect) so the
# event loop doesn't GC them mid-flight. Tasks remove themselves on
# completion.
_DETACHED_TASKS: set[asyncio.Task] = set()


def sse_line(payload: dict) -> str:
    """One SSE data frame."""
    return f"data: {json.dumps(payload)}\n\n"


class DetachHandoff:
    """Ownership handoff for finish-on-disconnect.

    The route creates one with the cleanup it would otherwise run in its
    own `finally` (close LLM client, release stream slot, unregister the
    cancel event). If the client disconnects mid-turn, `stream_sse_events`
    flips `detached` and a background drain task finishes the turn, then
    runs `cleanup`. The route's `finally` must check `detached` and skip
    its cleanup when True — at that point the drain task owns it.
    """

    def __init__(self, cleanup: Callable):
        self.cleanup = cleanup
        self.detached = False


def sse_response(generator) -> StreamingResponse:
    """Wrap an SSE line generator in a StreamingResponse with the standard
    headers: no proxy caching, keep-alive, and nginx buffering off (so
    events actually reach the client as they're produced)."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def stream_sse_events(
    request: Request,
    source: AsyncGenerator,
    serialize: Callable[[object], dict | None],
    *,
    first_payloads: Iterable[dict] = (),
    heartbeat_seconds: int = SSE_HEARTBEAT_SECONDS,
    log_label: str = "stream",
    detach: DetachHandoff | None = None,
    cancel_event: asyncio.Event | None = None,
    draining: Callable[[], bool] | None = None,
) -> AsyncGenerator[str, None]:
    """Drive a runtime event source into SSE lines.

    Owns the producer task, the heartbeat loop, error translation, and
    teardown (cancel the producer, `aclose()` the source so its `finally`
    runs now — releasing the session lock, reconciling pending tool runs —
    rather than waiting on GC). The caller owns everything acquired
    *around* the stream (slot, LLM client) in its own try/finally.

    Wire contract: each `first_payloads` dict, then one frame per
    serialized event (`serialize` returning None suppresses the event),
    `: ping` comments on heartbeat timeouts, and always a terminal
    `{"type": "done"}` — after clean completion, an `LLMError`, or an
    unexpected exception (both error paths emit `{"type": "error"}`
    first).

    `detach` decides what a dropped client means:

    - `None` (default) — abort generation; the source's `finally` marks
      in-flight work interrupted. Right for stateless surfaces where an
      unread answer is worthless.
    - `DetachHandoff` — the turn keeps running on a background task so
      its result persists; the user reloads and finds the full answer.
      The handoff's `cleanup` (close LLM client, release stream slot)
      runs after the drain finishes, and `detached` is flipped so the
      route's `finally` knows to skip its own cleanup.

    `cancel_event` (from `StreamCancelRegistry`) stops generation
    mid-stream — set by `POST /chat/cancel`. It works both while the
    client is connected (Stop button) and during a detached drain.

    `draining` is polled every couple of seconds; when it returns True
    (process shutting down) the stream ends with a structured
    `server_restarting` error instead of a dead socket.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def producer():
        try:
            async for event in source:
                await queue.put(event)
        except BaseException as exc:  # incl. CancelledError for disconnects
            await queue.put(exc)
        finally:
            await queue.put(_PRODUCER_DONE)

    producer_task = asyncio.create_task(producer())

    cancel_watcher: asyncio.Task | None = None
    if cancel_event is not None:
        async def _watch_cancel():
            await cancel_event.wait()
            logger.info("Cancel requested for %s — stopping producer", log_label)
            producer_task.cancel()
        cancel_watcher = asyncio.create_task(_watch_cancel())

    idle = 0.0
    try:
        for payload in first_payloads:
            yield sse_line(payload)
        while True:
            if draining is not None and draining():
                logger.info("Draining — ending %s", log_label)
                yield sse_line(error_to_sse_payload(
                    code="server_restarting",
                    message="Server is restarting — please retry in a moment.",
                ))
                break
            if await request.is_disconnected():
                logger.info("Client disconnected, stopping %s", log_label)
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_POLL_SECONDS)
                idle = 0.0
            except asyncio.TimeoutError:
                idle += _POLL_SECONDS
                if idle >= heartbeat_seconds:
                    # Keepalive comment — proxies reset their idle timer.
                    # The producer keeps running; only the queue-read timed out.
                    yield ": ping\n\n"
                    idle = 0.0
                continue
            if item is _PRODUCER_DONE:
                break
            if isinstance(item, asyncio.CancelledError) and cancel_event is not None and cancel_event.is_set():
                # User-requested stop, not a crash: surface a structured
                # terminal event. The source's aclose() below marks the
                # turn interrupted.
                yield sse_line(error_to_sse_payload(
                    code="cancelled", message="Generation stopped.",
                ))
                break
            if isinstance(item, LLMError):
                logger.warning("LLM error in %s: %s", log_label, item)
                yield sse_line(error_to_sse_payload(message=str(item)))
                break
            if isinstance(item, BaseException):
                # Re-raise non-LLM errors so the handler below logs them.
                raise item
            payload = serialize(item)
            if payload is not None:
                yield sse_line(payload)
        yield sse_line(done_payload())
    except Exception:
        logger.exception("Unexpected error in %s", log_label)
        yield sse_line(error_to_sse_payload(message="Internal error — check server logs"))
        yield sse_line(done_payload())
    finally:
        if (
            detach is not None
            and not producer_task.done()
            and not (cancel_event is not None and cancel_event.is_set())
            and not (draining is not None and draining())
        ):
            # Client went away mid-turn (cooperative break above, or
            # GeneratorExit/CancelledError thrown at a yield). Let the
            # turn finish so its answer persists; hand resource release
            # to the drain task.
            detach.detached = True
            _detach_drain(
                producer_task, source, cancel_watcher, detach.cleanup, log_label
            )
        else:
            if cancel_watcher is not None and not cancel_watcher.done():
                cancel_watcher.cancel()
            if not producer_task.done():
                producer_task.cancel()
            try:
                await producer_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await source.aclose()
            except Exception:
                logger.exception("Failed to close runtime source for %s", log_label)


def _detach_drain(
    producer_task: asyncio.Task,
    source: AsyncGenerator,
    cancel_watcher: asyncio.Task | None,
    detach_cleanup: Callable,
    log_label: str,
) -> None:
    """Finish a disconnected stream's turn in the background.

    Awaits the producer to completion (so the turn persists), closes the
    runtime source, then runs the route's cleanup (close LLM client,
    release stream slot). The cancel watcher stays alive during the
    drain so POST /chat/cancel still stops a backgrounded turn.
    """
    logger.info("Detaching %s — finishing turn in background", log_label)

    async def _drain():
        try:
            try:
                await producer_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await source.aclose()
            except Exception:
                logger.exception("Failed to close drained source for %s", log_label)
        finally:
            if cancel_watcher is not None and not cancel_watcher.done():
                cancel_watcher.cancel()
            try:
                await detach_cleanup()
            except Exception:
                logger.exception("Detached cleanup failed for %s", log_label)
            logger.info("Background drain finished for %s", log_label)

    task = asyncio.create_task(_drain())
    _DETACHED_TASKS.add(task)
    task.add_done_callback(_DETACHED_TASKS.discard)


def error_to_sse_payload(
    *,
    message: str,
    code: str | None = None,
    status: int | None = None,
) -> dict:
    """Wire shape for SSE error events. The frontend treats `type: error` as
    terminal; `code` distinguishes recoverable categories (rate_limited,
    not_found, configuration) when set."""
    payload: dict = {"type": "error", "message": message}
    if code is not None:
        payload["code"] = code
    if status is not None:
        payload["status"] = status
    return payload


def done_payload() -> dict:
    return {"type": "done"}


def event_to_sse_payload(event: RuntimeEvent) -> dict | None:
    if isinstance(event, AssistantStartedEvent):
        return {"type": "assistant_started", "turn_id": event.turn_id, "iterations": event.iterations}
    if isinstance(event, TextDeltaEvent):
        return {"type": "text", "text": event.text}
    if isinstance(event, ToolPendingEvent):
        return {"type": "tool_call", "tool_run_id": event.tool_run_id, "name": event.name, "input": event.input}
    if isinstance(event, CompactionStartedEvent):
        return {"type": "compaction", "meta": event.meta}
    if isinstance(event, RetryingEvent):
        return {
            "type": "retrying",
            "attempt": event.attempt,
            "delay_seconds": event.delay_seconds,
            "message": event.error or "Retrying after transient error",
        }
    if isinstance(event, ToolCompletedEvent):
        return {"type": "tool_result", "tool_run_id": event.tool_run_id, "name": event.name}
    if isinstance(event, ToolFailedEvent):
        return {"type": "tool_failed", "tool_run_id": event.tool_run_id, "name": event.name, "message": event.error or f"{event.name} failed"}
    if isinstance(event, TableUpdatedEvent):
        return {
            "type": "table_updated",
            "tool_run_id": event.tool_run_id,
            "row_count": event.row_count,
            "truncated": event.truncated,
            "columns": event.columns,
        }
    if isinstance(event, ReportCreatedEvent):
        return {
            "type": "report_created",
            "tool_run_id": event.tool_run_id,
            "report_id": event.report_id,
            "title": event.title,
            "row_count": event.row_count,
        }
    if isinstance(event, RuntimeErrorEvent):
        return {"type": "error", "message": event.error or "Runtime error"}
    if isinstance(event, (TurnStartedEvent, TurnFinishedEvent, AssistantRequiresFollowupEvent)):
        return None
    logger.warning("Unmapped runtime event variant %r dropped from SSE stream", type(event).__name__)
    return None
