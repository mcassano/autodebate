#!/usr/bin/env python3
"""Headless smoke test for the TUI: launch with Textual Pilot, say something,
wait for a persona to answer, verify it rendered, exit cleanly.

    python tests/test_tui.py
"""

import asyncio
import sys
import time

from autodebate.config import check_keys
from autodebate.tui import DebateApp, Speech

TIMEOUT_S = 150


async def main() -> int:
    check_keys()
    app = DebateApp(max_turns=1)
    async with app.run_test(size=(110, 35)) as pilot:
        app.engine.submit_user(
            "Give the table one idea about the future you'd defend even if everyone here disagrees."
        )
        deadline = time.monotonic() + TIMEOUT_S
        while time.monotonic() < deadline:
            await pilot.pause(1.0)
            if app.engine and app.engine.turns >= 1:
                break

        ok = bool(app.engine and app.engine.turns >= 1)
        speeches = [w for w in app.chat.query(Speech) if w.text.strip()]
        ok = ok and len(speeches) >= 1

        print(
            f"TUI smoke: {'OK' if ok else 'FAILED'} "
            f"(turns={app.engine.turns if app.engine else 0}, "
            f"tokens={app.engine.tokens if app.engine else 0})"
        )
        if ok:
            for w in speeches:
                print(f"--- {w.speaker}: {w.text[:400]}")
        else:
            print("--- chat contents at timeout:")
            from textual.widgets import Static

            for w in app.chat.query(Static):
                print("   ", str(w.renderable)[:200])

        if app.engine:
            app.engine.stop = True
            app.engine.running.set()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
