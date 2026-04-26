# Build Tasks: Chat Workspace

Generated from: `.design/chat-workspace/DESIGN_BRIEF.md`
Date: 2026-04-26

Each task is a vertical slice: structure + styling + interaction in one go,
verifiable against the brief, sized to fit a single session.

The shadcn registry (Radix + Nova preset, already installed) supplies
primitives — they are *added*, not built. "Add primitive X" is not its own
task; primitives are pulled in by the task that first needs them.

## Foundation

- [ ] **Smoke-test the tokens against shadcn primitives** — Add `button`, `input`, `select`, `tabs`, `sheet`, `dialog`, `tooltip`, `separator`, `skeleton`, `scroll-area`, `sonner` via `npx shadcn@latest add`. Replace the token-preview `App.tsx` with a primitives gallery (one of each, every variant, both modes). Confirm the sage-teal accent, paper-warm bg, and Plus Jakarta + Fraunces pairing render correctly through every shadcn primitive. _Risk-first: catches token-name mismatches before any feature work._

## Core UI

- [ ] **App shell layout** — Three-pane responsive shell (sidebar / main / inspector). Use shadcn `sidebar` + custom main + custom inspector rail. Resizable sidebar (default 280px) and inspector (default 360px, collapsible from the right edge with a thin handle). Skeleton content in each pane so the layout proves out before features land. _Visual priority: the most prominent structural element._
- [ ] **Composer** — Textarea + provider/model/tool selects on a persistent meta row + send button. Inline-grow textarea (no jump). Enter sends, Shift+Enter newlines. Anchored at the bottom of `main`; sticky on mobile. Disabled state during streaming with a stop button overlay. _Sets the aesthetic for the most-used surface._
- [ ] **Empty thread state** — When no conversation is selected, show a calm empty state — type-led, never illustrative-cute. Reuses the brief's "thread reads like a document" principle: a title in Fraunces, one sentence of muted-foreground guidance, no chrome. _New component._

## Streaming Chat (highest risk)

- [ ] **Thread rendering with mock data** — Build the assistant turn as a typographic sequence: claim → reasoning → tool-pending → tool-completed-summary → final-text → optional table → optional chart. Render from a hand-written mock transcript (no SSE yet). This is where aesthetic meets reality — get the typographic rhythm right before wiring streams. _Risk-first: aesthetic verification on the most-touched surface._
- [ ] **User turn rendering** — User message bubble (or non-bubble — decide during build per the "thread as document" principle). Multi-line, monospace inline-code support, link rendering, copy-to-clipboard on hover. _Depends on: Thread rendering._
- [ ] **Tool-call surface** — Restrained inline call-out for tool runs. Collapsed view: tool name + 1-line summary. Expanded view: structured input (JSON), structured output (truncated table/text), latency, status. Click to expand; never a code-blob dump by default. Uses shadcn `collapsible` and our mono token. _Depends on: Thread rendering._
- [ ] **Inline data table** — Render tool-returned tabular data with our typographic table style: tabular-nums, hairline row separators only when ≥ N rows, right-align numerics, generous padding. Handles ≤200 rows inline; larger results link to the CSV viewer. _Depends on: Thread rendering._
- [ ] **Inline chart** — Render tool-returned chart specs. Decision point: keep Chart.js (port the existing wrapper) or switch to `recharts` (shadcn-native). Re-evaluate on first chart task; pick the one that themes against our tokens with less work. _Depends on: Thread rendering._
- [ ] **SSE wiring (`/chat/stream`)** — Replace mock data with live SSE. Handle event types from `backend/server/sse.py`: `assistant_started`, `text_delta`, `tool_pending`, `tool_completed`, `assistant_requires_followup`, `retrying`, `error`, `done`. Aria-live region announces text deltas (debounced) and tool-call boundaries. Cookie + CSRF round-trip via Vite proxy. _Depends on: Thread rendering, Tool-call surface, Composer._
- [ ] **Conversation persistence** — Load existing transcript via `/chat/conversations/{id}/transcript` and replay assistant parts + tool runs in the new typographic style. Cross-fade thread on switch (no flash). _Depends on: SSE wiring._

## Sidebar

- [ ] **Sidebar shell + brand + new-chat** — Top section: brand mark + title + theme toggle + "New chat" button. Hairline border-bottom. _Depends on: App shell layout._
- [ ] **Conversations tab + list** — Tabs (`Chats` / `Reports`). Conversation rows: title (truncated), provider chip, relative time. Pinned group above unpinned with a hairline divider + uppercase eyebrow label. Hover reveals pin / delete actions. Active conversation highlighted with the secondary surface, not a heavy bg. _Depends on: Sidebar shell. Wires to `/chat/conversations`._
- [ ] **Sidebar search** — Inline search input filtering the visible list (chats or reports depending on active tab). Result count meta line. Empty-search state. _Depends on: Conversations tab._
- [ ] **User widget + menu** — Avatar + name + role + chevron. Click opens a `dropdown-menu` (shadcn primitive) with: Settings, Theme submenu (Light/Dark/System), Sign out. Sign-out triggers the destructive AlertDialog. _Depends on: Sidebar shell._

## Auxiliary Surfaces

- [ ] **Auth screens** — Sign in / sign up / verify-email / forgot-password / reset-password / Google OAuth-link. Single-column, generous, calm. Cards on the warm-paper bg, not full-bleed forms. Existing API contract (`/auth/*`). Uses shadcn `input`, `label`, `button`, `alert`. _New components, framework-agnostic IA._
- [ ] **Settings modal: Providers tab** — Provider list (Anthropic, OpenAI/Codex) with current key status, masked display, "Reveal" on hover, save on blur. Default-model selector per provider. Codex device-flow OAuth. Wires to `/settings/api-keys` and `/settings/oauth/codex/*`. _Depends on: shadcn `dialog`, `tabs`._
- [ ] **Settings modal: Account tab** — Email, password change, change-email, delete account (AlertDialog). Wires to `/auth/password`, `/auth/me`. _Depends on: Settings modal: Providers tab (shares dialog shell)._
- [ ] **Settings modal: Identities tab** — Google sign-in linking. Show linked identities (`/settings/identities`), link/unlink with the destructive AlertDialog when unlinking the last identity. _Depends on: Settings modal: Account tab._
- [ ] **CSV library list (Reports tab)** — Sidebar Reports tab: CSV entries with title, row count + size + created-at meta. Hover reveals delete + open. Wires to `/chat/exports` and `/exports/{filename}`. _Depends on: Conversations tab + list (shared row pattern)._
- [ ] **CSV viewer** — Full main-area surface when a CSV is opened. Header: title + meta + actions (Download, Delete, New session from CSV). Body: virtualized data table with tabular nums, sortable columns, sticky header. Empty / loading / error states. _Depends on: Inline data table (shares the table style)._

## Inspector

- [ ] **Inspector — session metadata + turn list** — Right rail surface. Session title, provider, model, created/updated, total turns, total tokens. Turn list in chronological order, click to highlight that turn in the thread. Clean three-line rows; not a debug dump. _Depends on: App shell layout, Conversation persistence._
- [ ] **Inspector — tool run detail** — On click of a tool run in the thread or the inspector list, expand its detail card in the inspector: full input JSON, full output, status, latency, retries. Mono token, collapsible JSON tree. _Depends on: Inspector — session metadata, Tool-call surface._
- [ ] **Inspector — compaction history** — When the runtime has compacted older turns, surface the summaries. Calm timeline view with the original turn count + summary text + when. _Depends on: Inspector — session metadata._

## Interactions & States

- [ ] **Theme toggle (light / dark / system)** — Three-state, persisted to `localStorage`. `prefers-color-scheme` is the default. Lives in the user-widget dropdown, not the sidebar header. Animates the transition (200ms cross-fade) honoring `prefers-reduced-motion`. _Depends on: User widget + menu._
- [ ] **Destructive AlertDialog** — Single shared component used by: sign out, delete account, delete conversation, delete CSV, unlink last identity. Calm copy, primary action visually quiet, destructive accent only on the confirm button. Uses shadcn `alert-dialog`. _Reuses across many tasks above._
- [ ] **Toast / inline error notifications** — shadcn `sonner` themed to our tokens. Used for: API errors, rate limits, copy-to-clipboard confirmations. Non-modal, dismiss-on-action, not stacked beyond 3. _Depends on: shadcn smoke test._
- [ ] **Command palette (⌘K)** — shadcn `command` primitive. Actions: new chat, switch chat (fuzzy on title), jump to settings, toggle theme, sign out, search reports. Keyboard-only flow; opens with ⌘K, closes with Esc. _Depends on: Sidebar tasks (data sources), User widget + menu._
- [ ] **Streaming reveal motion** — Subtle reveal animation for streaming text deltas (no caret blink theatrics). Tool-pending appears with a quiet spinner; tool-completed transitions to its summary card. All animations < 220ms, honoring `prefers-reduced-motion` (collapses to a single fade). _Depends on: SSE wiring._

## Responsive & Polish

- [ ] **Mobile / tablet layout** — Below 1024px: sidebar becomes a `sheet` from the left (hamburger in a thin topbar), inspector becomes a `sheet` from the right (icon in topbar). Below 768px: composer stays sticky at the bottom, content scrolls under. Touch-target minimums (44px). Long-press for row actions on touch, hover on pointer. _Breakpoints: 768, 1024._
- [ ] **Accessibility pass** — Audit against the brief's a11y section. Specific checks: contrast 4.5:1 body / 3:1 large in both modes; tab order matches visual order; modal focus traps; SSE deltas announced via debounced `aria-live="polite"`; charts ship with hidden tabular fallback; focus rings designed (not browser default); color not used alone for state; reduced-motion honored. _Depends on: all features._
- [ ] **Empty / loading / error states pass** — Every list (conversations, reports, identities, providers) and every async surface (auth screens, settings, transcript load, CSV viewer) gets explicit empty + loading skeleton + error treatments. Type-led, never illustrative. _Depends on: all features._

## Review

- [ ] **Design review** — Run `/design-review` against the brief. Capture screenshots at desktop / tablet / mobile breakpoints, light + dark, with focus visible on key interactive surfaces. Save under `.design/chat-workspace/screenshots/`. Address any must-fix items before promoting `frontend-next/` over `frontend/`.
