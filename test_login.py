"""test_login.py — standalone FIFA login + OTP test.

Opens one Chrome window, goes to the FIFA login page, fills credentials,
waits for the OTP field, fetches the code from IMAP, and submits it.

Run with:
    python test_login.py
"""

import asyncio
import os
import re
import time

# ── Config — edit these ──────────────────────────────────────────
EMAIL          = "brandon8289@harmonyofcommunity.site"
FIFA_PASSWORD  = "StrongPass123!"
EMAIL_PASSWORD = "StrongPass123!"   # IMAP password (same or different)
LOGIN_URL      = "https://auth.fifa.com/as/authorization.oauth2?client_id=2b27c197-da54-4279-9ba2-1a08e83beae6&response_type=code&scope=openid%20profile%20email&redirect_uri=https://fwc26-shop-usd.tickets.fifa.com/secured/content&nonce=abc"
# ─────────────────────────────────────────────────────────────────

OTP_SELECTORS = (
    'input[autocomplete="one-time-code"]',
    'input[name*="otp" i]',
    'input[name*="code" i]',
    'input[id*="otp" i]',
    'input[id*="code" i]',
    'input[placeholder*="code" i]',
    'input[placeholder*="sign" i]',
    'input[maxlength="6"][type="text"]',
    'input[maxlength="6"][type="number"]',
    'input[type="tel"][maxlength="6"]',
)


def _log(msg):
    print(f"{time.strftime('%H:%M:%S')} [TEST] {msg}", flush=True)


# ── IMAP fetch (inline, no dependency on email_reader.py) ────────
def _fetch_otp_now(email_addr, password, skip_otp=None):
    import imaplib, email as _email, datetime, email.utils as _eu
    today = datetime.date.today().strftime("%d-%b-%Y")

    with imaplib.IMAP4_SSL("imap.titan.email", 993) as mail:
        mail.login(email_addr, password)
        mail.select("INBOX")

        for criteria in [
            f'UNSEEN FROM "fifa.com" SINCE {today}',
            f'FROM "fifa.com" SINCE {today}',
            'UNSEEN FROM "fifa.com"',
        ]:
            status, msg_ids = mail.search(None, criteria)
            if status != "OK" or not msg_ids[0]:
                continue
            ids = msg_ids[0].split()
            if not ids:
                continue

            # Sort by Date header newest first
            candidates = []
            for mid in ids[-10:]:
                _, hdr = mail.fetch(mid, "(BODY[HEADER.FIELDS (DATE)])")
                ts = 0
                if hdr and hdr[0]:
                    raw = hdr[0][1]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="ignore")
                    for line in raw.splitlines():
                        if line.lower().startswith("date:"):
                            try:
                                tup = _eu.parsedate(line[5:].strip())
                                if tup:
                                    ts = time.mktime(tup)
                            except Exception:
                                pass
                candidates.append((ts, mid))
            candidates.sort(reverse=True)

            for _, mid in candidates:
                _, msg_data = mail.fetch(mid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, bytes):
                    continue
                msg = _email.message_from_bytes(raw)
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() in ("text/plain", "text/html"):
                            p = part.get_payload(decode=True)
                            if p:
                                body += p.decode("utf-8", errors="ignore")
                else:
                    p = msg.get_payload(decode=True)
                    if p:
                        body = p.decode("utf-8", errors="ignore")

                for pattern in [
                    r'(?:code|otp|pin|pass\s*code|verification)[^\d]{0,30}(\d{6})',
                    r'\b(\d{6})\b',
                ]:
                    for m in re.findall(pattern, body, re.IGNORECASE):
                        if skip_otp and m == skip_otp:
                            continue
                        mail.store(mid, "+FLAGS", "\\Seen")
                        return m
    return None


async def run():
    from patchright.async_api import async_playwright

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir="test_profile",
            channel="chrome",
            headless=False,
            no_viewport=True,
            ignore_https_errors=True,
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # ── Step 1: Go to login page ──────────────────────────────
        _log(f"Navigating to FIFA login...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        # ── Step 2: Fill email ────────────────────────────────────
        _log(f"Filling email: {EMAIL}")
        email_sel = 'input[type="email"], input[name="email"], input[id*="email"], input[id*="username"]'
        try:
            await page.wait_for_selector(email_sel, timeout=10000)
            await page.fill(email_sel, EMAIL)
        except Exception as e:
            _log(f"Email field not found: {e}")

        # ── Step 3: Fill password ─────────────────────────────────
        _log("Filling password...")
        try:
            await page.wait_for_selector('input[type="password"]', timeout=10000)
            await page.fill('input[type="password"]', FIFA_PASSWORD)
        except Exception as e:
            _log(f"Password field not found: {e}")

        # ── Step 4: Submit ────────────────────────────────────────
        _log("Submitting login form...")
        try:
            submit_sel = 'button[type="submit"], input[type="submit"]'
            await page.click(submit_sel, timeout=5000)
        except Exception:
            await page.keyboard.press("Enter")
        await asyncio.sleep(3)

        # ── Step 5: Wait for OTP field (up to 30s) ───────────────
        _log("Waiting for OTP field...")
        otp_sel_found = None
        for _ in range(15):
            for sel in OTP_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        otp_sel_found = sel
                        break
                except Exception:
                    pass
            # Also check digit boxes
            if not otp_sel_found:
                try:
                    boxes = [b for b in await page.query_selector_all('input[maxlength="1"]')
                             if await b.is_visible()]
                    if len(boxes) >= 6:
                        otp_sel_found = "DIGIT_BOXES"
                        break
                except Exception:
                    pass
            if otp_sel_found:
                break
            await asyncio.sleep(2)

        if not otp_sel_found:
            _log("OTP field not found — current URL: " + page.url)
            _log("Keeping browser open for 60s so you can inspect...")
            await asyncio.sleep(60)
            await context.close()
            return

        _log(f"OTP field detected ({otp_sel_found}) — fetching from IMAP...")

        # ── Step 6: Fetch OTP from email ─────────────────────────
        skip = None
        otp = None
        for attempt in range(1, 6):
            _log(f"IMAP fetch attempt {attempt}/5" + (f" (skipping {skip})" if skip else ""))
            try:
                loop = asyncio.get_event_loop()
                otp = await loop.run_in_executor(
                    None, _fetch_otp_now, EMAIL, EMAIL_PASSWORD, skip
                )
            except Exception as e:
                _log(f"IMAP error: {e}")
            if otp:
                _log(f"OTP retrieved: {otp}")
                break
            _log("OTP not found yet — waiting 5s...")
            await asyncio.sleep(5)

        if not otp:
            _log("Could not get OTP from email. Check EMAIL_PASSWORD and inbox.")
            await asyncio.sleep(60)
            await context.close()
            return

        # ── Step 7: Fill OTP ──────────────────────────────────────
        if otp_sel_found == "DIGIT_BOXES":
            boxes = [b for b in await page.query_selector_all('input[maxlength="1"]')
                     if await b.is_visible()]
            for i, box in enumerate(boxes[:6]):
                await box.click()
                await box.type(otp[i], delay=80)
            await asyncio.sleep(0.3)
            await page.keyboard.press("Enter")
            _log("OTP typed into digit boxes")
        else:
            await page.triple_click(otp_sel_found)
            await page.fill(otp_sel_found, otp)
            await asyncio.sleep(0.3)
            # Click SIGN IN button
            for btn_sel in (
                'button:has-text("SIGN IN")',
                'button:has-text("Sign In")',
                'button:has-text("Verify")',
                'button[type="submit"]',
            ):
                try:
                    btn = await page.query_selector(btn_sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        _log(f"Clicked '{btn_sel}'")
                        break
                except Exception:
                    pass
            else:
                await page.keyboard.press("Enter")
            _log("OTP submitted")

        # ── Step 8: Wait and report result ───────────────────────
        await asyncio.sleep(5)
        url = page.url
        _log(f"Final URL: {url}")
        if "auth.fifa.com" in url:
            body = await page.evaluate("document.body.innerText")
            if "invalid" in body.lower():
                _log("RESULT: Invalid Code — OTP was rejected")
            else:
                _log("RESULT: Still on auth page — unknown state")
        else:
            _log("RESULT: SUCCESS — left login page!")

        _log("Keeping browser open 30s...")
        await asyncio.sleep(30)
        await context.close()


if __name__ == "__main__":
    asyncio.run(run())
