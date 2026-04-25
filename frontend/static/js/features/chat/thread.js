import { state } from "../../core/state.js";
import { openCsv } from "../exports/service.js";
import { setInspectorOpen, renderInspector } from "../inspector/service.js";
import { setSidebarView } from "../navigation/sidebar.js";
import { autoResize, downloadCSV, escapeHtml, formatTime, groupBy, preview, renderMarkdown, statusLabel } from "../../core/utils.js";

export function renderProviderControls() {
  const providerSelect = document.getElementById("providerSelect");
  providerSelect.innerHTML = "";
  const providers = [...state.providers].sort((a, b) => Number(b.available) - Number(a.available));
  for (const provider of providers) {
    const opt = document.createElement("option");
    opt.value = provider.name;
    opt.textContent = provider.display_name + (provider.available ? "" : " — set key in Settings");
    opt.disabled = !provider.available;
    providerSelect.appendChild(opt);
  }
  const selected = providers.find(p => p.name === state.selectedProvider && p.available)
    || providers.find(p => p.available)
    || providers[0];
  if (selected) {
    state.selectedProvider = selected.name;
    providerSelect.value = selected.name;
  }
  const provider = state.providers.find(p => p.name === state.selectedProvider);
  const modelSelect = document.getElementById("modelSelect");
  modelSelect.innerHTML = "";
  if (!provider) return;
  for (const model of provider.models) {
    const opt = document.createElement("option");
    opt.value = model;
    opt.textContent = model;
    modelSelect.appendChild(opt);
  }
  state.selectedModel = provider.models.includes(state.selectedModel) ? state.selectedModel : provider.default_model;
  modelSelect.value = state.selectedModel;
}

export function onProviderChange(event) {
  state.selectedProvider = event.target.value;
  state.selectedModel = "";
  localStorage.setItem("nfl_chat_provider", state.selectedProvider);
  localStorage.removeItem("nfl_chat_model");
  renderProviderControls();
  renderSessionHeader();
}

export function onModelChange(event) {
  state.selectedModel = event.target.value;
  localStorage.setItem("nfl_chat_model", state.selectedModel);
  renderSessionHeader();
}

export function onToolChoiceChange(event) {
  state.toolChoice = event.target.value;
  localStorage.setItem("nfl_chat_tool_choice", state.toolChoice);
}


export function renderSessionHeader() {
  // No in-page header anymore; reflect the active session title in the browser tab
  // so multiple open tabs stay distinguishable.
  const transcript = state.activeSessionId ? state.transcripts.get(state.activeSessionId) : null;
  const sessionTitle = transcript?.title || state.liveTurn?.userText;
  document.title = sessionTitle ? `${sessionTitle} — NFL AI Stats Agent` : "NFL AI Stats Agent";
}

export function renderThread() {
  const thread = document.getElementById("thread");
  // Preserve scroll across re-renders: stick to the bottom if the user was
  // already there (so streaming updates follow the latest text), otherwise
  // keep their current position (so clicking a chip or turn doesn't yank them).
  const wasNearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 100;
  const savedScroll = thread.scrollTop;
  const transcript = state.activeSessionId ? state.transcripts.get(state.activeSessionId) : null;
  if (!transcript && !state.liveTurn) {
    thread.innerHTML = renderWelcome();
    for (const button of thread.querySelectorAll("[data-suggestion]")) {
      button.addEventListener("click", () => sendSuggestion(button.dataset.suggestion));
    }
    return;
  }

  // Show every turn, including compacted ones. Compaction is a context-
  // window concern (see `build_model_messages` — compacted turns are
  // excluded from LLM input) and must never hide chat history from the
  // user. The inserted summary turn stays visible too so the reader can
  // see the shape of what the LLM currently remembers.
  let turns = transcript ? transcript.turns.slice() : [];
  // A CSV-seeded session has a synthetic summary turn at index 0 that
  // primes the LLM with the SQL/columns/row count. It's meant for the
  // model, not the reader — surface a chip instead. source_csv_id lives on
  // the conversation list entry (not the transcript response), so look it
  // up there.
  const activeConvo = transcript
    ? state.conversations.find(c => c.id === state.activeSessionId)
    : null;
  const sourceCsvId = activeConvo?.source_csv_id || null;
  const seededFromCsv = Boolean(sourceCsvId) && turns.length && turns[0].role === "summary";
  if (seededFromCsv) turns = turns.slice(1);
  const toolRunsByTurn = groupBy(transcript?.tool_runs || [], run => run.turn_id);
  const partsByTurn = groupBy(transcript?.parts || [], part => part.turn_id);

  // Collapse consecutive assistant turns (tool-loop iterations for one user
  // question) into a single card. Each iteration persists as its own turn in
  // the backend transcript, but the user experience reads better as one
  // assistant response with all tools listed beneath.
  const items = [];
  let pendingAssistant = null;
  const flushAssistant = () => {
    if (pendingAssistant) {
      items.push(renderAssistantGroupCard(pendingAssistant, toolRunsByTurn, partsByTurn));
      pendingAssistant = null;
    }
  };
  for (const turn of turns) {
    if (turn.role === "assistant") {
      if (pendingAssistant === null) pendingAssistant = [];
      pendingAssistant.push(turn);
    } else {
      flushAssistant();
      items.push(renderTurnCard(turn, toolRunsByTurn.get(turn.id) || [], partsByTurn.get(turn.id) || []));
    }
  }
  flushAssistant();

  if (state.liveTurn) {
    if (state.liveTurn.userText) items.push(renderLiveUserCard());
    items.push(renderLiveTurnCard());
  }

  let bannerHtml = "";
  if (sourceCsvId) {
    const csvInfo = state.csvs.find(c => c.id === sourceCsvId);
    if (csvInfo) {
      bannerHtml = `<div class="thread-csv-banner" data-csv-id="${escapeHtml(sourceCsvId)}" title="Open CSV viewer">&#128196; Based on: ${escapeHtml(csvInfo.title || csvInfo.filename)}</div>`;
    } else {
      bannerHtml = `<div class="thread-csv-banner stale" title="CSV no longer in library">&#128196; CSV deleted</div>`;
    }
  }
  thread.innerHTML = `<div class="thread-inner">${bannerHtml}${items.join("")}</div>`;
  const banner = thread.querySelector(".thread-csv-banner:not(.stale)");
  if (banner) {
    banner.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = banner.dataset.csvId;
      openCsv(id);
      setSidebarView("csvs");
    });
  }
  // stopPropagation on each node so a chip click selects the chip's
  // iteration, not the wrapping assistant card.
  for (const node of thread.querySelectorAll("[data-turn-id]")) {
    node.addEventListener("click", (e) => {
      e.stopPropagation();
      state.selectedTurnId = node.getAttribute("data-turn-id");
      if (node.classList.contains("thinking-row") || node.classList.contains("summary-chip")) {
        setInspectorOpen(true);
      }
      // Selecting a turn changes zero content — it only moves the
      // .selected marker and updates the inspector. Toggle classes in
      // place instead of re-rendering the thread so Chart.js canvases
      // (and anything else expensive) don't have to tear down and
      // rebuild. Group cards are "selected" if the selected id belongs
      // to the group's outer article OR any descendant with a turn id
      // (e.g. a thinking-row for one of the iterations).
      for (const card of thread.querySelectorAll(".turn-card")) {
        const ids = [
          card.getAttribute("data-turn-id"),
          ...[...card.querySelectorAll("[data-turn-id]")].map(n => n.getAttribute("data-turn-id")),
        ].filter(Boolean);
        card.classList.toggle("selected", ids.includes(state.selectedTurnId));
      }
      renderInspector();
    });
  }
  for (const summary of thread.querySelectorAll(".thinking-summary")) {
    summary.addEventListener("click", (e) => {
      e.stopPropagation();
    });
  }
  for (const details of thread.querySelectorAll(".thinking[data-thinking-key]")) {
    const key = details.getAttribute("data-thinking-key");
    details.addEventListener("toggle", () => {
      if (details.open) state.thinkingOpen.add(key);
      else state.thinkingOpen.delete(key);
    });
  }
  for (const button of thread.querySelectorAll("[data-suggestion]")) {
    button.addEventListener("click", () => sendSuggestion(button.dataset.suggestion));
  }
  for (const button of thread.querySelectorAll("[data-export-url]")) {
    button.addEventListener("click", async (e) => {
      e.preventDefault();
      if (button.classList.contains("unavailable")) return;
      try {
        await downloadCSV(button.dataset.exportUrl, button.dataset.exportFname);
      } catch (err) {
        console.error("Download failed:", err);
        const is404 = String(err && err.message || "").includes("404");
        button.classList.add("unavailable");
        button.textContent = is404
          ? "CSV no longer available"
          : `Download failed: ${err.message}`;
      }
    });
  }
  thread.scrollTop = wasNearBottom ? thread.scrollHeight : savedScroll;
}

function renderInlineCharts(toolRuns) {
  // Scan completed create_chart tool runs and queue each for post-render
  // Chart.js mount. Emits one <canvas> per chart, placed below the
  // assistant's text so it reads as an answer, not as a tool artifact.
  const blocks = [];
  for (const run of toolRuns) {
    if (run.tool_name !== "create_chart") continue;
    if (run.status !== "completed" || !run.result) continue;
    let parsed;
    try {
      parsed = JSON.parse(run.result);
    } catch {
      continue;
    }
    if (!parsed || !parsed.chart_spec || !Array.isArray(parsed.data)) continue;
    const chartId = `tool-${run.id}`;
    state.pendingCharts.set(chartId, { spec: parsed.chart_spec, rows: parsed.data });
    blocks.push(`<div class="inline-chart"><canvas data-chart-id="${escapeHtml(chartId)}"></canvas></div>`);
  }
  return blocks.join("");
}

function renderTurnCard(turn, toolRuns, parts) {
  const roleClass = turn.role === "summary" ? "summary" : turn.role;
  const selected = turn.id === state.selectedTurnId ? " selected" : "";

  if (roleClass === "user") {
    return `
      <article class="turn-card user${selected}" data-turn-id="${escapeHtml(turn.id)}">
        <div>
          <div class="turn-bubble">${renderMarkdown((turn.text || "").trim())}</div>
          <div class="turn-meta-user">${escapeHtml(formatTime(turn.updated_at))}</div>
        </div>
      </article>`;
  }

  if (roleClass === "summary") {
    return `
      <article class="turn-card summary-chip${selected}" data-turn-id="${escapeHtml(turn.id)}">
        <button class="summary-chip-button" type="button">
          <span class="summary-chip-dot"></span>
          Conversation compacted · click for details
        </button>
      </article>`;
  }

  const turnResolved = turn.status !== "running" && turn.status !== "pending";
  const body = turn.text
    ? `<div class="turn-text">${renderMarkdown(turn.text)}</div>`
    : (turnResolved
        ? ""  // errored/completed turn with no text — let the error row below speak for itself
        : `<div class="inline-status"><span class="spinner"></span>Waiting on tool activity</div>`);

  const thinkingHtml = renderThinkingBlock(toolRuns, { groupKey: turn.id });
  const chartsHtml = renderInlineCharts(toolRuns);

  return `
    <article class="turn-card ${roleClass}${selected}" data-turn-id="${escapeHtml(turn.id)}">
      ${thinkingHtml}
      ${body}
      ${chartsHtml}
      ${turn.status === "error" && turn.error ? `<div class="inline-status" style="background:var(--red-soft);color:var(--red)">${escapeHtml(turn.error)}</div>` : ""}
      ${toolRuns.some(run => run.status === "running") ? `<div class="inline-status"><span class="spinner"></span>Tool execution persisted in transcript</div>` : ""}
    </article>`;
}

function renderAssistantGroupCard(iterations, toolRunsByTurn, partsByTurn) {
  // Fast path: a single iteration renders as a regular turn card.
  if (iterations.length === 1) {
    const t = iterations[0];
    return renderTurnCard(t, toolRunsByTurn.get(t.id) || [], partsByTurn.get(t.id) || []);
  }

  const last = iterations[iterations.length - 1];
  const selected = iterations.some(t => t.id === state.selectedTurnId) ? " selected" : "";

  // Concatenate non-empty iteration texts. Markdown paragraph breaks keep the
  // reading flow intact when the model emits commentary between tool calls.
  const combinedText = iterations
    .map(t => (t.text || "").trim())
    .filter(Boolean)
    .join("\n\n");

  // Collect tool runs across every iteration in chronological order. Each
  // chip carries its iteration's turn_id so clicking it selects that
  // iteration in the inspector.
  const allToolRuns = [];
  for (const it of iterations) {
    for (const run of toolRunsByTurn.get(it.id) || []) {
      allToolRuns.push(run);
    }
  }

  const totalInput = iterations.reduce((sum, t) => sum + (t.input_tokens || 0), 0);
  const totalOutput = iterations.reduce((sum, t) => sum + (t.output_tokens || 0), 0);
  // Only surface a turn-level error bubble when the final iteration ended in error.
  // If an earlier iteration failed but a later one recovered (typical tool-retry flow),
  // the failure is already visible as a red chip in the Thinking block — duplicating
  // it as a bottom bubble looks like the whole turn failed when it didn't.
  const lastError = last.status === "error" ? (last.error || iterations.map(t => t.error).find(Boolean)) : null;

  const metaBits = [
    last.status,
    `${iterations.length} iterations`,
    totalOutput ? `${totalOutput} output tok` : "",
    totalInput ? `${totalInput} input tok` : "",
    formatTime(last.updated_at),
  ].filter(Boolean).join(" · ");

  const lastResolved = last.status !== "running" && last.status !== "pending";
  const body = combinedText
    ? `<div class="turn-text">${renderMarkdown(combinedText)}</div>`
    : (lastResolved
        ? ""
        : `<div class="inline-status"><span class="spinner"></span>Waiting on tool activity</div>`);

  const thinkingHtml = renderThinkingBlock(allToolRuns, { groupKey: last.id });
  const chartsHtml = renderInlineCharts(allToolRuns);

  return `
    <article class="turn-card assistant${selected}" data-turn-id="${escapeHtml(last.id)}">
      ${thinkingHtml}
      ${body}
      ${chartsHtml}
      ${lastError ? `<div class="inline-status" style="background:var(--red-soft);color:var(--red)">${escapeHtml(lastError)}</div>` : ""}
      ${allToolRuns.some(run => run.status === "running") ? `<div class="inline-status"><span class="spinner"></span>Tool execution persisted in transcript</div>` : ""}
    </article>`;
}

function renderLiveUserCard() {
  const live = state.liveTurn;
  return `
    <article class="turn-card user">
      <div>
        <div class="turn-bubble">${renderMarkdown((live.userText || "").trim())}</div>
        <div class="turn-meta-user">sending…</div>
      </div>
    </article>`;
}

function renderCompactionBanner(meta) {
  // Phase-1 prune-only compaction: no summary turn was created, just
  // dropped old tool outputs from the active prompt. Render the cheaper
  // event distinctly so the user knows nothing was paraphrased.
  if (meta.summary_source === "prune_only") {
    const pruned = meta.pruned_tool_run_count ?? 0;
    return `<div class="inline-status">Pruned ${pruned} old tool result${pruned === 1 ? "" : "s"} · ${meta.active_tokens_before}→${meta.active_tokens_after} tok / ${meta.context_window} budget</div>`;
  }
  const turnCount = meta.source_turn_count ?? meta.source_turn_ids?.length;
  const turnLabel = turnCount != null ? `${turnCount} earlier turn${turnCount === 1 ? "" : "s"}` : "earlier turns";
  const summaryTokens = meta.summary_token_count;
  const summaryLabel = summaryTokens ? `${summaryTokens}-token summary` : "summary";
  const sourceTag = meta.summary_source === "heuristic"
    ? ` <span class="muted">(heuristic fallback)</span>`
    : "";
  return `<div class="inline-status">Compacted ${turnLabel} into a ${summaryLabel}${sourceTag} · ${meta.active_tokens_before} tok used / ${meta.context_window} budget</div>`;
}

function renderLiveTurnCard() {
  const live = state.liveTurn;
  const selected = !state.selectedTurnId ? " selected" : "";
  const isTyping = !live.assistantText && live.status === "responding";
  const showTypingStatus = !live.assistantText && !isTyping;
  return `
    <article class="turn-card assistant${selected}" data-live-turn="true">
      ${renderThinkingBlock(live.toolRuns, { forceOpen: true })}
      <div class="turn-text" data-live-text="true">${live.assistantText ? renderMarkdown(live.assistantText) : ""}</div>
      ${isTyping ? `<div class="typing-dots"><span></span><span></span><span></span></div>` : ""}
      ${showTypingStatus ? `<div class="inline-status"><span class="spinner"></span>${escapeHtml(statusLabel(live.status))}</div>` : ""}
      ${live.compaction ? renderCompactionBanner(live.compaction) : ""}
      ${live.notice ? `<div class="inline-status"><span class="spinner"></span>${escapeHtml(live.notice)}</div>` : ""}
      ${live.errors.map(error => `<div class="inline-status" style="background:var(--red-soft);color:var(--red)">${escapeHtml(error)}</div>`).join("")}
    </article>`;
}


function renderThinkingBlock(toolRuns, { forceOpen = false, groupKey = "" } = {}) {
  if (!toolRuns.length) return "";
  const running = toolRuns.some(r => r.status === "running");
  const open = forceOpen || running || state.thinkingOpen.has(groupKey);
  const total = toolRuns.length;
  const totalDuration = toolRuns.reduce((sum, r) => sum + (r.duration_ms || 0), 0);
  const summaryBits = [`${total} tool call${total === 1 ? "" : "s"}`];
  if (totalDuration) summaryBits.push(`${totalDuration}ms`);

  const rows = toolRuns.map(run => {
    const preview = extractToolPreview(run);
    const turnAttr = run.turn_id ? ` data-turn-id="${escapeHtml(run.turn_id)}"` : "";
    // While a tool is running, show an elapsed-seconds span updated in place
    // by the ticker in streaming.js — gives the user a heartbeat during
    // multi-second tool calls instead of a silent spinner. The span drops
    // from the DOM once status flips (tool_result / tool_failed).
    let elapsedHtml = "";
    if (run.status === "running" && run.startTime) {
      const elapsed = Math.floor((Date.now() - run.startTime) / 1000);
      elapsedHtml = `<span class="thinking-elapsed" data-tool-start="${run.startTime}">${elapsed}s</span>`;
    }
    return `
      <div class="thinking-row ${escapeHtml(run.status)}"${turnAttr}>
        <span class="thinking-status-dot"></span>
        <span class="thinking-tool">${escapeHtml(run.tool_name)}</span>
        <span class="thinking-preview">${escapeHtml(preview)}</span>
        ${elapsedHtml}
      </div>`;
  }).join("");

  const groupAttr = groupKey ? ` data-thinking-key="${escapeHtml(groupKey)}"` : "";
  return `
    <details class="thinking"${open ? " open" : ""}${groupAttr}>
      <summary class="thinking-summary">
        <span class="thinking-chevron">▸</span>
        <span class="thinking-label">Thinking</span>
        <span class="thinking-meta">${escapeHtml(summaryBits.join(" · "))}</span>
      </summary>
      <div class="thinking-body">${rows}</div>
    </details>`;
}

function extractToolPreview(run) {
  const input = run.input;
  if (!input) return "";
  if (typeof input === "string") return input.slice(0, 160);
  if (input.sql) return String(input.sql).replace(/\s+/g, " ").slice(0, 160);
  if (input.name) return `name="${input.name}"`;
  if (input.table_name) return `table=${input.table_name}`;
  if (input.gsis_id) return `gsis_id=${input.gsis_id}`;
  try { return JSON.stringify(input).slice(0, 160); } catch { return ""; }
}

function renderWelcome() {
  return `
    <div class="welcome">
      <div class="welcome-card">
        <h2>Every NFL stat since 1999 — just ask.</h2>
        <p>Players, seasons, play-by-play, advanced metrics, Next Gen Stats. Compare careers, dig into a single drive, or export a CSV for spreadsheet-land. The agent writes the SQL and shows its work.</p>
        <div class="suggestions">
          ${[
            ["Who led the NFL in passing yards in 2024?", "Season leaderboard"],
            ["Compare Patrick Mahomes and Josh Allen's 2024 seasons", "Side-by-side player stats"],
            ["Export the top 25 PPR scorers from 2024 to CSV", "Saved to your CSV library"],
            ["Which rookie QB has had the best season since 1999?", "25 years of history"],
          ].map(([label, desc]) => `
            <button class="suggestion" data-suggestion="${escapeHtml(label)}">
              <strong>${escapeHtml(label)}</strong>
              <span>${escapeHtml(desc)}</span>
            </button>`).join("")}
        </div>
      </div>
    </div>`;
}

export function fillSuggestion(text) {
  const input = document.getElementById("input");
  input.value = text;
  autoResize();
  input.focus();
}

export function sendSuggestion(text) {
  fillSuggestion(text);
  document.getElementById("sendBtn")?.click();
}
