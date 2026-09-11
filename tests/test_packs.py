#!/usr/bin/env python3
"""Validate every built-in persona pack: loads cleanly, well-formed, unique
names and colors, parseable model specs. No API calls, no provider probes —
safe for CI.

    python tests/test_packs.py
"""

import sys

from autodebate.config import available_packs, load_personas, parse_spec


def main() -> int:
    failures = []
    for name in available_packs():
        try:
            # apply_availability=False: validate the pack itself, not this machine
            lineup = load_personas(name, apply_availability=False)
            people = lineup.troupe or lineup.personas
            assert 2 <= len(people) <= 12, f"{len(people)} personas"
            names = [p.name for p in people]
            assert len(set(names)) == len(names), f"duplicate names: {names}"
            colors = [p.color for p in people]
            assert len(set(colors)) == len(colors), f"duplicate colors: {colors}"
            assert 2 <= len(lineup.personas) <= len(people), "bad initial seats"
            for p in people:
                assert p.style.strip() and len(p.archetype) >= 80, f"{p.name} is thin"
                parse_spec(p.model)
            if lineup.moderator:
                parse_spec(lineup.moderator)
            kind = (
                f"troupe of {len(people)}, {len(lineup.personas)} seats"
                if lineup.troupe
                else "fixed"
            )
            print(f"  {name:12s} ({kind}) {' · '.join(p.name for p in lineup.personas)}")
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
