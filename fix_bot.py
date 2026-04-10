"""Run this once to write the correct bot.py next to it."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
content = '''"""bot.py"""
import asyncio, os, time

SCRIPT_DIR       = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR     = os.path.join(SCRIPT_DIR, "profiles")
INJECTOR_EXT_DIR = os.path.join(SCRIPT_DIR, "injector_ext")
os.makedirs(PROFILES_DIR,     exist_ok=True)
os.makedirs(INJECTOR_EXT_DIR, exist_ok=True)

FIFA_ACCOUNTS: list[str] = [
    "brandon8289@harmonyofcommunity.site",
    "samantha5431@shereifandcompany.site",
    "maya4978@shereifandcompany.site",
    "aria1581@shereifandcompany.site",
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

_pages: list         = []
_pages_context: dict = {}
_monitor_started:    bool       = False
_forced_account_idx: int | None = None
_account_offset:     dict       = {}

def _log(msg):
    print(f"{time.strftime(\'%H:%M:%S\')} [BOT] {msg}", flush=True)

def _get_account(slot_id):
    if _forced_account_idx is not None:
        return FIFA_ACCOUNTS[_forced_account_idx % len(FIFA_ACCOUNTS)]
    base = _account_offset.get(slot_id, 0)
    return FIFA_ACCOUNTS[(slot_id + base) % len(FIFA_ACCOUNTS)]

async def _setup_localhost_route(context):
    async def _handle(route, request):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                resp = await sess.request(
                    method=request.method, url=request.url,
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() not in ("host","origin","referer")},
                    data=request.post_data or b"",
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                body = await resp.read()
                await route.fulfill(status=resp.status, headers=dict(resp.headers), body=body)
        except Exception:
            await route.continue_()
    try:
        await context.route("http://127.0.0.1:*/**", _handle)
    except Exception:
        pass

async def _setup_captcha_response_listener(page, slot_id, server_port=9099):
    import aiohttp
    async def _on_console(msg):
        try:
            text = msg.text
            if "CAPTCHA_SOLVED:" in text:
                token = text.split("CAPTCHA_SOLVED:", 1)[1].strip()
                _log(f"Slot #{slot_id}: captcha token — forwarding")
                try:
                    async with aiohttp.ClientSession() as sess:
                        await sess.post(f"http://127.0.0.1:{server_port}/captcha",
                                        json={"slot_id": slot_id, "token": token},
                                        timeout=aiohttp.ClientTimeout(total=5))
                except Exception as e:
                    _log(f"Slot #{slot_id}: forward error: {e}")
        except Exception:
            pass
    try:
        page.on("console", _on_console)
    except Exception:
        pass

async def _solve_datadome_interstitial(page, slot_id):
    while True:
        try:
            await asyncio.sleep(3)
            if page.is_closed():
                break
            url = page.url or ""
            if "interstitial" in url or "datadome" in url.lower():
                _log(f"Slot #{slot_id}: DataDome interstitial — click-through")
                try:
                    await page.wait_for_selector("button", timeout=5000)
                    await page.click("button")
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)

async def auto_login_monitor():
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
                    _log(f"Slot #{slot_id}: login — filling {account}")
                    email_sel = \'input[type="email"], input[name="email"], input[id*="email"]\'
                    try:
                        await page.wait_for_selector(email_sel, timeout=5000)
                        await page.fill(email_sel, account)
                    except Exception:
                        pass
                    try:
                        await page.wait_for_selector(\'input[type="password"]\', timeout=5000)
                        await page.fill(\'input[type="password"]\', FIFA_PASSWORD)
                    except Exception:
                        pass
                    try:
                        try:
                            await page.click(\'button[type="submit"], input[type="submit"]\', timeout=3000)
                        except Exception:
                            await page.keyboard.press("Enter")
                    except Exception:
                        pass
                except Exception as e:
                    _log(f"Slot #{slot_id}: login error: {str(e)[:80]}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            _log(f"auto_login_monitor error: {str(e)[:80]}")
            await asyncio.sleep(5)
    _log("auto_login_monitor: exited")
'''
out = os.path.join(HERE, "bot.py")
with open(out, "w", encoding="utf-8") as f:
    f.write(content)
print(f"Written: {out}")
