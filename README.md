<p align="center">
  <img src="docs/logo.svg" alt="autodebate — a steaming coffee cup" width="150">
</p>

<p align="center"><strong>strong coffee · stronger opinions</strong></p>

**Three of the world's best AI models sit at a coffee-shop table and argue — with live web access, real personas, and a chair waiting for you.**

- **The debates are genuinely good.** Not three chatbots agreeing — three models from three rival labs, each told to steelman-then-challenge, each able to search the live web, read papers, and pull stock quotes mid-argument. [Read a real exchange](#a-taste).
- **Recast the whole table with one flag.** `--personas arena` seats a prosecutor, defender, and judge on assigned sides. `--personas traders` seats a scalper, a macro trader, and a quant. `--personas locals` runs fully offline on Ollama — free, private, no API keys.
- **Watch it your way.** A streaming terminal TUI, a headless CLI, or a coffee-shop web UI you can deploy to Railway in two minutes and share with anyone.
- **Two dials, endless moods.** `--mode debate|casual|funny` for the register, `--speed fast|medium|slow` for the pace.

Built on a working hypothesis: **intellectual rigor + honest challenge between exceptional minds is what produces breakthroughs.**

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
autodebate --mode funny --speed fast --personas traders
```

`--mode` sets the register (`debate` default, `casual`, `funny`) and `--speed`
sets the beat between turns (`fast`, `medium` default, `slow`). Packs can set
their own defaults; your flags win. Web deploys read `AUTODEBATE_MODE` and
`AUTODEBATE_SPEED`.

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

## Persona packs

Recast the whole table with one flag. Five packs ship in the box
(`autodebate --packs` to list):

| Pack | Seats | The chemistry |
|------|-------|---------------|
| `stoics` | Aurelia · Nikos · Selene | Stoic, Cynic, and Epicurean argue about how to live now |
| `boardroom` | David · Vera · Juno | CFO, growth operator, and product visionary stress-test ideas like it's their money |
| `lab` | Elena · Ravi · Moss | Theorist, experimentalist, and ML-systems skeptic on how science actually moves |
| `arena` | Rhea · Theo · Sol | Prosecutor, defender, and judge — table a motion, watch it get tested on assigned sides |
| `traders` | Rico · Maggie · Wren | A scalper, a macro trader, and a quant — same tribe, different religions |
| `locals` | Ada · Bram · Cleo | Runs entirely on local Ollama models — free, private, offline |

```bash
autodebate --personas arena "Motion: this house believes remote work has failed."
AUTODEBATE_PERSONAS=stoics autodebate-web
```

Write your own as JSON (name, model spec, style, archetype per seat; color
optional; `moderator` and `brief` optional — `brief` is a one-line note to the
moderator about the table's format, which is how `arena` keeps its sides
straight), then `--personas path/to/pack.json`.

## A taste

```bash
autodebate --personas traders --mode funny "NVDA at all-time highs. Buying, selling, or hiding?"
```

> **Rico** *(tape-reading scalper)*: You're mistaking the granary for the grain. NVDA doesn't clear at a price, it clears like a ration line — hyperscalers pay upfront, sovereigns cut the queue… I'm not shorting a cartel, and I'm not paying 218 for a red grain ticket at the top of a vertical. If it prints 221 with offers thin, I buy the breakout and let the pharaoh pay me rent.
>
> **Maggie** *(macro trader)*: The pharaoh's been paying for grain with IOUs written against next year's harvest. A ration line where the customers are levered isn't a cartel, it's vendor financing with extra steps… Your scarcity premium doesn't die when a second granary opens — it dies when the guys in line discover their credit card was declined.
>
> **Wren** *(systematic quant)*: You didn't buy a shovel, you bought the most violently cyclical commodity in technology at the exact moment the trade became a magazine cover… Rico's granary and her credit card are the same position. Both are long one thing: that 2026 capex is a physical constant.

Each seat is a different model — DeepSeek, Claude, and Qwen — prompted to disagree, pulling live market data as they go. Full sessions land in `transcripts/`.

## How it works

1. **The loop**: drain anything you queued → the moderator picks the next speaker (and whispers a nudge, shown dimmed in the UI) → that persona streams its turn → repeat. The table falls silent on its own when the moderator sees natural closure or three personas pass in a row.
2. **Shared context**: everyone sees the same transcript. For each persona, its own past lines go in as `assistant` messages and everyone else's as `user` lines — the standard trick that makes multi-agent chat coherent.
3. **Cost guards**: the last 30 turns go verbatim; older context is compressed into a rolling brief by the cheap moderator model. The status bar shows live token usage.

## Project layout

```
src/autodebate/
  config.py     # personas, providers, pack loading — start here
  prompts.py    # every word spoken to the models
  engine.py     # the conversation loop (UI-agnostic, drives a Sink)
  tools.py      # the six tools; add your own in ~15 lines
  packs/        # persona packs (stoics, boardroom, lab, arena, locals)
  tui.py        # Textual front end
  cli.py        # headless stdout front end
  web.py        # FastAPI front end + WebSocket hub
  static/       # the café (no build step)
tests/          # smoke tests for all three front ends + pack validation
```

## Roadmap ideas

- **The café, properly**: an overhead view of a whole coffee shop with many tables — different personas and topics at each — and you sit down at the one that sounds interesting. Bigger than v1, but it's the direction.
- Free-for-all turn-taking mode · dollar cost meter · audience polls · letting the table invite a guest model mid-debate · more packs (the bar for a new one: a chemistry the default trio can't produce).

## Contributing

Issues and PRs welcome. Keep the engine UI-agnostic, keep it `ruff` clean
(`ruff check --fix . && ruff format .`), and keep personas challenging.

## License

[MIT](LICENSE)
