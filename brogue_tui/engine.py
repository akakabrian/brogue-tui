"""ctypes glue + worker-thread harness around libbroguepy.so.

Brogue is a blocking event loop: rogueMain() returns only when the player
quits. We run it on a worker thread and funnel the C→Python callbacks
(plotChar, next_event, pause_ms, notifyEvent) through thread-safe
primitives.

The Python side maintains a 100×34 grid of (glyph, fg_rgb, bg_rgb) tuples
that the TUI renders at its own cadence — typically 30 Hz. plotChar
pushes updates into that grid under a fine-grained lock.

Input is mirrored in reverse: Textual posts rogueEvent dicts onto an
event queue; the worker thread blocks on .get() when Brogue calls
next_event.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Engine constants (mirror of src/brogue/Rogue.h — duplicated here so a
# headless import doesn't require the .so).

COLS = 100
ROWS = 34
MESSAGE_LINES = 3
STAT_BAR_WIDTH = 20
DCOLS = COLS - STAT_BAR_WIDTH - 1        # 79
DROWS = ROWS - MESSAGE_LINES - 2         # 29

# rogueEvent eventType enum values (order matches src/brogue/Rogue.h).
KEYSTROKE = 0
MOUSE_UP = 1
MOUSE_DOWN = 2
RIGHT_MOUSE_DOWN = 3
RIGHT_MOUSE_UP = 4
MOUSE_ENTERED_CELL = 5

# Brogue-specific key codes (subset — full table in Rogue.h around line 1161).
UP_KEY = ord("k")
DOWN_KEY = ord("j")
LEFT_KEY = ord("h")
RIGHT_KEY = ord("l")
UPLEFT_KEY = ord("y")
UPRIGHT_KEY = ord("u")
DOWNLEFT_KEY = ord("b")
DOWNRIGHT_KEY = ord("n")
ESCAPE_KEY = 0o33       # \033
RETURN_KEY = 0o12       # \012
TAB_KEY = 0o11
DELETE_KEY = 0o177


# ---------------------------------------------------------------------------
# ctypes callback signatures — must match py-platform.c exactly.

_PLOT_CB = ctypes.CFUNCTYPE(
    None,
    ctypes.c_uint,      # glyph (unicode codepoint)
    ctypes.c_short,     # x
    ctypes.c_short,     # y
    ctypes.c_short,     # fr
    ctypes.c_short,     # fg
    ctypes.c_short,     # fb
    ctypes.c_short,     # br
    ctypes.c_short,     # bg
    ctypes.c_short,     # bb
)

_PAUSE_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_short)

_NEXT_CB = ctypes.CFUNCTYPE(
    None,
    ctypes.POINTER(ctypes.c_int),   # eventType out
    ctypes.POINTER(ctypes.c_long),  # param1 out
    ctypes.POINTER(ctypes.c_long),  # param2 out
    ctypes.POINTER(ctypes.c_int),   # ctrl out
    ctypes.POINTER(ctypes.c_int),   # shift out
    ctypes.c_int,                    # textInput in
)

_NOTIFY_CB = ctypes.CFUNCTYPE(
    None,
    ctypes.c_short,   # eventId
    ctypes.c_int,     # data1
    ctypes.c_int,     # data2
    ctypes.c_char_p,  # str1
    ctypes.c_char_p,  # str2
)


# ---------------------------------------------------------------------------
# Cell + event dataclasses.

@dataclass(slots=True)
class Cell:
    glyph: int = ord(" ")      # unicode codepoint, not a character
    fr: int = 0                # foreground RGB, 0..100 (Brogue scale)
    fg: int = 0
    fb: int = 0
    br: int = 0                # background RGB, 0..100
    bg: int = 0
    bb: int = 0


@dataclass(slots=True)
class RogueEvent:
    event_type: int = KEYSTROKE
    param1: int = 0
    param2: int = 0
    ctrl: bool = False
    shift: bool = False


# ---------------------------------------------------------------------------
# Shared library discovery — the Makefile drops libbroguepy.so next to
# the vendor dir; support both repo-dev layout and an installed wheel.

def _find_library() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [
        here.parent / "vendor" / "libbroguepy.so",
        here / "libbroguepy.so",
    ]:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "libbroguepy.so not built — run `make engine` from the repo root"
    )


def _find_data_dir() -> Path:
    here = Path(__file__).resolve().parent
    # Prefer the vendored bin/ (contains keymap + asset files Brogue reads).
    bin_dir = here.parent / "vendor" / "BrogueCE" / "bin"
    if bin_dir.exists():
        return bin_dir
    return here


# ---------------------------------------------------------------------------

class BrogueEngine:
    """Owns the libbroguepy.so handle and the Brogue worker thread.

    Callers: instantiate, then call .start() to kick off rogueMain on a
    worker. The engine will immediately start emitting plot_char calls
    which populate self.grid. The TUI polls self.grid on its render
    timer and paints what it sees.

    To drive input, call .post_event(RogueEvent(...)). The worker's
    blocked next_event call will wake up with your event."""

    def __init__(self, *, seed: int = 0, wizard: bool = False,
                 start_new_game: bool = True) -> None:
        lib_path = _find_library()
        self._lib = ctypes.CDLL(str(lib_path))
        self._configure_signatures()

        # Dimensions pulled from the .so so Python and C can't drift.
        self.cols = int(self._lib.brogue_cols())
        self.rows = int(self._lib.brogue_rows())
        self.dcols = int(self._lib.brogue_dcols())
        self.drows = int(self._lib.brogue_drows())
        self.msg_lines = int(self._lib.brogue_msg_lines())

        # The live display grid. plotChar writes into it on the worker
        # thread; the TUI reads it on the main thread. A single coarse
        # lock is plenty — plotChar calls are microsecond-fast.
        self._grid_lock = threading.Lock()
        self._grid: list[list[Cell]] = [
            [Cell() for _ in range(self.cols)] for _ in range(self.rows)
        ]
        # Monotonic counter — bumps on every plot_char call. The TUI can
        # compare against its last-seen value to skip full redraws when
        # nothing's changed.
        self._serial = 0

        # Event queue from Textual → worker. next_event blocks on this;
        # Textual's keybinding handlers call post_event() to fill it.
        self._events: queue.Queue[RogueEvent] = queue.Queue()

        # Notify-event hook — Python callback for game-over etc. Set by
        # the TUI layer.
        self.on_notify: Callable[[int, int, int, bytes, bytes], None] | None = None

        # Keep C callback trampolines alive as instance attrs, otherwise
        # ctypes will garbage-collect them and we get crashes when
        # Brogue calls back in.
        self._plot_trampoline = _PLOT_CB(self._on_plot_char)
        self._pause_trampoline = _PAUSE_CB(self._on_pause_ms)
        self._next_trampoline = _NEXT_CB(self._on_next_event)
        self._notify_trampoline = _NOTIFY_CB(self._on_notify)

        # Set data dir before registering callbacks — Brogue reads
        # keymap.txt off disk on startup.
        data_dir = str(_find_data_dir())
        self._lib.brogue_set_data_directory(data_dir.encode("utf-8"))
        self._data_dir = data_dir

        self._lib.brogue_set_callbacks(
            self._plot_trampoline,
            self._pause_trampoline,
            self._next_trampoline,
            self._notify_trampoline,
        )

        self._lib.brogue_configure(
            ctypes.c_uint64(seed),
            1 if wizard else 0,
            0,                           # stealth — off by default
            0,                           # trueColor — off
            1 if start_new_game else 0,
        )

        self._thread: threading.Thread | None = None
        self._running = False
        # Flag set by stop() to make _on_next_event feed ESC autopilot.
        self._die = False
        self._die_idx = 0
        # Game-over flag — set from py_notifyEvent when Brogue sends a
        # GAMEOVER_* notification.
        self.game_over = False
        self.game_over_reason = ""

    # --- ctypes signature boilerplate --------------------------------------

    def _configure_signatures(self) -> None:
        lib = self._lib
        lib.brogue_cols.restype = ctypes.c_int
        lib.brogue_rows.restype = ctypes.c_int
        lib.brogue_dcols.restype = ctypes.c_int
        lib.brogue_drows.restype = ctypes.c_int
        lib.brogue_msg_lines.restype = ctypes.c_int
        lib.brogue_run.restype = ctypes.c_int
        lib.brogue_depth_level.restype = ctypes.c_int
        lib.brogue_deepest_level.restype = ctypes.c_int
        lib.brogue_gold.restype = ctypes.c_long
        lib.brogue_seed.restype = ctypes.c_uint64
        lib.brogue_easy_mode.restype = ctypes.c_int

        lib.brogue_set_callbacks.argtypes = [
            _PLOT_CB, _PAUSE_CB, _NEXT_CB, _NOTIFY_CB,
        ]
        lib.brogue_set_callbacks.restype = None

        lib.brogue_configure.argtypes = [
            ctypes.c_uint64, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
        ]
        lib.brogue_configure.restype = None

        lib.brogue_set_data_directory.argtypes = [ctypes.c_char_p]
        lib.brogue_set_data_directory.restype = None

    # --- C callback implementations ----------------------------------------

    def _on_plot_char(self, glyph, x, y, fr, fg, fb, br, bg, bb):
        # Called on the worker thread.
        if 0 <= x < self.cols and 0 <= y < self.rows:
            with self._grid_lock:
                cell = self._grid[y][x]
                cell.glyph = int(glyph)
                cell.fr, cell.fg, cell.fb = int(fr), int(fg), int(fb)
                cell.br, cell.bg, cell.bb = int(br), int(bg), int(bb)
                self._serial += 1

    def _on_pause_ms(self, milliseconds: int) -> int:
        # Return 1 if an event is available within `milliseconds`.
        # queue.Queue doesn't expose a "wait-but-don't-consume" primitive;
        # easiest reliable option is to peek by checking empty().
        # When _die is set we always return 1 so Brogue moves on to
        # nextBrogueEvent — our _on_next_event then feeds the quit
        # autopilot instead of blocking.
        if self._die:
            return 1
        if milliseconds <= 0:
            return 1 if not self._events.empty() else 0
        # Spin-poll the queue — a .get(timeout=) would consume the event.
        deadline = time.monotonic() + milliseconds / 1000.0
        while time.monotonic() < deadline:
            if not self._events.empty() or self._die:
                return 1
            time.sleep(0.01)
        return 0

    def _on_next_event(self, et_ptr, p1_ptr, p2_ptr, ctrl_ptr, shift_ptr,
                       text_input):
        # Block until an event is posted from the Textual side.
        # If _die is set (via stop()), cycle through a small alphabet of
        # escape hatches: 'q' quits the title menu, ESCAPE + 'y' escapes
        # most in-game prompts and confirms a quit dialog, RETURN
        # acknowledges an "OK" button. This rotates through them so
        # whichever screen the engine lands on, one press will match.
        _autopilot = (
            ord("q"), ESCAPE_KEY, ord("Q"), ord("y"),
            RETURN_KEY, ESCAPE_KEY, ord("n"),
        )
        while True:
            try:
                ev = self._events.get(timeout=0.1)
                break
            except queue.Empty:
                if self._die:
                    code = _autopilot[self._die_idx % len(_autopilot)]
                    self._die_idx += 1
                    ev = RogueEvent(event_type=KEYSTROKE, param1=code)
                    break
        et_ptr[0] = ev.event_type
        p1_ptr[0] = ev.param1
        p2_ptr[0] = ev.param2
        ctrl_ptr[0] = 1 if ev.ctrl else 0
        shift_ptr[0] = 1 if ev.shift else 0

    def _on_notify(self, event_id, data1, data2, str1, str2):
        # GAMEOVER_* constants — src/brogue/Rogue.h, enum
        # notificationEventTypes (0..4: QUIT, DEATH, VICTORY,
        # SUPERVICTORY, RECORDING).
        if event_id in (0, 1, 2, 3, 4):
            self.game_over = True
            try:
                self.game_over_reason = (str1 or b"").decode("utf-8", "replace")
            except Exception:
                self.game_over_reason = ""
        if self.on_notify is not None:
            try:
                self.on_notify(event_id, data1, data2, str1, str2)
            except Exception:
                # Never let a Python-side callback error crash the game thread.
                pass

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the Brogue worker thread. Returns immediately."""
        if self._running:
            return
        self._running = True

        def _target():
            # Brogue reads keymap.txt via a path relative to getcwd, so
            # chdir into the data directory on the worker thread before
            # launching rogueMain. Main-thread cwd is untouched.
            import os
            os.chdir(self._data_dir)
            self._lib.brogue_run()
            self._running = False

        self._thread = threading.Thread(
            target=_target, name="brogue-engine", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Best-effort shutdown.

        Brogue's multi-screen menu/dialog stack has no single "exit now"
        path — titleMenu() waits for a button hotkey, an in-game prompt
        waits for y/n, etc. We set a `_die` flag that flips the
        next-event callback into "stream ESCAPE forever" mode, which
        unwinds most screen stacks. On top of that we push a seeded
        burst of (ESC / y / Q / q / Return) on the off-chance the stack
        is at a specific prompt needing a specific response.

        The worker thread is daemonic, so whether or not we successfully
        join within `timeout`, the process can still exit cleanly when
        Python shuts down."""
        if not self._running:
            return
        self._die = True
        for code in (ESCAPE_KEY, ord("y"), ord("n"),
                     ord("Q"), ord("q"), RETURN_KEY, ESCAPE_KEY):
            self.post_event(RogueEvent(event_type=KEYSTROKE, param1=code))
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if not self._thread.is_alive():
                # Worker exited but _target may not have cleared the flag
                # yet (race window); clear it here so is_running() is
                # honest to callers that poll after stop().
                self._running = False

    # --- public API --------------------------------------------------------

    def post_event(self, event: RogueEvent) -> None:
        """Push an event into the queue. Wakes the worker if it's blocked."""
        self._events.put(event)

    def post_key(self, key: int, *, ctrl: bool = False, shift: bool = False) -> None:
        self.post_event(RogueEvent(
            event_type=KEYSTROKE, param1=key, ctrl=ctrl, shift=shift,
        ))

    def post_mouse_down(self, x: int, y: int, *, right: bool = False) -> None:
        self.post_event(RogueEvent(
            event_type=RIGHT_MOUSE_DOWN if right else MOUSE_DOWN,
            param1=x, param2=y,
        ))

    def post_mouse_up(self, x: int, y: int, *, right: bool = False) -> None:
        self.post_event(RogueEvent(
            event_type=RIGHT_MOUSE_UP if right else MOUSE_UP,
            param1=x, param2=y,
        ))

    def snapshot(self) -> tuple[list[list[Cell]], int]:
        """Return (deep-copied grid, serial). Locks briefly."""
        with self._grid_lock:
            g = [[Cell(c.glyph, c.fr, c.fg, c.fb, c.br, c.bg, c.bb) for c in row]
                 for row in self._grid]
            return g, self._serial

    @property
    def serial(self) -> int:
        return self._serial

    def cell_at(self, x: int, y: int) -> Cell:
        """Return a copy of one cell. Safe to call concurrently."""
        with self._grid_lock:
            c = self._grid[y][x]
            return Cell(c.glyph, c.fr, c.fg, c.fb, c.br, c.bg, c.bb)

    # --- misc --------------------------------------------------------------

    @property
    def depth_level(self) -> int:
        return int(self._lib.brogue_depth_level())

    @property
    def deepest_level(self) -> int:
        return int(self._lib.brogue_deepest_level())

    @property
    def gold(self) -> int:
        return int(self._lib.brogue_gold())

    @property
    def seed(self) -> int:
        return int(self._lib.brogue_seed())

    def is_running(self) -> bool:
        return self._running
