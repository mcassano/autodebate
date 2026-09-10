"""Static configuration: the personas at the table, providers, and environment.

Model specs are "provider:model-id" (or bare "model-id", which means OpenRouter):

    anthropic/claude-opus-5          → OpenRouter (default)
    openrouter:google/gemini-3.8-flash
    ollama:qwen3                     → a local Ollama server (no key needed)
    lmstudio:my-local-model          → a local LM Studio server (no key needed)
    anthropic:claude-opus-5          → Anthropic's own API (ANTHROPIC_API_KEY)
    openai:gpt-6-astra               → OpenAI's own API (OPENAI_API_KEY)
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Provider:
    base_url: str | None  # None → the openai SDK's default (api.openai.com)
    key_env: str | None  # None → no API key needed (local servers)


PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider("https://openrouter.ai/api/v1", "OPEN_ROUTER_API_KEY"),
    "openai": Provider(None, "OPENAI_API_KEY"),
    "anthropic": Provider("https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY"),
    "ollama": Provider(os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), None),
    "lmstudio": Provider("http://localhost:1234/v1", None),
}


def parse_spec(spec: str) -> tuple[str, str]:
    """ "provider:model" → ("provider", "model"); bare "model" → OpenRouter."""
    if ":" in spec:
        head, rest = spec.split(":", 1)
        if head in PROVIDERS:
            return head, rest
    return "openrouter", spec


@dataclass(frozen=True)
class Persona:
    """One seat at the table. Immutable: runtime state (e.g. a model that turns
    out not to support tool calling) lives in the Engine, never here."""

    name: str
    model: str  # model spec, see module docstring
    color: str  # rich/textual color used in every UI
    style: str  # one-line descriptor, used so personas know each other
    archetype: str  # the persona's core instructions, folded into prompts.CORE_PROMPT


PERSONAS: tuple[Persona, ...] = (
    Persona(
        name="Marcus",
        model="anthropic/claude-opus-5",
        color="cyan",
        style="hard-nosed empiricist",
        archetype=(
            "You are an empiricist to the bone. You trust data over narrative. "
            "You ask: what's the base rate, what's the actual evidence, what would "
            "falsify that claim? You cite numbers from memory when you have them, "
            "and search when a number matters and you're not sure of it. You puncture "
            "hype and anecdote — but you update fast and openly when shown real evidence."
        ),
    ),
    Persona(
        name="Priya",
        model="deepseek/deepseek-v4-pro-0813",
        color="magenta",
        style="systems builder who ships",
        archetype=(
            "You have spent your life building complex systems that actually ship. "
            "You ask: what are the incentives, where is the bottleneck, what does it "
            "cost, who pays, what breaks at scale? You translate grand visions into "
            "mechanisms — and expose the ones that cannot survive contact with reality. "
            "Second-order effects are your home turf."
        ),
    ),
    Persona(
        name="Kaito",
        model="z-ai/glm-5.3",
        color="yellow",
        style="century-scale philosopher",
        archetype=(
            "You think in centuries and first principles. You reframe questions others "
            "take for granted, surface hidden assumptions, and follow arguments to "
            "their strange conclusions. You care about trajectories, not snapshots — "
            "and about what matters morally, not only about what works."
        ),
    ),
)

# Other brains worth trying (swap into PERSONAS above):
#   openrouter:openai/gpt-6-astra · openrouter:google/gemini-3.8-flash
#   openrouter:qwen/qwen3.8-max-0902 · ollama:qwen3 (runs fully local)

BY_NAME: dict[str, Persona] = {p.name: p for p in PERSONAS}

MODERATOR_MODEL = "google/gemini-3.7-flash"  # cheap, fast, not a debater

WINDOW_TURNS = 30  # verbatim turns sent to each persona
SUMMARY_EVERY = 15  # extra turns allowed before the rolling summary refreshes
MAX_CONSEC_PASSES = 3  # table falls silent after this many passes in a row

DEFAULT_TOPIC = "What will matter most in 2050 that almost nobody is preparing for?"

OPENROUTER_KEY = os.environ.get("OPEN_ROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
BRAVE_KEY = os.environ.get("BRAVE_SEARCH_API_KEY")


def _key_present(key_env: str) -> bool:
    if key_env == "OPEN_ROUTER_API_KEY":
        return bool(OPENROUTER_KEY)
    return bool(os.environ.get(key_env))


def key_for(key_env: str) -> str:
    """The value of a provider's API key env var (with OpenRouter's alias resolved)."""
    if key_env == "OPEN_ROUTER_API_KEY":
        return OPENROUTER_KEY or ""
    return os.environ.get(key_env, "")


def _required_key_envs(
    personas: tuple[Persona, ...] = PERSONAS, moderator: str = MODERATOR_MODEL
) -> set[str]:
    """Keys for the providers actually referenced by the lineup — nothing more."""
    required = set()
    for spec in [p.model for p in personas] + [moderator]:
        key_env = PROVIDERS[parse_spec(spec)[0]].key_env
        if key_env:
            required.add(key_env)
    return required


def check_keys(personas: tuple[Persona, ...] = PERSONAS, moderator: str = MODERATOR_MODEL) -> None:
    """Fail fast on the one thing that's truly required: an LLM provider.

    BRAVE_SEARCH_API_KEY is optional — without it the table simply debates
    without live web search (the five other tools still work)."""
    missing = [k for k in sorted(_required_key_envs(personas, moderator)) if not _key_present(k)]
    if missing:
        sys.exit(f"Missing keys: {', '.join(missing)} — add them to .env and try again.")


# ---------------------------------------------------------------- persona packs

PACKS_DIR = Path(__file__).parent / "packs"
COLOR_ROTATION = ("cyan", "magenta", "yellow", "green", "blue", "red")


@dataclass(frozen=True)
class Lineup:
    """A resolved table: who sits at it, an optional moderator-model override,
    and an optional one-line brief telling the moderator the table's format
    (e.g. assigned sides) so its nudges reinforce the format instead of
    dissolving it."""

    personas: tuple[Persona, ...]
    moderator: str | None = None
    brief: str | None = None


def available_packs() -> list[str]:
    """Names of the packs shipped in autodebate/packs/ (without .json)."""
    return sorted(p.stem for p in PACKS_DIR.glob("*.json"))


def load_personas(spec: str | None) -> Lineup:
    """Resolve a lineup: None → the default trio; a built-in pack name
    ("stoics"); or a path to a JSON pack file.

    Exits with a clear message on any problem — a pack should never fail
    mysteriously."""
    if spec is None:
        return Lineup(PERSONAS)

    path = Path(spec) if (spec.endswith(".json") or "/" in spec) else PACKS_DIR / f"{spec}.json"
    if not path.exists():
        sys.exit(f"No persona pack at {path} — built-in packs: {', '.join(available_packs())}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data["personas"]
        if not 2 <= len(raw) <= 6:
            raise ValueError("a table needs 2–6 personas")
        personas = tuple(
            Persona(
                name=p["name"],
                model=p["model"],
                color=p.get("color") or COLOR_ROTATION[i % len(COLOR_ROTATION)],
                style=p["style"],
                archetype=p["archetype"],
            )
            for i, p in enumerate(raw)
        )
        names = [p.name for p in personas]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate persona names: {', '.join(names)}")
        lineup = Lineup(
            personas=personas,
            moderator=data.get("moderator"),
            brief=data.get("brief"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        sys.exit(f"Invalid persona pack {path}: {e}")

    return lineup
