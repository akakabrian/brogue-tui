"""Optional REST API for remote agents.

Runs as a background aiohttp server on the same asyncio loop as the
Textual app. Intended for LLM agent harnesses that want to play Brogue
from outside the terminal — classic dungeon-crawl RL setup, plus humans
writing scripting layers.

Design notes:
- Shares the BrogueApp's engine instance (no duplication).
- POST /key  {"key": "j"}           — post a single keystroke
- POST /key  {"key": 27}            — post a raw codepoint
- POST /click {"x": 20, "y": 5, "right": false}
- GET  /state                        — engine summary (depth, gold, seed, serial)
- GET  /snapshot?format=text|rgb     — full 100×34 grid
- GET  /health                       — liveness probe

All endpoints are JSON in / JSON out. No auth — this is a local-loopback
tool; bind to 127.0.0.1 only and trust the OS boundary.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from .engine import BrogueEngine


_SPECIAL_KEYS = {
    "up": ord("k"), "down": ord("j"), "left": ord("h"), "right": ord("l"),
    "upleft": ord("y"), "upright": ord("u"),
    "downleft": ord("b"), "downright": ord("n"),
    "escape": 27, "esc": 27,
    "return": 10, "enter": 10,
    "space": 32,
    "tab": 9,
    "backspace": 127, "delete": 127,
}


def _code_from_keyspec(spec) -> int:
    """Translate a JSON 'key' value into a Brogue codepoint.

    Accepts ints directly, single chars (e.g. "n"), and known names
    ("up", "escape", "space"). Returns -1 if nothing matched."""
    if isinstance(spec, int):
        return spec
    if not isinstance(spec, str):
        return -1
    if len(spec) == 1:
        return ord(spec)
    return _SPECIAL_KEYS.get(spec.lower(), -1)


def build_app(engine: "BrogueEngine") -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "running": engine.is_running(),
            "serial": engine.serial,
        })

    async def state(_: web.Request) -> web.Response:
        return web.json_response({
            "running": engine.is_running(),
            "serial": engine.serial,
            "depth_level": engine.depth_level,
            "deepest_level": engine.deepest_level,
            "gold": engine.gold,
            "seed": engine.seed,
            "game_over": engine.game_over,
            "game_over_reason": engine.game_over_reason,
            "cols": engine.cols,
            "rows": engine.rows,
        })

    async def snapshot(req: web.Request) -> web.Response:
        fmt = req.query.get("format", "text")
        grid, serial = engine.snapshot()
        if fmt == "text":
            lines = [
                "".join(
                    chr(c.glyph) if 32 <= c.glyph < 0x10000 else " "
                    for c in row
                )
                for row in grid
            ]
            return web.json_response({"serial": serial, "rows": lines})
        elif fmt == "rgb":
            # (glyph, fr, fg, fb, br, bg, bb) tuples per cell — not
            # human-readable but the exact render data an agent needs.
            rows = [
                [
                    [c.glyph, c.fr, c.fg, c.fb, c.br, c.bg, c.bb]
                    for c in row
                ]
                for row in grid
            ]
            return web.json_response({"serial": serial, "rows": rows})
        else:
            return web.json_response({"error": f"unknown format: {fmt}"},
                                     status=400)

    async def post_key(req: web.Request) -> web.Response:
        body = await req.json()
        code = _code_from_keyspec(body.get("key"))
        if code < 0:
            return web.json_response({"error": "invalid 'key'"}, status=400)
        ctrl = bool(body.get("ctrl", False))
        shift = bool(body.get("shift", False))
        engine.post_key(code, ctrl=ctrl, shift=shift)
        return web.json_response({"ok": True, "code": code})

    async def post_click(req: web.Request) -> web.Response:
        body = await req.json()
        try:
            x = int(body["x"])
            y = int(body["y"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "x/y required"}, status=400)
        right = bool(body.get("right", False))
        engine.post_mouse_down(x, y, right=right)
        engine.post_mouse_up(x, y, right=right)
        return web.json_response({"ok": True, "x": x, "y": y, "right": right})

    app.router.add_get("/health", health)
    app.router.add_get("/state", state)
    app.router.add_get("/snapshot", snapshot)
    app.router.add_post("/key", post_key)
    app.router.add_post("/click", post_click)
    return app


async def serve(engine: "BrogueEngine", *, host: str = "127.0.0.1",
                port: int = 8777) -> "tuple[web.AppRunner, web.TCPSite]":
    """Start the server on the current asyncio loop. Returns the
    runner + site so the caller can `await runner.cleanup()` on exit."""
    app = build_app(engine)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    return runner, site
