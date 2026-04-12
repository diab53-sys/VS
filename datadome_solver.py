"""datadome_solver.py — Akamai Bot Manager 428 challenge handler.

Provides:
    solve_akamai_challenge(page, slot_id, proxy_url) -> bool

Called by queue_farmer.py when the queue page returns a /challenge.html
(Akamai 428).  Attempts a click-through solve; returns True on success.
"""

from __future__ import annotations

import asyncio
import time


async def solve_akamai_challenge(
    page: object,
    slot_id: int,
    proxy_url: str = "",
) -> bool:
    """Attempt to dismiss an Akamai Bot Manager challenge page.

    Strategy:
        1. Wait briefly for the page to settle.
        2. Try common challenge-page submit selectors.
        3. Re-check the URL — if challenge.html is gone, return True.
        4. If a checkbox is present, check it and resubmit.

    Args:
        page:      Patchright/Playwright Page object.
        slot_id:   Slot index for logging.
        proxy_url: (unused) Reserved for proxy-based solve integration.

    Returns:
        True  if the challenge appears to be dismissed.
        False if it could not be solved.
    """
    _log(slot_id, "solve_akamai_challenge: starting")

    try:
        # Give the challenge page a moment to render
        await asyncio.sleep(3)

        if _page_closed(page):
            return False

        url: str = getattr(page, "url", "") or ""
        if "/challenge.html" not in url:
            _log(slot_id, "challenge.html already gone — no action needed")
            return True

        # ── Attempt 1: click common submit / verify buttons ───────
        button_selectors = [
            'button[id*="confirm"]',
            'button[id*="verify"]',
            'button[id*="submit"]',
            'input[type="submit"]',
            'button[type="submit"]',
            'button',
        ]
        for sel in button_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    await asyncio.sleep(2)
                    url = getattr(page, "url", "") or ""
                    if "/challenge.html" not in url:
                        _log(slot_id, f"solved via selector '{sel}'")
                        return True
            except Exception:
                continue

        # ── Attempt 2: look for a checkbox (human-verify pattern) ─
        try:
            checkbox = await page.query_selector('input[type="checkbox"]')
            if checkbox:
                await checkbox.click()
                await asyncio.sleep(1)
                # Try submit again after checking
                try:
                    await page.click('input[type="submit"], button[type="submit"]', timeout=3000)
                    await asyncio.sleep(2)
                    url = getattr(page, "url", "") or ""
                    if "/challenge.html" not in url:
                        _log(slot_id, "solved via checkbox + submit")
                        return True
                except Exception:
                    pass
        except Exception:
            pass

        # ── Attempt 3: reload the target URL directly ─────────────
        try:
            from config import TARGET_URL
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)
            url = getattr(page, "url", "") or ""
            if "/challenge.html" not in url:
                _log(slot_id, "solved via direct navigation to TARGET_URL")
                return True
        except Exception:
            pass

        _log(slot_id, "all solve attempts failed")
        return False

    except asyncio.CancelledError:
        raise
    except Exception as e:
        _log(slot_id, f"unexpected error: {e}")
        return False


# ─── Helpers ─────────────────────────────────────────────────────
def _log(slot_id: int, msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [AKAMAI] slot={slot_id}  {msg}", flush=True)


def _page_closed(page: object) -> bool:
    try:
        return page.is_closed()  # type: ignore[attr-defined]
    except Exception:
        return True
