// API backend URL. Default to same-origin ("") so every fetch goes to whatever
// host serves index.html — this works when FastAPI serves the UI directly.
// Override via ?api=<url> for split-hosting or legacy file:// dev.
// Lives in this module (rather than an inline <script> in index.html) so
// the strict CSP in server/app.py doesn't need 'unsafe-inline' for scripts.
export const API_BASE = new URLSearchParams(location.search).get("api") || "";

export const state = {
  conversations: [],
  transcripts: new Map(),
  activeSessionId: localStorage.getItem("nfl_runtime_active_session") || null,
  selectedTurnId: null,
  liveTurn: null,
  isStreaming: false,
  providers: [],
  selectedProvider: localStorage.getItem("nfl_chat_provider") || "anthropic",
  selectedModel: localStorage.getItem("nfl_chat_model") || "",
  // Sticky global preference (like selectedProvider/selectedModel) — persists
  // across sessions; startNewSession intentionally doesn't reset it.
  toolChoice: localStorage.getItem("nfl_chat_tool_choice") || "auto",
  sidebarSearch: "",
  inspectorOpen: false,
  thinkingOpen: new Set(),
  sidebarView: localStorage.getItem("nfl_sidebar_view") || "chats",
  csvs: [],
  activeCsvId: localStorage.getItem("nfl_csv_active_id") || null,
  csvDetails: new Map(),
  chartInstances: new Map(),    // data-chart-id -> Chart.js instance (for lifecycle)
  pendingCharts: new Map(),     // data-chart-id -> {spec, rows} queued for post-render rebuild
};

let renderHook = () => {};

export function registerRenderHook(fn) {
  renderHook = typeof fn === "function" ? fn : () => {};
}

export function requestRender() {
  renderHook();
}

marked.setOptions({ breaks: true, gfm: true });

(function initTheme() {
  const stored = localStorage.getItem("nfl_theme");
  const initial = stored || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = initial;
})();
