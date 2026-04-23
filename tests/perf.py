"""Perf benchmark — runs after QA, catches regressions.

Three hot paths: (1) engine snapshot (deep-copy of 100×34 grid under
lock), (2) MapView.render_line per row, (3) full 34-row repaint.

Baseline numbers are reported — no hard ceilings (the perf budget will
tighten as we polish). The `test-perf` Makefile target just runs this
and prints — it's meant to be eyeballed, not automated-gated, because
CI boxes have variable clocks.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brogue_tui.engine import BrogueEngine  # noqa: E402


def _bench(label: str, fn, *, iters: int = 200) -> float:
    """Run `fn` `iters` times and report median μs/call."""
    times: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        times.append(time.perf_counter_ns() - t0)
    times.sort()
    median_ns = times[iters // 2]
    median_us = median_ns / 1000
    print(f"  {label:<40} {median_us:8.2f} μs  (median of {iters})")
    return median_us


def main() -> int:
    print("== brogue-tui perf ==")
    print("booting engine in title-menu mode…")
    engine = BrogueEngine(seed=0, start_new_game=False)
    engine.start()

    # Let the flame loop paint a full menu. With flames running, the
    # grid stays in near-steady-state churn, which is the interesting
    # shape for snapshot + render.
    t = time.monotonic()
    while time.monotonic() - t < 1.0:
        if engine.serial > 5_000:
            break
        time.sleep(0.02)
    print(f"  engine serial at bench start: {engine.serial}")

    # 1. snapshot — deep copy of the full 100×34 grid under a lock.
    _bench("engine.snapshot()", engine.snapshot)

    # 2. single-row read under the lock (what MapView does per row).
    def _row0_copy():
        with engine._grid_lock:
            row = engine._grid[0]
            return [(c.glyph, c.fr, c.fg, c.fb, c.br, c.bg, c.bb) for c in row]
    _bench("single row copy under lock", _row0_copy)

    # 3. cell_at — used by sparse accessors (mouse hit-testing etc.).
    _bench("engine.cell_at(5, 5)", lambda: engine.cell_at(5, 5))

    # 4. full-repaint loop — simulate MapView.render_line for every row.
    # We can't call render_line directly without an App context; model
    # the hot inner cost (row read + segment build) as a proxy. Mirrors
    # the style-cache from MapView.render_line so numbers are honest.
    from rich.color import Color
    from rich.segment import Segment
    from rich.style import Style

    style_cache: dict = {}

    def _fake_render_line(y: int):
        with engine._grid_lock:
            row = engine._grid[y]
            cells = [(c.glyph, c.fr, c.fg, c.fb, c.br, c.bg, c.bb) for c in row]
        segs = []
        cur_style = None
        cur_text: list[str] = []
        for glyph, fr, fg, fb, br, bg, bb in cells:
            fg_s = (fr * 255 // 100, fg * 255 // 100, fb * 255 // 100)
            bg_s = (br * 255 // 100, bg * 255 // 100, bb * 255 // 100)
            key = (fg_s, bg_s)
            style = style_cache.get(key)
            if style is None:
                style = Style(
                    color=Color.from_rgb(fg_s[0], fg_s[1], fg_s[2]),
                    bgcolor=Color.from_rgb(bg_s[0], bg_s[1], bg_s[2]),
                )
                style_cache[key] = style
            try:
                ch = chr(glyph) if glyph else " "
            except (ValueError, OverflowError):
                ch = "?"
            if style is cur_style:
                cur_text.append(ch)
            else:
                if cur_style is not None:
                    segs.append(Segment("".join(cur_text), cur_style))
                cur_style = style
                cur_text = [ch]
        if cur_style is not None:
            segs.append(Segment("".join(cur_text), cur_style))
        return segs

    _bench("fake_render_line(y=0)", lambda: _fake_render_line(0))

    def _full_frame():
        for y in range(engine.rows):
            _fake_render_line(y)

    _bench("full 34-row repaint", _full_frame, iters=50)

    engine.stop(timeout=1.0)
    print("== done ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
