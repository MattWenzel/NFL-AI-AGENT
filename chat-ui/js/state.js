const API_BASE = "http://localhost:8001";

const state = {
  conversations: [],
  transcripts: new Map(),
  activeSessionId: localStorage.getItem("nfl_runtime_active_session") || null,
  selectedTurnId: null,
  liveTurn: null,
  isStreaming: false,
  providers: [],
  selectedProvider: localStorage.getItem("nfl_chat_provider") || "anthropic",
  selectedModel: localStorage.getItem("nfl_chat_model") || "",
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

marked.setOptions({ breaks: true, gfm: true });

(function initTheme() {
  const stored = localStorage.getItem("nfl_theme");
  const initial = stored || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = initial;
})();
