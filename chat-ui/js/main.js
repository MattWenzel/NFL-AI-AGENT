document.getElementById("themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("nfl_theme", next);
});

document.getElementById("newChatBtn").addEventListener("click", startNewSession);

document.getElementById("inspectorCollapse").addEventListener("click", () => setInspectorOpen(false));
document.getElementById("inspectorToggle").addEventListener("click", () => setInspectorOpen(true));
document.getElementById("inspectorBackdrop").addEventListener("click", () => setInspectorOpen(false));
document.getElementById("providerSelect").addEventListener("change", onProviderChange);
document.getElementById("modelSelect").addEventListener("change", onModelChange);
document.getElementById("sendBtn").addEventListener("click", sendMessage);
document.getElementById("input").addEventListener("keydown", handleInputKeydown);
document.getElementById("input").addEventListener("input", autoResize);
document.getElementById("sidebarSearch").addEventListener("input", (e) => {
  state.sidebarSearch = e.target.value;
  if (state.sidebarView === "csvs") renderCsvList();
  else renderConversationList();
});
document.getElementById("tabChats").addEventListener("click", () => setSidebarView("chats"));
document.getElementById("tabCsvs").addEventListener("click", () => setSidebarView("csvs"));

async function init() {
  await loadProviders();
  await Promise.all([refreshConversations(), refreshCsvs()]);
  if (state.activeSessionId && state.conversations.some(c => c.id === state.activeSessionId)) {
    await loadTranscript(state.activeSessionId);
  } else {
    state.activeSessionId = null;
  }
  if (state.activeCsvId && !state.csvs.some(c => c.id === state.activeCsvId)) {
    state.activeCsvId = null;
    localStorage.removeItem("nfl_csv_active_id");
  }
  applySidebarView();
  document.addEventListener("click", handleCopyClick);
  render();
}

async function handleCopyClick(event) {
  const btn = event.target.closest(".copy-btn");
  if (!btn) return;
  event.preventDefault();
  event.stopPropagation();
  const text = copyTextFromNode(btn);
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    btn.classList.add("copied");
    btn.textContent = "Copied!";
    setTimeout(() => {
      btn.classList.remove("copied");
      btn.textContent = "Copy";
    }, 1500);
  } catch (err) {
    console.error("Copy failed:", err);
  }
}

function handleInputKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}

function autoResize() {
  const input = document.getElementById("input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 220) + "px";
}

window.fillSuggestion = fillSuggestion;
window.sendSuggestion = sendSuggestion;

init().catch((error) => {
  document.getElementById("thread").innerHTML = `<div class="thread-inner"><div class="turn-card"><div class="turn-text">Failed to initialize UI: ${escapeHtml(error.message)}</div></div></div>`;
});
