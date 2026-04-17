// Settings modal: manage per-provider API keys. Uses the inspector's
// .open-class pattern — CSS handles visibility, JS flips a class.

async function openSettingsModal() {
  const modal = document.getElementById("settingsModal");
  const backdrop = document.getElementById("settingsBackdrop");
  if (!modal || !backdrop) return;
  modal.classList.add("open");
  backdrop.classList.add("open");
  document.addEventListener("keydown", settingsKeyHandler);
  await renderSettingsBody();
}

function closeSettingsModal() {
  const modal = document.getElementById("settingsModal");
  const backdrop = document.getElementById("settingsBackdrop");
  if (modal) modal.classList.remove("open");
  if (backdrop) backdrop.classList.remove("open");
  document.removeEventListener("keydown", settingsKeyHandler);
}

function settingsKeyHandler(event) {
  if (event.key === "Escape") closeSettingsModal();
}

async function renderSettingsBody() {
  const body = document.getElementById("settingsBody");
  if (!body) return;
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
}

function renderProviderSection(item) {
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
