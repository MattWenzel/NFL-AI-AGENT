import { state } from "../../core/state.js";
import { confirmDialog } from "../../components/confirm.js";
import { openCsv } from "../exports/service.js";
import { loadTranscript, refreshConversations } from "../../core/api.js";
import { setSidebarView } from "../navigation/sidebar.js";
import { requestRender } from "../../core/render-dispatch.js";
import { fetchJSON, escapeHtml, formatTime } from "../../core/utils.js";

export function startNewSession() {
  state.activeSessionId = null;
  state.selectedTurnId = null;
  state.liveTurn = null;
  localStorage.removeItem("nfl_runtime_active_session");
  requestRender();
}

async function deleteConversation(sessionId) {
  await fetchJSON(`/chat/conversations/${sessionId}`, { method: "DELETE" });
  state.transcripts.delete(sessionId);
  await refreshConversations();
  if (state.activeSessionId === sessionId) {
    startNewSession();
  }
  requestRender();
}

async function renameConversation(sessionId, newTitle) {
  const updated = await fetchJSON(`/chat/conversations/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: newTitle }),
  });
  const idx = state.conversations.findIndex((s) => s.id === sessionId);
  if (idx !== -1) state.conversations[idx] = updated;
  const transcript = state.transcripts.get(sessionId);
  if (transcript) transcript.title = updated.title;
  requestRender();
}

async function togglePinned(sessionId, shouldPin) {
  const updated = await fetchJSON(`/chat/conversations/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pinned: shouldPin }),
  });
  const idx = state.conversations.findIndex((s) => s.id === sessionId);
  if (idx !== -1) state.conversations[idx] = updated;
  // Re-sort so pinned bubble up to the top of state.conversations to match
  // the server order (pinned_at DESC, then updated_at DESC).
  state.conversations.sort((a, b) => {
    if (a.pinned_at && !b.pinned_at) return -1;
    if (!a.pinned_at && b.pinned_at) return 1;
    if (a.pinned_at && b.pinned_at) return b.pinned_at.localeCompare(a.pinned_at);
    return (b.updated_at || "").localeCompare(a.updated_at || "");
  });
  requestRender();
}


export function renderConversationList() {
  const list = document.getElementById("conversationList");
  const countEl = document.getElementById("sidebarSearchCount");
  list.innerHTML = "";

  const query = state.sidebarSearch.trim().toLowerCase();
  const filtered = query
    ? state.conversations.filter(s => (s.title || "").toLowerCase().includes(query))
    : state.conversations;

  if (countEl) {
    countEl.textContent = query
      ? `${filtered.length} of ${state.conversations.length}`
      : (state.conversations.length ? `${state.conversations.length} total` : "");
  }

  const pinned = filtered.filter(s => s.pinned_at);
  const unpinned = filtered.filter(s => !s.pinned_at);

  if (pinned.length) {
    const header = document.createElement("div");
    header.className = "conversation-group-label";
    header.textContent = "Pinned";
    list.appendChild(header);
    for (const session of pinned) {
      list.appendChild(buildConversationItem(session));
    }
  }

  const groups = groupSessionsByTime(unpinned);
  const labels = { today: "Today", yesterday: "Yesterday", thisWeek: "This Week", thisMonth: "This Month", older: "Older" };

  for (const key of ["today", "yesterday", "thisWeek", "thisMonth", "older"]) {
    const sessions = groups[key];
    if (!sessions.length) continue;
    const header = document.createElement("div");
    header.className = "conversation-group-label";
    header.textContent = labels[key];
    list.appendChild(header);
    for (const session of sessions) {
      list.appendChild(buildConversationItem(session));
    }
  }
}

function buildConversationItem(session) {
  const item = document.createElement("div");
  item.className = "conversation-item" + (session.id === state.activeSessionId ? " active" : "");
  const isPinned = Boolean(session.pinned_at);
  const pinIcon = isPinned ? "&#9733;" : "&#9734;";
  const pinTitle = isPinned ? "Unpin session" : "Pin session";
  // A conversation can be linked to a CSV two ways: it was opened from one
  // (source_csv_id) or it generated some (exports.source_session_id). Prefer
  // the seed CSV because that's the context the chat was built around; fall
  // back to the most recently generated CSV from this session. Either way,
  // one 📄 icon; click opens the most relevant target.
  const seedCsv = session.source_csv_id ? state.csvs.find(c => c.id === session.source_csv_id) : null;
  const generatedCsvs = state.csvs
    .filter(c => c.source_session_id === session.id)
    .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  const linkedCsv = seedCsv || generatedCsvs[0] || null;
  const csvLinkCount = (seedCsv ? 1 : 0) + generatedCsvs.length;
  const linkTitle = linkedCsv
    ? (csvLinkCount > 1
        ? `${csvLinkCount} linked CSVs — open: ${linkedCsv.title || linkedCsv.filename}`
        : `Open CSV: ${linkedCsv.title || linkedCsv.filename}`)
    : null;
  const csvIcon = linkedCsv
    ? `<span class="csv-icon" data-csv-id="${escapeHtml(linkedCsv.id)}" title="${escapeHtml(linkTitle)}">&#128196;</span>`
    : (session.source_csv_id
        ? `<span class="csv-icon stale" title="CSV no longer in library">&#128196;</span>`
        : "");
  item.innerHTML = `
    <div class="conversation-top">
      <div style="flex:1">
        <div class="conversation-title">${escapeHtml(session.title || "New session")}</div>
        <div class="conversation-meta">
          <span>${session.message_count} turns</span>
          ${session.model || session.provider ? `<span>${escapeHtml(session.model || session.provider)}</span>` : ""}
          ${session.updated_at ? `<span>${escapeHtml(formatTime(session.updated_at))}</span>` : ""}
        </div>
      </div>
      <div class="conversation-actions">
        <div class="conversation-action-row">
          <button class="pin-btn${isPinned ? " active" : ""}" title="${pinTitle}">${pinIcon}</button>
          <button class="rename-btn" title="Rename session">&#9998;</button>
          <button class="delete-btn" title="Delete session">&times;</button>
        </div>
        ${csvIcon}
      </div>
    </div>`;
  item.addEventListener("click", () => loadTranscript(session.id));
  item.querySelector(".delete-btn").addEventListener("click", async (e) => {
    e.stopPropagation();
    const ok = await confirmDialog({
      title: "Delete this conversation?",
      message: `"${session.title || "New session"}" and its transcript will be permanently removed. This can't be undone.`,
      confirmText: "Delete",
      destructive: true,
    });
    if (!ok) return;
    await deleteConversation(session.id);
  });
  item.querySelector(".rename-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    beginRename(item, session);
  });
  item.querySelector(".pin-btn").addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      await togglePinned(session.id, !isPinned);
    } catch (err) {
      console.error("Pin toggle failed:", err);
    }
  });
  const icon = item.querySelector(".csv-icon:not(.stale)");
  if (icon) {
    icon.addEventListener("click", (e) => {
      e.stopPropagation();
      openCsv(icon.dataset.csvId);
      setSidebarView("csvs");
    });
  }
  return item;
}

function groupSessionsByTime(sessions) {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterdayStart = todayStart - 86400000;
  const weekStart = todayStart - 6 * 86400000;
  const monthStart = todayStart - 29 * 86400000;
  const groups = { today: [], yesterday: [], thisWeek: [], thisMonth: [], older: [] };
  for (const session of sessions) {
    const ts = session.updated_at ? new Date(session.updated_at).getTime() : 0;
    if (ts >= todayStart) groups.today.push(session);
    else if (ts >= yesterdayStart) groups.yesterday.push(session);
    else if (ts >= weekStart) groups.thisWeek.push(session);
    else if (ts >= monthStart) groups.thisMonth.push(session);
    else groups.older.push(session);
  }
  return groups;
}

function beginRename(item, session) {
  const titleEl = item.querySelector(".conversation-title");
  if (!titleEl || titleEl.querySelector("input")) return;
  const currentTitle = session.title || "New session";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "rename-input";
  input.value = currentTitle;
  input.maxLength = 200;
  titleEl.innerHTML = "";
  titleEl.appendChild(input);
  input.focus();
  input.select();
  input.addEventListener("click", (e) => e.stopPropagation());

  let settled = false;
  const cancel = () => {
    if (settled) return;
    settled = true;
    titleEl.textContent = currentTitle;
  };
  const save = async () => {
    if (settled) return;
    const next = input.value.trim();
    if (!next || next === currentTitle) { cancel(); return; }
    settled = true;
    try {
      await renameConversation(session.id, next);
    } catch (err) {
      console.error("Rename failed:", err);
      titleEl.textContent = currentTitle;
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
    else if (e.key === "Escape") { e.preventDefault(); cancel(); }
  });
  input.addEventListener("blur", save);
}
