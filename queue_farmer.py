"""Queue Farmer — pre-stage queue sessions.

Monitors for FIFA queue activation, then spawns 25 browser instances
in waves. Each instance solves CAPTCHA, waits in queue, auto-enters,
logs in, and holds a live shop session via keepalive pings.

Usage:
    python queue_farmer.py [--instances 25] [--account-start 50] [--server-port 9099]
"""

import asyncio
import json
import os
import shutil
import sys
import time
import builtins

_orig_print = builtins.print
def _flush_print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    try:
        _orig_print(*args, **kwargs)
    except UnicodeEncodeError:
        safe = [a.encode('ascii','replace').decode('ascii') if isinstance(a,str) else a for a in args]
        _orig_print(*safe, **kwargs)
builtins.print = _flush_print

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)  # fifa_buyer_clean root
# Search parent first (canonical layout), then script dir as fallback
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, PARENT_DIR)

from config import TARGET_URL, SERVER_PORT
from db import init_db
from bot import (
    _setup_localhost_route, _setup_captcha_response_listener,
    _solve_datadome_interstitial, auto_login_monitor,
    _pages, _pages_context,
    PROFILES_DIR, INJECTOR_EXT_DIR, FIFA_ACCOUNTS, FIFA_PASSWORD,
)

# ─── Constants ─────────────────────────────────────────────────────
FARM_SERVER_PORT = 9099
FARM_BASE_PORT = 9100       # init_script ports: 9100 + slot_id
SCOUT_POLL_S = 45           # seconds between queue-existence checks
DASHBOARD_INTERVAL_S = 30
SHOP_BASE = "https://fwc26-shop-usd.tickets.fifa.com"
KEEPALIVE_INTERVAL_S = 60

WAVE_CONFIG = [
    # (start_idx, end_idx, gap_seconds)
    # gap=0 means "rapid": wait for captcha solve, then launch next immediately
    (0,  10, 0),    # wave 1: slots 0-9, rapid-fire
    (10, 15, 20),   # wave 2: slots 10-14, 20s gaps
    (15, 20, 60),   # wave 3: slots 15-19, 60s gaps
    (20, 25, 60),   # wave 4: slots 20-24, 60s gaps
]


def log(msg):
    ts = time.strftime('%H:%M:%S')
    _flush_print(f"{ts} [FARMER] {msg}")


# ─── Per-Instance State ───────────────────────────────────────────
class FarmSlot:
    """Tracks one queued browser instance."""
    def __init__(self, slot_id, account, account_idx):
        self.id = slot_id
        self.account = account
        self.account_idx = account_idx
        self.status = "pending"       # pending|launching|captcha|in_queue|ready|logging_in|in_shop|dead
        self.queue_state = ""         # from window.queueinfo.state
        self.wait_time = ""
        self.queue_position = ""
        self.page = None
        self.context = None
        self.pw = None                # patchright playwright instance
        self.dd_task = None
        self.keepalive_task = None
        self.launched_at = 0.0
        self.error = ""


# ─── Sound Alert ──────────────────────────────────────────────────
def _play_beeps(count=5):
    try:
        import winsound
        for _ in range(count):
            winsound.Beep(1000, 300)
            time.sleep(0.15)
    except Exception:
        pass

def alert_ready(slot_id, account):
    import threading
    log(f"*** SLOT #{slot_id} ({account}) READY / IN SHOP! ***")
    threading.Thread(target=_play_beeps, args=(8,), daemon=True).start()


# ─── Launch One Instance ──────────────────────────────────────────
async def launch_instance(slot: FarmSlot, server_port: int):
    """Launch a patchright browser, navigate to queue, set up captcha solving."""
    slot.status = "launching"
    slot.launched_at = time.time()

    from patchright.async_api import async_playwright as patchright_playwright

    pw = await patchright_playwright().start()
    slot.pw = pw

    # Unique profile dir per slot — wipe for fresh cookies
    profile_dir = os.path.join(PROFILES_DIR, f"patchright_farmer_{slot.id}")
    if os.path.exists(profile_dir):
        shutil.rmtree(profile_dir, ignore_errors=True)
    os.makedirs(profile_dir, exist_ok=True)

    log(f"Slot #{slot.id}: Launching Chrome (account {slot.account})...")
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        channel="chrome",
        headless=False,
        no_viewport=True,
        proxy=None,          # Phase 1 always real IP
        ignore_https_errors=True,
    )
    slot.context = context
    await _setup_localhost_route(context)

    # Build init_script: bridge.js + injector.js + XHR abort patch
    bridge_path = os.path.join(INJECTOR_EXT_DIR, "bridge.js")
    injector_path = os.path.join(INJECTOR_EXT_DIR, "injector.js")
    with open(bridge_path, "r", encoding="utf-8") as f:
        bridge_js = f.read()
    with open(injector_path, "r", encoding="utf-8") as f:
        injector_js = f.read()

    init_script = f"""
window.__FIFA_BOT_INSTANCE_ID = {slot.id};
window.__FIFA_BOT_PORT = {server_port};
(() => {{
    const _origAbort = XMLHttpRequest.prototype.abort;
    const _origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function() {{
        this.__qbot_url = arguments[1] || '';
        return _origOpen.apply(this, arguments);
    }};
    XMLHttpRequest.prototype.abort = function() {{
        var _decoded = '';
        try {{ _decoded = decodeURIComponent(this.__qbot_url); }} catch(e) {{ _decoded = this.__qbot_url; }}
        if (this.__qbot_url && (_decoded.toUpperCase().indexOf('CAPTCHA=') >= 0 || this.__qbot_url.indexOf('CAPTCHA=') >= 0)) {{
            return;
        }}
        return _origAbort.apply(this, arguments);
    }};
}})();
{bridge_js}
{injector_js}
"""
    await context.add_init_script(script=init_script)

    # Fresh cookies
    try:
        await context.clear_cookies()
    except Exception:
        pass

    page = context.pages[0] if context.pages else await context.new_page()
    slot.page = page

    # Register page in bot.py's _pages list so auto_login_monitor can see it.
    # Pad the list if needed so slot.id == index in _pages.
    while len(_pages) <= slot.id:
        _pages.append(None)
    _pages[slot.id] = page
    _pages_context[slot.id] = context

    # Navigate to TARGET_URL (5 retries)
    slot.status = "navigating"
    log(f"Slot #{slot.id}: Navigating to queue...")
    for attempt in range(5):
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            break
        except Exception as e:
            log(f"Slot #{slot.id}: Nav failed ({attempt+1}/5): {str(e)[:60]}")
            if attempt < 4:
                await asyncio.sleep(5)
            else:
                slot.status = "dead"
                slot.error = f"Nav failed: {str(e)[:80]}"
                return

    # Set up captcha solving
    await _setup_captcha_response_listener(page, slot.id, server_port=server_port)
    slot.dd_task = asyncio.create_task(_solve_datadome_interstitial(page, slot.id))
    slot.status = "captcha"
    log(f"Slot #{slot.id}: In queue page, captcha solving active")


# ─── Monitor One Slot ─────────────────────────────────────────────
async def monitor_slot(slot: FarmSlot):
    """Poll queue state and manage slot lifecycle. Runs forever per slot."""
    last_state = ""
    keepalive_started = False

    while slot.status not in ("dead",):
        try:
            await asyncio.sleep(2)
            if not slot.page or slot.page.is_closed():
                slot.status = "dead"
                slot.error = "Page closed"
                break

            url = slot.page.url or ""

            # ── Shop phase: already logged in and in the shop ──
            if url.startswith(SHOP_BASE) and "register" not in url and "social-login" not in url:
                if slot.status != "in_shop":
                    log(f"Slot #{slot.id}: IN SHOP! ({slot.account})")
                    slot.status = "in_shop"
                    slot.queue_state = "SHOP"
                    alert_ready(slot.id, slot.account)
                # Start keepalive if not already running
                if not keepalive_started:
                    keepalive_started = True
                    slot.keepalive_task = asyncio.create_task(_keepalive_loop(slot))
                continue

            # ── Login phase ──
            if "auth.fifa.com" in url:
                if slot.status != "logging_in":
                    log(f"Slot #{slot.id}: On login page ({slot.account})")
                    slot.status = "logging_in"
                    slot.queue_state = "LOGIN"
                continue

            # ── Queue phase ──
            if "pkpcontroller" in url or "access.tickets.fifa.com" in url:
                try:
                    state_info = await asyncio.wait_for(slot.page.evaluate("""
                        (() => {
                            var qi = window.queueinfo || {};
                            var ai = window.admissionInfo || null;
                            if (!ai && qi.response) {
                                try { ai = JSON.parse(qi.response).admissionInfo; } catch(e) {}
                            }
                            return {
                                state: qi.state || '',
                                wait: ai ? (ai.waitingTime || '') : '',
                                pos: ai ? (ai.queuePosition || '') : '',
                                canEnter: ai ? (ai.canEnter === 'true') : false,
                            };
                        })()
                    """), timeout=5.0)
                except Exception:
                    state_info = {"state": "", "wait": "", "pos": "", "canEnter": False}

                q_state = state_info.get("state", "")
                slot.queue_state = q_state
                slot.wait_time = str(state_info.get("wait", ""))
                slot.queue_position = str(state_info.get("pos", ""))

                # State transition logging
                if q_state != last_state:
                    log(f"Slot #{slot.id}: {last_state or '?'} -> {q_state}")
                    last_state = q_state

                # Update status based on queue state
                if q_state == "AUTHREQ":
                    slot.status = "captcha"
                elif q_state in ("WAIT", "WAITMIN", "WAITLONG", "AUTHORIZED", "IDLE"):
                    slot.status = "in_queue"
                elif q_state in ("READY", "ADMITTED"):
                    if slot.status != "ready":
                        log(f"Slot #{slot.id}: READY! Auto-enter will fire via injector")
                        alert_ready(slot.id, slot.account)
                    slot.status = "ready"
                elif q_state in ("END", "ERROR"):
                    log(f"Slot #{slot.id}: Queue {q_state} — slot dead")
                    slot.status = "dead"
                    slot.error = f"Queue {q_state}"
                elif q_state == "PAUSE":
                    slot.status = "in_queue"

                # Detect NaN countdown (stuck session)
                if q_state in ("WAIT", "WAITLONG", "WAITMIN", ""):
                    try:
                        page_text = await asyncio.wait_for(
                            slot.page.evaluate("document.body?.innerText?.substring(0,300)||''"),
                            timeout=3.0)
                        if "NaN" in page_text:
                            log(f"Slot #{slot.id}: NaN countdown — reloading")
                            await slot.page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
                            await asyncio.sleep(3)
                    except Exception:
                        pass

                # Detect Akamai 428
                try:
                    is_akamai = "/challenge.html" in url
                    if not is_akamai:
                        page_text = await asyncio.wait_for(
                            slot.page.evaluate("document.body?.innerText?.substring(0,300)||''"),
                            timeout=3.0)
                        is_akamai = "not a robot" in page_text.lower() or "confirm you" in page_text.lower()
                    if is_akamai:
                        log(f"Slot #{slot.id}: Akamai 428 — solving...")
                        from datadome_solver import solve_akamai_challenge
                        solved = await solve_akamai_challenge(slot.page, slot.id, proxy_url="")
                        if solved:
                            log(f"Slot #{slot.id}: Akamai solved, returning to queue")
                            await slot.page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
                            await _setup_captcha_response_listener(slot.page, slot.id, server_port=FARM_SERVER_PORT)
                            await asyncio.sleep(3)
                except Exception:
                    pass

                continue

            # ── Social login redirect (part of login flow) ──
            if "social-login" in url:
                slot.status = "logging_in"
                continue

        except asyncio.CancelledError:
            break
        except Exception as e:
            log(f"Slot #{slot.id}: Monitor error: {str(e)[:60]}")
            await asyncio.sleep(5)

    log(f"Slot #{slot.id}: Monitor exiting (status={slot.status})")


# ─── Keepalive Loop ───────────────────────────────────────────────
async def _keepalive_loop(slot: FarmSlot):
    """Ping the shop every 60s to prevent session expiry (~7 min TTL)."""
    while slot.status == "in_shop":
        try:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
            if not slot.page or slot.page.is_closed():
                slot.status = "dead"
                slot.error = "Page closed during keepalive"
                break
            url = slot.page.url or ""
            if not url.startswith(SHOP_BASE):
                log(f"Slot #{slot.id}: Left shop during keepalive (url={url[:60]})")
                slot.status = "dead"
                slot.error = "Bounced from shop"
                break
            await asyncio.wait_for(slot.page.evaluate("""
                fetch('/ajax/countdown/update', {credentials: 'same-origin'})
                    .then(r => r.status).catch(() => 0)
            """), timeout=10.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log(f"Slot #{slot.id}: Keepalive error: {str(e)[:40]}")


# ─── Scout for Queue ──────────────────────────────────────────────
async def scout_for_queue(slots: dict, server_port: int, account_start: int) -> FarmSlot:
    """Keep checking TARGET_URL for queue. Returns the scout slot once queue is found."""
    scout_account_idx = account_start
    scout_account = FIFA_ACCOUNTS[scout_account_idx % len(FIFA_ACCOUNTS)]

    while True:
        log("Scout: Checking for queue...")
        slot = FarmSlot(0, scout_account, scout_account_idx)

        try:
            await launch_instance(slot, server_port)
        except Exception as e:
            log(f"Scout: Launch failed: {str(e)[:60]}")
            await asyncio.sleep(30)
            continue

        if slot.status == "dead":
            log(f"Scout: Launch failed — {slot.error}")
            await _cleanup_slot(slot)
            await asyncio.sleep(30)
            continue

        # Wait a few seconds for page to settle after redirect
        await asyncio.sleep(5)

        url = slot.page.url if slot.page else ""

        # Check if we landed on the queue
        if "pkpcontroller" in url or "access.tickets.fifa.com" in url:
            log(f"Scout: QUEUE DETECTED! URL: {url[:80]}")
            slots[0] = slot
            return slot

        # Check if we went straight to shop (no queue)
        if url.startswith(SHOP_BASE):
            log("Scout: No queue — went straight to shop. Will re-check.")
            await _cleanup_slot(slot)
            log(f"Scout: Sleeping {SCOUT_POLL_S}s before next check...")
            await asyncio.sleep(SCOUT_POLL_S)
            continue

        # Unknown state — could be DataDome, could be transitioning
        log(f"Scout: Ambiguous URL: {url[:80]} — waiting 10s then re-checking")
        await asyncio.sleep(10)
        url = slot.page.url if slot.page and not slot.page.is_closed() else ""
        if "pkpcontroller" in url or "access.tickets.fifa.com" in url:
            log(f"Scout: QUEUE DETECTED (delayed)! URL: {url[:80]}")
            slots[0] = slot
            return slot

        log("Scout: No queue. Cleaning up and retrying.")
        await _cleanup_slot(slot)
        await asyncio.sleep(SCOUT_POLL_S)


# ─── Wait for Captcha Solved ──────────────────────────────────────
async def wait_for_captcha_solved(slot: FarmSlot, timeout: int = 120):
    """Block until slot leaves AUTHREQ state (captcha solved) or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if slot.status in ("dead",):
            return
        if slot.status in ("in_queue", "ready", "logging_in", "in_shop"):
            log(f"Slot #{slot.id}: Captcha solved (status={slot.status})")
            return
        # Also check queue_state directly
        if slot.queue_state in ("WAIT", "WAITMIN", "WAITLONG", "AUTHORIZED", "READY", "ADMITTED"):
            return
        await asyncio.sleep(2)
    log(f"Slot #{slot.id}: Captcha wait timed out after {timeout}s — continuing anyway")


# ─── Spawn Waves ──────────────────────────────────────────────────
async def spawn_waves(slots: dict, server_port: int, total_instances: int, account_start: int):
    """Launch instances in configured waves. Slot 0 is already the scout."""
    for wave_start, wave_end, gap_seconds in WAVE_CONFIG:
        if wave_start >= total_instances:
            break
        end = min(wave_end, total_instances)

        wave_num = [i for i, (s, e, g) in enumerate(WAVE_CONFIG) if s == wave_start][0] + 1
        gap_desc = "rapid" if gap_seconds == 0 else f"{gap_seconds}s gaps"
        log(f"=== Wave {wave_num}: slots {wave_start}-{end-1} ({gap_desc}) ===")

        for i in range(wave_start, end):
            # Skip slot 0 — it's already launched by the scout
            if i == 0:
                # Start monitor for scout slot
                if 0 in slots:
                    asyncio.create_task(monitor_slot(slots[0]))
                    if gap_seconds == 0:
                        await wait_for_captcha_solved(slots[0])
                continue

            account_idx = account_start + i
            account = FIFA_ACCOUNTS[account_idx % len(FIFA_ACCOUNTS)]
            slot = FarmSlot(i, account, account_idx)
            slots[i] = slot

            try:
                await launch_instance(slot, server_port)
                asyncio.create_task(monitor_slot(slot))
            except Exception as e:
                log(f"Slot #{i}: Launch error: {str(e)[:60]}")
                slot.status = "dead"
                slot.error = str(e)[:80]

            if gap_seconds == 0:
                # Wave 1: wait for captcha to solve before next launch
                await wait_for_captcha_solved(slot)
            else:
                await asyncio.sleep(gap_seconds)


# ─── Dashboard ────────────────────────────────────────────────────
async def run_dashboard(slots: dict, total_instances: int):
    """Print status dashboard every DASHBOARD_INTERVAL_S seconds."""
    while True:
        await asyncio.sleep(DASHBOARD_INTERVAL_S)

        lines = []
        lines.append(f"\n{'='*64}")
        lines.append(f"  QUEUE FARMER -- {total_instances} instances")
        lines.append(f"{'='*64}")

        counts = {"captcha": 0, "in_queue": 0, "ready": 0, "logging_in": 0, "in_shop": 0, "dead": 0, "other": 0}

        for i in range(total_instances):
            slot = slots.get(i)
            if not slot:
                lines.append(f"  --  #{i:>2}  (not launched)")
                continue

            icon = "  "
            status = slot.status
            if status == "in_shop":
                icon = ">>"
                counts["in_shop"] += 1
            elif status == "ready":
                icon = "**"
                counts["ready"] += 1
            elif status == "in_queue":
                icon = "~~"
                counts["in_queue"] += 1
            elif status == "captcha":
                icon = ".."
                counts["captcha"] += 1
            elif status == "logging_in":
                icon = "->"
                counts["logging_in"] += 1
            elif status == "dead":
                icon = "!!"
                counts["dead"] += 1
            else:
                counts["other"] += 1

            detail = f"  [{slot.queue_state or '?':>10}]"
            if slot.wait_time and slot.wait_time not in ("", "null", "None"):
                detail += f"  wait={slot.wait_time}s"
            if slot.queue_position and slot.queue_position not in ("", "null", "0", "None"):
                detail += f"  pos={slot.queue_position}"
            if slot.error:
                detail += f"  err={slot.error[:30]}"
            if status == "in_shop":
                elapsed = int(time.time() - slot.launched_at) if slot.launched_at else 0
                detail += f"  alive={elapsed}s"

            acct_short = slot.account.split("@")[0] if slot.account else "?"
            lines.append(f"  {icon} #{i:>2}  {acct_short:<16} {status:<12}{detail}")

        # Summary line
        summary_parts = []
        for k, v in counts.items():
            if v > 0:
                summary_parts.append(f"{k}={v}")
        lines.append(f"{'─'*64}")
        lines.append(f"  {' | '.join(summary_parts)}")
        lines.append(f"{'='*64}")

        print("\n".join(lines))


# ─── Cleanup ──────────────────────────────────────────────────────
async def _cleanup_slot(slot: FarmSlot):
    """Close browser and playwright for a slot."""
    try:
        if slot.dd_task:
            slot.dd_task.cancel()
    except Exception:
        pass
    try:
        if slot.keepalive_task:
            slot.keepalive_task.cancel()
    except Exception:
        pass
    try:
        if slot.context:
            await slot.context.close()
    except Exception:
        pass
    try:
        if slot.pw:
            await slot.pw.stop()
    except Exception:
        pass
    # Remove from bot.py's _pages
    try:
        if slot.id < len(_pages):
            _pages[slot.id] = None
        _pages_context.pop(slot.id, None)
    except Exception:
        pass


# ─── Main ─────────────────────────────────────────────────────────
async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Queue Farmer — pre-stage queue sessions")
    parser.add_argument("--instances", type=int, default=25,
                        help="Total instances to spawn (default: 25)")
    parser.add_argument("--account-start", type=int, default=50,
                        help="Starting account index in FIFA_ACCOUNTS (default: 50)")
    parser.add_argument("--server-port", type=int, default=FARM_SERVER_PORT,
                        help=f"Solve server port (default: {FARM_SERVER_PORT})")
    args = parser.parse_args()

    total = args.instances
    account_start = args.account_start
    server_port = args.server_port

    # Load API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        env_path = os.path.join(PARENT_DIR, ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith("ANTHROPIC_API_KEY="):
                        api_key = line.strip().split("=", 1)[1]
    if not api_key:
        print("ERROR: No ANTHROPIC_API_KEY")
        sys.exit(1)

    print("=" * 60)
    print("  QUEUE FARMER")
    print(f"  Instances: {total}")
    print(f"  Accounts: #{account_start} - #{account_start + total - 1}")
    print(f"  Server port: {server_port}")
    print("=" * 60)

    init_db()

    # Kill old servers on our port range
    try:
        import subprocess as _sp
        result = _sp.run(["netstat", "-ano"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if f"127.0.0.1:{server_port}" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                if pid != "0":
                    _sp.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
    except Exception:
        pass

    # Start solve server
    from server_v2 import run_server
    runner, actual_port = await run_server(api_key, server_port)
    if actual_port != server_port:
        log(f"Server port shifted to {actual_port}")
        server_port = actual_port

    # Set up bot.py globals for auto_login_monitor
    import bot as _bot
    _bot._monitor_started = True
    _bot._forced_account_idx = None  # use page index as base
    for i in range(total):
        _bot._account_offset[i] = account_start  # _get_account(i) -> FIFA_ACCOUNTS[(i + account_start) % 152]

    # Start auto_login_monitor (handles login/OTP for all pages in _pages)
    login_task = asyncio.create_task(auto_login_monitor())
    log("auto_login_monitor started")

    # ─── Scout loop ───────────────────────────────────────────────
    slots: dict[int, FarmSlot] = {}
    scout_slot = await scout_for_queue(slots, server_port, account_start)

    # ─── Queue found — spawn all waves ────────────────────────────
    log(f"Starting wave spawning ({total} instances)...")

    # Start dashboard
    dashboard_task = asyncio.create_task(run_dashboard(slots, total))

    # Spawn all waves (slot 0 already launched by scout)
    await spawn_waves(slots, server_port, total, account_start)

    log(f"All {total} instances launched. Farming queue sessions...")
    log("Press Ctrl+C to stop.")

    # Run forever
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        log("Shutting down...")
        dashboard_task.cancel()
        login_task.cancel()
        for slot in slots.values():
            await _cleanup_slot(slot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
