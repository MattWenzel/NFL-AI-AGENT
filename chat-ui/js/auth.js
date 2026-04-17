// Authentication: token storage, auth header helper, /auth/status boot flow,
// inline login/register screens. Must be loaded before api.js / main.js since
// they depend on `authHeaders()` and `handleUnauthorized()`.

const AUTH_TOKEN_KEY = "nfl_auth_token";

let _currentUser = null;

function getAuthToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

function setAuthToken(token) {
  if (token) localStorage.setItem(AUTH_TOKEN_KEY, token);
  else localStorage.removeItem(AUTH_TOKEN_KEY);
}

function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

function getCurrentUser() {
  return _currentUser;
}

function authHeaders(extra) {
  const token = getAuthToken();
  const headers = { ...(extra || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

// Called by fetchJSON when a protected request returns 401. Drops the dead token
// and returns the page to the login screen without a full reload so we don't
// lose any draft text the user was typing.
async function handleUnauthorized() {
  clearAuthToken();
  _currentUser = null;
  await showAuthScreen();
}

async function bootAuth() {
  let status;
  try {
    const resp = await fetch(`${API_BASE}/auth/status`, { headers: authHeaders() });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    status = await resp.json();
  } catch (err) {
    showAuthError(`Cannot reach backend at ${API_BASE}. ${err.message}`);
    return false;
  }
  if (status.authenticated && status.user) {
    _currentUser = status.user;
    hideAuthScreen();
    return true;
  }
  // Not authenticated — show either register (no users yet) or login.
  renderAuthScreen(status.has_users ? "login" : "register");
  return false;
}

function showAuthScreen() {
  const el = document.getElementById("authScreen");
  if (el) el.hidden = false;
  const shell = document.querySelector(".shell");
  if (shell) shell.hidden = true;
}

function hideAuthScreen() {
  const el = document.getElementById("authScreen");
  if (el) el.hidden = true;
  const shell = document.querySelector(".shell");
  if (shell) shell.hidden = false;
}

function renderAuthScreen(mode) {
  const el = document.getElementById("authScreen");
  if (!el) return;
  showAuthScreen();
  const isRegister = mode === "register";
  // action="javascript:void(0)" guards against file:// quirks where a default
  // form submit navigates to the page's own URL and triggers a blank page.
  el.innerHTML = `
    <div class="auth-card">
      <div class="auth-brand"><span class="brand-mark">N</span><span>NFL Stats</span></div>
      <h1>${isRegister ? "Create your account" : "Sign in"}</h1>
      <p class="auth-sub">${isRegister
        ? "Nobody's registered yet. The first account becomes the owner of this instance."
        : "Welcome back."}</p>
      <form id="authForm" class="auth-form" action="javascript:void(0)" autocomplete="on">
        <label>
          Email
          <input name="email" type="email" autocomplete="email" required autofocus>
        </label>
        <label>
          Password
          <input name="password" type="password" autocomplete="${isRegister ? "new-password" : "current-password"}" required ${isRegister ? 'minlength="8"' : ""}>
        </label>
        <div class="auth-error" id="authFormError" hidden></div>
        <button type="submit" class="auth-submit">${isRegister ? "Create account" : "Sign in"}</button>
      </form>
    </div>
  `;
  const form = document.getElementById("authForm");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const email = String(fd.get("email") || "").trim();
    const password = String(fd.get("password") || "");
    const endpoint = isRegister ? "/auth/register" : "/auth/login";
    const errBox = document.getElementById("authFormError");
    errBox.hidden = true;
    form.classList.add("submitting");
    try {
      const resp = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(body.detail || `Request failed (${resp.status})`);
      }
      setAuthToken(body.token);
      _currentUser = body.user;
      hideAuthScreen();
      await postLoginInit();
    } catch (err) {
      errBox.textContent = err.message || String(err);
      errBox.hidden = false;
    } finally {
      form.classList.remove("submitting");
    }
  });
}

function showAuthError(message) {
  const el = document.getElementById("authScreen");
  if (!el) return;
  showAuthScreen();
  el.innerHTML = `
    <div class="auth-card">
      <h1>Can't reach the backend</h1>
      <p class="auth-sub">${escapeHtml(message)}</p>
      <button type="button" class="auth-submit" onclick="location.reload()">Retry</button>
    </div>
  `;
}

async function signOut() {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: authHeaders(),
    });
  } catch (err) {
    // Best-effort — we're clearing locally regardless.
    console.warn("Logout API call failed:", err);
  }
  clearAuthToken();
  _currentUser = null;
  location.reload();
}

// Hook that main.js calls after a successful login to boot the chat UI.
// Defined in main.js; we just reference it here so the flow stays linear.
async function postLoginInit() {
  if (typeof init === "function") {
    await init();
  }
}
