# Changelog

## 0.5.0 — 2026-09-10

Three new keyless tools, two more that unlock with a free key.

- **New, no key needed**: `wikipedia_search` (background and definitions),
  `world_bank_indicator` (GDP, inflation, unemployment, population by
  country), and `hn_search` (what the tech crowd is discussing).
- **New, optional key**: `fred_series` (US Federal Reserve economic data —
  `FRED_API_KEY`) and `tmdb_search` (film lookups — `TMDB_API_KEY`), joining
  `brave_search` as tools that quietly opt in when their key is set.
- Quickstart now needs only `OPEN_ROUTER_API_KEY` — the other eight tools
  either need no key or are clearly optional in `.env.example`.

## 0.4.0 — 2026-09-10

The cast now follows the conversation.

- **The troupe is the default evening**: eight personas in the café, three at
  the table. The moderator can `SWITCH` a seat when the topic genuinely shifts
  (at most once every eight turns — the table has weight, not whiplash), and a
  newcomer gets a sitrep of what they missed. `autodebate` with no flags is a
  rotating cast; `--personas classic` is the original fixed trio.
- **You have the same power**: `/seat Noa [for Marcus]` slides anyone from the
  café into the table; `/cast traders` changes the whole lineup mid-session.
- **Providers are probed at startup** — keys for hosted providers, a one-second
  ping for Ollama/LM Studio. Unreachable personas are "out tonight" and never
  seated, so an OpenRouter user never gets a local persona (and vice versa).
  Choosing a pack explicitly with an unreachable provider fails fast and loud
  instead of flailing at runtime.
- New troupe seats: Noa (film & culture critic), Sana (geopolitics), Gus
  (house comic) — plus Maggie and Wren joining from the traders table.
- Moderator reply parsing is now a pure, unit-tested function (9 cases in CI).

## 0.3.1 — 2026-09-10

Tooling only, no app changes.

- `./ci.sh` runs every CI check locally (ruff, keyless imports, pack
  validation; `--with-api` adds the live smoke tests). GitHub Actions now
  calls the same script, so local and CI can't drift.
- Commits are signed (SSH) and show Verified on GitHub.
- README: SVG cup logo replaces the ASCII block; new "A taste" excerpt from
  the funny traders table.

## 0.3.0 — 2026-09-10

Registers, pacing, and a table of traders.

- **Two dials**: `--mode debate|casual|funny` changes the table's register
  (house rules + moderator nudges, personas untouched); `--speed
  fast|medium|slow` sets the beat between turns. Packs can ship their own
  defaults; flags and `AUTODEBATE_MODE`/`AUTODEBATE_SPEED` override.
- **New pack: `traders`** — a tape-reading scalper, a macro catalyst trader,
  and a systematic quant. Like-minded by tribe, not by opinion.
- **Fix: chronological UI.** Tool calls now render above the in-progress
  speech they feed, instead of appearing after the finished answer (TUI + web).
- **Fix: `stock_quote` works** — moved to Yahoo Finance's public chart
  endpoint after stooq's CSV endpoint disappeared.
- Fix: silence the cosmetic httpcore2 async-generator teardown traceback on
  exit (narrow filter, everything else still reports).

## 0.2.0 — 2026-09-10

Persona packs, plus fixes from a day of real use.

- **Persona packs**: recast the table with `--personas NAME` (or a path to a
  JSON pack, or `AUTODEBATE_PERSONAS` for web deploys). Five built-ins:
  `stoics`, `boardroom`, `lab`, `arena`, `locals` (fully local Ollama).
  Packs can override the moderator model and set a one-line `brief` that
  tells the moderator the table's format — `arena` uses it to keep the
  prosecutor/defender/judge on their assigned sides.
- **Fix: Brave Search actually works now.** The API path was wrong
  (`/app/v1/` → `/res/v1/`); searches were silently failing and the personas
  were gracefully covering for it. Tool failures are now also shown to the
  human, not just the model.
- Fix: streaming responses are explicitly closed (no more async-generator
  warnings on exit).
- Pack validation: `tests/test_packs.py` checks every built-in pack with no
  API calls.

## 0.1.0 — 2026-09-10

First public release.

- Three personas at one table (empiricist / systems builder / philosopher),
  each backed by a different model lineage; an invisible moderator picks who
  speaks next and whispers a nudge.
- Three front ends off one UI-agnostic engine: streaming terminal TUI
  (Textual), headless `--cli` stdout mode, and the web café (FastAPI +
  WebSocket, coffee-shop skin, one shared table per deployment).
- Six read-only tools: brave_search (optional, key-gated), web_fetch,
  calculator, arxiv_search, stock_quote, current_datetime.
- Provider prefixes on model specs: OpenRouter (default), OpenAI and
  Anthropic direct, Ollama / LM Studio for fully local tables.
- Cost guards: 30-turn verbatim window + rolling summaries, live token meter,
  `--max-turns` cap, and the web table pauses when no browser is listening.
- Markdown transcript of every session appended as it happens
  (`transcripts/`), served at `/transcript` in web mode.
- Railway deploy via `railway.toml`.
