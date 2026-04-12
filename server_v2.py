"""server_v2.py — local aiohttp CAPTCHA-solve server for Queue Farmer v2.

Endpoints (all on 127.0.0.1:<port>):
    GET  /health   → 200 {"status": "ok"}
    POST /captcha  → receive captcha token from browser (bridge.js)
    POST /state    → receive queue-state heartbeat from injector.js
    POST /ping     → receive URL/state ping from injector.js

The server stores received captcha tokens in memory so that other
components can query them.  Extend the /captcha handler to integrate
with an external solving service if needed.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any

from aiohttp import web

# ─── In-memory token store ────────────────────────────────────────
# Deque of {"slot_id", "token", "ts"} dicts (most-recent last).
_captcha_queue: deque[dict[str, Any]] = deque(maxlen=500)
_state_log:     deque[dict[str, Any]] = deque(maxlen=1000)
_ping_log:      deque[dict[str, Any]] = deque(maxlen=500)

# Optional: API key stored for solve integrations
_api_key: str = ""


# ─── Handlers ────────────────────────────────────────────────────
async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "ts": time.time()})


async def _captcha(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    slot_id = int(data.get("slot_id", -1))
    token   = str(data.get("token", ""))

    entry = {"slot_id": slot_id, "token": token, "ts": time.time()}
    _captcha_queue.append(entry)

    print(
        f"{time.strftime('%H:%M:%S')} [SERVER] /captcha  slot={slot_id}  "
        f"token_len={len(token)}",
        flush=True,
    )
    return web.json_response({"status": "received", "slot_id": slot_id})


async def _state(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    slot_id = int(data.get("slot_id", -1))
    state   = str(data.get("state", ""))
    info    = data.get("info", {})

    _state_log.append({"slot_id": slot_id, "state": state, "info": info, "ts": time.time()})
    return web.json_response({"status": "ok"})


async def _ping(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    _ping_log.append({
        "slot_id": int(data.get("slot_id", -1)),
        "url":     str(data.get("url", ""))[:120],
        "state":   str(data.get("state", "")),
        "ts":      time.time(),
    })
    return web.json_response({"status": "pong"})


# ─── App factory ─────────────────────────────────────────────────
def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health",  _health)
    app.router.add_post("/captcha", _captcha)
    app.router.add_post("/state",   _state)
    app.router.add_post("/ping",    _ping)
    return app


# ─── Public entry point ───────────────────────────────────────────
async def run_server(api_key: str, port: int) -> tuple[web.AppRunner, int]:
    """Start the solve server.  Returns ``(runner, actual_port)``.

    If ``port`` is already in use, the next port is tried automatically.
    Call ``await runner.cleanup()`` on shutdown.
    """
    global _api_key
    _api_key = api_key

    app = _build_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    # Try requested port, fall back to port+1 if occupied
    for attempt_port in (port, port + 1, port + 2):
        try:
            site = web.TCPSite(runner, "127.0.0.1", attempt_port)
            await site.start()
            print(
                f"{time.strftime('%H:%M:%S')} [SERVER] Listening on "
                f"http://127.0.0.1:{attempt_port}",
                flush=True,
            )
            return runner, attempt_port
        except OSError:
            continue

    raise RuntimeError(f"Could not bind solve server on ports {port}–{port + 2}")
