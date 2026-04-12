"""server_v2.py — local aiohttp CAPTCHA-solve server for Queue Farmer v2.

Endpoints (all on 127.0.0.1:<port>):
    GET  /health            → 200 {"status": "ok"}
    POST /captcha           → receive DataDome token; auto-solve if key configured
    GET  /solution/<slot>   → browser polls for solved cookie/token
    POST /state             → receive queue-state heartbeat from injector.js
    POST /ping              → receive URL/state ping from injector.js

Captcha solving priority:
    1. CapMonster  (CAPMONSTER_API_KEY in .env)
    2. 2captcha    (TWOCAPTCHA_API_KEY in .env)
    3. Log-only    (manual fallback — human must solve in browser)

Add one of these to your .env:
    CAPMONSTER_API_KEY=your-key
    TWOCAPTCHA_API_KEY=your-key
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from typing import Any

from aiohttp import web

# ─── In-memory stores ────────────────────────────────────────────
_captcha_queue: deque[dict[str, Any]] = deque(maxlen=500)
_solutions:     dict[int, str]        = {}   # slot_id -> solved_cookie
_state_log:     deque[dict[str, Any]] = deque(maxlen=1000)
_ping_log:      deque[dict[str, Any]] = deque(maxlen=500)

_api_key:          str = ""
_capmonster_key:   str = os.environ.get("CAPMONSTER_API_KEY", "")
_twocaptcha_key:   str = os.environ.get("TWOCAPTCHA_API_KEY", "")


# ─── Handlers ────────────────────────────────────────────────────
async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "ts": time.time()})


async def _captcha(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    slot_id  = int(data.get("slot_id", -1))
    token    = str(data.get("token", ""))
    page_url = str(data.get("url", ""))

    entry = {"slot_id": slot_id, "token": token, "url": page_url, "ts": time.time()}
    _captcha_queue.append(entry)

    _ts = time.strftime("%H:%M:%S")
    print(f"{_ts} [SERVER] /captcha  slot={slot_id}  token_len={len(token)}", flush=True)

    # Attempt auto-solve in background (non-blocking)
    if token and (_capmonster_key or _twocaptcha_key):
        asyncio.create_task(_auto_solve(slot_id, token, page_url))
    else:
        if not (_capmonster_key or _twocaptcha_key):
            print(
                f"{_ts} [SERVER] No solver key configured. "
                "Add CAPMONSTER_API_KEY or TWOCAPTCHA_API_KEY to .env for auto-solve.",
                flush=True,
            )

    return web.json_response({"status": "received", "slot_id": slot_id})


async def _solution(request: web.Request) -> web.Response:
    """Browser polls this endpoint to get the solved cookie for its slot."""
    slot_id = int(request.match_info.get("slot_id", -1))
    cookie  = _solutions.pop(slot_id, None)
    if cookie:
        return web.json_response({"status": "ready", "cookie": cookie})
    return web.json_response({"status": "pending"})


async def _state(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    _state_log.append({
        "slot_id": int(data.get("slot_id", -1)),
        "state":   str(data.get("state", "")),
        "info":    data.get("info", {}),
        "ts":      time.time(),
    })
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


# ─── Auto-solve logic ─────────────────────────────────────────────
async def _auto_solve(slot_id: int, token: str, page_url: str) -> None:
    """Try to solve DataDome token via CapMonster or 2captcha."""
    import aiohttp as _aiohttp

    website_url = page_url or "https://geo.captcha-delivery.com/captcha/"

    # ── CapMonster ────────────────────────────────────────────────
    if _capmonster_key:
        try:
            async with _aiohttp.ClientSession() as sess:
                # Create task
                create = await sess.post(
                    "https://api.capmonster.cloud/createTask",
                    json={
                        "clientKey": _capmonster_key,
                        "task": {
                            "type": "DataDomeSliderTask",
                            "websiteURL": website_url,
                            "captchaUrl": f"https://geo.captcha-delivery.com/captcha/?initialCid=&hash={token}",
                            "userAgent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36"
                            ),
                        },
                    },
                    timeout=_aiohttp.ClientTimeout(total=15),
                )
                create_data = await create.json()
                task_id = create_data.get("taskId")
                if not task_id:
                    raise ValueError(f"No taskId: {create_data}")

                # Poll for result (up to 120 s)
                for _ in range(24):
                    await asyncio.sleep(5)
                    result = await sess.post(
                        "https://api.capmonster.cloud/getTaskResult",
                        json={"clientKey": _capmonster_key, "taskId": task_id},
                        timeout=_aiohttp.ClientTimeout(total=10),
                    )
                    result_data = await result.json()
                    if result_data.get("status") == "ready":
                        cookie = result_data.get("solution", {}).get("cookie", "")
                        if cookie:
                            _solutions[slot_id] = cookie
                            print(
                                f"{time.strftime('%H:%M:%S')} [SERVER] "
                                f"CapMonster solved slot={slot_id}",
                                flush=True,
                            )
                            return
        except Exception as e:
            print(f"{time.strftime('%H:%M:%S')} [SERVER] CapMonster error: {e}", flush=True)

    # ── 2captcha ──────────────────────────────────────────────────
    if _twocaptcha_key:
        try:
            async with _aiohttp.ClientSession() as sess:
                submit = await sess.post(
                    "https://2captcha.com/in.php",
                    data={
                        "key":        _twocaptcha_key,
                        "method":     "datadome",
                        "captcha_url": f"https://geo.captcha-delivery.com/captcha/?initialCid=&hash={token}",
                        "pageurl":    website_url,
                        "userAgent":  (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                        "json":       1,
                    },
                    timeout=_aiohttp.ClientTimeout(total=15),
                )
                submit_data = await submit.json()
                req_id = submit_data.get("request")
                if not req_id or submit_data.get("status") == 0:
                    raise ValueError(f"Submit failed: {submit_data}")

                for _ in range(24):
                    await asyncio.sleep(5)
                    poll = await sess.get(
                        f"https://2captcha.com/res.php?key={_twocaptcha_key}"
                        f"&action=get&id={req_id}&json=1",
                        timeout=_aiohttp.ClientTimeout(total=10),
                    )
                    poll_data = await poll.json()
                    if poll_data.get("status") == 1:
                        cookie = poll_data.get("request", "")
                        if cookie:
                            _solutions[slot_id] = cookie
                            print(
                                f"{time.strftime('%H:%M:%S')} [SERVER] "
                                f"2captcha solved slot={slot_id}",
                                flush=True,
                            )
                            return
        except Exception as e:
            print(f"{time.strftime('%H:%M:%S')} [SERVER] 2captcha error: {e}", flush=True)

    print(
        f"{time.strftime('%H:%M:%S')} [SERVER] "
        f"Auto-solve failed for slot={slot_id} — manual solve required.",
        flush=True,
    )


# ─── App factory ─────────────────────────────────────────────────
def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health",             _health)
    app.router.add_post("/captcha",           _captcha)
    app.router.add_get("/solution/{slot_id}", _solution)
    app.router.add_post("/state",             _state)
    app.router.add_post("/ping",              _ping)
    return app


# ─── Public entry point ───────────────────────────────────────────
async def run_server(api_key: str, port: int) -> tuple[web.AppRunner, int]:
    """Start the solve server. Returns (runner, actual_port)."""
    global _api_key, _capmonster_key, _twocaptcha_key
    _api_key        = api_key
    _capmonster_key = os.environ.get("CAPMONSTER_API_KEY", _capmonster_key)
    _twocaptcha_key = os.environ.get("TWOCAPTCHA_API_KEY", _twocaptcha_key)

    solver = "CapMonster" if _capmonster_key else ("2captcha" if _twocaptcha_key else "NONE")
    print(f"{time.strftime('%H:%M:%S')} [SERVER] Captcha solver: {solver}", flush=True)

    app    = _build_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

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
