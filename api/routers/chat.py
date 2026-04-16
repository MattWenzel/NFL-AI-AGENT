"""FastAPI chat endpoints for the NFL stats agent."""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.providers import (
    BaseLLMClient, create_client, get_provider, list_providers, get_default_provider,
    provider_is_available, LLMError,
)
from agent.runtime import ChatRuntime, RuntimeEvent, TOOLS
from agent.runtime_store import RuntimeStore, SessionRecord, safe_load_tool_input

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Idle interval after which /chat/stream emits an SSE comment keepalive so
# reverse proxies (nginx 60s default, Cloudflare 100s) don't drop the
# connection while a long-running tool call is in flight.
SSE_HEARTBEAT_SECONDS = 15


def _get_store(request: Request) -> RuntimeStore:
    store = getattr(request.app.state, "runtime_store", None)
    if store is None:
        raise RuntimeError(
            "runtime_store not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return store


def _get_runtime(request: Request) -> ChatRuntime:
    runtime = getattr(request.app.state, "chat_runtime", None)
    if runtime is None:
        raise RuntimeError(
            "chat_runtime not attached to app.state — the FastAPI lifespan must set it before requests run."
        )
    return runtime


async def _close_client(client: BaseLLMClient) -> None:
    try:
        await client.aclose()
    except Exception:
        logger.exception("Failed to close provider client")


def _create_client_for_request(provider: str | None = None, model: str | None = None) -> BaseLLMClient:
    """Create an LLM client for a request, with provider-aware validation."""
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


def _prepare_chat(
    request: Request, body: "ChatRequest"
) -> tuple[BaseLLMClient, str, SessionRecord, ChatRuntime]:
    """Resolve provider/client/session/runtime for a chat request.

    Central place for the client → provider → session wiring shared by the
    /message and /stream endpoints. Returns everything the caller needs to
    drive a run_session loop; the caller owns closing the client.
    """
    client = _create_client_for_request(body.provider, body.model)
    provider_name = body.provider or get_default_provider()
    info = get_provider(provider_name)
    store = _get_store(request)
    runtime = _get_runtime(request)
    session = store.get_or_create_session(
        body.conversation_id,
        provider=provider_name,
        model=body.model or client.model,
        context_window=info.effective_context_window,
    )
    return client, provider_name, session, runtime


# ---------- Request/Response models ----------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000, description="User message")
    conversation_id: str | None = Field(None, description="Existing conversation ID (omit to create new)")
    provider: str | None = Field(None, description="LLM provider (anthropic, openai)")
    model: str | None = Field(None, description="Model name override")


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    tool_calls: list[dict] = Field(default_factory=list)
    truncated: bool = Field(False, description="True when the agent hit its iteration limit")


class ConversationInfo(BaseModel):
    id: str
    message_count: int
    title: str
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None


class ConversationTranscriptResponse(BaseModel):
    session_id: str
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None
    turns: list[dict]
    parts: list[dict]
    tool_runs: list[dict]
    summaries: list[dict]


class ProviderResponse(BaseModel):
    name: str
    display_name: str
    models: list[str]
    default_model: str
    available: bool
    context_window: int
    supports_streaming: bool
    supports_tools: bool


# ---------- Endpoints ----------

@router.get("/providers", response_model=list[ProviderResponse])
async def get_providers():
    """List available LLM providers and their configuration."""
    result = []
    for info in list_providers():
        result.append(ProviderResponse(
            name=info.name,
            display_name=info.display_name,
            models=info.models,
            default_model=info.default_model,
            available=provider_is_available(info),
            context_window=info.context_window,
            supports_streaming=info.supports_streaming,
            supports_tools=info.supports_tools,
        ))
    return result


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    request: Request,
    body: ChatRequest,
):
    """Send a message and get a complete response."""
    client, provider_name, session, runtime = _prepare_chat(request, body)

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
        await _close_client(client)

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


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
):
    """Send a message and stream the response via SSE."""
    client, provider_name, session, runtime = _prepare_chat(request, body)

    async def event_generator():
        logger.debug("chat/stream  session=%s  msg=%s", session.id, body.message[:100])
        source = None
        try:
            yield f"data: {json.dumps({'type': 'conversation_id', 'id': session.id})}\n\n"
            source = runtime.run_session(
                session,
                body.message,
                client,
                tools=TOOLS,
                provider_name=provider_name,
            ).__aiter__()
            while True:
                if await request.is_disconnected():
                    logger.info("Client disconnected, stopping stream  session=%s", session.id)
                    break
                try:
                    event = await asyncio.wait_for(source.__anext__(), timeout=SSE_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Keepalive comment — proxies reset their idle timer.
                    yield ": ping\n\n"
                    continue
                except StopAsyncIteration:
                    break
                payload = _stream_payload(event)
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
            # Explicitly close the runtime generator so its `finally` block runs
            # now (releases the session lock, reconciles pending tool runs)
            # rather than waiting on garbage collection. A second request on
            # the same session otherwise blocks until GC cleans up.
            if source is not None:
                try:
                    await source.aclose()
                except Exception:
                    logger.exception("Failed to close runtime source  session=%s", session.id)
            await _close_client(client)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[ConversationInfo])
async def list_conversations(request: Request):
    """List all active conversations."""
    result = []
    for item in _get_store(request).list_sessions():
        result.append(
            ConversationInfo(
                id=item["id"],
                message_count=item["turn_count"],
                title=item["title"],
                provider=item.get("provider"),
                model=item.get("model"),
                updated_at=item.get("updated_at"),
            )
        )
    return result


@router.get("/conversations/{conversation_id}/transcript", response_model=ConversationTranscriptResponse)
async def get_conversation_transcript(request: Request, conversation_id: str):
    """Return the persisted transcript with turns, tool runs, and compaction summaries."""
    try:
        transcript = _get_store(request).get_transcript(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    tool_runs = []
    for turn_id, runs in transcript.tool_runs_by_turn.items():
        for run in runs:
            tool_runs.append({
                "id": run.id,
                "turn_id": turn_id,
                "tool_name": run.tool_name,
                "status": run.status,
                "input": safe_load_tool_input(run.input_json, tool_run_id=run.id),
                "result": run.result_text,
                "error": run.error_text,
                "hint": run.hint,
                "duration_ms": run.duration_ms,
                "compacted": run.compacted,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
            })
    parts = []
    for turn_id, records in transcript.parts_by_turn.items():
        for part in records:
            parts.append({
                "id": part.id,
                "turn_id": turn_id,
                "kind": part.kind,
                "order_index": part.order_index,
                "content": part.content,
                "name": part.name,
                "tool_run_id": part.tool_run_id,
                "created_at": part.created_at,
            })
    return ConversationTranscriptResponse(
        session_id=conversation_id,
        title=transcript.session.title,
        provider=transcript.session.provider,
        model=transcript.session.model,
        updated_at=transcript.session.updated_at,
        turns=[
            {
                "id": turn.id,
                "role": turn.role,
                "status": turn.status,
                "text": turn.text,
                "compacted": turn.compacted,
                "error": turn.error,
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "created_at": turn.created_at,
                "updated_at": turn.updated_at,
            }
            for turn in transcript.turns
        ],
        parts=parts,
        tool_runs=tool_runs,
        summaries=[
            {
                "id": summary.id,
                "summary_turn_id": summary.summary_turn_id,
                "source_turn_ids": summary.source_turn_ids,
                "created_at": summary.created_at,
            }
            for summary in transcript.summaries
        ],
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(request: Request, conversation_id: str):
    """Delete a conversation."""
    if _get_store(request).delete_session(conversation_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Conversation not found")


def _stream_payload(event: RuntimeEvent) -> dict | None:
    if event.type == "assistant_started":
        return {"type": "assistant_started", "turn_id": event.turn_id, "iterations": event.iterations}
    if event.type == "text_delta":
        return {"type": "text", "text": event.text}
    if event.type == "tool_pending":
        return {"type": "tool_call", "tool_run_id": event.tool_run_id, "name": event.name, "input": event.input}
    if event.type == "compaction_started":
        return {"type": "compaction", "meta": event.meta}
    if event.type == "tool_completed":
        return {"type": "tool_result", "tool_run_id": event.tool_run_id, "name": event.name}
    if event.type == "tool_failed":
        return {"type": "tool_failed", "tool_run_id": event.tool_run_id, "name": event.name, "message": event.error or f"{event.name} failed"}
    if event.type == "runtime_error":
        return {"type": "error", "message": event.error or "Runtime error"}
    return None
