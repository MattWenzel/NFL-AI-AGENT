# Design Brief: Chat Workspace (NFL AI Stats Agent)

## Problem

The user has a question about NFL stats — *"how does Saquon's 2024 RB1 season compare to Barry Sanders' best year?"*, *"who are the top 10 fantasy WRs by yards-after-catch in 2025?"* — and they don't want to write SQL to find out. They want to ask in plain language, get a real answer with reasoning and the underlying numbers shown, occasionally pull the result into a CSV, and trust that the tool isn't bullshitting them.

Today's vanilla UI works, but feels like a developer tool — dense slate panels, GitHub-dark in dark mode, IBM Plex everywhere, runtime inspector visible by default. That's correct for the maintainer; it's wrong for the audience. The user doesn't want to feel like they're using an internal admin console; they want to feel like they're using a polished consumer-grade analytical assistant that happens to be powered by an AI agent + a real database.

## Solution

A chat-first analytical workspace where the conversation is the surface. The agent's reasoning, tool calls, and resulting data appear inline in the thread with the same typographic care a magazine gives to a feature article — but stripped of the *editorial* register, since the app is fundamentally a tool, not a publication. Supporting machinery (provider/model selector, runtime inspector, CSV library, settings, account) is reachable but never demanding. Everything is calmer, lighter, and more deliberate than the vanilla UI it replaces.

The end-state feeling is closer to *Things 3 / Granola / Apple Notes / Linear* than *ChatGPT / shadcn-default / Vercel-dashboard*.

## Experience Principles

1. **Calm over chrome** — Surface the answer; hide the machinery. Borders, dividers, cards, badges, and pills earn their place or get cut. The default state is whitespace.
2. **Type does the work** — Hierarchy comes from typography (size, weight, leading, color), not boxes. A well-set thread reads like a document, not a chat feed.
3. **Data is content, not decoration** — Tables, charts, citations, and tool-call evidence get first-class typographic and spatial treatment. They are not throwaway DOM tables collapsed into a sidebar.

## Aesthetic Direction

- **Philosophy**: Apple-polished productivity. Restrained, considered, calm. The aesthetic register of a paid macOS productivity app — *Things 3*, *Granola*, *Apple Notes (modern macOS)*, *Reflect*, *Highlight*, *Linear* — applied to an analytical chat workspace.
- **Tone**: confident, quiet, polished. Never cute. Never urgent. Never gray-on-gray dev-tool cold. Comfortable enough to spend an hour in without eye fatigue.
- **Reference points**:
  - *Things 3* — Apple-polished productivity exemplar; type pairing, accent discipline, hairline borders, generous radii.
  - *Granola* — chat-adjacent AI tool that managed real personality without gimmicks.
  - *Apple Notes (Sonoma/Tahoe)* — confident neutrals, restrained accent, type-led structure.
  - *Linear* — accent discipline, dark mode that is genuinely dark not just inverted.
  - *Apple App Store / Apple.com newsroom* — for the occasional editorial-display moment (large numerals, hero lines).
  - *Stripe docs* — typographic confidence + restrained palette without dev-tool coldness.
- **Anti-references**:
  - *ChatGPT* — generic chat-feed bubbles, default gray.
  - *shadcn default install* — Geist + neutral oklch + Lucide. The skill explicitly bans this lane.
  - *Notion* — utilitarian, generic, type-undifferentiated.
  - *Vercel dashboard* — too dev-tool, too cold.
  - Inter / Roboto / Arial / Geist / Space Grotesk (banned by `frontend-design` skill).
  - Indigo / purple gradients — the AI-app default.

## Existing Patterns

The vanilla `frontend/` is treated as **prior art**, not as the starting vocabulary. This is a full redesign on an experimental branch (`ui-overhaul`). The IA is preserved (chat / sidebar / inspector / composer / settings); the visual language is new.

- **Typography (current → new)**: IBM Plex Sans + Mono → an Apple-leaning pairing. Working candidates: text/UI = a humanist grotesk *not on the banned list* (Söhne if licensed, otherwise *General Sans*, *Switzer*, or *Manrope* — pick during the tokens phase); display = *Fraunces* (variable serif) reserved for hero numerals + section labels; mono = *JetBrains Mono* or *IBM Plex Mono* for code/SQL.
- **Color (current → new)**:
  - Light: slate (#E6EAF0 bg) → warm neutral (off-white, not pure white; warm gray-with-paper-warmth).
  - Dark: GitHub-style (#0D1117) → considered dark — not pure black, not generic neutral. Apple-style #1c1c1e family.
  - Accent: indigo (#4F46E5) → **single quiet accent**, not blue, not purple. Working candidates: oxblood, dusty terracotta, forest, desaturated sage. Pick exactly one in the tokens phase.
- **Spacing**: 4px base — keep, possibly upscale to 4/8 hybrid (Apple's pattern).
- **Radii**: current 6–16px → larger and more consistent. Apple-style: 8 (small UI), 12 (cards), 16 (large surfaces), 22+ (sheets/modals).
- **Components**: existing components are *not* reused. The shadcn registry (Radix-Nova preset, already installed) supplies primitives — Button, Input, Select, Dialog, Sheet, Sidebar, AlertDialog, Toast (Sonner), Tabs, Separator, ScrollArea, Tooltip, Command (palette), DropdownMenu, Popover, Avatar, Card, Skeleton, DataTable. We compose into our own components themed against our own tokens.
- **Backend**: untouched. All API endpoints (`/auth`, `/chat`, `/exports`, `/settings`) proxied to FastAPI by Vite during dev.

## Component Inventory

| Component | Status | Notes |
| --------- | ------ | ----- |
| App shell (sidebar + thread + inspector) | New | Three-pane responsive layout. shadcn `Sidebar` + custom panes. |
| Auth screens (sign in / sign up / verify / reset / OAuth) | New | Single-column, generous, calm. Existing API contract. |
| Sidebar — chats list | New | Conversation rows with title + provider chip + relative time. Pinned group above. |
| Sidebar — reports tab | New | CSV library entries with row count + size + created. |
| Sidebar — search | New | Inline filter on chats/reports. |
| Sidebar — user widget | New | Avatar + name, opens menu (Settings / Sign out). |
| Thread | New | Document-feel: turn-as-section. Tool calls inline. Streaming with subtle reveal. |
| Tool-call surface | New | Restrained call-out block in thread; expand for full input/output. Not a code-blob dump. |
| Tables (inline data results) | New | Typographic, tabular nums, banded only when ≥ N rows. |
| Charts | Modify | Currently Chart.js. Decide during build: keep + restyle, or replace with `recharts` for shadcn integration. |
| Composer (textarea + provider/model/tool selects + send) | New | Feels weightless. Inline-grow textarea, persistent meta row. |
| Inspector (right rail) | New | Turn details, tool runs, compaction summaries. Dignified, not hidden. |
| Settings modal (Providers, Account, Identities) | New | shadcn `Dialog` + `Tabs`. Key fields use masked display + reveal. |
| Confirm / destructive prompts | New | shadcn `AlertDialog`. |
| Toast / inline error banners | New | shadcn `Sonner`. |
| CSV viewer | New | Title + row meta header, virtualized data table, download + delete + new-session-from-csv actions. |
| Empty states | New | Type-led, never illustrative-cute. |
| Skeletons / loading | New | shadcn `Skeleton` themed to tokens. |
| Theme toggle (light / dark / system) | New | Three-state, not just a sun/moon flip. Defaults to system. |

## Key Interactions

- **Composing**: textarea grows inline (no jump). Provider/model/tool selects sit on a persistent meta row that does not relocate when typing. Send button anchored. Enter = send, Shift+Enter = newline. After send, the textarea collapses smoothly back to one line.
- **Streaming**: assistant text streams in with subtle reveal (no caret-blink theatrics). Tool-pending appears as a restrained inline block with a quiet spinner; tool-completed shows a one-line summary that expands on click. Charts and tables animate in once data is final, not progressively.
- **Conversation switching**: sidebar row click → thread cross-fades to the new conversation. No flash, no full-page spinner.
- **Pinning**: pin/unpin via row hover action; pinned conversations move to a "Pinned" group above with a hairline divider.
- **Inspector**: collapsible from the right edge with a thin handle. Clicking a turn in the thread highlights its row in the inspector and scrolls it into view (not the other way around — thread leads).
- **Settings**: modal opens centered, slides up subtly. Tab switch is keyboard-driven (←/→ on tablist). API-key entry: `••••••••` mask, "Reveal" button on hover, save on blur.
- **Destructive (sign-out, delete account, delete conversation, delete CSV)**: AlertDialog with calm copy ("Sign out of this account?"), primary action visually quiet, destructive accent only on the confirm button.
- **Theme**: light / dark / system. The selector is in the user menu, not in the sidebar header.
- **Keyboard**: ⌘K opens a Command palette (shadcn `Command`) for: new chat, switch chat, jump to settings, toggle theme, sign out, search reports.

## Responsive Behavior

- **Desktop (≥1024px)**: three-column shell — sidebar (resizable, default 280px) + thread (fluid) + inspector (resizable, default 360px, collapsible).
- **Tablet (768–1023px)**: two-column — sidebar + thread. Inspector becomes a `Sheet` from the right.
- **Mobile (<768px)**: single-column — thread is the page. Sidebar is a `Sheet` from the left (hamburger). Inspector is a `Sheet` from the right (icon in topbar). Composer stays sticky at the bottom; content scrolls under it (does not jump on keyboard open — let the browser handle viewport).
- **Touch**: tap targets ≥44px. Pinning, deleting, and other row actions appear on long-press on touch, hover on pointer.

## Accessibility Requirements

- **Contrast**: WCAG AA in both modes — 4.5:1 body text, 3:1 large text. Tested at typical and high-contrast OS settings.
- **Keyboard**: every action reachable. Composer is the focus default on chat view. Tab order follows visual order. Modals trap focus and restore on close.
- **Screen reader**: streaming assistant text announced via a polite `aria-live` region, debounced so each chunk doesn't fire. Tool-call entry/exit announced. Charts ship with a hidden tabular fallback (`<table>` with the same data).
- **Focus rings**: visible, designed — not browser default. Match the accent token at reduced opacity (e.g. accent at 35%, 2px ring, 2px offset).
- **Reduced motion**: honor `prefers-reduced-motion`. Streaming reveals collapse to a single transition; chart entrance animations disable; modal slide becomes a fade.
- **Color independence**: never communicate state with color alone (errors get an icon + text label, not just a red border).

## Out of Scope

- **Backend changes** — the API surface is fixed. The refactor on `main` (4 commits) is the contract.
- **Migrating production to React** — this is experimental on `ui-overhaul`. The vanilla `frontend/` continues to ship until we promote.
- **Mobile-native app** (iOS / Android wrappers).
- **Real-time multiplayer / shared conversations.**
- **Authentication flow restructure** — keep the existing sign-in / sign-up / OAuth screens' *behavior*; redesign the *surfaces*.
- **Replacing Chart.js outright** — decision deferred to build phase. If `recharts` (shadcn-native) gives us better theming consistency without losing the existing chart features, we switch; otherwise restyle.
- **Onboarding flow / tour** — first-run experience is "show the empty thread and the composer." No coachmarks, no tour modal.
- **Theming customization** — users do not pick palettes. There is one accent. The theme toggle is light/dark/system only.
