"""Modal screens — Textual-level overlays that don't exist in native Brogue.

Brogue ships its own menus, dialogs, and help; this module only adds the
Textual-shell-specific UIs: a cheat sheet for how to drive the re-shell
itself (key mapping table, mouse behaviour, how to quit), and a small
"about" panel.

Do NOT overlap these with Brogue's in-game menus (Escape, `?`, inventory
etc.) — those belong to the engine. Use app-scoped shortcuts (`ctrl+h`,
`ctrl+?`) so we never fight for keys on the dungeon screen.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


HELP_TEXT = """\
[bold]brogue-tui — Textual re-shell[/bold]

This is Brogue Community Edition running behind a Textual UI. All the
usual Brogue keys still work — the shell just adds windowing, a live
sidebar, and (optionally) a remote agent API.

[bold cyan]Movement[/bold cyan]
  hjkl          ← ↓ ↑ →  (also arrow keys)
  yubn          diagonals (upleft / upright / downleft / downright)
  <space>       rest / acknowledge

[bold cyan]In-game[/bold cyan]
  i             inventory
  ?             Brogue's built-in help
  z / s         apply item / search
  S / L         save / load (Brogue-native dialogs)
  Q             quit (Brogue will confirm)

[bold cyan]Shell-only shortcuts[/bold cyan]
  ctrl+q        quit the Textual app (does NOT save)
  ctrl+c        emergency quit
  ?             open this help (outside of any Brogue dialog)
  escape        close this dialog / dismiss modal

[bold cyan]Mouse[/bold cyan]
  left-click    mouse-down + mouse-up at the cell (Brogue's pathing)
  right-click   RIGHT_MOUSE_DOWN + RIGHT_MOUSE_UP (peek at tile)

[bold cyan]CLI flags[/bold cyan]
  --seed N      pre-seed the main menu's "New Seeded Game" prompt
  --wizard      launch in wizard mode (debug helpers, no scores)
  --agent PORT  start the REST API on localhost:PORT

Press [bold]escape[/bold] to return to the game.
"""


class HelpScreen(ModalScreen):
    """Shows shell-level help — NOT Brogue's in-game help."""

    BINDINGS = [
        # Non-conflicting with priority app bindings — escape is the
        # universal "close modal" key in Textual, we keep that.
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("q", "dismiss", "Close", priority=True),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > Container {
        width: 80;
        height: 30;
        padding: 1 2;
        border: thick cyan;
        background: $surface;
    }
    HelpScreen Static {
        width: 100%;
        height: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(HELP_TEXT, id="help-body")
