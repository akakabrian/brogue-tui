"""Agent REST API smoke tests.

Boots a BrogueApp with --agent on a free port, hits each endpoint, and
asserts the response shape. Stays within one process because aiohttp
and the engine share the Textual loop.

Run with `make test-api` or `.venv/bin/python -m tests.api_qa`.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp  # noqa: E402

from brogue_tui.agent_api import build_app  # noqa: E402
from brogue_tui.engine import BrogueEngine  # noqa: E402


def _free_port() -> int:
    # Ask the OS for any free port — avoids collisions with the
    # taro-local services we already run (8765 / 8766 / 8767 etc.).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def run_all() -> int:
    from aiohttp import web

    engine = BrogueEngine(seed=0, start_new_game=False)
    engine.start()
    # Give Brogue a moment to populate the grid.
    await asyncio.sleep(0.5)

    app = build_app(engine)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, host="127.0.0.1", port=port)
    await site.start()
    base = f"http://127.0.0.1:{port}"

    passed = 0
    total = 0
    errors: list[str] = []

    async def check(name: str, coro):
        nonlocal passed, total
        total += 1
        try:
            await coro
            print(f"  PASS — {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL — {name}: {e}")
            errors.append(f"{name}: {e}")
        except Exception as e:
            print(f"  ERROR — {name}: {type(e).__name__}: {e}")
            errors.append(f"{name}: {type(e).__name__}: {e}")

    async with aiohttp.ClientSession() as sess:

        async def t_health():
            async with sess.get(f"{base}/health") as r:
                assert r.status == 200
                body = await r.json()
                assert body["ok"] is True
                assert "serial" in body
        await check("/health", t_health())

        async def t_state():
            async with sess.get(f"{base}/state") as r:
                body = await r.json()
                for k in ("running", "serial", "depth_level", "gold",
                          "seed", "cols", "rows", "game_over"):
                    assert k in body, f"state missing {k}"
                assert body["cols"] == 100 and body["rows"] == 34
        await check("/state shape", t_state())

        async def t_snapshot_text():
            async with sess.get(f"{base}/snapshot?format=text") as r:
                body = await r.json()
                assert "rows" in body
                assert len(body["rows"]) == 34
                assert all(len(row) == 100 for row in body["rows"])
        await check("/snapshot text", t_snapshot_text())

        async def t_snapshot_rgb():
            async with sess.get(f"{base}/snapshot?format=rgb") as r:
                body = await r.json()
                assert len(body["rows"]) == 34
                # Each cell is a 7-tuple [glyph, fr, fg, fb, br, bg, bb].
                assert all(len(cell) == 7
                           for row in body["rows"] for cell in row)
        await check("/snapshot rgb", t_snapshot_rgb())

        async def t_post_key():
            before = engine.serial
            async with sess.post(f"{base}/key", json={"key": "n"}) as r:
                assert r.status == 200
                body = await r.json()
                assert body["ok"] is True
            # 'n' on the title menu kicks a new game → many plots.
            # Allow up to 3 s for the dungeon to render.
            for _ in range(30):
                await asyncio.sleep(0.1)
                if engine.serial > before + 500:
                    break
            assert engine.serial > before, "serial didn't advance after /key"
        await check("/key (letter)", t_post_key())

        async def t_post_key_named():
            async with sess.post(f"{base}/key",
                                 json={"key": "escape"}) as r:
                assert r.status == 200
                body = await r.json()
                assert body["code"] == 27
        await check("/key (named)", t_post_key_named())

        async def t_post_click():
            async with sess.post(f"{base}/click",
                                 json={"x": 10, "y": 5}) as r:
                assert r.status == 200
                body = await r.json()
                assert body["x"] == 10 and body["y"] == 5
        await check("/click", t_post_click())

        async def t_bad_key():
            async with sess.post(f"{base}/key",
                                 json={"key": "not_a_key"}) as r:
                assert r.status == 400
        await check("/key invalid", t_bad_key())

    await runner.cleanup()
    engine.stop(timeout=2.0)

    print()
    print(f"== api_qa: {passed}/{total} passed ==")
    for e in errors:
        print(f"   FAIL {e}")
    return 0 if passed == total else 1


def main() -> int:
    return asyncio.run(run_all())


if __name__ == "__main__":
    sys.exit(main())
