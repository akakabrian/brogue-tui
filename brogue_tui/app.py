"""Textual application — re-shell over BrogueCE.

Brogue already paints its own 100×34 grid (sidebar cols 0-19, message area
rows 0-2, dungeon map cols 21-99 rows 3-31, status bar row 32-33). Our
Textual app mirrors that grid from the engine's plotChar callbacks into a
single MapView widget, forwards Textual keys / mouse events back into the
engine's event queue, and layers optional side panels on top.

Stage 3 keeps things minimal: one big map pane + a small footer. Later
stages add a message-log pane (spill area for messages that scroll off
Brogue's 3-line message region) and an inventory overlay.
"""

from __future__ import annotations

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.geometry import Region, Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Footer, Header, Static

from .engine import (
    BrogueEngine,
    Cell,
    ESCAPE_KEY,
    RETURN_KEY,
    TAB_KEY,
    DELETE_KEY,
)
from .screens import HelpScreen


# Brogue RGB components are 0..100; Textual / Rich want 0..255. Clamp + scale.
def _rgb(r: int, g: int, b: int) -> tuple[int, int, int]:
    def c(v: int) -> int:
        if v < 0:
            return 0
        if v > 100:
            return 255
        return (v * 255) // 100
    return (c(r), c(g), c(b))


# Key translation: Textual event.key strings → Brogue key codepoints.
# Brogue's main menu, dungeon commands, and text prompts all go through
# the same `KEYSTROKE` event type, so the table is simple and uniform.
_KEY_MAP: dict[str, int] = {
    "enter":       RETURN_KEY,
    "return":      RETURN_KEY,
    "escape":      ESCAPE_KEY,
    "tab":         TAB_KEY,
    "backspace":   DELETE_KEY,
    "delete":      DELETE_KEY,
    "space":       ord(" "),
    # Arrow keys → Brogue's vi-style direction keys (the engine accepts
    # both, but vi keys are the canonical internal codes).
    "up":          ord("k"),
    "down":        ord("j"),
    "left":        ord("h"),
    "right":       ord("l"),
}


class MapView(ScrollView):
    """Renders the engine grid via `render_line`.

    ScrollView + render_line means Textual only calls back for visible
    rows, which is a small win on this 34-row grid but keeps parity with
    the simcity-tui pattern where it's a big win on a 120×100 world.

    The widget polls the engine's serial counter on a timer; if it
    bumped since the last redraw, we issue a full `refresh()`. Per-row
    dirty-tracking isn't worth the complexity here — Brogue's typical
    plotChar burst updates dozens of cells at a time and we're well
    under 10 ms per full repaint even on a cheap VPS.
    """

    DEFAULT_CSS = """
    MapView {
        background: #000;
        color: #fff;
    }
    """

    def __init__(self, engine: BrogueEngine, **kw) -> None:
        super().__init__(**kw)
        self.engine = engine
        self._last_serial = -1
        # Cache for a blank Style — the common "no content yet" cell.
        self._blank = Style(color="rgb(200,200,200)", bgcolor="rgb(0,0,0)")
        # Virtual size is fixed to the engine grid.
        self.virtual_size = Size(engine.cols, engine.rows)
        # Style cache keyed on (fg_rgb_tuple, bg_rgb_tuple). Brogue's
        # palette is effectively continuous in 0..100 per channel, but
        # the flame-anim + menu screen only explores ~200 distinct
        # (fg,bg) pairs, and an in-game dungeon is narrower still — so
        # this cache has huge hit rate after the first second.
        self._style_cache: dict[tuple[tuple[int, int, int],
                                      tuple[int, int, int]], Style] = {}

    def on_mount(self) -> None:
        # Poll the engine serial at 30 Hz. If it's bumped, repaint.
        self.set_interval(1 / 30, self._maybe_refresh)

    def _maybe_refresh(self) -> None:
        s = self.engine.serial
        if s != self._last_serial:
            self._last_serial = s
            self.refresh()

    def render_line(self, y: int) -> Strip:
        scroll_y = int(self.scroll_offset.y)
        world_y = y + scroll_y
        engine = self.engine
        if world_y < 0 or world_y >= engine.rows:
            return Strip.blank(self.size.width, self._blank)

        # Grab just the row we need — the snapshot path copies the whole
        # grid, which is a 34× penalty here. Pull row-by-row from the
        # live grid under the lock; cells are cheap to copy individually.
        row_cells: list[Cell] = []
        with engine._grid_lock:  # friendly access — single-process module
            row = engine._grid[world_y]
            for c in row:
                row_cells.append(Cell(c.glyph, c.fr, c.fg, c.fb,
                                      c.br, c.bg, c.bb))

        segments: list[Segment] = []
        # Runs of identical style compress into a single Segment — helps
        # Brogue's long status rows where colour barely changes.
        cur_style: Style | None = None
        cur_text: list[str] = []
        cache = self._style_cache
        for cell in row_cells:
            fg = _rgb(cell.fr, cell.fg, cell.fb)
            bg = _rgb(cell.br, cell.bg, cell.bb)
            key = (fg, bg)
            style = cache.get(key)
            if style is None:
                style = Style(
                    color=Color.from_rgb(fg[0], fg[1], fg[2]),
                    bgcolor=Color.from_rgb(bg[0], bg[1], bg[2]),
                )
                cache[key] = style
            # Translate Brogue's codepoint to a character. Codepoints
            # outside BMP should never appear (Brogue uses a small glyph
            # table), but be defensive.
            try:
                ch = chr(cell.glyph) if cell.glyph else " "
            except (ValueError, OverflowError):
                ch = "?"
            if style is cur_style:
                cur_text.append(ch)
            else:
                if cur_style is not None:
                    segments.append(Segment("".join(cur_text), cur_style))
                cur_style = style
                cur_text = [ch]
        if cur_style is not None:
            segments.append(Segment("".join(cur_text), cur_style))

        strip = Strip(segments)
        # Crop to the visible viewport in case the terminal is narrower
        # than 100 cols — ScrollView normally handles this but we pass
        # through so small terminals still render something usable.
        scroll_x = int(self.scroll_offset.x)
        return strip.crop(scroll_x, scroll_x + self.size.width)


class Sidebar(Static):
    """Engine-state sidebar — Textual-side chrome Brogue doesn't have.

    Brogue paints its own sidebar (cols 0-19 of the grid) with HP /
    status / conditions. The panel here is orthogonal: debug + session
    info (seed, plot serial, game-over state) that's useful during
    development and when driving the engine via the agent API. We
    deliberately don't duplicate Brogue's in-game stats — they're
    already on the main grid."""

    DEFAULT_CSS = """
    Sidebar {
        width: 28;
        height: 100%;
        padding: 1;
        border: round $primary;
        background: $surface;
    }
    """

    def __init__(self, engine: BrogueEngine, **kw) -> None:
        super().__init__("", **kw)
        self.engine = engine

    def on_mount(self) -> None:
        self.border_title = "session"
        self.set_interval(0.5, self._refresh_panel)
        self._refresh_panel()

    def _refresh_panel(self) -> None:
        e = self.engine
        lines = [
            "[bold cyan]brogue-tui[/bold cyan]",
            "",
            f"[dim]seed[/dim]       {e.seed}",
            f"[dim]depth[/dim]      {e.depth_level}",
            f"[dim]deepest[/dim]    {e.deepest_level}",
            f"[dim]gold[/dim]       {e.gold}",
            "",
            f"[dim]serial[/dim]     {e.serial}",
            f"[dim]running[/dim]    {'yes' if e.is_running() else 'no'}",
        ]
        if e.game_over:
            lines.append("")
            reason = e.game_over_reason or "(ended)"
            lines.append(f"[bold red]game over[/bold red]: {reason[:20]}")
        lines.append("")
        lines.append("[dim]ctrl+h[/dim] help")
        lines.append("[dim]ctrl+q[/dim] quit shell")
        self.update("\n".join(lines))


class BrogueApp(App):
    """Main Textual app.

    Key contract: the app owns **one** engine instance, started in
    `on_mount`. Every keystroke / mouse click gets packaged as a
    RogueEvent and pushed onto the engine's event queue. The engine's
    worker thread wakes up, processes it, and emits plotChar calls that
    the MapView reflects at its next 30 Hz tick.

    Priority bindings: we don't use `priority=True` here because the
    MapView isn't focusable in a way that steals keys — but we mark
    Escape and Ctrl+C as app-level so quit always works even if a modal
    screen is open later."""

    CSS_PATH = "tui.tcss"

    BINDINGS = [
        # App-level quit — belt and suspenders over Brogue's own "press Q
        # twice to quit". Users expect Ctrl+C to bail.
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        Binding("ctrl+q", "quit", "Quit", show=True),
        # ctrl+h is the shell-level help. Plain '?' is Brogue's, so we
        # don't steal it — the chord is deliberately out of the way.
        Binding("ctrl+h", "show_help", "Shell Help", show=True),
    ]

    TITLE = "brogue-tui"
    SUB_TITLE = "Textual re-shell over BrogueCE"

    def __init__(self, *, seed: int = 0, wizard: bool = False,
                 agent_port: int | None = None) -> None:
        super().__init__()
        self.engine = BrogueEngine(
            seed=seed, wizard=wizard, start_new_game=False,
        )
        self.agent_port = agent_port
        self._agent_runner = None

    # --- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield MapView(self.engine, id="map")
            yield Sidebar(self.engine, id="sidebar")
        yield Footer()

    # --- lifecycle ---------------------------------------------------------

    def on_mount(self) -> None:
        self.engine.start()
        # Focus the map so arrow keys go through on_key rather than into
        # any default footer scroll logic.
        map_view = self.query_one("#map", MapView)
        map_view.focus()
        if self.agent_port is not None:
            # Start the agent API on the Textual asyncio loop. Failure
            # to bind (port in use etc.) is logged to the Footer but
            # doesn't prevent the game from running.
            self.run_worker(self._start_agent(), exclusive=True)

    async def _start_agent(self) -> None:
        from . import agent_api
        assert self.agent_port is not None  # guarded by on_mount
        port = self.agent_port
        try:
            self._agent_runner, _site = await agent_api.serve(
                self.engine, port=port,
            )
            self.notify(f"agent API listening on 127.0.0.1:{port}")
        except OSError as e:
            self.notify(f"agent API failed to bind: {e}", severity="warning")

    def on_unmount(self) -> None:
        # Tell Brogue to quit cleanly; worker thread exits and the lib
        # is released when Python GC gets to it.
        try:
            self.engine.stop(timeout=1.5)
        except Exception:
            pass
        if self._agent_runner is not None:
            try:
                self.run_worker(self._agent_runner.cleanup(), exclusive=True)
            except Exception:
                pass

    def action_show_help(self) -> None:
        """Ctrl+H — open the shell help modal."""
        self.push_screen(HelpScreen())

    # --- input forwarding --------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        # Short-circuit the app-level quit bindings — let Textual handle them.
        if event.key in ("ctrl+c", "ctrl+q"):
            return

        code: int | None = None
        ctrl = False
        shift = False

        if event.key in _KEY_MAP:
            code = _KEY_MAP[event.key]
        elif event.character and len(event.character) == 1:
            ch = event.character
            code = ord(ch)
            shift = ch.isupper() and ch.isalpha()
        else:
            # Unmapped — drop silently. Brogue's vocabulary is narrow.
            return

        self.engine.post_key(code, ctrl=ctrl, shift=shift)
        event.stop()

    def on_click(self, event: events.Click) -> None:
        # Mouse click on the map → Brogue mouse event. event.x / event.y
        # are *screen* coordinates; compute cell-relative via the widget
        # the user actually clicked. ScrollView adds scroll offset back
        # for us via `get_widget_at`, so we keep the math trivial.
        try:
            widget, _ = self.get_widget_at(event.x, event.y)
        except Exception:
            return
        if not isinstance(widget, MapView):
            return
        # Translate to widget-relative coords, then add scroll to get
        # engine-grid coords.
        region = widget.region
        x = event.x - region.x + int(widget.scroll_offset.x)
        y = event.y - region.y + int(widget.scroll_offset.y)
        if 0 <= x < self.engine.cols and 0 <= y < self.engine.rows:
            right = event.button == 3
            self.engine.post_mouse_down(x, y, right=right)
            self.engine.post_mouse_up(x, y, right=right)
            event.stop()


def run(*, seed: int = 0, wizard: bool = False,
        agent_port: int | None = None) -> None:
    """Entry point. `brogue.py` calls this."""
    BrogueApp(seed=seed, wizard=wizard, agent_port=agent_port).run()
