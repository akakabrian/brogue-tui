# brogue-tui — design decisions

## Upstream
- **Engine:** [BrogueCE](https://github.com/tmewett/BrogueCE) (tmewett fork,
  active, GPLv3). Vendored at `vendor/BrogueCE/`.
- **Brogue is already terminal-native.** Its job is to render a 100×34 grid
  via `plotChar(x, y, glyph, fg_rgb, bg_rgb)` and block on
  `nextKeyOrMouseEvent` for input. So "bindings" = wrapping those two hooks.

## Binding strategy — custom C platform + shared library

Brogue has a pluggable platform abstraction (`struct brogueConsole` in
`src/platform/platform.h`). Four ship with the repo: `sdlConsole`,
`cursesConsole`, `webConsole`, `nullConsole`. **We add a fifth: `pyConsole`.**

Rather than reimplement the curses renderer or capture tty output from a
subprocess (fragile; colour escapes are hard to parse reliably), we build
Brogue as **`libbroguepy.so`** with our custom platform baked in, and
`ctypes`-load it from Python. The Python side:

1. Spawns Brogue in a **worker thread** — `rogueMain()` is a blocking
   event loop; we can't run it on the asyncio loop.
2. Receives `plotChar` calls via a C callback trampoline that pushes into
   a thread-safe grid buffer on the Python side.
3. Feeds input via a thread-safe queue; `next_event` blocks on that queue
   until Python posts a key or mouse event.

**Advantages over subprocess+pty capture:**
- Zero parsing — we get (x, y, glyph, fg, bg) tuples directly.
- Clean modifier key handling (`controlKey` / `shiftKey` are struct fields).
- `notifyEvent` gives us victory / death / game-over callbacks for free.
- Deterministic testing via Pilot — feed events synchronously.

**Advantages over CFFI:** we don't need to expose every internal struct.
The platform ABI is already narrow — 6 function pointers. `ctypes` on a
few `c_void_p` + CFUNCTYPE callbacks is enough.

## Dimensions
- `COLS = 100`, `ROWS = 34` (`31 + MESSAGE_LINES(3)`).
- `DCOLS = 79` (map width), `DROWS = 29` (map height), offset (21, 2).
- Sidebar is on the left (20 cols). Message lines at the top. Status bar
  at bottom.

## TUI layout (target final stage)
- **Map pane** — full 100×34 grid as rendered by Brogue, with Textual
  colour+glyph rendering. Mouse click → `MOUSE_DOWN` event.
- **Sidebar stats** — we can either let Brogue paint the stats (cols 0-19)
  or intercept and render our own Textual widget; starting with letting
  Brogue paint, we can re-shell later.
- **Message log pane** — below Brogue's own message area, a scrollable
  RichLog with history beyond the 3 engine lines.
- **Inventory pane** — optional right-side panel that calls into Brogue's
  item list via the same ctypes FFI.

## Render contract
- Brogue calls `plotChar` whenever a cell changes. We maintain a
  `(glyph, fg, bg)` grid on the Python side, mutated from the worker
  thread. Textual re-renders at ~30 Hz via `set_interval`.
- Lock: a `threading.Lock` around the grid; `plotChar` holds for <1 µs.

## Input contract
- Textual captures keys/clicks, translates to `rogueEvent`, pushes to a
  queue. The worker thread's `nextKeyOrMouseEvent` `.get()`s from it.
- `pauseForMilliseconds` uses `queue.get(timeout=ms/1000)` to peek.

## Build
- `Makefile` target `engine` builds the vendored Brogue with our
  `py-platform.c` linked in as a shared lib. We strip the `main.c` CLI
  shim and expose a `brogue_init(argv…)` + `brogue_run()` entry pair.

## Save / load / recordings
- Brogue already has its own save/recording files (`.broguesave`,
  `.broguerec`). We shell those via the existing menu + CLI flags —
  no wrapper needed.

## Open questions parked for later
- Does Brogue make assumptions about stdin/stdout being TTYs outside of
  the platform callbacks? Answer discovered during Stage 2 smoke test.
- Can we run multiple game loops in the same process? Probably not
  without refactoring `rogue` globals — but we don't need to.
