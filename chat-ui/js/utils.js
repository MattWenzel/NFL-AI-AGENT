async function fetchJSON(path, init) {
  const resp = await fetch(`${API_BASE}${path}`, init);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `Request failed (${resp.status})`);
  }
  return resp.json();
}

function preview(text, maxChars) {
  return text.length > maxChars ? text.slice(0, maxChars) + "..." : text;
}

function statusLabel(status) {
  if (status === "tooling") return "Running tools...";
  if (status === "thinking") return "Synthesizing answer...";
  if (status === "error") return "Turn ended with an error.";
  return "Starting response...";
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function renderMarkdown(text) {
  let html = marked.parse(text || "");
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

function copyTextFromNode(btn) {
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

function groupBy(items, keyFn) {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }
  return map;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

async function downloadCSV(url, filename) {
  const resp = await fetch(url);
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

