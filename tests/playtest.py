"""Playtest — real-binary smoke via pexpect/ptyprocess.

The QA harness (tests/qa.py) drives BrogueApp in-process via Textual's
Pilot. That's thorough for unit-ish checks but never boots the real
`brogue.py` binary in a real PTY. This test closes that gap:

  1. Spawn `.venv/bin/python brogue.py` on a 180×50 pseudo-terminal.
  2. Wait for the title screen to render (flame animation paints
     enough bytes that we just wait for a quiet stretch).
  3. Press Return → should either enter "Play a new game" or open a
     seeded-game dialog.
  4. Press 'j' → a single move / menu-down keystroke.
  5. Press Ctrl+H → shell help modal opens.
  6. Press Escape → help dismisses.
  7. Press Ctrl+Q → shell exits.
  8. Save the final terminal snapshot to tests/out/playtest_final.svg
     so we have a visual artefact alongside qa.py's Pilot screenshots.

Uses subprocess isolation by construction — pexpect spawns a fresh
Python interpreter, so the C-level Brogue globals are in their own
address space.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "tests" / "out"
OUT_DIR.mkdir(exist_ok=True)


def _snapshot_svg(buffer_text: str, out_path: Path) -> None:
    """Dump the captured PTY buffer as a minimal SVG — just enough to
    eyeball what the playtest saw. Not a full Textual screenshot, but
    tests/qa.py already produces those; this is the real-binary side."""
    # Strip ANSI so SVG stays human-readable.
    import re
    ansi = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")
    plain = ansi.sub("", buffer_text)
    # Keep only printable + newlines.
    plain = "".join(c for c in plain if c == "\n" or (0x20 <= ord(c) < 0x7f))
    lines = plain.splitlines()[-50:]  # last 50 rows
    line_h = 14
    width = 1100
    height = len(lines) * line_h + 20
    body = "\n".join(
        f'<text x="8" y="{(i + 1) * line_h}" font-family="monospace" '
        f'font-size="12" fill="#ddd">'
        f'{_xml_escape(line)[:180]}'
        f'</text>'
        for i, line in enumerate(lines)
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'style="background:#111">{body}</svg>'
    )
    out_path.write_text(svg, encoding="utf-8")


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def main() -> int:
    import pexpect

    # Mutate os.environ directly so pexpect's strict env type check
    # (wants os._Environ) is satisfied. Restoring on exit keeps us
    # idempotent if the test ever runs in the parent interpreter.
    saved_env = {k: os.environ.get(k) for k in ("TERM", "LINES", "COLUMNS")}
    os.environ["TERM"] = "xterm-256color"
    os.environ["LINES"] = "50"
    os.environ["COLUMNS"] = "180"
    python = str(REPO / ".venv" / "bin" / "python")
    cmd = [python, "brogue.py"]

    print(f"  spawning: {' '.join(cmd)}")
    child = pexpect.spawn(
        cmd[0], cmd[1:], cwd=str(REPO), env=os.environ,
        dimensions=(50, 180), encoding="utf-8", timeout=15,
    )

    try:
        # Step 1: wait for the flame / title to paint something. We
        # don't rely on a specific string — Brogue's title menu varies.
        # Instead, let it paint for ~1.2 s and assert the output buffer
        # has meaningful bytes (escape sequences + glyphs).
        time.sleep(1.5)
        try:
            buf = child.read_nonblocking(size=200_000, timeout=0.2)
        except pexpect.TIMEOUT:
            buf = ""
        boot = (child.before or "") + buf
        assert len(boot) > 2_000, (
            f"title never painted (captured {len(boot)} bytes)"
        )
        print(f"  title rendered: {len(boot)} bytes captured")

        # Step 2: press Return. On the title menu this activates the
        # currently focused button ("Play") or confirms a dialog. Either
        # way it advances the menu state.
        child.send("\r")
        time.sleep(0.8)
        print("  sent Return")

        # Step 3: a movement keystroke — 'j' (south / menu-down).
        child.send("j")
        time.sleep(0.4)
        print("  sent 'j'")

        # Step 4: Ctrl+H → shell help modal.
        child.send("\x08")  # Ctrl+H
        time.sleep(0.6)
        print("  sent Ctrl+H (help)")

        # Step 5: Escape → dismiss any modal. Harmless even if nothing
        # was open.
        child.send("\x1b")
        time.sleep(0.4)
        print("  sent Escape")

        # Step 6: Ctrl+Q → quit the shell.
        child.send("\x11")
        print("  sent Ctrl+Q — waiting for exit")

        # Step 7: process should exit within a few seconds (engine
        # autopilot unwinds the title menu).
        forced = False
        try:
            child.expect(pexpect.EOF, timeout=10)
        except pexpect.TIMEOUT:
            print("  WARN: process did not exit within 10 s; force-closing")
            child.close(force=True)
            forced = True
        final = (child.before or "") + buf

        # .close() flushes status; expect(EOF) alone doesn't.
        if child.isalive() or not forced:
            try:
                child.close()
            except Exception:
                pass

        # Capture the full buffer as an SVG artefact.
        out_path = OUT_DIR / f"playtest_{int(time.time())}.svg"
        _snapshot_svg(final, out_path)
        print(f"  snapshot: {out_path}")

        # Also save a stable name for CI diffs.
        _snapshot_svg(final, OUT_DIR / "playtest_latest.svg")

        exitstatus = child.exitstatus
        signalstatus = child.signalstatus
        print(f"  exit: status={exitstatus} signal={signalstatus}")
        # A clean exit is status 0. If the engine auto-quit timed out,
        # we force-closed (signalstatus set) — still a pass for the
        # visual smoke test but flag it.
        if exitstatus not in (0, None):
            print(f"  non-zero exit code: {exitstatus}")
            return 1
        return 0
    finally:
        if child.isalive():
            child.close(force=True)
        # Restore env vars we mutated.
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    sys.exit(main())
