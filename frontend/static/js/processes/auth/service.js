// Authentication: cookie-based session auth + CSRF double-submit.
// The session cookie is HttpOnly — JS never touches the auth token. A
// separate `csrf_token` cookie is readable by JS and echoed in the
// X-CSRF-Token header (see utils.js).
//
import { API_BASE } from "../../core/state.js";

// Cross-tab auth sync key. Cookies themselves aren't observable via the
// `storage` event, so we broadcast a tiny state marker on login/logout.
const AUTH_STATE_KEY = "nfl_auth_state";

let _currentUser = null;
let _inviteRequired = false;
let _verificationRequired = false;
let _googleOAuthEnabled = false;
let postLoginInitHook = null;

const OAUTH_ERROR_MESSAGES = {
  cancelled: "Google sign-in was cancelled.",
  invalid_state: "Sign-in session expired. Try again.",
  email_unverified: "Your Google email isn't verified. Verify it with Google and try again.",
  oauth_disabled: "Google sign-in isn't configured on this server.",
  rate_limited: "Too many sign-in attempts. Wait a few minutes and try again.",
  signin_failed: "Google sign-in failed. Try again.",
  start_failed: "Could not start Google sign-in. Try again.",
  missing_params: "Google sign-in response was malformed. Try again.",
};

function consumeOAuthErrorFromURL() {
  const params = new URLSearchParams(location.search);
  const code = params.get("oauth_error");
  if (!code) return null;
  // Strip the param so a refresh doesn't re-show the banner.
  params.delete("oauth_error");
  const qs = params.toString();
  history.replaceState(null, "", location.pathname + (qs ? `?${qs}` : "") + location.hash);
  return OAUTH_ERROR_MESSAGES[code] || "Sign-in failed. Try again.";
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function broadcastAuthState(state) {
  try {
    localStorage.setItem(AUTH_STATE_KEY, `${state}:${Date.now()}`);
  } catch (_) {
    // localStorage may be unavailable in private-browsing contexts; the
    // cross-tab sync degrades to "no-op" there but the auth flow still works.
  }
}

export function getCurrentUser() {
  return _currentUser;
}

// Called by fetchJSON when a protected request returns 401. Returns the page
// to the login screen without a full reload so drafts aren't lost.
export async function handleUnauthorized() {
  _currentUser = null;
  broadcastAuthState("signed_out");
  await showAuthScreen();
}

// Cross-tab session sync via a harmless marker key. The cookie itself handles
// the server-side authority; this only drives UI state across tabs.
window.addEventListener("storage", (event) => {
  if (event.key !== AUTH_STATE_KEY) return;
  // Any auth-state change in another tab → reload so the new identity
  // (or lack thereof) is applied consistently.
  location.reload();
});

export async function bootAuth() {
  let status;
  try {
    // credentials:"same-origin" lets the session cookie travel.
    const resp = await fetch(`${API_BASE}/auth/status`, { credentials: "same-origin" });
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    status = await resp.json();
  } catch (err) {
    showAuthError(`Cannot reach backend at ${API_BASE}. ${err.message}`);
    return false;
  }
  _inviteRequired = !!status.invite_required;
  _verificationRequired = !!status.verification_required;
  _googleOAuthEnabled = !!status.google_oauth_enabled;

  // Verification-link deep link: the email CTA points at /#/verify?token=...
  // Handle it before the regular auth gate so the user isn't bounced to
  // login first.
  if (location.hash.startsWith("#/verify")) {
    const consumed = await consumeVerificationLink();
    if (consumed) return true;
  }

  if (status.authenticated && status.user) {
    _currentUser = status.user;
    hideAuthScreen();
    return true;
  }
  const banner = consumeOAuthErrorFromURL();
  renderAuthScreen(status.has_users ? "login" : "register", banner ? { banner } : {});
  return false;
}

export function isGoogleOAuthEnabled() {
  return _googleOAuthEnabled;
}

async function consumeVerificationLink() {
  const params = new URLSearchParams(location.hash.replace(/^#\/verify\??/, ""));
  const token = params.get("token");
  if (!token) return false;
  // Clear the hash so a refresh doesn't re-consume.
  history.replaceState(null, "", location.pathname + location.search);
  try {
    const resp = await fetch(`${API_BASE}/auth/verify-email`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      renderAuthScreen("login", {
        banner: body.detail || "Verification failed. Request a new link.",
      });
      return false;
    }
    _currentUser = body.user;
    broadcastAuthState("signed_in");
    hideAuthScreen();
    await postLoginInit();
    return true;
  } catch (err) {
    renderAuthScreen("login", { banner: err.message || "Verification failed." });
    return false;
  }
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

export function renderAuthScreen(mode, options = {}) {
  const el = document.getElementById("authScreen");
  if (!el) return;
  showAuthScreen();
  const isRegister = mode === "register";
  const banner = options.banner
    ? `<div class="auth-error" style="display:block">${escapeHtml(options.banner)}</div>`
    : "";
  el.innerHTML = `
    <div class="auth-card">
      <div class="auth-brand"><span class="brand-mark">N</span><span>NFL Stats</span></div>
      <h1>${isRegister ? "Create your account" : "Sign in"}</h1>
      <p class="auth-sub">${isRegister ? "Get started in a few seconds." : "Welcome back."}</p>
      ${banner}
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
        ${_googleOAuthEnabled ? `
        <div class="auth-alt">
          <div class="auth-divider"><span>or</span></div>
          <a class="auth-google" href="/auth/oauth/google/start">
            <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
              <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 01-1.796 2.716v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z"/>
              <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.185l-2.908-2.258c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 009 18z"/>
              <path fill="#FBBC05" d="M3.964 10.706A5.41 5.41 0 013.682 9c0-.592.102-1.167.282-1.706V4.962H.957A8.996 8.996 0 000 9c0 1.452.348 2.827.957 4.038l3.007-2.332z"/>
              <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 00.957 4.962L3.964 7.294C4.672 5.167 6.656 3.58 9 3.58z"/>
            </svg>
            Continue with Google
          </a>
        </div>` : `<div class="auth-alt" hidden></div>`}
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
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const respBody = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(respBody.detail || `Request failed (${resp.status})`);
      }
      if (respBody.status === "verification_pending") {
        renderVerificationPending(respBody.email || email);
        return;
      }
      _currentUser = respBody.user;
      broadcastAuthState("signed_in");
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

function renderVerificationPending(emailAddr) {
  const el = document.getElementById("authScreen");
  if (!el) return;
  showAuthScreen();
  const safeEmail = escapeHtml(emailAddr);
  el.innerHTML = `
    <div class="auth-card">
      <div class="auth-brand"><span class="brand-mark">N</span><span>NFL Stats</span></div>
      <h1>Check your email</h1>
      <p class="auth-sub">We sent a verification link to <strong>${safeEmail}</strong>. Click it to finish signing up.</p>
      <div class="auth-error" id="verifyError" hidden></div>
      <div class="auth-feedback" id="verifyFeedback" hidden></div>
      <button type="button" class="auth-submit" id="resendVerification">Resend email</button>
      <p class="auth-switch">
        <a href="#" data-auth-mode="login">Back to sign in</a>
      </p>
    </div>
  `;
  const resendBtn = el.querySelector("#resendVerification");
  const feedback = el.querySelector("#verifyFeedback");
  const err = el.querySelector("#verifyError");
  resendBtn.addEventListener("click", async () => {
    resendBtn.disabled = true;
    resendBtn.textContent = "Sending…";
    err.hidden = true;
    try {
      const resp = await fetch(`${API_BASE}/auth/resend-verification`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: emailAddr }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${resp.status})`);
      }
      feedback.hidden = false;
      feedback.textContent = "Verification email resent. Check your inbox.";
    } catch (e) {
      err.hidden = false;
      err.textContent = e.message || String(e);
    } finally {
      resendBtn.disabled = false;
      resendBtn.textContent = "Resend email";
    }
  });
  const switchLink = el.querySelector("[data-auth-mode]");
  if (switchLink) {
    switchLink.addEventListener("click", (ev) => {
      ev.preventDefault();
      renderAuthScreen("login");
    });
  }
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
    // Include CSRF header since /auth/logout is CSRF-protected.
    const csrf = (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/) || [])[1];
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "same-origin",
      headers: csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {},
    });
  } catch (err) {
    console.warn("Logout API call failed:", err);
  }
  _currentUser = null;
  broadcastAuthState("signed_out");
  location.reload();
}

// Hook that main.js calls after a successful login to boot the chat UI.
export function setPostLoginInit(fn) {
  postLoginInitHook = fn;
}

async function postLoginInit() {
  if (typeof postLoginInitHook === "function") {
    await postLoginInitHook();
  }
}
