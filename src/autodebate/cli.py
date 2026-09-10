"""Headless stdout front end (`autodebate --cli`) and the CLI entry point."""

from __future__ import annotations

import argparse
import asyncio

from rich.console import Console
from rich.markup import escape

from .config import (
    DEFAULT_TOPIC,
    MODERATOR_MODEL,
    Lineup,
    Persona,
    available_packs,
    check_keys,
    load_personas,
)
from .engine import Engine


class CliSink:
    def __init__(self) -> None:
        self.console = Console()

    async def status(self, text: str) -> None:
        pass

    async def speaker_start(self, persona: Persona) -> None:
        self.console.print(f"\n[bold {persona.color}]{persona.name}[/]  ", end="")

    async def token(self, text: str) -> None:
        print(text, end="", flush=True)

    async def speaker_end(self, persona: Persona, text: str) -> None:
        print(flush=True)

    async def dim(self, text: str) -> None:
        self.console.print(f"[dim]{escape(text)}[/]")

    async def user_message(self, text: str) -> None:
        self.console.print(f"\n[bold green]You[/]  {escape(text)}")

    async def error(self, text: str) -> None:
        self.console.print(f"[bold red]⚠ {escape(text)}[/]")


async def run_cli(topic: str, turns: int, lineup: Lineup) -> None:
    sink = CliSink()
    engine = Engine(
        sink,
        max_turns=turns,
        exit_on_cap=True,
        personas=lineup.personas,
        moderator_model=lineup.moderator or MODERATOR_MODEL,
        brief=lineup.brief,
    )
    sink.console.print(f"[dim]logging to {engine.log.path}[/]")
    engine.submit_user(topic)
    try:
        await engine.run()
    finally:
        await engine.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="autodebate",
        description="☕ coffee-shop debate harness — strong coffee, stronger opinions",
    )
    ap.add_argument("topic", nargs="?", help="opening message for the table")
    ap.add_argument("--cli", action="store_true", help="headless stdout mode")
    ap.add_argument("--turns", type=int, default=6, help="AI turns in --cli mode (default 6)")
    ap.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="auto-pause the TUI after N AI turns (default: unlimited)",
    )
    ap.add_argument(
        "--personas",
        metavar="PACK",
        help="persona pack: a built-in name (see --packs) or a path to a JSON pack",
    )
    ap.add_argument("--packs", action="store_true", help="list built-in persona packs and exit")
    args = ap.parse_args()

    if args.packs:
        print("Built-in persona packs:")
        for name in available_packs():
            lineup = load_personas(name)
            seats = ", ".join(p.name for p in lineup.personas)
            print(f"  {name:12s} {seats}")
        print("\nUse one with: autodebate --personas NAME — or pass a path to your own JSON pack.")
        return

    lineup = load_personas(args.personas)
    check_keys(lineup.personas, lineup.moderator or MODERATOR_MODEL)

    if args.cli:
        asyncio.run(run_cli(args.topic or DEFAULT_TOPIC, args.turns, lineup))
    else:
        from .tui import DebateApp  # defer textual import in --cli mode

        DebateApp(
            max_turns=args.max_turns,
            opening=args.topic,
            lineup=lineup,
        ).run()


if __name__ == "__main__":
    main()
