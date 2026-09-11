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
from dataclasses import dataclass, replace
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

# The two dials. Mode = the table's register (prompt text lives in prompts.MODES).
# Speed = the dramatic beat between a finished turn and the next speaker.
MODE_NAMES = ("debate", "casual", "funny")
SPEED_DELAYS = {"fast": 0.0, "medium": 2.5, "slow": 8.0}
DEFAULT_MODE = "debate"
DEFAULT_SPEED = "medium"


def check_dials(mode: str, speed: str) -> None:
    """Validate mode/speed coming from env vars (argparse covers CLI flags)."""
    if mode not in MODE_NAMES:
        sys.exit(f"Unknown mode {mode!r} — pick from {', '.join(MODE_NAMES)}")
    if speed not in SPEED_DELAYS:
        sys.exit(f"Unknown speed {speed!r} — pick from {', '.join(SPEED_DELAYS)}")


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
MIN_SWITCH_GAP = 8  # turns the moderator must wait between seat switches


@dataclass(frozen=True)
class Lineup:
    """A resolved table. `personas` are the current seats; `troupe`, when
    non-empty, is everyone in the café tonight (seats included) — the moderator
    may rotate people through. `out_tonight` names troupe members whose
    provider wasn't reachable at load time."""

    personas: tuple[Persona, ...]
    moderator: str | None = None
    brief: str | None = None
    mode: str | None = None
    speed: str | None = None
    troupe: tuple[Persona, ...] = ()
    out_tonight: tuple[str, ...] = ()


def available_packs() -> list[str]:
    """Names of the packs shipped in autodebate/packs/ (without .json)."""
    return sorted(p.stem for p in PACKS_DIR.glob("*.json"))


def _probe(provider_key: str) -> bool:
    """Can this provider actually be used from here? Key'd providers answer by
    key presence; local servers get a one-second ping. Probed once per run."""
    p = PROVIDERS[provider_key]
    if p.key_env:
        return _key_present(p.key_env)
    try:
        import httpx

        base = (p.base_url or "").rstrip("/")
        url = (
            base.removesuffix("/v1") + "/api/tags" if provider_key == "ollama" else base + "/models"
        )
        with httpx.Client(timeout=1.0) as client:
            return client.get(url).status_code == 200
    except Exception:
        return False


def _split_by_reachability(personas: tuple[Persona, ...]) -> tuple[list[Persona], list[str]]:
    available: list[Persona] = []
    out: list[str] = []
    probes: dict[str, bool] = {}
    for persona in personas:
        provider = parse_spec(persona.model)[0]
        if provider not in probes:
            probes[provider] = _probe(provider)
        (available if probes[provider] else out).append(
            persona if probes[provider] else persona.name
        )
    return available, out


def _validate_dials(mode: str | None, speed: str | None, path: Path) -> None:
    modes = ", ".join(MODE_NAMES)
    speeds = ", ".join(SPEED_DELAYS)
    if mode is not None and mode not in MODE_NAMES:
        sys.exit(f"Invalid persona pack {path}: unknown mode {mode!r} — pick from {modes}")
    if speed is not None and speed not in SPEED_DELAYS:
        sys.exit(f"Invalid persona pack {path}: unknown speed {speed!r} — pick from {speeds}")


def _build_persona(raw: dict, i: int) -> Persona:
    return Persona(
        name=raw["name"],
        model=raw["model"],
        color=raw.get("color") or COLOR_ROTATION[i % len(COLOR_ROTATION)],
        style=raw["style"],
        archetype=raw["archetype"],
    )


def _check_unique(people: tuple[Persona, ...], field: str) -> None:
    values = [getattr(p, field) for p in people]
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate persona {field}s: {', '.join(values)}")


def load_personas(spec: str | None, apply_availability: bool = True) -> Lineup:
    """Resolve a lineup: None → the default troupe; a built-in pack name
    ("stoics"); or a path to a JSON pack file.

    A pack with "personas" is a fixed table. A pack with "troupe" (+ "seats")
    is a rotating cast: everyone reachable is in the café, the first `seats`
    take the table. With apply_availability, troupe members whose provider is
    unreachable are held out (out_tonight); fixed packs only warn. Exits with
    a clear message on any problem — a pack should never fail mysteriously."""
    path = (
        PACKS_DIR / "troupe.json"
        if spec is None
        else (Path(spec) if (spec.endswith(".json") or "/" in spec) else PACKS_DIR / f"{spec}.json")
    )
    if not path.exists():
        sys.exit(f"No persona pack at {path} — built-in packs: {', '.join(available_packs())}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        common = dict(
            moderator=data.get("moderator"),
            brief=data.get("brief"),
            mode=data.get("mode"),
            speed=data.get("speed"),
        )
        _validate_dials(common["mode"], common["speed"], path)

        if "troupe" in data:
            troupe = tuple(_build_persona(p, i) for i, p in enumerate(data["troupe"]))
            if not 3 <= len(troupe) <= 12:
                raise ValueError("a troupe needs 3–12 personas")
            _check_unique(troupe, "name")
            seats = int(data.get("seats", 3))
            if not 2 <= seats <= len(troupe):
                raise ValueError(f"seats must be 2–{len(troupe)}")
            out: list[str] = []
            if apply_availability:
                available, out = _split_by_reachability(troupe)
            else:
                available = list(troupe)
            if len(available) < 2:
                raise ValueError(
                    "fewer than 2 of the troupe are reachable tonight "
                    f"(out: {', '.join(out) or 'none'}) — check providers/keys"
                )
            return Lineup(
                personas=tuple(available[:seats]),
                troupe=tuple(available),
                out_tonight=tuple(out),
                **common,
            )

        personas = tuple(_build_persona(p, i) for i, p in enumerate(data["personas"]))
        if not 2 <= len(personas) <= 6:
            raise ValueError("a table needs 2–6 personas")
        _check_unique(personas, "name")
        out = []
        if apply_availability:
            available, out = _split_by_reachability(personas)
            if not available:
                raise ValueError(
                    f"none of {', '.join(p.name for p in personas)} are reachable "
                    "— check providers/keys"
                )
        return Lineup(personas=personas, out_tonight=tuple(out), **common)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        sys.exit(f"Invalid persona pack {path}: {e}")


def apply_troupe_availability(lineup: Lineup) -> Lineup:
    """Re-resolve a lineup loaded with apply_availability=False against live
    provider reachability. Callers that build a lineup at import time (the
    web app's module-level `create_app()`) must load with
    apply_availability=False and call this instead at actual server start —
    same reason check_keys is deferred to lifespan: a keyless import must
    never crash."""
    if not lineup.troupe:
        available, out = _split_by_reachability(lineup.personas)
        if not available:
            sys.exit(
                f"none of {', '.join(p.name for p in lineup.personas)} are reachable "
                "— check providers/keys"
            )
        return replace(lineup, personas=tuple(available), out_tonight=tuple(out))

    seats = len(lineup.personas)
    available, out = _split_by_reachability(lineup.troupe)
    if len(available) < 2:
        sys.exit(
            "fewer than 2 of the troupe are reachable tonight "
            f"(out: {', '.join(out) or 'none'}) — check providers/keys"
        )
    return replace(
        lineup,
        personas=tuple(available[:seats]),
        troupe=tuple(available),
        out_tonight=tuple(out),
    )


def missing_keys(personas: tuple[Persona, ...], moderator: str) -> list[str]:
    """Provider keys the lineup needs but the environment doesn't have."""
    return [k for k in sorted(_required_key_envs(personas, moderator)) if not _key_present(k)]
