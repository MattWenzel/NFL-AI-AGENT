import { state } from "./state.js";
import { bootAuth, getCurrentUser, setPostLoginInit, signOut } from "./auth.js";
import { loadProviders, loadTranscript, refreshConversations, refreshCsvs } from "./api.js";
import { openCsv, renderCsvList } from "./csv.js";
import { setInspectorOpen } from "./inspector.js";
import { applySidebarView, renderConversationList, setSidebarView, startNewSession } from "./sidebar.js";
import { openSettingsModal, closeSettingsModal } from "./settings.js";
import { sendMessage } from "./streaming.js";
import { fillSuggestion, onModelChange, onProviderChange, onToolChoiceChange, render, sendSuggestion } from "./thread.js";
import { copyTextFromNode, escapeHtml } from "./utils.js";

document.getElementById("themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("nfl_theme", next);
});

// --- Mobile sidebar drawer ---
const sidebarEl = document.querySelector(".sidebar");
const sidebarBackdrop = document.getElementById("sidebarBackdrop");

function setSidebarOpen(open) {
  sidebarEl.classList.toggle("open", open);
  sidebarBackdrop.classList.toggle("open", open);
}

function closeSidebarIfMobile() {
  if (window.matchMedia("(max-width: 900px)").matches) setSidebarOpen(false);
}

document.getElementById("mobileMenuBtn").addEventListener("click", () => setSidebarOpen(true));
sidebarBackdrop.addEventListener("click", () => setSidebarOpen(false));

document.getElementById("newChatBtn").addEventListener("click", () => {
  startNewSession();
  closeSidebarIfMobile();
});

document.getElementById("inspectorCollapse").addEventListener("click", () => setInspectorOpen(false));
document.getElementById("inspectorToggle").addEventListener("click", () => setInspectorOpen(true));
document.getElementById("inspectorBackdrop").addEventListener("click", () => setInspectorOpen(false));
document.getElementById("mobileInspectorBtn").addEventListener("click", () => setInspectorOpen(true));

// Tapping a conversation or CSV item dismisses the drawer on mobile so the
// reader immediately sees the selected content.
document.getElementById("conversationList").addEventListener("click", (e) => {
  if (e.target.closest(".conversation-item")) closeSidebarIfMobile();
});
document.getElementById("csvList").addEventListener("click", (e) => {
  if (e.target.closest(".csv-item, .csv-card")) closeSidebarIfMobile();
});
document.getElementById("providerSelect").addEventListener("change", onProviderChange);
document.getElementById("modelSelect").addEventListener("change", onModelChange);
(function initToolChoiceSelect() {
  const sel = document.getElementById("toolChoiceSelect");
  if (!sel) return;
  sel.value = state.toolChoice;
  sel.addEventListener("change", onToolChoiceChange);
})();
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

// --- Sidebar footer / user menu ---
const userWidget = document.getElementById("userWidget");
const userMenu = document.getElementById("userMenu");

function toggleUserMenu(force) {
  const show = typeof force === "boolean" ? force : userMenu.hasAttribute("hidden");
  if (show) {
    userMenu.removeAttribute("hidden");
    userWidget.setAttribute("aria-expanded", "true");
  } else {
    userMenu.setAttribute("hidden", "");
    userWidget.setAttribute("aria-expanded", "false");
    // Reset any in-progress sign-out confirm when the menu closes so the
    // next open starts from the default state.
    resetSignOutConfirm();
  }
}

function resetSignOutConfirm() {
  document.getElementById("signOutConfirm").setAttribute("hidden", "");
  document.getElementById("openSettingsBtn").removeAttribute("hidden");
  document.getElementById("signOutBtn").removeAttribute("hidden");
}

userWidget.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleUserMenu();
});

document.addEventListener("click", (event) => {
  if (!userMenu.hasAttribute("hidden") && !userMenu.contains(event.target) && event.target !== userWidget) {
    toggleUserMenu(false);
  }
});

document.getElementById("openSettingsBtn").addEventListener("click", () => {
  toggleUserMenu(false);
  closeSidebarIfMobile();
  openSettingsModal();
});

document.getElementById("signOutBtn").addEventListener("click", () => {
  // Two-step guard: first click reveals the confirm row, second click (on the
  // red button) actually signs out. Prevents an accidental mis-click from
  // ending the session.
  document.getElementById("openSettingsBtn").setAttribute("hidden", "");
  document.getElementById("signOutBtn").setAttribute("hidden", "");
  document.getElementById("signOutConfirm").removeAttribute("hidden");
  document.getElementById("confirmSignOutBtn").focus();
});

document.getElementById("cancelSignOutBtn").addEventListener("click", resetSignOutConfirm);

document.getElementById("confirmSignOutBtn").addEventListener("click", () => {
  toggleUserMenu(false);
  signOut();
});

document.getElementById("settingsCloseBtn").addEventListener("click", closeSettingsModal);
document.getElementById("settingsBackdrop").addEventListener("click", closeSettingsModal);

function renderUserWidget(user) {
  const avatar = document.getElementById("userAvatar");
  const name = document.getElementById("userName");
  if (!user) {
    avatar.textContent = "?";
    name.textContent = "Not signed in";
    return;
  }
  const initial = (user.email || "?").trim().charAt(0).toUpperCase();
  avatar.textContent = initial || "?";
  name.textContent = user.email;
}

export async function init() {
  renderUserWidget(getCurrentUser());
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

export function autoResize() {
  const input = document.getElementById("input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 220) + "px";
}

window.fillSuggestion = fillSuggestion;
window.sendSuggestion = sendSuggestion;
setPostLoginInit(init);

(async function boot() {
  try {
    const authed = await bootAuth();
    if (authed) {
      await init();
    }
    // If not authed, bootAuth already rendered the sign-in/create-account screen.
  } catch (error) {
    document.getElementById("thread").innerHTML = `<div class="thread-inner"><div class="turn-card"><div class="turn-text">Failed to initialize UI: ${escapeHtml(error.message)}</div></div></div>`;
  }
})();
