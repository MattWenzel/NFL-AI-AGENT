import { API_BASE } from "./state.js";
import { handleUnauthorized } from "./auth.js";

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCookie(name) {
  // document.cookie is a flat "a=1; b=2" string. Parsing once per call is
  // cheap and avoids staleness if another tab updates the cookie.
  const pairs = (document.cookie || "").split(";");
  for (const raw of pairs) {
    const eq = raw.indexOf("=");
    if (eq === -1) continue;
    const k = raw.slice(0, eq).trim();
    if (k !== name) continue;
    return decodeURIComponent(raw.slice(eq + 1).trim());
  }
  return null;
}

export async function fetchJSON(path, init) {
  const options = { ...(init || {}) };
  // Opt-out for endpoints where a 401 means "the password you just typed is
  // wrong" rather than "your session expired" (change-password, delete-account).
  // Bouncing the user to login there would be hostile.
  const skipAuthRedirect = options.skipAuthRedirect === true;
  delete options.skipAuthRedirect;
  // Send cookies so the HttpOnly session cookie travels. Same-origin only —
  // third-party origins can't read our auth state.
  options.credentials = options.credentials || "same-origin";
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (MUTATING_METHODS.has(method)) {
    // Double-submit CSRF: echo the csrf_token cookie in the X-CSRF-Token
    // header. Browsers will send both on same-origin requests; cross-origin
    // attackers can't read the cookie to forge the header.
    const csrf = readCookie("csrf_token");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  options.headers = headers;
  const resp = await fetch(`${API_BASE}${path}`, options);
  if (resp.status === 401 && !skipAuthRedirect) {
    // Cookie expired or was revoked — bounce to the sign-in screen.
    if (typeof handleUnauthorized === "function") await handleUnauthorized();
    throw new Error("Session expired — please sign in again.");
  }
  if (!resp.ok) {
    const text = await resp.text();
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch (_) {}
    throw new Error(detail || `Request failed (${resp.status})`);
  }
  return resp.json();
}

export function preview(text, maxChars) {
  return text.length > maxChars ? text.slice(0, maxChars) + "..." : text;
}

export function statusLabel(status) {
  if (status === "tooling") return "Running tools...";
  if (status === "thinking") return "Synthesizing answer...";
  if (status === "error") return "Turn ended with an error.";
  return "Starting response...";
}

export function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

export function renderMarkdown(text) {
  // Codex/ChatGPT sometimes prefixes links to files it produced with
  // `sandbox:` — an artifact of its training environment. Strip it before
  // `marked` parses, so the downstream /exports/ rewrite sees a plain path
  // (and Firefox doesn't treat `sandbox:` as an unknown protocol and offer
  // to hand the click off to xdg-open).
  const cleaned = (text || "").replace(/\]\(sandbox:/g, "](");
  let html = marked.parse(cleaned);
  html = html.replace(
    /<table>([\s\S]*?)<\/table>/g,
    (_, inner) => `<div class="copy-wrap" data-copy-kind="table"><button type="button" class="copy-btn" data-copy-action="table">Copy</button><div style="overflow-x:auto"><table>${inner}</table></div></div>`
  );
  html = html.replace(
    /<pre>([\s\S]*?)<\/pre>/g,
    (_, inner) => `<div class="copy-wrap"><button type="button" class="copy-btn" data-copy-action="pre">Copy</button><pre>${inner}</pre></div>`
  );
  html = html.replace(
    /<a href="(\/exports\/[^"]+)"[^>]*>([\s\S]*?)<\/a>/g,
    (match, path, label) => `<a class="download-btn" href="#" data-export-url="${API_BASE + path}" data-export-fname="${path.split("/").pop()}">${label}</a>`
  );
  return html;
}

export function copyTextFromNode(btn) {
  const wrap = btn.closest(".copy-wrap");
  if (!wrap) return "";
  const kind = btn.dataset.copyAction;
  if (kind === "pre") {
    const code = wrap.querySelector("pre");
    return code ? code.textContent : "";
  }
  if (kind === "table") {
    const table = wrap.querySelector("table");
    if (!table) return "";
    return Array.from(table.rows)
      .map(row => Array.from(row.cells).map(c => c.textContent.trim()).join("\t"))
      .join("\n");
  }
  return "";
}

export function groupBy(items, keyFn) {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }
  return map;
}

export function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function autoResize() {
  const input = document.getElementById("input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 220) + "px";
}

export async function downloadCSV(url, filename) {
  const resp = await fetch(url, { credentials: "same-origin" });
  if (resp.status === 401 && typeof handleUnauthorized === "function") {
        await handleUnauthorized();
    throw new Error("Session expired — please sign in again.");
  }
  if (!resp.ok) throw new Error(`Download failed (${resp.status})`);
  const blob = await resp.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
