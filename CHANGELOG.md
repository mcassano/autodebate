# Changelog

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
