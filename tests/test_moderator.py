#!/usr/bin/env python3
"""Unit tests for the moderator reply parser — pure, no API, safe for CI.

python tests/test_moderator.py
"""

import sys

from autodebate.engine import parse_moderator_reply

SEATED = {"Marcus", "Priya", "Kaito"}
TROUPE = SEATED | {"Noa", "Sana", "Gus"}

CASES = [
    # (reply, last_speaker, expected nxt, expected nudge, expected switch)
    (
        "NEXT: Priya\nNUDGE: challenge Marcus on the base rate",
        "Marcus",
        "Priya",
        "challenge Marcus on the base rate",
        None,
    ),
    ("NEXT: NOBODY\nNUDGE: -", "Marcus", None, None, None),
    # last speaker may not be picked again
    ("NEXT: Marcus\nNUDGE: go again", "Marcus", None, "go again", None),
    # unknown name → no valid next
    ("NEXT: Hal\nNUDGE: -", "Marcus", None, None, None),
    # a valid switch: seated out, café member in (NEXT naming a non-seated
    # name is invalid — the engine gives the newcomer the floor itself)
    (
        "NEXT: Noa\nNUDGE: take the film question\nSWITCH: Kaito -> Noa",
        "Priya",
        None,
        "take the film question",
        ("Kaito", "Noa"),
    ),
    # switch targets a non-troupe name → ignored
    ("NEXT: Priya\nNUDGE: -\nSWITCH: Marcus -> Zeus", "Kaito", "Priya", None, None),
    # switch evicting someone not seated → ignored
    ("NEXT: Priya\nNUDGE: -\nSWITCH: Sana -> Noa", "Kaito", "Priya", None, None),
    # malformed noise degrades gracefully
    ("I think Priya should speak next, honestly.", "Marcus", None, None, None),
    # whitespace/punctuation tolerance
    ('NEXT: "Kaito."\nNUDGE: name the crux', "Priya", "Kaito", "name the crux", None),
]


def main() -> int:
    failures = 0
    for reply, last, want_next, want_nudge, want_switch in CASES:
        d = parse_moderator_reply(reply, SEATED, TROUPE, last)
        got = (d.nxt, d.nudge, d.switch)
        want = (want_next, want_nudge, want_switch)
        if got != want:
            failures += 1
            print(f"  FAILED: {reply!r}\n    want {want}\n    got  {got}")
    if failures:
        print(f"\n{failures}/{len(CASES)} parser cases failed")
        return 1
    print(f"all {len(CASES)} parser cases OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
