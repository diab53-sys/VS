"""bot.py — core browser automation helpers shared by queue_farmer.py."""

import asyncio
import os
import time

# ─── Paths ────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR    = os.path.join(SCRIPT_DIR, "profiles")
INJECTOR_EXT_DIR = os.path.join(SCRIPT_DIR, "injector_ext")

# ─── Load .env at import time ────────────────────────────────────
# This ensures EMAIL_PASSWORD, ANTHROPIC_API_KEY etc. are always
# available regardless of how the bot is started (bat, PowerShell, etc.)
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(SCRIPT_DIR, ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
    except ImportError:
        pass

_load_dotenv()

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
async def _solve_datadome_interstitial(page, slot_id: int, server_port: int = 9099):
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

            # ── Check if solve server already has a cookie ─────────
            try:
                import aiohttp as _aiohttp
                async with _aiohttp.ClientSession() as _sess:
                    _r = await _sess.get(
                        f"http://127.0.0.1:{server_port}/solution/{slot_id}",
                        timeout=_aiohttp.ClientTimeout(total=3),
                    )
                    _sol = await _r.json()
                    if _sol.get("status") == "ready":
                        raw = _sol.get("cookie", "")
                        if raw:
                            # Parse: may be "datadome=VALUE; ..." or plain VALUE
                            if "datadome=" in raw.lower():
                                cname = "datadome"
                                cval = raw.lower().split("datadome=")[1].split(";")[0].strip()
                            else:
                                cname = "datadome"
                                cval = raw.split(";")[0].strip()
                            from config import TARGET_URL
                            from urllib.parse import urlparse as _up
                            _host = _up(TARGET_URL).hostname or "tickets.fifa.com"
                            for _dom in (f".{_host}", ".tickets.fifa.com"):
                                try:
                                    await page.context.add_cookies([{
                                        "name": cname, "value": cval,
                                        "domain": _dom, "path": "/",
                                        "secure": True, "httpOnly": True,
                                        "sameSite": "Strict",
                                    }])
                                except Exception:
                                    pass
                            _log(f"Slot #{slot_id}: DataDome cookie applied — navigating back")
                            try:
                                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
                            except Exception:
                                pass
                            continue
            except Exception:
                pass

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


# ─── OTP / verification state tracking ──────────────────────────
_otp_waiting:        set[int]        = set()   # slots currently running _auto_fill_otp
_last_otp_attempt:   dict[int, float] = {}     # slot_id -> timestamp of last OTP submit
_last_login_attempt: dict[int, float] = {}     # slot_id -> timestamp of last login submit

# Selectors that indicate FIFA is waiting for an OTP / verification code
_OTP_SELECTORS: tuple[str, ...] = (
    'input[name*="otp" i]',
    'input[name*="code" i]',
    'input[autocomplete="one-time-code"]',
    'input[placeholder*="code" i]',
    'input[placeholder*="verification" i]',
    'input[maxlength="6"][type="text"]',
    'input[maxlength="6"][type="number"]',
    'input[type="tel"][maxlength="6"]',
)

# How long to wait before re-submitting credentials (avoid hammering FIFA auth)
_LOGIN_COOLDOWN_S: float = 60.0


# ─── Auto-login monitor ───────────────────────────────────────────
async def auto_login_monitor():
    """
    Long-running background task.
    Watches every page in _pages; when it detects the FIFA auth/login
    page it automatically fills credentials and submits.

    Improvements:
        - Detects OTP / verification-code fields and pauses (no re-submit)
        - 60-second cooldown between credential submissions per slot
        - Resets tracking when the slot leaves the login page
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
                        _otp_waiting.discard(slot_id)
                        _last_login_attempt.pop(slot_id, None)
                        continue

                    url = page.url or ""
                    on_login = "auth.fifa.com" in url or "social-login" in url

                    if not on_login:
                        # Left the login page — reset state
                        _otp_waiting.discard(slot_id)
                        _last_login_attempt.pop(slot_id, None)
                        _last_otp_attempt.pop(slot_id, None)
                        continue

                    # Resolve account early — needed by both OTP and login paths
                    account = _get_account(slot_id)

                    # ── Check for OTP / verification-code field ────
                    otp_found = False
                    for sel in _OTP_SELECTORS:
                        try:
                            el = await page.query_selector(sel)
                            if el:
                                otp_found = True
                                break
                        except Exception:
                            pass

                    if otp_found:
                        # ── Detect "Invalid Code" — clear state and resend ──
                        try:
                            err_text = await page.evaluate(
                                "document.body.innerText"
                            )
                            if "invalid code" in err_text.lower() or "invalid" in err_text.lower():
                                if slot_id in _otp_waiting or _last_otp_attempt.get(slot_id, 0) > 0:
                                    _log(f"Slot #{slot_id}: Invalid Code detected — requesting resend")
                                    _otp_waiting.discard(slot_id)
                                    _last_otp_attempt.pop(slot_id, None)
                                    # Click "Resend Sign-In Code" button
                                    for resend_sel in (
                                        'button:has-text("Resend")',
                                        'button:has-text("resend")',
                                        'a:has-text("Resend")',
                                        '[class*="resend"]',
                                    ):
                                        try:
                                            resend_el = await page.query_selector(resend_sel)
                                            if resend_el and await resend_el.is_visible():
                                                await resend_el.click()
                                                _log(f"Slot #{slot_id}: Clicked Resend — waiting for new OTP")
                                                break
                                        except Exception:
                                            pass
                        except Exception:
                            pass

                        if slot_id not in _otp_waiting:
                            # Cooldown: don't re-attempt OTP within 45 s of last submit
                            since_last = time.time() - _last_otp_attempt.get(slot_id, 0)
                            if since_last < 45.0:
                                continue
                            _otp_waiting.add(slot_id)
                            _log(f"Slot #{slot_id}: OTP field detected — fetching from email")
                            asyncio.create_task(
                                _auto_fill_otp(page, slot_id, account)
                            )
                        continue   # Do NOT re-submit the login form

                    # ── Cooldown: avoid re-submitting too fast ─────
                    now = time.time()
                    if now - _last_login_attempt.get(slot_id, 0) < _LOGIN_COOLDOWN_S:
                        continue

                    # ── Fill credentials and submit ────────────────
                    _log(f"Slot #{slot_id}: login page — filling {account}")

                    email_sel = 'input[type="email"], input[name="email"], input[id*="email"]'
                    try:
                        await page.wait_for_selector(email_sel, timeout=5000)
                        await page.fill(email_sel, account)
                    except Exception:
                        pass

                    try:
                        await page.wait_for_selector('input[type="password"]', timeout=5000)
                        await page.fill('input[type="password"]', FIFA_PASSWORD)
                    except Exception:
                        pass

                    try:
                        submit_sel = 'button[type="submit"], input[type="submit"]'
                        try:
                            await page.click(submit_sel, timeout=3000)
                        except Exception:
                            await page.keyboard.press("Enter")
                    except Exception:
                        pass

                    _last_login_attempt[slot_id] = time.time()

                except Exception as e:
                    _log(f"Slot #{slot_id}: login monitor error: {str(e)[:80]}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            _log(f"auto_login_monitor error: {str(e)[:80]}")
            await asyncio.sleep(5)

    _log("auto_login_monitor: exited")


# ─── Auto OTP filler ─────────────────────────────────────────────
async def _auto_fill_otp(page, slot_id: int, account: str) -> None:
    """Fetch OTP from Titan Email and type it into the browser."""
    submitted = False
    try:
        from email_reader import fetch_otp

        email_password = os.environ.get("EMAIL_PASSWORD", FIFA_PASSWORD)
        _log(f"Slot #{slot_id}: fetching OTP for {account} via IMAP...")

        otp = await fetch_otp(account, email_password, timeout_s=90)

        if not otp:
            _log(f"Slot #{slot_id}: OTP not found in email — manual solve needed")
            _otp_waiting.discard(slot_id)
            return

        _log(f"Slot #{slot_id}: OTP {otp} retrieved — filling into browser")

        if page.is_closed():
            _otp_waiting.discard(slot_id)
            return

        # ── Try individual digit boxes first (FIFA uses 6×maxlength=1) ──
        try:
            digit_boxes = await page.query_selector_all(
                'input[maxlength="1"]'
            )
            # Filter to only visible, enabled boxes
            visible_boxes = []
            for box in digit_boxes:
                try:
                    if await box.is_visible() and await box.is_enabled():
                        visible_boxes.append(box)
                except Exception:
                    pass
            if len(visible_boxes) >= 6:
                for i, box in enumerate(visible_boxes[:6]):
                    await box.click()
                    await box.type(otp[i], delay=80)
                await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
                _log(f"Slot #{slot_id}: OTP submitted via 6 digit boxes")
                submitted = True
        except Exception:
            pass

        # ── Fallback: single input field ──────────────────────────
        if not submitted:
            for sel in _OTP_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await page.triple_click(sel)
                        await page.fill(sel, otp)
                        await asyncio.sleep(0.3)
                        # Try clicking the submit/sign-in button first
                        btn_clicked = False
                        for btn_sel in (
                            'button:has-text("SIGN IN")',
                            'button:has-text("Sign In")',
                            'button:has-text("Verify")',
                            'button:has-text("Submit")',
                            'button[type="submit"]',
                            'input[type="submit"]',
                        ):
                            try:
                                btn = await page.query_selector(btn_sel)
                                if btn and await btn.is_visible():
                                    await btn.click()
                                    btn_clicked = True
                                    break
                            except Exception:
                                pass
                        if not btn_clicked:
                            await page.keyboard.press("Enter")
                        _log(f"Slot #{slot_id}: OTP submitted via single field ({sel})")
                        submitted = True
                        break
                except Exception:
                    continue

        if not submitted:
            _log(f"Slot #{slot_id}: OTP field gone before fill — may have auto-submitted")

        # Track submission time; leave slot in _otp_waiting so monitor
        # won't immediately re-trigger — cleared when page navigates away.
        _last_otp_attempt[slot_id] = time.time()

    except Exception as e:
        _log(f"Slot #{slot_id}: _auto_fill_otp error: {str(e)[:120]}")
    finally:
        # Only clear _otp_waiting on failure so monitor doesn't re-trigger
        # immediately. On success, auto_login_monitor clears it when the
        # page navigates away from the login URL.
        if not submitted:
            _otp_waiting.discard(slot_id)
