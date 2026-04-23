# brogue-tui

A Textual re-shell over [BrogueCE](https://github.com/tmewett/BrogueCE).
Brogue's core is terminal-native already — this project wraps the
engine's `plotChar` / `nextKeyOrMouseEvent` callbacks with a custom
`pyConsole` platform, loads the resulting shared library via `ctypes`,
and puts a modern Textual window (map pane + session sidebar + help
overlay + optional agent REST API) in front of it.

## Install

Linux / macOS with `gcc`, `make`, `python3.12+`:

```
make all          # clones BrogueCE, builds libbroguepy.so, sets up venv
.venv/bin/python brogue.py
```

## Flags

- `--seed N` — pre-seed the "New Seeded Game" dialog.
- `--wizard` — launch in wizard mode (Brogue's debug helpers; no
  highscore submission).
- `--agent PORT` — start the agent REST API on `127.0.0.1:PORT`.

## Keys

All standard Brogue keys (hjkl / yubn / i / ?). Shell-only shortcuts:

- `ctrl+h` — open this re-shell's help (not Brogue's own `?`).
- `ctrl+q` — quit the Textual app (does NOT save the game; use Brogue's
  `S` for saves).
- `ctrl+c` — emergency quit.

## Agent API

`--agent 8777` starts a small JSON-over-HTTP server for remote
controllers:

| Method | Path        | Body                                   | Notes                                |
|--------|-------------|----------------------------------------|--------------------------------------|
| GET    | /health     |                                        | Liveness.                            |
| GET    | /state      |                                        | Depth, gold, seed, plot serial, etc. |
| GET    | /snapshot   | `?format=text` or `?format=rgb`        | Full 100×34 grid.                    |
| POST   | /key        | `{"key": "n"}` or `{"key": 27}`        | Post a keystroke.                    |
| POST   | /click      | `{"x": 10, "y": 5, "right": false}`    | Mouse-down + mouse-up at the cell.   |

## Tests

```
make test         # full — TUI (Pilot) + agent API + perf
make test-only PAT=mount
make test-api
make test-perf
```

Each TUI scenario runs in its own subprocess because the Brogue C
library holds callback pointers in globals — sharing an interpreter
between scenarios lets stale worker threads trample new callbacks.

## Layout

```
brogue_tui/
  engine.py        # ctypes glue + worker thread + cell grid buffer
  app.py           # Textual App + MapView + Sidebar widget
  screens.py       # HelpScreen (shell-level help only)
  agent_api.py     # aiohttp REST routes
  tui.tcss         # layout styles
vendor/
  BrogueCE/        # upstream C sources (gitignored; cloned by `make bootstrap`)
  libbroguepy.so   # built shared library
tests/
  qa.py            # Textual Pilot scenarios — 10 green
  api_qa.py        # aiohttp scenarios — 8 green
  perf.py          # hot-path timings (snapshot / render / full repaint)
  smoke_engine.py  # headless engine smoke, no Textual
```

See `DECISIONS.md` for the binding-strategy rationale.

## License

GPLv3, matching BrogueCE (the vendored source dominates).
