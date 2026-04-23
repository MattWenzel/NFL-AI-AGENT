import { API_BASE, state } from "./state.js";
import { loadTranscript, refreshConversations, refreshCsvs } from "./api.js";
import { handleUnauthorized } from "./auth.js";
import { requestRender } from "./render-dispatch.js";
import { autoResize, renderMarkdown } from "./utils.js";

function readCsrfCookie() {
  const match = (document.cookie || "").match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export async function sendMessage() {
  const input = document.getElementById("input");
  const text = input.value.trim();
  if (!text || state.isStreaming) return;

  const previousTranscript = state.activeSessionId ? state.transcripts.get(state.activeSessionId) : null;
  state.isStreaming = true;
  state.liveTurn = {
    sessionId: state.activeSessionId,
    userText: text,
    assistantText: "",
    status: "starting",
    toolRuns: [],
    errors: [],
    compaction: null,
    notice: null,
  };
  input.value = "";
  autoResize();
  requestRender();

  try {
    const resp = await fetch(`${API_BASE}/chat/stream`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": readCsrfCookie(),
      },
      body: JSON.stringify({
        message: text,
        conversation_id: state.activeSessionId || undefined,
        provider: state.selectedProvider || undefined,
        model: state.selectedModel || undefined,
        // Omit "auto" — it's the server default; sending undefined keeps
        // the wire identical to pre-picker behavior when the user hasn't
        // changed the dropdown.
        tool_choice: state.toolChoice && state.toolChoice !== "auto"
          ? state.toolChoice
          : undefined,
      }),
    });
    if (resp.status === 401) {
      if (typeof handleUnauthorized === "function") await handleUnauthorized();
      throw new Error("Session expired — please sign in again.");
    }
    if (!resp.ok) {
      throw new Error(await resp.text());
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        const event = JSON.parse(raw);
        handleStreamEvent(event, previousTranscript);
      }
    }
  } catch (error) {
    state.liveTurn.status = "error";
    state.liveTurn.errors.push(error.message);
    requestRender();
  } finally {
    state.isStreaming = false;
    document.getElementById("sendBtn").disabled = false;
    document.getElementById("input").focus();
  }
}

function patchLiveText() {
  // Direct-DOM update of the streaming assistant turn's text — bypasses
  // render() so text deltas don't re-parse markdown across the whole
  // transcript. Returns true if the patch succeeded; callers can fall
  // back to render() when the live card isn't mounted yet.
  const el = document.querySelector('[data-live-turn="true"] [data-live-text="true"]');
  if (!el || !state.liveTurn) return false;
  el.innerHTML = renderMarkdown(state.liveTurn.assistantText || "");
  // Stick to the bottom while streaming so the reader follows new text.
  const thread = document.getElementById("thread");
  if (thread) {
    const nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;
    if (nearBottom) thread.scrollTop = thread.scrollHeight;
  }
  return true;
}

function handleStreamEvent(event) {
  if (!state.liveTurn) return;
  if (event.type === "conversation_id") {
    state.liveTurn.sessionId = event.id;
    state.activeSessionId = event.id;
    localStorage.setItem("nfl_runtime_active_session", event.id);
  } else if (event.type === "assistant_started") {
    state.liveTurn.status = "responding";
  } else if (event.type === "text") {
    state.liveTurn.status = "responding";
    state.liveTurn.notice = null;
    state.liveTurn.assistantText += event.text;
    // Critical: skip the full re-render on text deltas. Streaming emits
    // dozens of text events per second, and with many prior turns in the
    // transcript each render re-parses markdown + rebuilds Chart.js
    // instances across the whole thread — main thread chokes. Instead
    // patch only the live turn's text node in place.
    if (patchLiveText()) return;
    // Fall through to render() only if the live card isn't in the DOM yet
    // (first text delta of the turn).
  } else if (event.type === "tool_call") {
    state.liveTurn.status = "tooling";
    state.liveTurn.toolRuns.push({
      id: event.tool_run_id,
      tool_name: event.name,
      input: event.input,
      status: "running",
      result: null,
      error: null,
    });
  } else if (event.type === "tool_result") {
    const tool = state.liveTurn.toolRuns.find(run => run.id === event.tool_run_id);
    if (tool) tool.status = "completed";
    state.liveTurn.status = "thinking";
  } else if (event.type === "tool_failed") {
    const tool = state.liveTurn.toolRuns.find(run => run.id === event.tool_run_id);
    if (tool) {
      tool.status = "error";
      tool.error = event.message;
    }
    // Don't push the tool error into liveTurn.errors — the failed chip in the
    // Thinking block already surfaces it. Duplicating it as a bottom bubble
    // makes the turn look broken even when the model retries and recovers.
  } else if (event.type === "compaction") {
    state.liveTurn.compaction = event.meta;
  } else if (event.type === "retrying") {
    // Provider hit a transient overload before any text streamed.
    // Surface as a notice on the live turn so the user sees progress
    // instead of a silent stall during the backoff sleep.
    const seconds = Math.max(1, Math.round(event.delay_seconds || 0));
    state.liveTurn.notice = `Retrying after rate limit (attempt ${event.attempt}, ~${seconds}s)`;
  } else if (event.type === "error") {
    state.liveTurn.status = "error";
    state.liveTurn.errors.push(event.message);
  } else if (event.type === "done") {
    finishLiveTurn();
    return;
  }
  requestRender();
}

async function finishLiveTurn() {
  const sessionId = state.liveTurn ? state.liveTurn.sessionId : state.activeSessionId;
  state.isStreaming = false;
  // Clear the live turn BEFORE reloading the transcript. loadTranscript
  // triggers render(); if the live turn is still present, the thread
  // renders the just-persisted turn AND a stale live-turn card for the
  // same content — Chart.js can bind to the wrong canvas (or to one
  // whose parent gets replaced by the final render), and the chart
  // ends up empty until the user refreshes.
  state.liveTurn = null;
  if (sessionId) {
    await Promise.all([refreshConversations(), refreshCsvs()]);
    await loadTranscript(sessionId);
  } else {
    requestRender();
  }
}
