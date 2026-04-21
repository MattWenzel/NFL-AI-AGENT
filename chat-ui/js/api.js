import { state } from "./state.js";
import { fetchJSON } from "./utils.js";
import { requestRender } from "./render-dispatch.js";

export async function loadProviders() {
  try {
    state.providers = await fetchJSON("/chat/providers");
  } catch {
    state.providers = [{
      name: "anthropic",
      display_name: "Anthropic",
      models: ["claude-sonnet-4-6"],
      default_model: "claude-sonnet-4-6",
      available: false,
      context_window: 200000,
      supports_streaming: true,
      supports_tools: true,
    }];
  }
  requestRender();
}


export async function refreshConversations() {
  state.conversations = await fetchJSON("/chat/conversations");
  if (state.activeSessionId && !state.conversations.some(c => c.id === state.activeSessionId)) {
    state.activeSessionId = null;
    state.selectedTurnId = null;
  }
}

export async function refreshCsvs() {
  try {
    state.csvs = await fetchJSON("/chat/exports");
  } catch (err) {
    console.error("Failed to load CSVs:", err);
    state.csvs = [];
  }
}


export async function loadTranscript(sessionId) {
  const transcript = await fetchJSON(`/chat/conversations/${sessionId}/transcript`);
  state.transcripts.set(sessionId, transcript);
  state.activeSessionId = sessionId;
  state.selectedTurnId = pickDefaultTurn(transcript);
  localStorage.setItem("nfl_runtime_active_session", sessionId);
  requestRender();
}

function pickDefaultTurn(transcript) {
  const turns = transcript.turns;
  const preferred = [...turns].reverse().find(turn => turn.role !== "user");
  return preferred ? preferred.id : (turns[0] ? turns[0].id : null);
}
