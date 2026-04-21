// Authentication: token storage, auth header helper, /auth/status boot flow,
// inline login/register screens. Must be loaded before api.js / main.js since
// they depend on `authHeaders()` and `handleUnauthorized()`.

import { API_BASE } from "./state.js";
import { escapeHtml } from "./utils.js";

const AUTH_TOKEN_KEY = "nfl_auth_token";

let _currentUser = null;
// Populated by bootAuth() from /auth/status. When true, the register form
// renders an "Invite code" input and the server enforces the match.
let _inviteRequired = false;
let postLoginInitHook = null;

export function getAuthToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

export function setAuthToken(token) {
  if (token) localStorage.setItem(AUTH_TOKEN_KEY, token);
  else localStorage.removeItem(AUTH_TOKEN_KEY);
}

export function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

export function getCurrentUser() {
  return _currentUser;
}

export function authHeaders(extra) {
  const token = getAuthToken();
  const headers = { ...(extra || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

// Called by fetchJSON when a protected request returns 401. Drops the dead token
// and returns the page to the login screen without a full reload so we don't
// lose any draft text the user was typing.
export async function handleUnauthorized() {
  clearAuthToken();
  _currentUser = null;
  await showAuthScreen();
}

// Cross-tab session sync. If the user signs out (or signs in) in another tab,
// react here instead of waiting for the next network call to 401.
window.addEventListener("storage", (event) => {
  if (event.key !== AUTH_TOKEN_KEY) return;
  if (!event.newValue) {
    // Token cleared elsewhere — another tab signed out. Drop local state and
    // show the auth screen. Full reload avoids chasing in-flight requests that
    // are still using the old token in memory.
    _currentUser = null;
    location.reload();
  } else if (event.newValue !== event.oldValue) {
    // Token changed (different user logged in, or same user after session refresh).
    // Reload so the app re-boots with the new identity.
    location.reload();
  }
});

export async function bootAuth() {
  let status;
  try {
    const resp = await fetch(`${API_BASE}/auth/status`, { headers: authHeaders() });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    status = await resp.json();
  } catch (err) {
    showAuthError(`Cannot reach backend at ${API_BASE}. ${err.message}`);
    return false;
  }
  _inviteRequired = !!status.invite_required;
  if (status.authenticated && status.user) {
    _currentUser = status.user;
    hideAuthScreen();
    return true;
  }
  // Not authenticated — show either register (no users yet) or login.
  renderAuthScreen(status.has_users ? "login" : "register");
  return false;
}

export function showAuthScreen() {
  const el = document.getElementById("authScreen");
  if (el) el.hidden = false;
  const shell = document.querySelector(".shell");
  if (shell) shell.hidden = true;
}

export function hideAuthScreen() {
  const el = document.getElementById("authScreen");
  if (el) el.hidden = true;
  const shell = document.querySelector(".shell");
  if (shell) shell.hidden = false;
}

export function renderAuthScreen(mode) {
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
      <p class="auth-sub">${isRegister ? "Get started in a few seconds." : "Welcome back."}</p>
      <form id="authForm" class="auth-form" action="javascript:void(0)" autocomplete="on">
        <label>
          Email
          <input name="email" type="email" autocomplete="email" required autofocus>
        </label>
        <label>
          Password
          <input class="auth-pw" name="password" type="password" autocomplete="${isRegister ? "new-password" : "current-password"}" required ${isRegister ? 'minlength="8"' : ""}>
        </label>
        ${isRegister ? `
        <label>
          Confirm password
          <input class="auth-pw" name="passwordConfirm" type="password" autocomplete="new-password" required minlength="8">
        </label>
        ${_inviteRequired ? `
        <label>
          Invite code
          <input name="inviteCode" type="text" autocomplete="off" spellcheck="false" required>
        </label>
        ` : ""}
        ` : ""}
        <label class="auth-show-pw">
          <input type="checkbox" id="authShowPw">
          <span>Show password${isRegister ? "s" : ""}</span>
        </label>
        <div class="auth-error" id="authFormError" hidden></div>
        <button type="submit" class="auth-submit">${isRegister ? "Create account" : "Sign in"}</button>
        <div class="auth-alt" hidden><!-- Reserved for "Continue with Google" when OAuth ships. --></div>
        <p class="auth-switch">${isRegister
          ? 'Already have an account? <a href="#" data-auth-mode="login">Sign in</a>'
          : 'New here? <a href="#" data-auth-mode="register">Create an account</a>'}</p>
      </form>
    </div>
  `;
  const showPwToggle = el.querySelector("#authShowPw");
  if (showPwToggle) {
    showPwToggle.addEventListener("change", () => {
      const type = showPwToggle.checked ? "text" : "password";
      el.querySelectorAll("input.auth-pw").forEach((inp) => { inp.type = type; });
    });
  }
  const switchLink = el.querySelector("[data-auth-mode]");
  if (switchLink) {
    switchLink.addEventListener("click", (event) => {
      event.preventDefault();
      renderAuthScreen(switchLink.dataset.authMode);
    });
  }
  const form = document.getElementById("authForm");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const email = String(fd.get("email") || "").trim();
    const password = String(fd.get("password") || "");
    const endpoint = isRegister ? "/auth/register" : "/auth/login";
    const errBox = document.getElementById("authFormError");
    errBox.hidden = true;
    const body = { email, password };
    if (isRegister) {
      const confirm = String(fd.get("passwordConfirm") || "");
      if (password !== confirm) {
        errBox.textContent = "Passwords don't match.";
        errBox.hidden = false;
        return;
      }
      if (_inviteRequired) {
        body.invite_code = String(fd.get("inviteCode") || "").trim();
      }
    }
    form.classList.add("submitting");
    try {
      const resp = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const respBody = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(respBody.detail || `Request failed (${resp.status})`);
      }
      setAuthToken(respBody.token);
      _currentUser = respBody.user;
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

export function showAuthError(message) {
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

export async function signOut() {
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
export function setPostLoginInit(fn) {
  postLoginInitHook = fn;
}

async function postLoginInit() {
  if (typeof postLoginInitHook === "function") {
    await postLoginInitHook();
  }
}
