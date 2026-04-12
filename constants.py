"""constants.py — all tuneable parameters for Queue Farmer v2.

Edit values here rather than in queue_farmer.py. All timing is in seconds
unless the name ends in _MS (milliseconds).
"""

from __future__ import annotations

# ─── Server Ports ────────────────────────────────────────────────
FARM_SERVER_PORT: int = 9099   # CAPTCHA solve server (farmer)
FARM_BASE_PORT:   int = 9100   # Reserved for future worker servers

# ─── Scout Timing ────────────────────────────────────────────────
SCOUT_POLL_S:               float = 45.0   # How long between scout attempts when no queue
SCOUT_POST_LAUNCH_SETTLE_S: float = 5.0    # Wait after browser launch before checking URL
SCOUT_AMBIGUOUS_WAIT_S:     float = 10.0   # Wait in ambiguous URL state before re-checking
SCOUT_FAILURE_COOLDOWN_S:   float = 30.0   # Cooldown after consecutive scout failures

# ─── Dashboard ───────────────────────────────────────────────────
DASHBOARD_INTERVAL_S: float = 30.0

# ─── Shop / Keepalive ────────────────────────────────────────────
SHOP_BASE:          str   = "https://fwc26-shop-usd.tickets.fifa.com"
KEEPALIVE_BASE_S:   float = 60.0   # Base keepalive ping interval
KEEPALIVE_JITTER_S: float = 30.0   # Max extra random jitter on top of base

# ─── Navigation Retries ──────────────────────────────────────────
NAV_MAX_RETRIES:       int   = 3
NAV_TIMEOUT_MS:        int   = 30_000   # ms — passed to page.goto()
NAV_RETRY_BASE_DELAY_S: float = 2.0

# ─── Exponential Backoff ─────────────────────────────────────────
BACKOFF_BASE_S:   float = 2.0
BACKOFF_MAX_S:    float = 60.0
BACKOFF_FACTOR:   float = 2.0

# ─── Slot Monitor ────────────────────────────────────────────────
MONITOR_POLL_S:     float = 3.0    # How often monitor_slot() polls
EVALUATE_TIMEOUT_S: float = 10.0   # Timeout for page.evaluate() calls

# ─── Captcha Wait ────────────────────────────────────────────────
CAPTCHA_WAIT_TIMEOUT_S: int = 120  # Max wait for captcha solve before continuing

# ─── Wave Config ─────────────────────────────────────────────────
# Sentinel: slot gap is jittered (wait for captcha + random delay) instead of fixed.
WAVE_JITTER_SENTINEL: int = -1
WAVE1_MIN_JITTER_S:   float = 1.5   # Min random gap between Wave-1 slot launches
WAVE1_MAX_JITTER_S:   float = 4.0   # Max random gap between Wave-1 slot launches

# (wave_start, wave_end_exclusive, gap_seconds)
# gap_seconds == WAVE_JITTER_SENTINEL → jittered (wave 1 behaviour)
WAVE_CONFIG: list[tuple[int, int, int]] = [
    (0,  10, WAVE_JITTER_SENTINEL),  # Wave 1: slots  0-9,  jittered gaps
    (10, 15, 20),                    # Wave 2: slots 10-14, 20 s fixed
    (15, 20, 60),                    # Wave 3: slots 15-19, 60 s fixed
    (20, 25, 60),                    # Wave 4: slots 20-24, 60 s fixed
]

# ─── Shutdown ────────────────────────────────────────────────────
SHUTDOWN_CLEANUP_TIMEOUT_S: float = 15.0

# ─── Server Health Check ─────────────────────────────────────────
SERVER_HEALTH_TIMEOUT_S: float = 5.0

# ─── Browser Init Script ─────────────────────────────────────────
# Injected before every page load.  Sets globals that bridge.js reads,
# then inlines bridge.js and injector.js.
# NOTE: Use only {slot_id}, {server_port}, {bridge_js}, {injector_js}
# as format placeholders — no other bare braces in this string.
INIT_SCRIPT_TEMPLATE: str = (
    "window.__FIFA_BOT_PORT = {server_port};\n"
    "window.__FIFA_BOT_INSTANCE_ID = {slot_id};\n"
    "{bridge_js}\n"
    "{injector_js}\n"
)

# ─── Queue-State Evaluator JS ────────────────────────────────────
# Returned by page.evaluate() as a Python dict.
# Keys: state, wait, pos, canEnter, bodyText, hasNaN, isAkamai, url
MONITOR_EVAL_JS: str = """
(function () {
    var qi  = window.queueinfo || {};
    var ai  = window.admissionInfo || null;
    if (!ai && qi.response) {
        try { ai = JSON.parse(qi.response).admissionInfo; } catch (e) {}
    }
    var state = qi.state || '';
    var wait  = ai ? (ai.waitingTime   || '') : (qi.waitTime || '');
    var pos   = ai ? (ai.queuePosition || '') : (qi.position  || '');
    var canEnter = !!(ai ? ai.canEnter : qi.canEnter);
    var bodyText = document.body ? document.body.innerText.slice(0, 400) : '';
    var url      = window.location.href;
    var hasNaN   = /\\bNaN\\b/.test(bodyText);
    var isAkamai = url.indexOf('/challenge.html') !== -1
                || (document.title || '').indexOf('Akamai') !== -1;
    return {
        state:    state,
        wait:     String(wait),
        pos:      String(pos),
        canEnter: canEnter,
        bodyText: bodyText,
        hasNaN:   hasNaN,
        isAkamai: isAkamai,
        url:      url
    };
})()
"""
