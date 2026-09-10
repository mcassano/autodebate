#!/usr/bin/env python3
"""Validate every built-in persona pack: loads cleanly, well-formed, unique
names, parseable model specs. No API calls — safe for CI.

    python tests/test_packs.py
"""

import sys

from autodebate.config import available_packs, load_personas, parse_spec


def main() -> int:
    failures = []
    for name in available_packs():
        try:
            lineup = load_personas(name)
            personas = lineup.personas
            assert 2 <= len(personas) <= 6, f"{len(personas)} personas"
            names = [p.name for p in personas]
            assert len(set(names)) == len(names), f"duplicate names: {names}"
            colors = [p.color for p in personas]
            assert len(set(colors)) == len(colors), f"duplicate colors: {colors}"
            for p in personas:
                assert p.style.strip() and len(p.archetype) >= 80, f"{p.name} is thin"
                parse_spec(p.model)
            if lineup.moderator:
                parse_spec(lineup.moderator)
            print(f"  {name:12s} {' · '.join(names)}")
        except (AssertionError, SystemExit) as e:
            failures.append((name, e))
            print(f"  {name:12s} FAILED: {e}")

    if failures:
        print(f"\n{len(failures)} pack(s) failed")
        return 1
    print(f"\nall {len(available_packs())} packs OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
