#!/usr/bin/env python3
"""brogue-tui — Textual re-shell over BrogueCE.

Run with `.venv/bin/python brogue.py` or `make run`. Flags mirror the
native Brogue CLI where they overlap (--seed, --wizard); the Textual
layer adds no scenario-picker yet — the Brogue main menu handles new
game / load / options. Use --help for the full list.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="brogue-tui",
        description="Textual re-shell over BrogueCE",
    )
    parser.add_argument(
        "--seed", type=int, default=0,
        help="start a new game with a specific seed (0 = menu / last seed)",
    )
    parser.add_argument(
        "--wizard", action="store_true",
        help="enable wizard mode (debugging helpers; no highscore)",
    )
    args = parser.parse_args()

    from brogue_tui.app import run
    run(seed=args.seed, wizard=args.wizard)
    return 0


if __name__ == "__main__":
    sys.exit(main())
