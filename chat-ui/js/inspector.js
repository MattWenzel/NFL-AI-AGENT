function setInspectorOpen(open) {
  state.inspectorOpen = open;
  document.getElementById("inspector").classList.toggle("open", open);
  document.getElementById("inspectorBackdrop").classList.toggle("open", open);
  document.getElementById("inspectorToggle").classList.toggle("hidden", open);
}

function renderInspector() {
  const root = document.getElementById("inspectorBody");
  const transcript = state.activeSessionId ? state.transcripts.get(state.activeSessionId) : null;
  const live = state.liveTurn;
  const selectedTurn = transcript?.turns.find(turn => turn.id === state.selectedTurnId) || null;
  const selectedToolRuns = selectedTurn ? transcript.tool_runs.filter(run => run.turn_id === selectedTurn.id) : [];

  const cards = [];
  if (transcript) {
    const compactedTurns = transcript.turns.filter(turn => turn.compacted).length;
    cards.push(`
      <section class="panel-card">
        <h4>Session</h4>
        <div class="stats-grid">
          <div class="stat"><div class="stat-label">Title</div><div class="stat-value">${escapeHtml(transcript.title || "Untitled")}</div></div>
          <div class="stat"><div class="stat-label">Turns</div><div class="stat-value">${transcript.turns.length}</div></div>
          <div class="stat"><div class="stat-label">Model</div><div class="stat-value">${escapeHtml(transcript.model || transcript.provider || "n/a")}</div></div>
          <div class="stat"><div class="stat-label">Compacted</div><div class="stat-value">${compactedTurns}</div></div>
        </div>
      </section>`);
  }

  if (selectedTurn) {
    const tokenStats = selectedTurn.role === "assistant"
      ? `<div class="stat"><div class="stat-label">Input tok</div><div class="stat-value">${selectedTurn.input_tokens || 0}</div></div>
         <div class="stat"><div class="stat-label">Output tok</div><div class="stat-value">${selectedTurn.output_tokens || 0}</div></div>`
      : "";
    const toolCount = selectedToolRuns.length;
    const toolStat = selectedTurn.role === "assistant"
      ? `<div class="stat"><div class="stat-label">Tool runs</div><div class="stat-value">${toolCount}</div></div>`
      : "";
    cards.push(`
      <section class="panel-card">
        <h4>Selected Turn</h4>
        <div class="stats-grid">
          <div class="stat"><div class="stat-label">Role</div><div class="stat-value">${escapeHtml(selectedTurn.role)}</div></div>
          <div class="stat"><div class="stat-label">Status</div><div class="stat-value">${escapeHtml(selectedTurn.status)}</div></div>
          ${tokenStats}
          ${toolStat}
          <div class="stat"><div class="stat-label">Updated</div><div class="stat-value">${escapeHtml(formatTime(selectedTurn.updated_at))}</div></div>
        </div>
        ${selectedTurn.error ? `<div class="inline-status" style="background:var(--red-soft);color:var(--red);margin-top:8px">${escapeHtml(selectedTurn.error)}</div>` : ""}
      </section>`);
  }

  if (selectedTurn?.role === "summary") {
    cards.push(`
      <section class="panel-card">
        <h4>Compaction Summary</h4>
        <div class="detail-prose">${renderMarkdown(selectedTurn.text || "")}</div>
      </section>`);
  }

  if (selectedToolRuns.length || (live && live.toolRuns.length)) {
    const runs = selectedToolRuns.length ? selectedToolRuns : live.toolRuns;
    cards.push(`
      <section class="panel-card panel-bare">
        <h4>Tool Runs</h4>
        <div class="detail-list">
          ${runs.map(run => `
            <div class="detail-item">
              <div class="detail-top">
                <div class="detail-name">${escapeHtml(run.tool_name)}</div>
                <div class="pill">${escapeHtml(run.status)}</div>
              </div>
              <div class="detail-json">${escapeHtml(JSON.stringify(run.input || run.input_json || {}, null, 2))}</div>
              ${run.result ? `<div class="detail-text">${escapeHtml(run.result)}</div>` : ""}
              ${run.error ? `<div class="detail-text" style="background:var(--red-soft);color:var(--red)">${escapeHtml(run.error)}</div>` : ""}
            </div>
          `).join("")}
        </div>
      </section>`);
  }

  if (transcript?.summaries?.length) {
    cards.push(`
      <section class="panel-card">
        <h4>Compaction</h4>
        <div class="summary-list">
          ${transcript.summaries.map(summary => `
            <div class="summary-entry">
              <strong>${summary.source_turn_ids.length} turns compacted</strong><br>
              ${escapeHtml(formatTime(summary.created_at))}
            </div>
          `).join("")}
        </div>
      </section>`);
  }

  if (live?.compaction) {
    cards.push(`
      <section class="panel-card">
        <h4>Live Compaction</h4>
        <div class="detail-text">${escapeHtml(JSON.stringify(live.compaction, null, 2))}</div>
      </section>`);
  }

  if (!cards.length) {
    cards.push(`
      <section class="panel-card">
        <h4>Inspector</h4>
        <div class="detail-text">Select a session or a turn to inspect its transcript, tool runs, and compaction details.</div>
      </section>`);
  }
  root.innerHTML = cards.join("");
}

