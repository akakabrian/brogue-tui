"""QA harness — Textual Pilot scenarios.

Each scenario is async, takes `(app, pilot)`, and asserts on app/engine
state. On pass we save an SVG screenshot; on fail we save one too (so
you can diff the broken render against the last green one).

Run with `make test` (all) or `make test-only PAT=<substring>` (filter
by scenario name).

Gate for Stage 4: every scenario in SCENARIOS below green. That's the
baseline — every later stage has to keep them green.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

# Make `brogue_tui` importable when run via `python -m tests.qa`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brogue_tui.app import BrogueApp, MapView  # noqa: E402
from brogue_tui.engine import (  # noqa: E402
    BrogueEngine,
    Cell,
    KEYSTROKE,
    RETURN_KEY,
    RogueEvent,
)


OUT_DIR = Path(__file__).resolve().parent / "out"
OUT_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Scenario:
    name: str
    fn: Callable[..., Awaitable[None]]


# --- helpers -------------------------------------------------------------

async def _wait_for_serial(app: BrogueApp, target: int, *,
                           timeout: float = 5.0) -> bool:
    """Poll the engine until its plotChar serial passes `target`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app.engine.serial >= target:
            return True
        await asyncio.sleep(0.05)
    return False


def _grid_text(engine: BrogueEngine, y: int) -> str:
    """Render a grid row as a best-effort string. Used for 'does the
    welcome message appear' style assertions."""
    with engine._grid_lock:
        row = engine._grid[y]
        return "".join(
            chr(c.glyph) if 32 <= c.glyph < 0x10000 else "?"
            for c in row
        )


# --- scenarios -----------------------------------------------------------

async def mount_clean(app: BrogueApp, pilot) -> None:
    """App mounts without exceptions; MapView widget exists."""
    map_view = app.query_one("#map", MapView)
    assert map_view is not None, "MapView not mounted"
    assert map_view.engine is app.engine
    assert app.engine.is_running(), "engine worker thread not started"


async def engine_paints_welcome(app: BrogueApp, pilot) -> None:
    """After mount, Brogue's flame/menu animation advances plotChar bursts.

    We land on the animated title menu (start_new_game=False). The
    flame animation runs continuously, so serial climbs into the tens
    of thousands within a second. We also let the menu buttons
    initialize and confirm at least one recognisable menu label
    appears anywhere on the grid."""
    # Flames update at ~16 Hz, each updateMenuFlames call paints ~400
    # cells. 20 000 is easy in the first 2 s.
    ok = await _wait_for_serial(app, 20_000, timeout=4.0)
    assert ok, f"too few plotChars after 4 s (serial={app.engine.serial})"
    haystack = " ".join(
        _grid_text(app.engine, y) for y in range(app.engine.rows)
    ).lower()
    markers = ("play", "quit", "new game", "view")
    assert any(m in haystack for m in markers), (
        f"none of {markers} appear on the rendered grid; "
        f"first 400 chars: {haystack[:400]!r}"
    )


async def map_view_renders_cells(app: BrogueApp, pilot) -> None:
    """MapView.render_line produces non-empty strips for painted rows."""
    await _wait_for_serial(app, 500)
    # Let the 30 Hz redraw timer fire at least once, and the menu's
    # flame + button layout settle.
    await pilot.pause(0.3)
    map_view = app.query_one("#map", MapView)
    # Row 0 of the title menu is mostly background flame fill; scan all
    # rows and assert at least one produces visible text.
    found = False
    for y in range(app.engine.rows):
        strip = map_view.render_line(y)
        segs = list(strip)
        if not segs:
            continue
        text = "".join(s.text for s in segs)
        if text.strip():
            found = True
            break
    assert found, "no row rendered any non-blank text"


async def key_press_reaches_engine(app: BrogueApp, pilot) -> None:
    """Pressing a key posts a RogueEvent to the engine queue."""
    await _wait_for_serial(app, 200)
    # Let the menu's flame loop settle before posting keystrokes.
    await pilot.pause(0.2)
    before = app.engine.serial
    # Press 'n' — on the title menu this triggers the New Game quickstart,
    # which produces several thousand plotChar calls as the dungeon
    # renders. That's a solid end-to-end signal that the keypress made
    # it through on_key → engine.post_key → worker thread.
    await pilot.press("n")
    ok = await _wait_for_serial(app, before + 500, timeout=4.0)
    assert ok, (
        f"no plotChar activity after 'n' "
        f"(before={before}, after={app.engine.serial})"
    )


async def new_game_starts(app: BrogueApp, pilot) -> None:
    """'n' hotkey on the title menu kicks a new game; depth_level→1."""
    await _wait_for_serial(app, 200)
    # Let the flame-animation loop be established before posting; the
    # menu ignores input until pauseBrogue() wakes up at least once.
    await pilot.pause(0.2)
    app.engine.post_key(ord("n"))
    # Wait for depth_level to become 1 (main menu reports 0).
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if app.engine.depth_level >= 1:
            return
        await asyncio.sleep(0.1)
    assert False, f"depth_level never became ≥ 1 (still {app.engine.depth_level})"


async def render_line_colours_match_engine(app: BrogueApp, pilot) -> None:
    """The strip returned by render_line carries a style derived from
    the engine cell's RGB — verifies the colour pipeline end-to-end."""
    await _wait_for_serial(app, 500)
    await pilot.pause(0.1)
    map_view = app.query_one("#map", MapView)
    # Find a row with non-trivial colour — the message area usually has
    # bright-white text on black. Walk rows until we find a segment with
    # a non-empty foreground style.
    for y in range(app.engine.rows):
        strip = map_view.render_line(y)
        for seg in strip:
            if seg.style and seg.style.color:
                # Smoke: got a colour. That proves the _rgb path runs.
                return
    assert False, "no coloured segments in any row — render pipeline broken"


async def mouse_click_forwards_to_engine(app: BrogueApp, pilot) -> None:
    """Clicking on the map posts a MOUSE_DOWN/UP pair."""
    await _wait_for_serial(app, 200)
    # We can't easily assert that Brogue *consumed* the click without
    # deeper engine inspection; instead, monkey-patch post_mouse_down to
    # record the call, and verify our on_click calls it.
    calls: list[tuple[int, int, bool]] = []
    orig_down = app.engine.post_mouse_down

    def _spy(x, y, *, right=False):
        calls.append((x, y, right))
        return orig_down(x, y, right=right)

    app.engine.post_mouse_down = _spy  # type: ignore[assignment]
    try:
        await pilot.click("#map", offset=(5, 1))
        await pilot.pause(0.1)
    finally:
        app.engine.post_mouse_down = orig_down  # type: ignore[assignment]

    assert calls, "on_click did not forward any mouse event to the engine"


async def engine_stops_cleanly(app: BrogueApp, pilot) -> None:
    """Shutting down the engine joins the worker thread within a bounded
    wait. Regression guard — an early bug had the worker spinning on a
    blocked queue forever."""
    # Make sure the game is up and plotting first.
    await _wait_for_serial(app, 200)
    app.engine.stop(timeout=2.0)
    # `is_running` flips to False when the worker exits.
    assert not app.engine.is_running(), "engine did not shut down"


SCENARIOS: list[Scenario] = [
    Scenario("mount_clean", mount_clean),
    Scenario("engine_paints_welcome", engine_paints_welcome),
    Scenario("map_view_renders_cells", map_view_renders_cells),
    Scenario("render_line_colours_match_engine", render_line_colours_match_engine),
    Scenario("key_press_reaches_engine", key_press_reaches_engine),
    Scenario("mouse_click_forwards_to_engine", mouse_click_forwards_to_engine),
    Scenario("new_game_starts", new_game_starts),
    Scenario("engine_stops_cleanly", engine_stops_cleanly),
]


# --- runner --------------------------------------------------------------

async def _run_one(scn: Scenario) -> tuple[str, bool, str]:
    app = BrogueApp(seed=1)
    try:
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause(0.1)  # let on_mount finish
            try:
                await scn.fn(app, pilot)
                app.save_screenshot(str(OUT_DIR / f"{scn.name}.PASS.svg"))
                return (scn.name, True, "")
            except AssertionError as e:
                app.save_screenshot(str(OUT_DIR / f"{scn.name}.FAIL.svg"))
                return (scn.name, False, f"AssertionError: {e}")
            except Exception as e:
                try:
                    app.save_screenshot(str(OUT_DIR / f"{scn.name}.ERROR.svg"))
                except Exception:
                    pass
                return (scn.name, False, f"{type(e).__name__}: {e}")
    finally:
        # Hard-stop the engine whichever thread we're on, so run_test
        # cleanup doesn't leak the daemon worker across scenarios.
        try:
            app.engine.stop(timeout=1.0)
        except Exception:
            pass


async def run_all(pattern: str | None = None) -> int:
    selected = [s for s in SCENARIOS if pattern is None or pattern in s.name]
    if not selected:
        print(f"no scenarios matched pattern {pattern!r}")
        return 1
    results = []
    for scn in selected:
        print(f"  ▸ {scn.name} …", flush=True)
        name, ok, detail = await _run_one(scn)
        status = "PASS" if ok else "FAIL"
        print(f"    {status} — {name}  {detail}", flush=True)
        results.append((name, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print()
    print(f"== qa: {passed}/{total} passed ==")
    failures = [r for r in results if not r[1]]
    for name, _, detail in failures:
        print(f"   FAIL {name}: {detail}")
    return 0 if passed == total else 1


def main() -> int:
    pattern = sys.argv[1] if len(sys.argv) > 1 else None
    # Reduce Textual / asyncio log noise so test output is readable.
    os.environ.setdefault("TEXTUAL", "")
    return asyncio.run(run_all(pattern))


if __name__ == "__main__":
    sys.exit(main())
