function renderCsvList() {
  const list = document.getElementById("csvList");
  const countEl = document.getElementById("sidebarSearchCount");
  list.innerHTML = "";

  const query = state.sidebarSearch.trim().toLowerCase();
  const filtered = query
    ? state.csvs.filter(c => (c.title || c.filename || "").toLowerCase().includes(query))
    : state.csvs;

  if (countEl) {
    countEl.textContent = query
      ? `${filtered.length} of ${state.csvs.length}`
      : (state.csvs.length ? `${state.csvs.length} total` : "");
  }

  if (!state.csvs.length) {
    const empty = document.createElement("div");
    empty.className = "csv-empty";
    empty.textContent = "No saved reports yet. Ask the agent to export data and it'll land here.";
    list.appendChild(empty);
    return;
  }

  for (const csv of filtered) {
    list.appendChild(buildCsvItem(csv));
  }
}

function buildCsvItem(csv) {
  const item = document.createElement("div");
  item.className = "csv-item" + (csv.id === state.activeCsvId ? " active" : "");
  item.innerHTML = `
    <div class="conversation-top">
      <div style="flex:1">
        <div class="conversation-title">${escapeHtml(csv.title || csv.filename || "Untitled")}</div>
        <div class="conversation-meta">
          <span>${csv.row_count} rows</span>
          <span>${formatFileSize(csv.file_size)}</span>
          ${csv.created_at ? `<span>${escapeHtml(formatTime(csv.created_at))}</span>` : ""}
        </div>
      </div>
      <div class="conversation-actions">
        <button class="rename-btn" title="Rename CSV">&#9998;</button>
        <button class="delete-btn" title="Delete CSV">&times;</button>
      </div>
    </div>`;
  item.addEventListener("click", () => openCsv(csv.id));
  item.querySelector(".delete-btn").addEventListener("click", async (e) => {
    e.stopPropagation();
    const ok = await confirmDialog({
      title: "Delete this CSV?",
      message: `"${csv.title || csv.filename}" will be removed from the library and the file deleted from disk.`,
      confirmText: "Delete",
      destructive: true,
    });
    if (!ok) return;
    try {
      await deleteCsv(csv.id);
    } catch (err) {
      console.error("Delete CSV failed:", err);
    }
  });
  item.querySelector(".rename-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    beginCsvRename(item, csv);
  });
  return item;
}

// -----------------------------------------------------------------------

function formatCsvCell(val) {
  // Trim IEEE-754 trailing noise in the preview only. The CSV file on disk
  // keeps its exact numeric values (the agent chose ROUND() or didn't), so
  // the download stays faithful; this just prevents the preview table from
  // surfacing "471.20000000000005" when the meaningful value is 471.2.
  if (val == null || val === "") return "";
  const str = String(val);
  if (/\.\d{5,}/.test(str)) {
    const num = Number(str);
    if (Number.isFinite(num)) {
      return parseFloat(num.toFixed(4)).toString();
    }
  }
  return str;
}

function formatFileSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

async function openCsv(csvId) {
  state.activeCsvId = csvId;
  localStorage.setItem("nfl_csv_active_id", csvId);
  if (!state.csvDetails.has(csvId)) {
    try {
      const detail = await fetchJSON(`/chat/exports/${csvId}`);
      state.csvDetails.set(csvId, detail);
    } catch (err) {
      console.error("Failed to load CSV detail:", err);
    }
  }
  render();
}

async function renameCsv(csvId, newTitle) {
  const updated = await fetchJSON(`/chat/exports/${csvId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: newTitle }),
  });
  const idx = state.csvs.findIndex(c => c.id === csvId);
  if (idx !== -1) state.csvs[idx] = updated;
  if (state.csvDetails.has(csvId)) {
    const detail = state.csvDetails.get(csvId);
    state.csvDetails.set(csvId, { ...detail, title: updated.title, updated_at: updated.updated_at });
  }
  render();
}

async function deleteCsv(csvId) {
  await fetchJSON(`/chat/exports/${csvId}`, { method: "DELETE" });
  state.csvs = state.csvs.filter(c => c.id !== csvId);
  state.csvDetails.delete(csvId);
  if (state.activeCsvId === csvId) {
    state.activeCsvId = null;
    localStorage.removeItem("nfl_csv_active_id");
  }
  render();
}

function beginCsvRename(item, csv) {
  const titleEl = item.querySelector(".conversation-title");
  if (!titleEl || titleEl.querySelector("input")) return;
  const currentTitle = csv.title || csv.filename || "Untitled";
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
      await renameCsv(csv.id, next);
    } catch (err) {
      console.error("Rename CSV failed:", err);
      titleEl.textContent = currentTitle;
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
    else if (e.key === "Escape") { e.preventDefault(); cancel(); }
  });
  input.addEventListener("blur", save);
}

function renderCsvViewer() {
  const thread = document.getElementById("thread");
  const detail = state.csvDetails.get(state.activeCsvId);
  const summary = state.csvs.find(c => c.id === state.activeCsvId);
  if (!detail) {
    thread.innerHTML = `<div class="csv-viewer"><div class="csv-viewer-missing">Loading CSV…</div></div>`;
    return;
  }
  const columns = detail.columns || [];
  const rows = detail.preview_rows || [];
  const title = detail.title || (summary && summary.title) || detail.filename;
  const headerCells = columns.map(col => `<th>${escapeHtml(col)}</th>`).join("");
  const bodyRows = rows.map(row => {
    const cells = columns.map(col => `<td>${escapeHtml(formatCsvCell(row[col]))}</td>`).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
  const tableHtml = columns.length
    ? `<div class="csv-viewer-table-wrap">
         <table>
           <thead><tr>${headerCells}</tr></thead>
           <tbody>${bodyRows}</tbody>
         </table>
         ${detail.preview_truncated ? `<div class="csv-viewer-truncated">Preview limited to ${rows.length} rows. Download for full data.</div>` : ""}
       </div>`
    : `<div class="csv-viewer-missing">CSV file is missing on disk — the library row is still here for reference.</div>`;

  thread.innerHTML = `
    <div class="csv-viewer">
      <div class="csv-viewer-header">
        <h2 class="csv-viewer-title">${escapeHtml(title)}</h2>
        <div class="csv-viewer-meta">
          <span>${detail.row_count} rows</span>
          <span>${columns.length} columns</span>
          <span>${formatFileSize(detail.file_size)}</span>
          ${detail.created_at ? `<span>${escapeHtml(formatTime(detail.created_at))}</span>` : ""}
          <span class="mono">${escapeHtml(detail.filename)}</span>
        </div>
        <div class="csv-viewer-actions">
          <button type="button" class="button-primary" id="startChatWithCsv">Start chat about this CSV</button>
          <button type="button" class="button-secondary" id="downloadCsvBtn">Download</button>
        </div>
      </div>
      <details class="csv-viewer-sql"${state.thinkingOpen.has(`sql-${detail.id}`) ? " open" : ""}>
        <summary>Source SQL</summary>
        <pre><code>${escapeHtml(detail.sql || "")}</code></pre>
      </details>
      ${tableHtml}
    </div>`;
  const btn = document.getElementById("startChatWithCsv");
  if (btn) {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await startChatFromCsv(detail.id);
      } catch (err) {
        console.error("Start chat from CSV failed:", err);
        btn.disabled = false;
      }
    });
  }
  const dlBtn = document.getElementById("downloadCsvBtn");
  if (dlBtn) {
    dlBtn.addEventListener("click", async () => {
      dlBtn.disabled = true;
      try {
        await downloadCSV(`${API_BASE}${detail.download_url}`, detail.filename);
      } catch (err) {
        console.error("CSV download failed:", err);
      } finally {
        dlBtn.disabled = false;
      }
    });
  }
  const sqlDetails = thread.querySelector(".csv-viewer-sql");
  if (sqlDetails) {
    const key = `sql-${detail.id}`;
    sqlDetails.addEventListener("toggle", () => {
      if (sqlDetails.open) state.thinkingOpen.add(key);
      else state.thinkingOpen.delete(key);
    });
  }
}

async function startChatFromCsv(csvId) {
  const payload = {
    provider: state.selectedProvider || null,
    model: state.selectedModel || null,
  };
  const resp = await fetchJSON(`/chat/exports/${csvId}/new-session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const newId = resp.conversation_id;
  await refreshConversations();
  state.sidebarView = "chats";
  localStorage.setItem("nfl_sidebar_view", "chats");
  applySidebarView();
  await loadTranscript(newId);
}

