"""
Qatar Festival Monitor
Correct Festival Version
Refresh every 45 seconds
"""
import time
import os
from datetime import datetime
import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ================= LOAD ENV =================
load_dotenv(r"C:\Tickets_monitor\.env")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("Telegram credentials missing in .env")

# ================= CONFIG =================
PROFILE_PATH = r"C:\Tickets_monitor\chrome_profile"
BASE_URL = "https://official-tickets.roadtoqatar.qa"
CHECK_INTERVAL = 45
SESSIONS = [
    {"id": "2744030", "name": "Egypt v Spain"},
    {"id": "2742971", "name": "Spain v Argentina — Finalissima"},
]
CATEGORY_ORDER = ["CAT1", "CAT2", "CAT3"]
CATEGORY_EMOJI = {
    "CAT1": "🥇",
    "CAT2": "🥈",
    "CAT3": "🥉",
}

# ================= TELEGRAM =================
def send_telegram(message, session_id):
    buy_url = f"{BASE_URL}/qatar-football-festival/select/{session_id}?viewCode=Vista_Principal"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🎟 BUY NOW", "url": buy_url}]
            ]
        }
    }
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)

# ================= ALARM =================
def play_alarm():
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1500, 300)
    except Exception:
        pass  # Non-Windows or winsound unavailable

# ================= BROWSER =================
def setup_browser():
    options = Options()
    options.add_argument(f"--user-data-dir={PROFILE_PATH}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver

# ================= FETCH =================
def fetch_availability(driver, session_id):
    # BUG FIX: throw on HTTP error so the second .then doesn't call
    # callback(undefined), which triggered a double-callback via Selenium
    # and caused the async script to return None or crash unpredictably.
    js = """
        const sessionId = arguments[0];
        const callback = arguments[arguments.length - 1];
        fetch(`/channels-api/v1/catalog/sessions/${sessionId}/availability-prices`, {
            headers: {
                'Accept': 'application/json',
                'ob-channel-id': '4627',
                'ob-client': 'channels',
                'ob-language': 'en-GB'
            },
            credentials: 'include'
        })
        .then(r => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(data => callback(data))
        .catch(err => callback({error: err.toString()}));
    """
    data = driver.execute_async_script(js, session_id)
    if not data:
        print("No API response")
        return None
    if isinstance(data, dict) and data.get("error"):
        print("API error:", data["error"])
        return None

    result = {}
    if isinstance(data, list):
        for item in data:
            cat = item.get("code", "").upper()
            available = item.get("availability", {}).get("available", 0)
            price = item.get("rates", [{}])[0].get("price", {}).get("total", 0)
            result[cat] = {
                "available": available,
                "price": price
            }
    print("DEBUG:", result)
    return result

# ================= MAIN =================
def main():
    driver = setup_browser()
    last_snapshot = {}

    # Open matches in tabs
    for i, session in enumerate(SESSIONS):
        url = f"{BASE_URL}/qatar-football-festival/select/{session['id']}?viewCode=Vista_Principal"
        if i == 0:
            driver.get(url)
        else:
            driver.execute_script("window.open('');")
            driver.switch_to.window(driver.window_handles[i])
            driver.get(url)
        time.sleep(8)

    print("Monitoring started...\n")

    while True:
        for i, session in enumerate(SESSIONS):
            driver.switch_to.window(driver.window_handles[i])
            data = fetch_availability(driver, session["id"])
            if not data:
                continue

            key = session["id"]
            if last_snapshot.get(key) == data:
                continue  # Nothing changed — skip entirely

            last_snapshot[key] = data

            # Build list of categories with tickets available
            available_lines = []
            for cat in CATEGORY_ORDER:
                if cat not in data:
                    continue
                qty = data[cat]["available"]
                price = data[cat]["price"]
                if qty > 0:
                    emoji = CATEGORY_EMOJI.get(cat, "🎟")
                    available_lines.append(f"{emoji} *{cat}*: {qty:,} @ {price:.1f} QAR")

            if not available_lines:
                # Data changed but no tickets available — log only, no alert
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {session['name']}: data updated, no tickets available")
                continue

            # BUG FIX: only alert and alarm when tickets are actually available.
            # Previously, the alarm and "TICKETS AVAILABLE!" message fired on
            # every data change — including when all quantities dropped to 0.
            message = "🚨 *TICKETS AVAILABLE!*\n\n"
            message += f"{session['name']}\n\n"
            message += "\n".join(available_lines)
            message += f"\n\n⏰ {datetime.now().strftime('%H:%M:%S')}"

            print(message)
            send_telegram(message, session["id"])
            play_alarm()

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
