# frontend

The React + TypeScript + Vite UI for the NFL AI Agent. Tailwind for styling, [shadcn/ui](https://ui.shadcn.com/) for component primitives, [lucide-react](https://lucide.dev/) for icons.

Full architecture writeup is in [`../docs/ui.md`](../docs/ui.md). This README is a quick reference for working in this directory.

## Develop

```bash
npm install
npm run dev      # Vite dev server with HMR (separate port; talks to the backend at :8001)
npm run build    # production build → dist/
npm run preview  # serve the built dist/ locally
npm run lint     # eslint
```

The backend (`python3 run.py`, from the repo root) automatically serves `dist/` at `/` when present, with `/assets/*` for hashed bundles. When `dist/` is missing, the backend falls back to the legacy `frontend/` (vanilla JS) — useful while you're working on backend-only changes without rebuilding.

## Layout

```
src/
├── App.tsx                     top-level shell — chat / reports / database routing + persistence
├── main.tsx                    React entry
├── components/
│   ├── auth/                   sign-in / sign-up screens
│   ├── chat/                   transcript renderer (exchange grouping, streaming)
│   ├── command/                ⌘K command palette
│   ├── composer/               message composer + provider/model/tool-choice dropdowns
│   ├── database/               Database tab — DatabaseView, DbHelperChat, HelperComposer, HelperMessageList
│   ├── inspector/              right-pane tool-call details
│   ├── layout/                 AppShell (three-column desktop, drawers on mobile)
│   ├── settings/               account + provider keys + linked identities
│   ├── sidebar/                conversation/report list with column-search
│   ├── tables/                 Reports view — TableChatView, save flow
│   ├── theme/                  theme tokens + provider
│   ├── thread/                 legacy thread renderer (still used in spots)
│   └── ui/                     shadcn primitives
└── lib/
    ├── api.ts                  apiFetch + CSRF + 401 handling
    ├── chatStore.ts            central chat state (sessions, exchanges, streaming)
    ├── chatContext.tsx         React context for chatStore
    ├── dbHelperChat.ts         useDbHelperChat() — Database-tab helper hook (stateless)
    ├── tablesStore.ts          Reports / table_chat state
    ├── tablesContext.tsx       context for the Reports view
    ├── activeTable.ts          current Report selection
    ├── database.ts             /database/* API client
    ├── tables.ts               /reports + /tables API client
    ├── auth.ts                 /auth/* API client
    ├── settings.ts             /settings/* API client
    ├── providers.ts            LLM provider registry mirror
    ├── sse.ts                  openSseStream() — POST + stream reader
    ├── csv.ts                  CSV download helpers
    ├── theme.ts                theme storage
    ├── datetime.ts             formatting helpers
    ├── types.ts                shared TypeScript types
    └── utils.ts                cn()
```

`@/` resolves to `frontend/src/`.

## Conventions

- Components are function components with hooks; no class components.
- State that needs to outlive the component tree lives in a store module under `lib/` (e.g. `chatStore.ts`, `tablesStore.ts`) and is exposed via a context provider in `App.tsx`.
- API calls go through `apiFetch` (`lib/api.ts`) — never raw `fetch`. CSRF + 401 handling is centralized there.
- SSE streams use `openSseStream` (`lib/sse.ts`); same generator powers regular chat and the Database helper chat.
- Tailwind + shadcn — no custom CSS files. Design tokens live in `tailwind.config.ts` and `index.css`.

## What lives where

| You want to... | Look in |
|---|---|
| Add a new API call | `lib/<feature>.ts`; use `apiFetch` |
| Add a new view in the main pane | `App.tsx` (extend the `View` discriminator) + a new component dir |
| Add a new SSE event | `chatStore.sendMessage` switch (or `dbHelperChat.send`) + the server-side emitter (see `../docs/transport.md`) |
| Add a new right-pane surface | Pass `alternateInspector` from `App.tsx` for the relevant view |
| Tweak the design system | `tailwind.config.ts` + `index.css` |
| Add a shadcn primitive | `npx shadcn add <component>` (registers in `components/ui/`) |
