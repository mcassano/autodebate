# Changelog

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
