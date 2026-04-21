# Prompts & Guides

The agent has a deliberately slim system prompt and a set of on-demand markdown guides loaded through a tool. This doc covers why that split exists, how the base prompt is assembled, and how guides are stored, loaded, and served.

## File map

- `agent/system_prompt.py` — base system prompt template + `get_base_prompt()`.
- `agent/guides/*.md` — seven topic-specific reference docs.
- `tools/get_guide.py` — guide loader tool.

## The split: prompt vs. guide

Two knobs you could tune when designing an LLM agent's static context:

1. **Put everything in the system prompt.** Simple, always available, but the prompt is re-tokenized every turn and grows without bound. Every guide you add costs tokens for every request, including requests that don't need it.
2. **Put reference material in tools the model calls on demand.** Cheaper per-turn (guide only pulled when relevant), cacheable (guide contents are deterministic and mounted once at import), and lets you grow the library without prompt bloat.

This repo does option 2. The base prompt is ~2–3K tokens; guides are 1–3K tokens each and loaded via `get_guide` only when a question touches their topic.

Why: a typical "how many TDs did Mahomes throw in 2024" question doesn't need the `play_by_play` guide (372 PBP columns) or the `postseason` game-type cheatsheet. Loading them anyway would cost ~10K tokens per question for no benefit. Guide-as-tool pushes that cost onto questions that actually benefit from it.

## The base prompt

`agent/system_prompt.py:5`. A single f-string template with one variable: `{today}`. `get_base_prompt()` (`system.py:122`) evaluates it at call time, so "today's date" in the prompt matches the server's clock on the day of the request.

Structure (in order):

| Section | Purpose |
|---------|---------|
| Intro + date | One-line role, today's date, "current NFL season is 2025" anchor. |
| **Data Integrity** | Hard rule: compute numbers in SQL, never derive in head. The highest-stakes section — LLM arithmetic hallucinations are indistinguishable from correct answers to users. |
| **Conversation Memory** | How to treat `<prior_conversation_summary>` when the runtime injects one. See [compaction.md](compaction.md). |
| **Database at a glance** | 13-row table: every table, row count, year range, ID field, notes. Lets the model answer "which table has NGS" without a schema call. |
| **Guide Index** | Maps question topic → `get_guide({"topic": ...})`. Seven topics. Explicit parallelization instruction. |
| **Tool Usage** | Prescribed order: `search_players` → `get_guide` → `get_schema` → `execute_sql` → `create_csv_export`/`create_chart`. "Don't announce intent" rule at the top. |
| **Critical Gotchas** | 8 non-negotiable rules that bite models that skim the guides (e.g., `game_stats.player_id` holds GSIS IDs; `game_type` vs `season_type`; no such thing as a plain `sacks` column). |
| Pre-SQL checklist | Always alias tables, filter season on snap_counts, don't build mega-CTEs, etc. |
| Presentation rules | Comma-separated thousands, percentages, markdown tables. |
| **CSV Export Workflow** | Preview → confirm → export pattern for `create_csv_export`. |

The prompt is intentionally prescriptive. This is not a general-purpose system prompt; it's a domain-tuned playbook with known-failure-mode avoidance baked in. Each gotcha traces to a specific mistake that cost a tool iteration during development.

## Why these sections, not others

- **No examples of good/bad queries.** Examples bloat the prompt and tend to be followed too literally. Guides hold query templates.
- **No tool input schemas.** The provider SDKs handle this from `TOOLS` — repeating schemas in prose would be redundant and drift-prone.
- **No deployment or admin info.** That belongs to `CLAUDE.md` and the transport layer, not the LLM.
- **"Don't announce intent" sits high in Tool Usage.** Models otherwise narrate "I'll first look at the schema, then..." which burns output tokens and can get the turn truncated before the tool call lands.

## Guide system

Seven markdown files in `agent/guides/` (`get_guide.py:9`):

| Topic | When to load |
|-------|--------------|
| `fantasy` | Fantasy scoring, leaderboards, kicker scoring formula. |
| `player_stats` | Weekly/season stats, snap counts, NGS, PFR advanced, QBR column catalogs. |
| `play_by_play` | Any query touching the 372-column `play_by_play` table. |
| `drives` | Drive-level aggregations (TOP, scoring drives, three-and-outs). |
| `postseason` | Playoff queries — the `game_type`/`season_type` cheatsheet lives here. |
| `player_profile` | Bio, IDs, draft, combine, depth charts. |
| `games` | Schedules, results, weather, betting lines. |

Guides are plain markdown: column reference tables, gotchas, and copy-pasteable SQL templates. They're edited in place — no schema, no frontmatter, no template language. A `.md` file is a guide.

### Loading

`get_guide.py:20`. All seven files are read into a module-level dict **at import time**:

```python
_GUIDES = _load_all()   # dict[topic] -> file contents
```

This means:

- **First guide call per process is hot.** No disk I/O on the request path.
- **Guide edits require a server restart.** The running process caches content. (Noted in `CLAUDE.md`.)
- **A missing guide file doesn't crash startup.** `_load_all` returns `""` for missing files; the tool returns a clear error at call time if the topic is empty on disk.

### Serving

`get_guide.py:31`. Topic validation against the static `_TOPICS` list, then return `{"topic": ..., "content": ...}` as a JSON string. Same pattern as every other tool handler — JSON in, JSON out.

The **topic enum** lives in two places: `_TOPICS` in `get_guide.py` and the `enum` field of the `get_guide` input schema in `definitions.py`. These must match; there is no runtime guard, so adding a topic means editing both. (This is a narrower drift surface than the dispatch/definitions split, which does have a guard — see [tools.md](tools.md#registry-drift-guard).)

## How guides reach the model

Guides are **not** spliced into the system prompt. They arrive as tool results:

```
user: "biggest WPA play in a Super Bowl?"
assistant tool_use: get_guide(topic="play_by_play")
assistant tool_use: get_guide(topic="postseason")   # parallel
tool_result: {"topic": "play_by_play", "content": "<markdown>"}
tool_result: {"topic": "postseason",   "content": "<markdown>"}
assistant: writes SQL using both guides as context
```

On the next iteration, both guide contents are in the message history as `tool_result` blocks. The model treats them as reference material for this conversation — they persist until compaction drops them.

### Compaction interaction

Old `get_guide` tool results are a prime candidate for compaction. The summarizer preserves "which guides have been loaded" in the summary so the model doesn't re-fetch; the raw guide text is dropped. See [compaction.md](compaction.md) for retention rules.

## Parallelization guidance

The Guide Index explicitly tells the model to emit multiple `get_guide` calls in the same tool-use block when a question spans topics. The runtime honors this naturally — tools dispatch under `asyncio.gather` (see [runtime.md](runtime.md#the-iteration-loop)), so parallel guide loads are actually parallel, not sequential.

Without this instruction, models tend to fetch one guide, wait, fetch another, wait — doubling latency for zero gain. Two lines in the prompt fix it.

## Adding or changing content

- **Edit a guide**: change the `.md` file, restart the server. No code changes.
- **Add a new guide**: (1) drop `newtopic.md` into `agent/guides/`, (2) add `"newtopic"` to `_TOPICS` in `get_guide.py`, (3) add `"newtopic"` to the `enum` in `definitions.py`, (4) add a row to the Guide Index in `system.py`. Restart.
- **Tune the base prompt**: edit `_SYSTEM_PROMPT_TEMPLATE`. No restart needed for the CLI (it re-imports per invocation), but the running API server caches imports — restart.
