"""bot.py — core browser automation helpers shared by queue_farmer.py."""

import asyncio
import os
import time

# ─── Paths ────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR    = os.path.join(SCRIPT_DIR, "profiles")
INJECTOR_EXT_DIR = os.path.join(SCRIPT_DIR, "injector_ext")

os.makedirs(PROFILES_DIR, exist_ok=True)
os.makedirs(INJECTOR_EXT_DIR, exist_ok=True)

# ─── Accounts ─────────────────────────────────────────────────────
# Add all your FIFA account email addresses here (one per line).
FIFA_ACCOUNTS: list[str] = [
    "brandon8289@harmonyofcommunity.site",
    "samantha5431@shereifandcompany.site",
    "maya4978@shereifandcompany.site",
    "aria1581@shereifandcompany.site",
    "samantha5431@shereifandcompany.site",
    "grace8673@shereifandcompany.site",
    "andrew3104@shereifandcompany.site",
    "violet5044@shereifandcompany.site",
    "jason1149@shereifandcompany.site",
    "jason7883@shereifandcompany.site",
    "natalie4475@shereifandcompany.site",
    "hazel5220@shereifandcompany.site",
    "riley3892@shereifandcompany.site",
    "hannah9632@shereifandcompany.site",
    "luna7090@shereifandcompany.site",
    "lucas3915@shereifandcompany.site",
    "christopher9298@shereifandcompany.site",
    "ryan1592@shereifandcompany.site",
    "kennedy1134@shereifandcompany.site",
    "andrew8256@shereifandcompany.site",
    "nora2119@shereifandcompany.site",
    "owen5336@shereifandcompany.site",
    "sara1905@shereifandcompany.site",
    "natalie2996@shereifandcompany.site",
    "maya0260@shereifandcompany.site",
]
FIFA_PASSWORD: str = "StrongPass123!"

# ─── Shared page registry (filled by queue_farmer) ────────────────
_pages: list        = []   # index == slot_id
_pages_context: dict = {}  # slot_id -> BrowserContext

# ─── Module-level flags set by queue_farmer ───────────────────────
_monitor_started:    bool       = False
_forced_account_idx: int | None = None
_account_offset:     dict       = {}  # slot_id -> base account index


# ─── Internal helper ──────────────────────────────────────────────
def _log(msg: str):
    print(f"{time.strftime('%H:%M:%S')} [BOT] {msg}", flush=True)


def _get_account(slot_id: int) -> str:
    if _forced_account_idx is not None:
        return FIFA_ACCOUNTS[_forced_account_idx % len(FIFA_ACCOUNTS)]
    base = _account_offset.get(slot_id, 0)
    return FIFA_ACCOUNTS[(slot_id + base) % len(FIFA_ACCOUNTS)]


# ─── Localhost route ──────────────────────────────────────────────
async def _setup_localhost_route(context):
    """
    Allow the injected JS to reach http://127.0.0.1:<port> from inside
    the browser by forwarding those fetch/XHR calls through a Playwright
    route handler (bypasses CORS / mixed-content blocks).
    """
    async def _handle(route, request):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                resp = await sess.request(
                    method  = request.method,
                    url     = request.url,
                    headers = {k: v for k, v in request.headers.items()
                               if k.lower() not in ("host", "origin", "referer")},
                    data    = request.post_data or b"",
                    timeout = aiohttp.ClientTimeout(total=10),
                )
                body = await resp.read()
                await route.fulfill(
                    status  = resp.status,
                    headers = dict(resp.headers),
                    body    = body,
                )
        except Exception:
            await route.continue_()

    try:
        await context.route("http://127.0.0.1:*/**", _handle)
    except Exception:
        pass


# ─── Captcha response listener ────────────────────────────────────
async def _setup_captcha_response_listener(page, slot_id: int, server_port: int = 9099):
    """
    Listen for captcha tokens emitted by bridge.js (via console log or
    network) and forward them to the local solve server.
    """
    import aiohttp

    async def _on_console(msg):
        try:
            text = msg.text
            if "CAPTCHA_SOLVED:" in text:
                token = text.split("CAPTCHA_SOLVED:", 1)[1].strip()
                _log(f"Slot #{slot_id}: captcha token received — forwarding")
                try:
                    async with aiohttp.ClientSession() as sess:
                        await sess.post(
                            f"http://127.0.0.1:{server_port}/captcha",
                            json    = {"slot_id": slot_id, "token": token},
                            timeout = aiohttp.ClientTimeout(total=5),
                        )
                except Exception as e:
                    _log(f"Slot #{slot_id}: captcha forward error: {e}")
        except Exception:
            pass

    try:
        page.on("console", _on_console)
    except Exception:
        pass


# ─── DataDome interstitial solver ─────────────────────────────────
async def _solve_datadome_interstitial(page, slot_id: int):
    """
    Background coroutine: watch for DataDome interstitial pages and
    attempt to dismiss / solve them.  Runs until page closes.

    Strategies (tried in order):
        1. Checkbox click  (common for non-flagged IPs)
        2. Press-and-hold  (common when IP is mildly suspicious)
        3. Submit fallback (last resort)
    """
    while True:
        try:
            await asyncio.sleep(3)
            if page.is_closed():
                break
            url = page.url or ""

            is_datadome = (
                "geo.captcha-delivery.com" in url
                or "captcha-delivery.com" in url
                or "interstitial" in url
                or "datadome" in url.lower()
            )
            if not is_datadome:
                continue

            _log(f"Slot #{slot_id}: DataDome challenge detected at {url[:80]}")

            # ── Strategy 1: Checkbox ───────────────────────────────
            for sel in (
                "#captcha-checkbox",
                ".captcha__human__checkbox",
                '[data-ddm-tag="checkpoint-checkbox"]',
                'input[type="checkbox"]',
            ):
                try:
                    el = await page.wait_for_selector(sel, timeout=3000)
                    if el:
                        await el.click()
                        await asyncio.sleep(3)
                        if page.is_closed():
                            break
                        if "captcha-delivery.com" not in (page.url or ""):
                            _log(f"Slot #{slot_id}: DataDome solved via checkbox")
                            break
                except Exception:
                    continue
            else:
                pass  # no break — try next strategy

            if page.is_closed():
                break
            if "captcha-delivery.com" not in (page.url or ""):
                continue  # solved

            # ── Strategy 2: Press-and-hold button ─────────────────
            for sel in (
                ".captcha__human__btn",
                "#captcha-button",
                '[data-ddm-tag="checkpoint-press"]',
                "button",
            ):
                try:
                    el = await page.query_selector(sel)
                    if el:
                        box = await el.bounding_box()
                        if box:
                            cx = box["x"] + box["width"] / 2
                            cy = box["y"] + box["height"] / 2
                            await page.mouse.move(cx, cy)
                            await asyncio.sleep(0.3)
                            await page.mouse.down()
                            await asyncio.sleep(3.5)   # hold ~3.5 s
                            await page.mouse.up()
                            await asyncio.sleep(2)
                            if page.is_closed():
                                break
                            if "captcha-delivery.com" not in (page.url or ""):
                                _log(f"Slot #{slot_id}: DataDome solved via press-and-hold")
                                break
                except Exception:
                    continue

            if page.is_closed():
                break
            if "captcha-delivery.com" not in (page.url or ""):
                continue  # solved

            # ── Strategy 3: Submit button fallback ─────────────────
            for sel in ("#captcha-submit", 'button[type="submit"]', "button"):
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click()
                        await asyncio.sleep(2)
                        _log(f"Slot #{slot_id}: DataDome fallback submit clicked")
                        break
                except Exception:
                    continue

            # Wait before next poll to avoid hammering
            await asyncio.sleep(5)

        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)


# ─── Auto-login monitor ───────────────────────────────────────────
async def auto_login_monitor():
    """
    Long-running background task.
    Watches every page in _pages; when it detects the FIFA auth/login
    page it automatically fills credentials and submits.
    """
    _log("auto_login_monitor: started")
    while True:
        try:
            await asyncio.sleep(3)
            for slot_id, page in enumerate(_pages):
                if page is None:
                    continue
                try:
                    if page.is_closed():
                        continue
                    url = page.url or ""
                    if "auth.fifa.com" not in url and "social-login" not in url:
                        continue

                    account = _get_account(slot_id)
                    _log(f"Slot #{slot_id}: login page detected — filling {account}")

                    # Email field
                    email_sel = 'input[type="email"], input[name="email"], input[id*="email"]'
                    try:
                        await page.wait_for_selector(email_sel, timeout=5000)
                        await page.fill(email_sel, account)
                    except Exception:
                        pass

                    # Password field
                    try:
                        await page.wait_for_selector('input[type="password"]', timeout=5000)
                        await page.fill('input[type="password"]', FIFA_PASSWORD)
                    except Exception:
                        pass

                    # Submit
                    try:
                        submit_sel = 'button[type="submit"], input[type="submit"]'
                        try:
                            await page.click(submit_sel, timeout=3000)
                        except Exception:
                            await page.keyboard.press("Enter")
                    except Exception:
                        pass

                except Exception as e:
                    _log(f"Slot #{slot_id}: login monitor error: {str(e)[:80]}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            _log(f"auto_login_monitor error: {str(e)[:80]}")
            await asyncio.sleep(5)

    _log("auto_login_monitor: exited")
