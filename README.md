```
        (  )   (  )
       )  (   )  (
      .-------------.
      |  autodebate |]
      '-------------'
   strong coffee ·
   stronger opinions
```

**Eavesdrop on three brilliant minds arguing in a coffee shop — and pull up a chair whenever you like.**

Three personas, each backed by a different frontier-class LLM, sit at a small table and talk about the future. They don't interrupt each other. They steelman, challenge, build on each other — and occasionally go quiet to think. An invisible moderator decides who speaks next. Each of them can search the live web, read pages, check papers, and do the math when a fact would sharpen an argument. You listen from the next table, and whenever something makes you lean in, you say it — and the table turns to you.

Built on a working hypothesis: **intellectual rigor + honest challenge between exceptional minds is what produces breakthroughs.**

## Why it's interesting

- **Three training lineages, one table.** The seats are backed by models from three different labs on purpose — same-priors panels converge; this table genuinely disagrees.
- **It's a debate, not a panel.** Personas are instructed to steelman-then-challenge, never flatter, and pass when they have nothing to add.
- **Live facts at the table.** Six read-only tools: web search, page reading, arXiv, stock quotes, a calculator, and the current date.
- **Cheap to leave running.** One frontier anchor + two top open-weight seats, a tiny moderator model, rolling context summaries, a token meter, and a turn cap.
- **Small enough to read.** The engine is one file (`src/autodebate/engine.py`); the UIs never call a model.

## The lineup

| Seat | Persona | Lens | Model |
|------|---------|------|-------|
| 1 | **Marcus** | hard-nosed empiricist — base rates, falsifiability, evidence | `anthropic/claude-opus-5` |
| 2 | **Priya** | systems builder — incentives, bottlenecks, what breaks at scale | `deepseek/deepseek-v4-pro-0813` |
| 3 | **Kaito** | century-scale philosopher — first principles, hidden assumptions | `z-ai/glm-5.3` |

The moderator (picks the next speaker, compresses old context) runs on `google/gemini-3.7-flash`.
Swap any seat by editing `PERSONAS` in `src/autodebate/config.py`.

## Quickstart

You need two keys (both have free tiers):
[OpenRouter](https://openrouter.ai/keys) and [Brave Search API](https://brave.com/search/api/).

```bash
git clone https://github.com/mcassano/autodebate.git
cd autodebate
cp .env.example .env        # then put your keys in .env

# with uv (recommended):
uv venv && uv pip install -e .
uv run autodebate

# or plain pip:
python -m venv .venv && source .venv/bin/activate
pip install -e .
autodebate
```

## Three ways to sit at the table

**Terminal TUI** — a streaming group chat you eavesdrop on:

```bash
autodebate                          # quiet until you speak first
autodebate "AGI timelines"          # opens with that message for you
autodebate --max-turns 40           # auto-pause after 40 AI turns (cost guard)
```

Type + `enter` to queue something to say (spoken at the next turn boundary) ·
`ctrl+p` pause/resume · `ctrl+q` quit.

**Headless CLI** — print the debate to stdout:

```bash
autodebate --cli "topic" --turns 6
```

**Web café** — the same table in a browser, with a coffee-shop skin. Share the
URL and anyone can eavesdrop or pull up a chair:

```bash
autodebate-web                      # serves on $PORT (default 8000)
```

The web table only talks while at least one browser is connected — when the café
empties, the conversation pauses itself, so a public deployment doesn't run up a
bill overnight. Set `AUTODEBATE_TOPIC` if you want the table to open with a
question already on it. The session's markdown transcript is served at `/transcript`.

## Deploy to Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template)

Or manually: New Project → Deploy from GitHub repo → set two environment variables
(`OPEN_ROUTER_API_KEY`, `BRAVE_SEARCH_API_KEY`; optionally `AUTODEBATE_TOPIC`) →
Deploy. `railway.toml` in this repo handles the rest. Note the filesystem is
ephemeral, so `/transcript` is the way to read a session back.

## Bring your own backend

Any OpenAI-compatible endpoint works as a seat. Model specs in `config.py` carry
an optional provider prefix:

```python
model = "anthropic/claude-opus-5"  # OpenRouter (default)
model = "openrouter:qwen/qwen3.8-max-0902"
model = "ollama:qwen3"  # local Ollama — no key needed
model = "lmstudio:my-local-model"  # local LM Studio — no key needed
model = "anthropic:claude-opus-5"  # Anthropic direct (ANTHROPIC_API_KEY)
model = "openai:gpt-6-astra"  # OpenAI direct (OPENAI_API_KEY)
```

`ollama:` seats + a `ollama:` moderator = a fully local, free coffee shop.
Only the keys for providers actually in use are required. Models that can't do
tool calling are detected at runtime and simply debate without tools.

## A taste

Opening message: *"What will matter most in 2050 that almost nobody is preparing for?"*
(trimmed for length — full sessions land in `transcripts/`)

> **Priya:** The overlooked constraint in 2050 won't be generating things — it'll be proving things. Once synthesis is free, every photo, log, signature, and sensor stream becomes an adversarial product. The entire trust stack — courts, insurance, title, food safety — still assumes a document is evidence. That assumption quietly fails…
>
> **Marcus:** …the implied golden age is what I won't grant. PCAOB inspections find deficiencies in something like 40% of the audits they pull, and that's credentialed CPAs with statutory authority. "Cryptographically perfect proof of a false event, issued at scale for a fee" isn't the 2050 failure mode; it's the 2008 business model, and it didn't need a single generative model to work…
>
> **Kaito:** …Every sensor is a *claim generator*, and hostile parties don't have to defeat your measurement — they just have to flood it. GPS spoofing near conflict zones is already disrupting over a thousand civil flights a day… The actual 2050 bottleneck is adversarially robust ground truth at scale — a measurement problem, a physics problem, and an institutional design problem all at once.

## How it works

1. **The loop**: drain anything you queued → the moderator picks the next speaker (and whispers a nudge, shown dimmed in the UI) → that persona streams its turn → repeat. The table falls silent on its own when the moderator sees natural closure or three personas pass in a row.
2. **Shared context**: everyone sees the same transcript. For each persona, its own past lines go in as `assistant` messages and everyone else's as `user` lines — the standard trick that makes multi-agent chat coherent.
3. **Cost guards**: the last 30 turns go verbatim; older context is compressed into a rolling brief by the cheap moderator model. The status bar shows live token usage.

## Project layout

```
src/autodebate/
  config.py     # personas, providers, constants — start here
  prompts.py    # every word spoken to the models
  engine.py     # the conversation loop (UI-agnostic, drives a Sink)
  tools.py      # the six tools; add your own in ~15 lines
  tui.py        # Textual front end
  cli.py        # headless stdout front end
  web.py        # FastAPI front end + WebSocket hub
  static/       # the café (no build step)
tests/          # smoke tests for all three front ends
```

## Roadmap ideas

- **The café, properly**: an overhead view of a whole coffee shop with many tables — different personas and topics at each — and you sit down at the one that sounds interesting. Bigger than v1, but it's the direction.
- Persona packs (`--personas stoics.json`) · free-for-all turn-taking mode · dollar cost meter · audience polls · letting the table invite a guest model mid-debate.

## Contributing

Issues and PRs welcome. Keep the engine UI-agnostic, keep it `ruff` clean
(`ruff check --fix . && ruff format .`), and keep personas challenging.

## License

[MIT](LICENSE)
