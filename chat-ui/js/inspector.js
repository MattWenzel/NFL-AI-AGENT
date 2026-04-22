import { state } from "./state.js";
import { escapeHtml, formatTime, renderMarkdown } from "./utils.js";

export function setInspectorOpen(open) {
  state.inspectorOpen = open;
  document.getElementById("inspector").classList.toggle("open", open);
  document.getElementById("inspectorBackdrop").classList.toggle("open", open);
  document.getElementById("inspectorToggle").classList.toggle("hidden", open);
}

export function renderInspector() {
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
              <div class="detail-json">${escapeHtml(JSON.stringify(run.input || {}, null, 2))}</div>
              ${run.result ? `<div class="detail-text">${escapeHtml(run.result)}</div>` : ""}
              ${run.error ? `<div class="detail-text" style="background:var(--red-soft);color:var(--red)">${escapeHtml(run.error)}</div>` : ""}
            </div>
          `).join("")}
        </div>
      </section>`);
  }

  if (transcript?.summaries?.length) {
    const turnsById = new Map(transcript.turns.map(t => [t.id, t]));
    cards.push(`
      <section class="panel-card">
        <h4>Compaction</h4>
        <div class="summary-list">
          ${transcript.summaries.map(summary => {
            const summaryTurn = turnsById.get(summary.summary_turn_id);
            const preview = summaryTurn?.text ? summaryTurn.text.slice(0, 140) : "";
            return `
              <div class="summary-entry" data-summary-turn-id="${escapeHtml(summary.summary_turn_id)}">
                <strong>${summary.source_turn_ids.length} turns compacted</strong>
                <span class="muted"> · ${escapeHtml(formatTime(summary.created_at))}</span>
                ${preview ? `<div class="summary-preview">${escapeHtml(preview)}${summaryTurn.text.length > 140 ? "…" : ""}</div>` : ""}
              </div>`;
          }).join("")}
        </div>
      </section>`);
  }

  if (live?.compaction) {
    const meta = live.compaction;
    const turnCount = meta.source_turn_count ?? meta.source_turn_ids?.length ?? 0;
    const summarySource = meta.summary_source === "llm" ? "LLM" : "heuristic";
    cards.push(`
      <section class="panel-card">
        <h4>Live Compaction</h4>
        <div class="stats-grid">
          <div class="stat"><div class="stat-label">Turns compacted</div><div class="stat-value">${turnCount}</div></div>
          <div class="stat"><div class="stat-label">Summary tokens</div><div class="stat-value">${meta.summary_token_count ?? "—"}</div></div>
          <div class="stat"><div class="stat-label">Tokens before</div><div class="stat-value">${meta.active_tokens_before ?? "—"}</div></div>
          <div class="stat"><div class="stat-label">Window</div><div class="stat-value">${meta.context_window ?? "—"}</div></div>
          <div class="stat"><div class="stat-label">Turns kept</div><div class="stat-value">${meta.kept_turn_count ?? "—"}</div></div>
          <div class="stat"><div class="stat-label">Retention budget</div><div class="stat-value">${meta.retention_budget_tokens ?? "—"}</div></div>
          <div class="stat"><div class="stat-label">Summary from</div><div class="stat-value">${summarySource}</div></div>
        </div>
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
