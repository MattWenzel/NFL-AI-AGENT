# UI

A React + Vite + TypeScript browser app under `frontend/`. Tailwind for styling, [shadcn/ui](https://ui.shadcn.com/) for the component primitives, [lucide-react](https://lucide.dev/) for icons. Built with `npm run build`; the resulting `frontend/dist/` is served by FastAPI at the same origin (see [transport.md](transport.md#ui-wiring)). No separate static server, no CORS.

This doc covers the boot flow, the central `chatStore`, SSE consumption, the AppShell + alternate-inspector pattern (used by the Database tab), the Reports view, and the auth screen.

## File map

```
frontend/
├── index.html                    # Vite entry; mounts <div id="root">
├── vite.config.ts                # build → frontend/dist/
├── package.json
└── src/
    ├── main.tsx                  # ReactDOM.createRoot(...).render(<App />)
    ├── App.tsx                   # top-level shell: routing between chat / reports / database, persistence
    ├── components/
    │   ├── auth/                 # sign-in / sign-up screens
    │   ├── chat/                 # transcript renderer (exchange grouping, streaming)
    │   ├── command/              # ⌘K command palette
    │   ├── composer/             # message composer + provider/model/tool-choice dropdowns
    │   ├── database/             # Database tab — DatabaseView, DbHelperChat, HelperComposer, HelperMessageList
    │   ├── inspector/            # right-pane tool-call details (the regular agent's transcript)
    │   ├── layout/               # AppShell — three-column desktop, drawers on mobile
    │   ├── settings/             # account + provider keys + linked identities
    │   ├── sidebar/              # conversation/report list with column-search
    │   ├── tables/               # Reports view — TableChatView, save flow
    │   ├── theme/                # theme tokens + provider
    │   ├── thread/               # legacy thread renderer (still used in spots)
    │   └── ui/                   # shadcn primitives (Button, Dialog, Input, Sheet, …)
    └── lib/
        ├── api.ts                # apiFetch + CSRF plumbing + 401 handling
        ├── auth.ts               # /auth/* API client
        ├── chatStore.ts          # central chat state — sessions, exchanges, streaming, providers
        ├── chatContext.tsx       # React context + provider for chatStore
        ├── dbHelperChat.ts       # useDbHelperChat() — Database-tab helper hook (stateless)
        ├── tablesStore.ts        # Reports / table_chat state
        ├── tablesContext.tsx     # context for the Reports view
        ├── activeTable.ts        # current Report selection
        ├── database.ts           # /database/* API client
        ├── tables.ts             # /reports + /tables API client
        ├── sse.ts                # openSseStream() — POST + stream reader
        ├── providers.ts          # LLM provider registry mirror
        ├── settings.ts           # /settings/* API client
        ├── csv.ts                # CSV download helpers
        ├── theme.ts              # theme storage
        ├── datetime.ts           # formatting helpers
        ├── types.ts              # shared TypeScript types
        └── utils.ts              # cn()
```

`@/` resolves to `frontend/src/`.

## Boot flow

`main.tsx` mounts `<App />` into `#root`. `App.tsx`:

1. Reads the persisted `lastView` from `localStorage` (a `{kind: 'chat' | 'reports' | 'database', ...}` discriminated union) so refresh restores the same tab.
2. `useAuth()` calls `GET /auth/status`. While loading, renders a spinner. Unauthenticated → renders `<AuthScreen />`. Authenticated → renders the main app.
3. `<ChatProvider>` and `<TablesProvider>` wrap the tree, exposing `chatStore` and `tablesStore` via context.
4. The shell is `<AppShell sidebar={...} main={...} inspector={...} />`. The active view (`chat` / `reports` / `database`) decides which component sits in `main`. The Database tab additionally passes an `alternateInspector={<DbHelperChat />}` so the right pane swaps to the helper-chat panel for that view only.
5. Persistence effect: any change to the active view writes `lastView` back to `localStorage`.

## `chatStore` — central agent state

`lib/chatStore.ts` (~570 lines). The biggest single module in the UI; conceptually the React equivalent of the legacy `state` object plus `render()` glue. Owns:

- `conversations: ConversationListEntry[]` — sidebar list metadata.
- `transcripts: Map<sessionId, Transcript>` — full transcripts, lazy-loaded on selection.
- `activeSessionId: string | null` — persisted; what's currently in the main pane.
- `selectedTurnId: string | null` — what the inspector shows.
- `liveExchange: LiveExchange | null` — the in-flight user→assistant pair, with sub-states for streaming text and per-tool status. Distinct from the persisted transcript so the streaming render can be efficient and the post-stream reload doesn't double-render anything.
- `providers`, `selectedProvider`, `selectedModel`, `toolChoice` — composer state, persisted to `localStorage`.
- `isStreaming: boolean` — guards double-sends.

Mutations go through reducer-style methods (`sendMessage`, `selectTurn`, `loadTranscript`, …) that produce a new state object; React's `useSyncExternalStore` (or the equivalent context-based hook in `chatContext.tsx`) re-renders subscribers. No manual `requestRender()` like the legacy app — React handles diffing.

The "exchange" abstraction is unique to this UI. A user message + the assistant's response (which may include multiple iterations and tool calls) is grouped into one `Exchange` for rendering, so the chat reads as a conversation rather than a flat list of turns.

## SSE consumption

`lib/sse.ts`. `openSseStream(path, body, {signal})` is a tiny generator that wraps `fetch(...)` with `Accept: text/event-stream`, reads the response body as a stream, and yields parsed `data: {json}` events. Used by both the regular chat (`chatStore.sendMessage` against `/chat/stream`) and the Database helper (`useDbHelperChat.send` against `/database/helper-chat/stream`).

```ts
for await (const event of openSseStream('/chat/stream', body, { signal: ctrl.signal })) {
  switch (event.type) {
    case 'text': /* append delta */ break
    case 'tool_call': /* push to liveExchange */ break
    case 'tool_result': /* mark completed */ break
    case 'tool_failed': /* mark failed */ break
    case 'compaction': /* meta for inspector */ break
    case 'retrying': /* notice text */ break
    case 'error': /* push error */ break
    case 'done': /* finalize */ break
  }
}
```

`AbortController` cancellation flows through to the underlying fetch, so the stop button on the composer cleanly aborts the request — and the runtime's `finally` block flips any in-flight tool runs to `interrupted` server-side (see [runtime.md](runtime.md#cleanup-on-early-exit)).

## AppShell + the alternate-inspector pattern

`components/layout/AppShell.tsx`. Three-column desktop shell: sidebar (left, resizable), main (center), inspector (right, optional). Mobile collapses sidebar and inspector into Sheet drawers.

The right pane has two modes:

- **Default**: `inspector` prop renders the regular `<Inspector />` — tool-call details, compaction metadata for the selected turn.
- **Alternate**: when the parent passes `alternateInspector={<X />}`, that takes precedence over `inspector`. Used exclusively by the Database tab to swap in `<DbHelperChat />` instead of the regular inspector.

The alternate path also opts into different click-away behavior: clicks anywhere outside the alternate panel — including in the main pane — close it (the helper is a side conversation, not a drill-down on the main view), with exemptions for buttons / selects / Radix popover content that need to handle their own clicks first. The default path treats clicks inside `<main>` as in-bounds (because they're often "select another message to inspect").

## Reports view (table_chat)

`components/tables/TableChatView.tsx`. A Report is a `kind="table_chat"` session with a backing `TableStateRecord` (see [persistence.md](persistence.md)). The view shows the live table at the top, a chat thread below it, and a composer at the bottom — same composer as regular chat. The agent has access to `set_table` (rewrite the SQL backing the table) and `create_report` (spawn a new Report from a query). A lock toggle on the toolbar flips `table_states.locked`; while locked, the agent's `set_table` calls are rejected server-side.

Saving a result via the Database tab's "Save as Report" button auto-locks the new Report so it doesn't get rewritten by an off-hand follow-up.

## Database view

`components/database/DatabaseView.tsx`. Read-only schema browser + SQL editor. The right pane is `<DbHelperChat />`, backed by `useDbHelperChat()` (`lib/dbHelperChat.ts`) — see [database-browser.md](database-browser.md) for the architecture. The helper drives the editor via a ref forwarded into `DatabaseView` (`useImperativeHandle`-exposed `runQuery(sql)`).

## Auth screen

`components/auth/`. Three sign-in paths in order of UI prominence:

1. **Continue with Google** — a single button; click flows through `/auth/oauth/google/start`. The button is suppressed when the server hasn't been configured with `GOOGLE_OAUTH_CLIENT_ID`.
2. **Sign in with ChatGPT** — kicks off `POST /auth/oauth/openai/start`, shows the device code, polls `GET /auth/oauth/openai/status` every 2 s until the user finishes the flow at `https://auth.openai.com/codex/device`.
3. **Email + password** — register or log in. The registration form requires an invite code if the server has one configured.

On success, every path lands on the same `init()` — same as a cold load against a valid session cookie. One entry point.

## Fetch helpers

`lib/api.ts`. `apiFetch(path, opts)`:

- Sends `credentials: "same-origin"` (cookies travel automatically on the same origin).
- Reads `csrf_token` from `document.cookie` and echoes it as `X-CSRF-Token` on mutating methods. The double-submit pattern is enforced server-side via `verify_csrf` (see [auth.md](auth.md#browser-session-cookie--csrf)).
- Throws `ApiError(status, detail)` on non-2xx.
- 401 → calls the registered `onUnauthorized` handler (set by `App.tsx`). Clears the user from `chatStore` and re-mounts the auth screen *without a page reload*, preserving any draft text in the composer.

Endpoint-specific clients (`auth.ts`, `database.ts`, `tables.ts`, `settings.ts`) wrap `apiFetch` with typed request/response shapes.

## Settings dialog

`components/settings/`. Three sections in one dialog (max height 85vh, capped at 720px so it fits laptop displays):

- **Account** — password change, account delete, plus the linked-identities list (password / google / openai-codex). Unlink buttons are disabled when removing the identity would leave the user with no way to sign in.
- **Providers** — per-provider API key status. Anthropic + OpenAI take a raw key; "ChatGPT" renders a Connect button that runs the Codex device flow ([auth.md](auth.md#codex-oauth-flow)).
- **Appearance** — theme toggle.

The dialog never sees plaintext keys after they're set — server returns only `has_key` (and for Codex, `email` + `expires_at`). From the UI's perspective it's a write-only blob.

## Theming and design tokens

Tailwind with custom design tokens in `tailwind.config.ts` and CSS variables on `<html>`. The `theme/` provider syncs the active theme to `localStorage` and applies a class to `<html>` so Tailwind's `dark:` variants work everywhere. IBM Plex is wired in via `index.html` for the display font.

## Adding UI

- **New endpoint**: write a typed wrapper in the matching `lib/<feature>.ts` using `apiFetch`.
- **New view in the main pane**: extend the `View` discriminator in `App.tsx`, add a render branch, persist via `lastView`.
- **New SSE event**: add a case in the consumer (`chatStore` or `dbHelperChat`), and add a server-side emitter to [transport.md's](transport.md#sse-event-catalog) catalog.
- **New right-pane surface**: pass `alternateInspector` from `App.tsx` for the relevant view; otherwise stick with the default `<Inspector />`.
