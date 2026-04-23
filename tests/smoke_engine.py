"""Stage-2 gate: can we start Brogue, drive some input, and see cells change?

Runs headless — no Textual, just the engine + its grid buffer.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brogue_tui.engine import (
    BrogueEngine, RogueEvent, KEYSTROKE, RETURN_KEY, ESCAPE_KEY,
    DOWN_KEY,
)


def main() -> int:
    print("== brogue-tui smoke: booting engine ==")
    engine = BrogueEngine(seed=1, start_new_game=True)
    print(f"grid: {engine.cols} × {engine.rows}")
    print(f"dungeon: {engine.dcols} × {engine.drows}")

    engine.start()

    # Wait for the first plotChar burst to populate the grid.
    print("waiting for plotChar bursts…")
    last_serial = 0
    for _ in range(30):
        time.sleep(0.1)
        serial = engine.serial
        if serial != last_serial:
            print(f"  serial: {last_serial} → {serial}")
            last_serial = serial
        if serial > 200:
            break

    if engine.serial == 0:
        print("FAIL: engine emitted no plotChar calls")
        engine.stop()
        return 1

    # Take a snapshot of the grid and print a compact summary.
    grid, serial = engine.snapshot()
    non_blank = sum(
        1 for row in grid for c in row if c.glyph != ord(" ")
    )
    print(f"serial={serial}  non-blank cells={non_blank}")

    # Print the top 5 rows as a very rough "did we get a main menu?" preview.
    print("--- top 5 rows ---")
    for y in range(5):
        line = "".join(chr(c.glyph) if 32 <= c.glyph < 0x10000 else "?"
                       for c in grid[y])
        print(f"  {y:02d} | {line}")

    # Advance through the main menu: Return selects the highlighted option
    # (New Game). Send a Return, wait, see what happens.
    print("posting RETURN to pick New Game…")
    engine.post_key(RETURN_KEY)

    time.sleep(0.5)
    grid, s2 = engine.snapshot()
    print(f"post-return serial={s2}, depth={engine.depth_level}, gold={engine.gold}")

    # Send a few movement keys so we can see a character move.
    for _ in range(3):
        engine.post_key(DOWN_KEY)
        time.sleep(0.1)

    grid, s3 = engine.snapshot()
    print(f"post-moves serial={s3}, depth={engine.depth_level}")
    # Sample row 5 of the dungeon area to confirm we're rendering walls/floor.
    row_y = engine.msg_lines + 5
    line = "".join(chr(c.glyph) if 32 <= c.glyph < 0x10000 else "?"
                   for c in grid[row_y])
    print(f"  row {row_y} | {line}")

    print("stopping engine…")
    engine.stop()
    print("OK — engine shut down cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
