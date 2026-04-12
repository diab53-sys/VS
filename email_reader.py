"""email_reader.py — async IMAP OTP fetcher for Titan Email (Hostinger).

Connects to imap.titan.email, searches for the latest FIFA verification
email, and extracts the 6-digit OTP code.

Add to .env:
    EMAIL_PASSWORD=your-email-password
    (defaults to FIFA_PASSWORD if not set)
"""

from __future__ import annotations

import asyncio
import datetime
import email
import imaplib
import os as _os

# ─── Load .env at import time ────────────────────────────────────
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        _dir = _os.path.dirname(_os.path.abspath(__file__))
        env_path = _os.path.join(_dir, ".env")
        if _os.path.exists(env_path):
            load_dotenv(env_path, override=False)
    except ImportError:
        pass

_load_dotenv()
import os
import re
import time
from email.header import decode_header
from typing import Optional

# ─── Titan Email IMAP settings ───────────────────────────────────
IMAP_SERVER: str = "imap.titan.email"
IMAP_PORT:   int = 993

# Semaphore created lazily inside the event loop (avoids DeprecationWarning)
_imap_semaphore: Optional[asyncio.Semaphore] = None

def _get_semaphore() -> asyncio.Semaphore:
    global _imap_semaphore
    if _imap_semaphore is None:
        _imap_semaphore = asyncio.Semaphore(5)
    return _imap_semaphore


async def fetch_otp(
    email_address: str,
    password: str,
    timeout_s: int = 90,
    poll_interval_s: int = 4,
) -> Optional[str]:
    """Async wrapper — polls IMAP until OTP found or timeout.

    Args:
        email_address:   Full email (e.g. samantha5431@shereifandcompany.site)
        password:        IMAP password for this mailbox
        timeout_s:       Max seconds to wait for the email
        poll_interval_s: Seconds between IMAP polls

    Returns:
        6-digit OTP string, or None if not found within timeout.
    """
    loop = asyncio.get_event_loop()
    deadline = time.time() + timeout_s

    async with _get_semaphore():
        while time.time() < deadline:
            try:
                otp = await loop.run_in_executor(
                    None,
                    _fetch_otp_sync,
                    email_address,
                    password,
                )
                if otp:
                    return otp
            except Exception as e:
                print(
                    f"{time.strftime('%H:%M:%S')} [EMAIL] IMAP error "
                    f"({email_address}): {e}",
                    flush=True,
                )

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval_s, remaining))

    return None


def _fetch_otp_sync(email_address: str, password: str) -> Optional[str]:
    """Single IMAP connection attempt — returns the NEWEST OTP or None."""
    # IMAP date format for SINCE: DD-Mon-YYYY (e.g. 12-Apr-2026)
    today = datetime.date.today().strftime("%d-%b-%Y")

    try:
        with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT) as mail:
            mail.login(email_address, password)
            mail.select("INBOX")

            # 1. Newest UNSEEN FIFA email received today (highest priority)
            otp = _search_and_extract(mail, f'UNSEEN FROM "fifa.com" SINCE {today}')
            if otp:
                return otp

            # 2. Any FIFA email today (seen or not) — _search_and_extract
            #    always processes newest IMAP IDs first
            otp = _search_and_extract(mail, f'FROM "fifa.com" SINCE {today}')
            if otp:
                return otp

            # 3. tickets.fifa.com today
            otp = _search_and_extract(mail, f'FROM "tickets.fifa.com" SINCE {today}')
            if otp:
                return otp

            # 4. Wider fallback: any UNSEEN FIFA email (previous days)
            otp = _search_and_extract(mail, 'UNSEEN FROM "fifa.com"')
            if otp:
                return otp

    except imaplib.IMAP4.error as e:
        raise RuntimeError(f"IMAP auth/connection error: {e}")

    return None


def _search_and_extract(mail: imaplib.IMAP4_SSL, criteria: str) -> Optional[str]:
    """Search inbox with criteria and extract OTP from the newest matching email."""
    try:
        status, msg_ids = mail.search(None, criteria)
        if status != "OK" or not msg_ids[0]:
            return None

        # IMAP IDs are in ascending order; reverse to process newest first
        ids = msg_ids[0].split()
        for msg_id in reversed(ids[-5:]):
            try:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, bytes):
                    continue
                msg = email.message_from_bytes(raw)
                otp = _extract_otp(msg)
                if otp:
                    # Mark as seen so future UNSEEN searches skip it
                    try:
                        mail.store(msg_id, "+FLAGS", "\\Seen")
                    except Exception:
                        pass
                    return otp
            except Exception:
                continue
    except Exception:
        pass
    return None


def _extract_otp(msg: email.message.Message) -> Optional[str]:
    """Extract 6-digit OTP from email body (plain text or HTML)."""
    bodies: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        bodies.append(payload.decode("utf-8", errors="ignore"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                bodies.append(payload.decode("utf-8", errors="ignore"))
        except Exception:
            pass

    for body in bodies:
        # Match patterns like "532227", "code: 532227", "Your code is 532227"
        patterns = [
            r'(?:code|otp|pin|pass\s*code|verification)[^\d]{0,30}(\d{6})',
            r'\b(\d{6})\b',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, body, re.IGNORECASE)
            if matches:
                return matches[0]

    return None
