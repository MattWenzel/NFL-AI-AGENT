# UI

A zero-framework browser app. Vanilla JavaScript modules, a handful of globals, and a single `<script type="module">` entrypoint. Everything lives under `frontend/` and mounts into the single `frontend/index.html` shell. No build step, no bundler, no npm.

This doc covers the boot flow, state shape, SSE consumption, the `patchLiveText` performance trick, settings/auth plumbing, and cross-tab sync.

## File map

- `frontend/index.html` — HTML shell, static CSS links, and `static/js/app/main.js`.
- `frontend/static/js/app/` — boot, event wiring, and the top-level render orchestrator.
- `frontend/static/js/core/` — shared state, fetch helpers, render dispatch, markdown/DOM utilities, Chart.js lifecycle.
- `frontend/static/js/processes/auth/` — token/session state, `/auth/status` boot, login/register screens.
- `frontend/static/js/processes/chat/` — composer controls, SSE streaming, transcript rendering, live-turn fast path.
- `frontend/static/js/processes/conversations/` — conversation sidebar list and selection/deletion flows.
- `frontend/static/js/processes/exports/` — CSV/report list, preview, rename/delete, download, seed-new-chat flows.
- `frontend/static/js/processes/inspector/` — runtime inspector panel.
- `frontend/static/js/processes/navigation/` — sidebar tab switching and drawer state.
- `frontend/static/js/processes/settings/` — settings modal, provider credentials, linked identities, Codex OAuth device flow.
- `frontend/static/js/components/` — small reusable widgets such as confirm dialogs.
- `frontend/static/css/base/`, `components/`, `processes/` — CSS grouped by the same high-level ownership.

The browser loads only `static/js/app/main.js`; native ES module imports pull in the rest. Relative import paths now encode ownership, so missing paths fail loudly in the browser console.

## Boot flow

`static/js/app/main.js`. Self-invoking async IIFE:

```
boot()
  ├─ bootAuth()                        # auth.js:62
  │    ├─ fetch /auth/status with stored bearer
  │    ├─ if authenticated: hideAuthScreen, return true
  │    └─ else: renderAuthScreen(login|register), return false
  │
  └─ if authed: init()                  # main.js:128
       ├─ renderUserWidget(user)
       ├─ loadProviders()
       ├─ parallel: refreshConversations + refreshCsvs
       ├─ if activeSessionId valid: loadTranscript(id)
       ├─ applySidebarView() (chats | csvs tab)
       └─ render()
```

The login form on success stores the token, sets `_currentUser`, hides the auth screen, and calls `postLoginInit()` → `init()` — the same boot as a cold page load from a valid token. One entry point.

## The global `state`

`state.js:3`. A single plain object holding everything the UI tracks. Key fields:

| Field | Type | Purpose |
|-------|------|---------|
| `conversations` | `array` | List metadata for the sidebar (title, updated_at, turn count). |
| `transcripts` | `Map<id, transcript>` | Full transcripts, lazy-loaded on selection. |
| `activeSessionId` | `string \| null` | Currently open conversation; persisted to localStorage. |
| `selectedTurnId` | `string \| null` | Which turn's tool runs show in the inspector. |
| `liveTurn` | `object \| null` | The in-flight turn being streamed. See below. |
| `isStreaming` | `bool` | Guards against double-sends. |
| `providers` | `array` | From `/chat/providers`. |
| `selectedProvider` / `selectedModel` | `string` | Current dropdown selections; persisted. |
| `inspectorOpen` | `bool` | Right panel visibility. |
| `thinkingOpen` | `Set<turnId>` | Per-turn "show tool calls" toggles. |
| `csvs`, `activeCsvId`, `csvDetails` | — | CSV library tab state. |
| `chartInstances` | `Map<chartId, Chart>` | Chart.js instance lifecycle — necessary because Chart.js binds to canvas refs that re-renders would invalidate. |
| `pendingCharts` | `Map<chartId, spec>` | Chart specs queued during streaming; applied after final render. |
| `toolChoice` | `"auto" \| "required" \| "none"` | Composer dropdown; persisted to localStorage; sent as `tool_choice` on the next `/chat/stream` call. |
| `sidebarView`, `sidebarSearch` | `string` | Sidebar tab (chats / csvs) + filter text. |

No reactive framework. Mutations to `state` are followed by a call to `requestRender()` (`render-dispatch.js`) which coalesces repeated calls in a single tick into one `render()` invocation. This works because the app is small — most operations touch one section at a time, and the perf-critical path (text deltas) bypasses the full re-render entirely via `patchLiveText`.

## SSE consumption

`streaming.js:1`. `sendMessage()` is the central handler.

Flow:

1. Guard: don't fire if `isStreaming` or text is empty.
2. Initialize `state.liveTurn` with empty `assistantText`, empty `toolRuns`, status `"starting"`.
3. `fetch('/chat/stream', { headers: authHeaders({...}), body: {...} })`.
4. On 401 → `handleUnauthorized()` (auth.js) clears the token and shows the auth screen.
5. On other non-2xx → throw.
6. Read the response body as a stream. Decode chunks, split on `\n`, collect complete `data: {json}` lines, parse, dispatch to `handleStreamEvent(event)`.
7. Buffer incomplete trailing lines for the next chunk (important — events can cross chunk boundaries).

### Event dispatch

`handleStreamEvent` (`streaming.js:87`) branches on `event.type`:

| Event | State change |
|-------|--------------|
| `conversation_id` | Set `activeSessionId`, persist to localStorage. Essential for new conversations — the session id is only known server-side until the first response. |
| `assistant_started` | `liveTurn.status = "responding"`. |
| `text` | Append to `liveTurn.assistantText`; patch DOM via `patchLiveText`; skip `render()` on fast path. |
| `tool_call` | Push a `toolRun` with status `"running"`. |
| `tool_result` | Flip the matching `toolRun` to `"completed"`; status → `"thinking"`. |
| `tool_failed` | Flip `toolRun` to `"error"` with message. Do **not** push to `liveTurn.errors` — the inline failed chip already surfaces it. |
| `compaction` | Store `event.meta` on `liveTurn.compaction` for the inspector. |
| `retrying` | Write a transient `liveTurn.notice` ("Retrying in 3s — rate limited") so the composer shows backoff progress instead of a silent stall. |
| `error` | Status → `"error"`; push message to `liveTurn.errors`. |
| `done` | `finishLiveTurn()` — clear liveTurn, refresh conversations/csvs, reload transcript. |

Every branch except `text` (fast path) ends with `render()`.

### The `patchLiveText` fast path

`streaming.js:70`. The single most important line of JS in the app for perceived performance.

Streaming emits dozens of text deltas per second. A full `render()` on each one would:

1. Re-parse markdown for every turn in the transcript (marked.js isn't free).
2. Tear down and rebuild Chart.js instances on every chart in the thread.
3. Recompute scroll positions and cause visible layout thrash.

On a long conversation with charts, this chokes the main thread. The typing animation stops animating.

`patchLiveText` directly updates the DOM node of the live turn's text:

```js
const el = document.querySelector('[data-live-turn="true"] [data-live-text="true"]');
el.innerHTML = renderMarkdown(state.liveTurn.assistantText || "");
```

Only the live turn's text node changes; every other turn in the thread is untouched. The function also nudges the scroll position to the bottom **only if the user is already near the bottom** (`scrollHeight - scrollTop - clientHeight < 120`). This lets the reader scroll up to review an earlier turn without the stream yanking them back.

Returns `true` if the patch landed. The first text delta of a turn is before the live card is in the DOM, so it returns `false` and the handler falls through to a full `render()` — one full render per turn, rather than one per delta.

### `finishLiveTurn`

`streaming.js:141`. On `done`:

1. **Clear `state.liveTurn` BEFORE reloading the transcript.** The loaded transcript re-creates the persisted turn; if `liveTurn` is still present, the thread renders both, Chart.js binds to the wrong canvas, and the chart ends up empty until refresh.
2. Refresh conversations (so titles / timestamps update) and CSVs (so a newly-generated export appears in the library tab) in parallel.
3. Reload the transcript and render.

## Auth integration

### Token storage

`auth.js:5`. `localStorage.setItem("nfl_auth_token", token)`. Survives page reloads; scoped to origin. Not HttpOnly (can't be — it's in JS), so XSS risk is real — but the app has no user-generated HTML paths except markdown, and `renderMarkdown` (see `utils.js`) goes through marked.js with default sanitization.

`authHeaders({...})` (`auth.js:29`) merges `Authorization: Bearer <token>` into any fetch headers. Every protected request goes through it.

### Cross-tab sync

`auth.js:47`. A `storage` event listener watches the auth token key. If another tab clears the token (signed out) or changes it (signed in as different user), the current tab reloads. This covers:

- User signs out in tab A → tab B reloads to the auth screen.
- User signs in as someone else in tab A → tab B reloads with the new identity.

Full `location.reload()` rather than cleanup-in-place. Cleanup would need to chase every in-flight request that's still using the old token in closure scope; reload is simpler and correct.

### `handleUnauthorized`

`auth.js:39`. Called by fetch wrappers (`api.js`, `streaming.js`) when a protected request returns 401. Clears the token, sets `_currentUser = null`, and shows the auth screen **without a page reload** — preserves any draft text the user was composing in the message input.

## Inspector and Thread rendering

### Thread

`thread.js` (largest file, ~480 lines). Renders all turns into the main column. Handles:

- Markdown-to-HTML via `renderMarkdown` (delegates to marked.js with GFM + line breaks).
- Tool call display: collapsible per-turn "Thinking..." block with status chips per tool.
- Chart mount points: a `<canvas>` per chart; rendering happens post-DOM-paint via `pendingCharts` queue in `state` and a `requestAnimationFrame` flush.
- "Copy" buttons on code blocks and chart data.
- Live turn vs persisted turn: the live turn is marked with `data-live-turn="true"` so `patchLiveText` can find it quickly.

### Inspector

`inspector.js`. Right-side panel showing the currently-selected turn's tool runs in detail — full inputs, full outputs, duration, hint. Also renders compaction metadata when `liveTurn.compaction` is set.

Toggled via `inspectorOpen` state. On mobile, rendered as a drawer over the thread; on desktop, as a persistent column.

### Sidebar

`sidebar.js`. Two tabs (chats, csvs) switched via `state.sidebarView`. Chats tab renders the conversation list with a search filter (`state.sidebarSearch`). CSVs tab lists generated exports. Both share the same search input.

## Settings modal

`settings.js`. Two tabs, Providers and Account.

### Providers tab

Fetched on open:

- `GET /settings/api-keys` → per-provider `ApiKeyStatus` (has_key; for Codex OAuth, also `email` and `expires_at`).
- `PUT /settings/api-keys/{provider}` with a plaintext key to save; `PUT` with null/empty (or `DELETE`) to clear.

For providers with `credential_shape === "codex_oauth"` (ChatGPT), the UI renders a **Connect** button instead of a key input, and the click kicks off the device-code flow:

1. `POST /settings/oauth/codex/start` — receives `pending_id`, `user_code`, `verification_url`, `expires_in`.
2. Renders the code + a link to `https://auth.openai.com/codex/device`. User enters the code there.
3. Polls `GET /settings/oauth/codex/status` every 2 s until the flow reaches a terminal state (`complete` / `expired` / `error`).
4. On `complete`, re-loads provider status (now shows "Connected as <email>, expires <date>").
5. On close-before-complete, `DELETE /settings/oauth/codex/cancel` tears down the background task.

Keys are stored encrypted server-side via Fernet (see [auth.md](auth.md#api-keys)); the UI never has access to the ciphertext or the encryption key. OAuth bundles are stored the same way. From the UI's perspective it's a write-only blob.

### Account tab

Password change (`PUT /auth/password`) and account delete (`DELETE /auth/me`). Account delete requires password confirmation in the form — server also re-validates.

## Fetch helpers

`api.js`. Thin wrappers:

- `fetchJSON(url, opts)` — handles 401 → `handleUnauthorized`, parses JSON, throws on non-2xx.
- `loadProviders()`, `loadTranscript(id)`, `refreshConversations()`, `refreshCsvs()` — endpoint-specific wrappers that update `state` and call `render()`.

Every wrapper uses `authHeaders()` so the bearer token is always included. No interceptor pattern — FastAPI's OpenAPI docs have a "try it out" form that doesn't know about our token, and that was fine to accept as a tradeoff for keeping the fetch path boring.

## Charts

`charts.js` + `state.chartInstances`. Chart.js is heavy — ~200KB — but the app needs proper interactive charts, not static images. The tricky bit is lifecycle: Chart.js binds to a canvas element, and if the canvas gets re-parented by a re-render, the chart has to be destroyed and rebuilt.

`state.chartInstances` keys by a stable `data-chart-id` attribute the renderer assigns. On re-render:

1. Check if a chart instance exists for this id and canvas.
2. If yes, leave it alone.
3. If no (new chart or canvas replaced), destroy any stale instance and build a new one.

`pendingCharts` queues specs produced during streaming so charts don't render mid-stream (when the rest of the thread is still re-rendering rapidly). Charts are flushed after `finishLiveTurn` reloads the transcript.

## No framework — why?

The app has maybe 1500 effective lines of JS. Most state transitions are linear (user types → send → stream → reload). React / Vue / Svelte would add a build step, a reactive system, component boundaries, and meaningfully more cognitive overhead for this code size. `render()` from scratch on most mutations + `patchLiveText` for the one hot path covers it.

The cost: there's no component boundary. Adding a new pane means reading every file to figure out where state lives and where `render()` is called. This would be brittle at 5× the size. At current size, it's faster.

## Adding UI

- **New endpoint integration**: write a fetch wrapper in `api.js` and a call site wherever it fires. Use `authHeaders()` and `fetchJSON` to get the 401 handling free.
- **New state field**: add to `state.js`, update everywhere that reads or mutates it, call `render()`.
- **New DOM element**: add to `frontend/index.html`, wire event listeners in `static/js/app/main.js`. Style in the matching `frontend/static/css/` ownership folder.
- **New streaming event**: add a case to `handleStreamEvent`, add a server-side emitter to [transport.md's](transport.md#sse-event-catalog) catalog.
