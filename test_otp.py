"""test_otp.py — standalone OTP fetch test. Run with: python test_otp.py"""

import imaplib
import email
import datetime
import re
import time

IMAP_SERVER = "imap.titan.email"
IMAP_PORT   = 993

EMAIL    = "brandon8289@harmonyofcommunity.site"
PASSWORD = "StrongPass123!"   # change if different

def fetch():
    today = datetime.date.today().strftime("%d-%b-%Y")
    print(f"Connecting to {IMAP_SERVER} as {EMAIL} ...")

    with imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT) as mail:
        mail.login(EMAIL, PASSWORD)
        mail.select("INBOX")
        print("Login OK\n")

        for label, criteria in [
            ("UNSEEN today",  f'UNSEEN FROM "fifa.com" SINCE {today}'),
            ("ALL today",     f'FROM "fifa.com" SINCE {today}'),
            ("UNSEEN all",    'UNSEEN FROM "fifa.com"'),
            ("ALL all",       'FROM "fifa.com"'),
        ]:
            status, msg_ids = mail.search(None, criteria)
            ids = msg_ids[0].split() if status == "OK" and msg_ids[0] else []
            print(f"[{label}] found {len(ids)} emails: {[i.decode() for i in ids]}")

            if not ids:
                continue

            # Fetch Date header for each to show ordering
            candidates = []
            for mid in ids[-10:]:
                _, hdr = mail.fetch(mid, "(BODY[HEADER.FIELDS (DATE SUBJECT)])")
                date_str = subject = ""
                if hdr and hdr[0]:
                    raw = hdr[0][1]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="ignore")
                    for line in raw.splitlines():
                        if line.lower().startswith("date:"):
                            date_str = line[5:].strip()
                        if line.lower().startswith("subject:"):
                            subject = line[8:].strip()
                try:
                    import email.utils as eu
                    ts = time.mktime(eu.parsedate(date_str)) if date_str else 0
                except Exception:
                    ts = 0
                candidates.append((ts, mid, date_str, subject))

            candidates.sort(key=lambda x: x[0], reverse=True)
            print(f"  Sorted newest→oldest:")
            for ts, mid, date_str, subject in candidates:
                print(f"    id={mid.decode()} | {date_str[:30]} | {subject[:40]}")

            # Extract OTP from newest
            for ts, mid, date_str, subject in candidates:
                _, msg_data = mail.fetch(mid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, bytes):
                    continue
                msg = email.message_from_bytes(raw)
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
                    matches = re.findall(pattern, body, re.IGNORECASE)
                    if matches:
                        print(f"\n  >>> OTP FOUND: {matches[0]} (from email id={mid.decode()}, date={date_str[:30]})\n")
                        return matches[0]

            print(f"  No OTP found in [{label}] emails\n")

    print("No OTP found at all.")
    return None

if __name__ == "__main__":
    fetch()
