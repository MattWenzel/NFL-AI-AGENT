// Settings modal: manage per-provider API keys. Uses the inspector's
// .open-class pattern — CSS handles visibility, JS flips a class.
//
// Providers with credential_shape === "codex_oauth" render a device-code
// flow instead of a paste-key input: POST /settings/oauth/codex/start,
// show the user_code + verification URL, poll /status until the user
// completes sign-in on auth.openai.com/codex/device.

import { fetchJSON, escapeHtml, formatTime } from "../../core/utils.js";
import { getCurrentUser, isGoogleOAuthEnabled } from "../auth/service.js";
import { confirmDialog } from "../../components/confirm.js";
import { loadProviders } from "../../core/api.js";

const CODEX_POLL_MS = 2000;

// Tracks the in-flight device-code flow so we can cancel it when the
// modal closes or the user starts a fresh attempt.
let codexFlowState = null; // { pendingId, timer, cancelled }

let settingsActiveTab = "providers";

export async function openSettingsModal() {
  const modal = document.getElementById("settingsModal");
  const backdrop = document.getElementById("settingsBackdrop");
  if (!modal || !backdrop) return;
  modal.classList.add("open");
  backdrop.classList.add("open");
  document.addEventListener("keydown", settingsKeyHandler);
  settingsActiveTab = "providers";
  attachSettingsTabListeners();
  updateSettingsTabActiveState();
  await renderSettingsBody();
}

function attachSettingsTabListeners() {
  const tabs = document.querySelectorAll("#settingsModal .settings-tab");
  tabs.forEach((tab) => {
    tab.onclick = () => {
      const next = tab.dataset.tab;
      if (next === settingsActiveTab) return;
      cancelCodexFlow({ silent: true });
      settingsActiveTab = next;
      updateSettingsTabActiveState({ focus: true });
      renderSettingsBody();
    };
  });
}

function updateSettingsTabActiveState({ focus = false } = {}) {
  const tabs = document.querySelectorAll("#settingsModal .settings-tab");
  let activeTabEl = null;
  tabs.forEach((tab) => {
    const isActive = tab.dataset.tab === settingsActiveTab;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
    tab.setAttribute("tabindex", isActive ? "0" : "-1");
    if (isActive) activeTabEl = tab;
  });
  // Keep the tabpanel labelled by the currently-active tab for screen readers.
  const panel = document.getElementById("settingsBody");
  if (panel && activeTabEl) {
    panel.setAttribute("aria-labelledby", activeTabEl.id);
  }
  if (focus && activeTabEl) activeTabEl.focus();
}

export function closeSettingsModal() {
  const modal = document.getElementById("settingsModal");
  const backdrop = document.getElementById("settingsBackdrop");
  if (modal) modal.classList.remove("open");
  if (backdrop) backdrop.classList.remove("open");
  document.removeEventListener("keydown", settingsKeyHandler);
  cancelCodexFlow({ silent: true });
}

function settingsKeyHandler(event) {
  if (event.key === "Escape") closeSettingsModal();
}

async function renderSettingsBody() {
  const body = document.getElementById("settingsBody");
  if (!body) return;
  if (settingsActiveTab === "account") {
    body.innerHTML = renderAccountSection();
    const pwForm = document.getElementById("accountPasswordForm");
    if (pwForm) pwForm.addEventListener("submit", onChangePassword);
    const delForm = document.getElementById("accountDeleteForm");
    if (delForm) delForm.addEventListener("submit", onDeleteAccount);
    // Fetch identities async so the rest of Account tab renders immediately
    // and doesn't block on the network round-trip.
    await renderIdentitiesSection();
    return;
  }
  body.innerHTML = `<div class="settings-loading">Loading…</div>`;
  let items;
  try {
    items = await fetchJSON("/settings/api-keys");
  } catch (err) {
    body.innerHTML = `<div class="settings-error">Failed to load: ${escapeHtml(err.message)}</div>`;
    return;
  }
  body.innerHTML = items.map(renderProviderSection).join("");
  body.querySelectorAll("form[data-provider]").forEach((form) => {
    form.addEventListener("submit", onSaveKey);
    const clearBtn = form.querySelector("button[data-action='clear']");
    if (clearBtn) clearBtn.addEventListener("click", onClearKey);
  });
  body.querySelectorAll("[data-codex-action='connect']").forEach((btn) => {
    btn.addEventListener("click", onCodexConnect);
  });
  body.querySelectorAll("[data-codex-action='disconnect']").forEach((btn) => {
    btn.addEventListener("click", onCodexDisconnect);
  });
}

function renderAccountSection() {
  const user = (typeof getCurrentUser === "function" ? getCurrentUser() : null) || {};
  const emailLine = user.email
    ? `<p class="settings-account-identity">${escapeHtml(user.email)}${user.role ? ` · ${escapeHtml(user.role)}` : ""}</p>`
    : "";
  return `
    <section class="settings-section settings-account">
      ${emailLine}

      <h4 class="settings-subheading">Linked sign-in methods</h4>
      <div id="settingsIdentities" class="settings-loading">Loading…</div>
      <div id="settingsIdentitiesFeedback" class="settings-feedback" hidden></div>

      <h4 class="settings-subheading">Change password</h4>
      <form id="accountPasswordForm" autocomplete="off">
        <label>
          Current password
          <input type="password" name="current_password" autocomplete="current-password" required>
        </label>
        <label>
          New password
          <input type="password" name="new_password" minlength="8" autocomplete="new-password" required>
        </label>
        <label>
          Confirm new password
          <input type="password" name="new_password_confirm" minlength="8" autocomplete="new-password" required>
        </label>
        <div class="settings-row-actions">
          <button type="submit" class="settings-save">Save new password</button>
        </div>
        <div class="settings-feedback" hidden></div>
      </form>

      <h4 class="settings-subheading settings-danger-heading">Delete account</h4>
      <p class="settings-danger-copy">
        This permanently removes your account, conversations, saved CSVs, and stored API keys.
        This can't be undone.
      </p>
      <form id="accountDeleteForm" autocomplete="off">
        <label>
          Confirm with your password
          <input type="password" name="password" autocomplete="current-password" required>
        </label>
        <div class="settings-row-actions">
          <button type="submit" class="settings-save destructive">Delete account</button>
        </div>
        <div class="settings-feedback" hidden></div>
      </form>
    </section>
  `;
}

async function renderIdentitiesSection() {
  const container = document.getElementById("settingsIdentities");
  if (!container) return;
  let rows;
  try {
    rows = await fetchJSON("/settings/identities");
  } catch (err) {
    container.innerHTML = `<div class="settings-error">Failed to load: ${escapeHtml(err.message)}</div>`;
    return;
  }
  const hasGoogle = rows.some((r) => r.provider === "google");
  const canLinkGoogle = isGoogleOAuthEnabled() && !hasGoogle;
  const listHtml = rows.length
    ? rows.map(renderIdentityRow).join("")
    : `<p class="settings-empty">No sign-in methods linked yet.</p>`;
  const linkHtml = canLinkGoogle
    ? `<div class="settings-row-actions" style="margin-top:8px;">
         <button type="button" class="settings-save" id="linkGoogleBtn">Link Google account</button>
       </div>`
    : "";
  container.classList.remove("settings-loading");
  container.innerHTML = listHtml + linkHtml;
  container.querySelectorAll("[data-unlink-provider]").forEach((btn) => {
    btn.addEventListener("click", onUnlinkIdentity);
  });
  const linkBtn = document.getElementById("linkGoogleBtn");
  if (linkBtn) linkBtn.addEventListener("click", onLinkGoogle);
}

function renderIdentityRow(item) {
  const label = item.provider === "google" ? "Google" : item.provider === "password" ? "Password" : item.provider;
  const display = item.provider === "password" ? "set" : item.display || "";
  const unlinkBtn = item.removable && item.provider !== "password"
    ? `<button type="button" class="settings-clear" data-unlink-provider="${escapeHtml(item.provider)}">Unlink</button>`
    : `<span class="settings-status off">${item.removable ? "" : "Required"}</span>`;
  return `
    <div class="settings-identity-row">
      <div class="identity-meta">
        <span class="identity-provider">${escapeHtml(label)}</span>
        <span class="identity-display">${escapeHtml(display)}</span>
      </div>
      ${unlinkBtn}
    </div>
  `;
}

async function onLinkGoogle(event) {
  event.preventDefault();
  const btn = event.currentTarget;
  btn.disabled = true;
  btn.textContent = "Starting…";
  try {
    const { auth_url } = await fetchJSON("/settings/identities/google/link", {
      method: "POST",
    });
    // Navigate the browser to Google. The current session cookie travels;
    // the callback will recognize this as a link flow via the user_id
    // stashed in the pending-flow registry.
    location.href = auth_url;
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Link Google account";
    const feedback = document.getElementById("settingsIdentitiesFeedback");
    if (feedback) {
      feedback.hidden = false;
      feedback.className = "settings-feedback error";
      feedback.textContent = err.message || "Could not start Google linking.";
    }
  }
}

async function onUnlinkIdentity(event) {
  event.preventDefault();
  const btn = event.currentTarget;
  const provider = btn.dataset.unlinkProvider;
  const feedback = document.getElementById("settingsIdentitiesFeedback");
  const ok = await confirmDialog({
    title: `Unlink ${provider}?`,
    message: `You'll stop being able to sign in with ${provider}. You can link it again later.`,
    confirmText: "Unlink",
    destructive: true,
  });
  if (!ok) return;
  btn.disabled = true;
  btn.textContent = "Unlinking…";
  if (feedback) feedback.hidden = true;
  try {
    await fetchJSON(`/settings/identities/${encodeURIComponent(provider)}`, {
      method: "DELETE",
    });
    await renderIdentitiesSection();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Unlink";
    if (feedback) {
      feedback.hidden = false;
      feedback.className = "settings-feedback error";
      feedback.textContent = err.message || "Unlink failed.";
    }
  }
}

function renderProviderSection(item) {
  if (item.credential_shape === "codex_oauth") {
    return renderCodexSection(item);
  }
  const status = item.has_key
    ? `<span class="settings-status on">Set${item.updated_at ? ` · updated ${formatTime(item.updated_at)}` : ""}</span>`
    : `<span class="settings-status off">Not set</span>`;
  return `
    <section class="settings-section">
      <header>
        <h3>${escapeHtml(item.display_name || item.provider)}</h3>
        ${status}
      </header>
      <form data-provider="${escapeHtml(item.provider)}" autocomplete="off">
        <label>
          API key
          <input type="password" name="api_key" placeholder="${item.has_key ? "Replace stored key…" : "Paste your key"}" autocomplete="off" spellcheck="false">
        </label>
        <div class="settings-row-actions">
          <button type="submit" class="settings-save">${item.has_key ? "Replace" : "Save"}</button>
          ${item.has_key ? `<button type="button" class="settings-clear" data-action="clear">Remove key</button>` : ""}
        </div>
        <div class="settings-feedback" hidden></div>
      </form>
    </section>
  `;
}

function renderCodexSection(item) {
  const provider = escapeHtml(item.provider);
  const displayName = escapeHtml(item.display_name || item.provider);
  if (item.has_key) {
    const emailLine = item.email
      ? `<div class="codex-identity">Linked · ${escapeHtml(item.email)}</div>`
      : `<div class="codex-identity">Linked</div>`;
    const expiresLine = item.expires_at
      ? `<div class="codex-sub">Tokens refresh automatically · next expiry ${formatTime(new Date(item.expires_at).toISOString())}</div>`
      : `<div class="codex-sub">Tokens refresh automatically</div>`;
    return `
      <section class="settings-section" data-codex-section="${provider}">
        <header>
          <h3>${displayName}</h3>
          <span class="settings-status on">Connected</span>
        </header>
        ${emailLine}
        ${expiresLine}
        <div class="settings-row-actions" style="margin-top:12px;">
          <button type="button" class="settings-clear" data-codex-action="disconnect" data-provider="${provider}">Disconnect</button>
        </div>
        <div class="settings-feedback" hidden></div>
      </section>
    `;
  }
  return `
    <section class="settings-section" data-codex-section="${provider}">
      <header>
        <h3>${displayName}</h3>
        <span class="settings-status off">Not connected</span>
      </header>
      <p class="codex-sub">Sign in with your ChatGPT account to use Codex models.</p>
      <div class="settings-row-actions">
        <button type="button" class="settings-save" data-codex-action="connect" data-provider="${provider}">Connect ChatGPT</button>
      </div>
      <div class="codex-flow" hidden></div>
      <div class="settings-feedback" hidden></div>
    </section>
  `;
}

async function onSaveKey(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const provider = form.dataset.provider;
  const input = form.querySelector("input[name='api_key']");
  const feedback = form.querySelector(".settings-feedback");
  const key = (input.value || "").trim();
  if (!key) {
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = "Paste a key or use Remove to clear the stored one.";
    return;
  }
  feedback.hidden = true;
  form.classList.add("saving");
  try {
    await fetchJSON(`/settings/api-keys/${encodeURIComponent(provider)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key }),
    });
    input.value = "";
    await loadProviders();
    await renderSettingsBody();
  } catch (err) {
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = err.message || "Save failed";
  } finally {
    form.classList.remove("saving");
  }
}

async function onChangePassword(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const feedback = form.querySelector(".settings-feedback");
  const fd = new FormData(form);
  const current = String(fd.get("current_password") || "");
  const next = String(fd.get("new_password") || "");
  const confirm = String(fd.get("new_password_confirm") || "");
  feedback.hidden = true;
  if (next !== confirm) {
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = "New passwords don't match.";
    return;
  }
  form.classList.add("saving");
  try {
    await fetchJSON("/auth/password", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: current, new_password: next }),
      skipAuthRedirect: true,
    });
    form.reset();
    feedback.hidden = false;
    feedback.className = "settings-feedback success";
    feedback.textContent = "Password updated. Other sessions have been signed out.";
  } catch (err) {
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = err.message || "Password change failed";
  } finally {
    form.classList.remove("saving");
  }
}

async function onDeleteAccount(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const feedback = form.querySelector(".settings-feedback");
  const password = String(new FormData(form).get("password") || "");
  feedback.hidden = true;
  const ok = await confirmDialog({
    title: "Delete your account?",
    message: "Your account, conversations, saved CSVs, and stored API keys will be permanently removed. This can't be undone.",
    confirmText: "Delete forever",
    destructive: true,
  });
  if (!ok) return;
  form.classList.add("saving");
  try {
    await fetchJSON("/auth/me", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
      skipAuthRedirect: true,
    });
    // Session cookie was cleared server-side as part of the delete response.
    // Reload to land on the sign-in screen.
    location.reload();
  } catch (err) {
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = err.message || "Account deletion failed";
  } finally {
    form.classList.remove("saving");
  }
}

async function onClearKey(event) {
  event.preventDefault();
  const form = event.currentTarget.closest("form");
  const provider = form.dataset.provider;
  const feedback = form.querySelector(".settings-feedback");
  form.classList.add("saving");
  try {
    await fetchJSON(`/settings/api-keys/${encodeURIComponent(provider)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: null }),
    });
    await loadProviders();
    await renderSettingsBody();
  } catch (err) {
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = err.message || "Remove failed";
  } finally {
    form.classList.remove("saving");
  }
}

// ---------------- Codex OAuth device-code flow ----------------

async function onCodexConnect(event) {
  event.preventDefault();
  const btn = event.currentTarget;
  const section = btn.closest("section.settings-section");
  if (!section) return;
  const flow = section.querySelector(".codex-flow");
  const feedback = section.querySelector(".settings-feedback");
  feedback.hidden = true;

  // Previous attempt still running — cancel it before starting fresh.
  cancelCodexFlow({ silent: true });

  btn.disabled = true;
  btn.textContent = "Starting…";

  let start;
  try {
    start = await fetchJSON("/settings/oauth/codex/start", { method: "POST" });
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Connect ChatGPT";
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = err.message || "Could not start sign-in";
    return;
  }

  btn.hidden = true;
  flow.hidden = false;
  flow.innerHTML = renderCodexFlowPanel(start);
  const cancelBtn = flow.querySelector("[data-codex-action='cancel']");
  if (cancelBtn) cancelBtn.addEventListener("click", onCodexCancel);
  const copyBtn = flow.querySelector("[data-codex-action='copy-code']");
  if (copyBtn) copyBtn.addEventListener("click", onCodexCopyCode);

  // Open the verification page in a new tab for convenience. Users can
  // still copy the code manually if their browser blocks it.
  try {
    window.open(start.verification_url, "_blank", "noopener");
  } catch (_) {
    // Popup blocker — the link in the panel is the fallback.
  }

  codexFlowState = {
    pendingId: start.pending_id,
    cancelled: false,
    timer: null,
  };
  pollCodexStatus(section);
}

function renderCodexFlowPanel(start) {
  const safeCode = escapeHtml(start.user_code);
  return `
    <div class="codex-flow-panel">
      <ol class="codex-steps">
        <li>Open <a href="${escapeHtml(start.verification_url)}" target="_blank" rel="noopener">auth.openai.com/codex/device</a></li>
        <li>
          Enter this code:
          <span class="codex-code-row">
            <code class="codex-code" data-codex-code="${safeCode}">${safeCode}</code>
            <button type="button" class="codex-copy" data-codex-action="copy-code" aria-label="Copy code">Copy</button>
          </span>
        </li>
      </ol>
      <div class="codex-status">Waiting for sign-in…</div>
      <div class="settings-row-actions" style="margin-top:12px;">
        <button type="button" class="settings-clear" data-codex-action="cancel">Cancel</button>
      </div>
    </div>
  `;
}

async function onCodexCopyCode(event) {
  event.preventDefault();
  const btn = event.currentTarget;
  const panel = btn.closest(".codex-flow-panel");
  const codeEl = panel ? panel.querySelector(".codex-code") : null;
  const code = codeEl ? (codeEl.dataset.codexCode || codeEl.textContent || "").trim() : "";
  if (!code) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(code);
    } else {
      // Fallback for older browsers / non-secure contexts — manual selection.
      const range = document.createRange();
      range.selectNodeContents(codeEl);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      document.execCommand("copy");
      sel.removeAllRanges();
    }
    const original = btn.textContent;
    btn.textContent = "Copied";
    btn.classList.add("copied");
    setTimeout(() => {
      btn.textContent = original;
      btn.classList.remove("copied");
    }, 1500);
  } catch (_) {
    btn.textContent = "Copy failed";
    setTimeout(() => { btn.textContent = "Copy"; }, 1500);
  }
}

async function pollCodexStatus(section) {
  if (!codexFlowState || codexFlowState.cancelled) return;
  const { pendingId } = codexFlowState;
  let data;
  try {
    data = await fetchJSON(
      `/settings/oauth/codex/status?pending_id=${encodeURIComponent(pendingId)}`
    );
  } catch (err) {
    if (codexFlowState && codexFlowState.cancelled) return;
    showCodexFlowError(section, err.message || "Status check failed");
    return;
  }
  if (codexFlowState && codexFlowState.cancelled) return;
  if (data.status === "pending") {
    codexFlowState.timer = setTimeout(() => pollCodexStatus(section), CODEX_POLL_MS);
    return;
  }
  if (data.status === "complete") {
    codexFlowState = null;
    await loadProviders();
    await renderSettingsBody();
    return;
  }
  if (data.status === "expired") {
    showCodexFlowError(section, "Sign-in window expired. Try again.");
    return;
  }
  showCodexFlowError(section, data.error || "Sign-in failed. Try again.");
}

function showCodexFlowError(section, message) {
  codexFlowState = null;
  const flow = section.querySelector(".codex-flow");
  const btn = section.querySelector("[data-codex-action='connect']");
  const feedback = section.querySelector(".settings-feedback");
  if (flow) {
    flow.hidden = true;
    flow.innerHTML = "";
  }
  if (btn) {
    btn.hidden = false;
    btn.disabled = false;
    btn.textContent = "Connect ChatGPT";
  }
  if (feedback) {
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = message;
  }
}

function onCodexCancel(event) {
  event.preventDefault();
  cancelCodexFlow({ silent: false });
}

function cancelCodexFlow({ silent }) {
  if (!codexFlowState) return;
  const pendingId = codexFlowState.pendingId;
  if (codexFlowState.timer) clearTimeout(codexFlowState.timer);
  codexFlowState.cancelled = true;
  codexFlowState = null;
  // Best-effort server cancel — ignore errors. Don't await when silent
  // so modal close remains snappy.
  const p = fetchJSON(
    `/settings/oauth/codex/cancel?pending_id=${encodeURIComponent(pendingId)}`,
    { method: "DELETE" }
  ).catch(() => {});
  if (!silent) {
    p.finally(() => {
      renderSettingsBody();
    });
  }
}

async function onCodexDisconnect(event) {
  event.preventDefault();
  const btn = event.currentTarget;
  const section = btn.closest("section.settings-section");
  if (!section) return;
  const provider = btn.dataset.provider;
  const feedback = section.querySelector(".settings-feedback");
  const ok = await confirmDialog({
    title: "Disconnect ChatGPT?",
    message: "You'll need to sign in again to use Codex models.",
    confirmText: "Disconnect",
    destructive: true,
  });
  if (!ok) return;
  feedback.hidden = true;
  btn.disabled = true;
  try {
    await fetchJSON(`/settings/api-keys/${encodeURIComponent(provider)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: null }),
    });
    await loadProviders();
    await renderSettingsBody();
  } catch (err) {
    btn.disabled = false;
    feedback.hidden = false;
    feedback.className = "settings-feedback error";
    feedback.textContent = err.message || "Disconnect failed";
  }
}
