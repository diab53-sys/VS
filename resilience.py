"""resilience.py — structured logging, state tracking, and typed exceptions.

Provides:
    log      — structured logger (log.info / warn / error / debug)
    tracker  — per-slot BotState tracker
    BotState — enum of slot lifecycle states
    Typed exception hierarchy for bot error handling
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import defaultdict
from enum import Enum
from typing import Any


# ─── Bot State Enum ──────────────────────────────────────────────
class BotState(Enum):
    LAUNCHING     = "launching"
    QUEUE_LOADING = "queue_loading"
    QUEUE_CAPTCHA = "queue_captcha"
    QUEUE_WAITING = "queue_waiting"
    AKAMAI_SOLVE  = "akamai_solve"
    LOGIN_FORM    = "login_form"
    SHOP_READY    = "shop_ready"
    DEAD          = "dead"


# ─── Typed Exceptions ────────────────────────────────────────────
class NavigationTimeoutError(Exception):
    def __init__(self, msg: str = "", *, page_idx: int | None = None) -> None:
        super().__init__(msg)
        self.page_idx = page_idx


class NetworkError(Exception):
    pass


class PageClosedError(Exception):
    def __init__(self, msg: str = "", *, page_idx: int | None = None) -> None:
        super().__init__(msg)
        self.page_idx = page_idx


class AkamaiChallengeError(Exception):
    pass


class TransientError(Exception):
    pass


# ─── Structured Logger ───────────────────────────────────────────
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_MIN_LEVEL = "DEBUG"

_LEVEL_LABELS = {
    "INFO":  "INFO ",
    "WARN":  "WARN ",
    "ERROR": "ERROR",
    "DEBUG": "DEBUG",
}


class _Logger:
    """Structured logger with counters and async counter-loop support.

    Usage:
        log.info("category", "event_name", key=value, ...)
        log.warn("launch", "nav_failed", page_idx=3, attempt=2, error="...")
        await log.counter_loop(60)   # background counter aggregator
        log.print_counters(header="FINAL")
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock() if False else None  # created lazily

    # ── Core emit ────────────────────────────────────────────────
    def _emit(self, level: str, category: str, event: str, **kwargs: Any) -> None:
        if _LEVEL_ORDER.get(level, 0) < _LEVEL_ORDER.get(_MIN_LEVEL, 0):
            return
        ts = time.strftime("%H:%M:%S")
        label = _LEVEL_LABELS.get(level, level)
        parts = [f"{ts} [{label}] [{category}] {event}"]
        for k, v in kwargs.items():
            parts.append(f"{k}={v!r}")
        print("  ".join(parts), flush=True)

        # Increment event counter
        key = f"{category}.{event}"
        self._counters[key] += 1

    def info(self, category: str, event: str, **kwargs: Any) -> None:
        self._emit("INFO", category, event, **kwargs)

    def warn(self, category: str, event: str, **kwargs: Any) -> None:
        self._emit("WARN", category, event, **kwargs)

    def error(self, category: str, event: str, **kwargs: Any) -> None:
        self._emit("ERROR", category, event, **kwargs)

    def debug(self, category: str, event: str, **kwargs: Any) -> None:
        self._emit("DEBUG", category, event, **kwargs)

    # ── Counter aggregation ───────────────────────────────────────
    async def counter_loop(self, interval_s: int = 60) -> None:
        """Background coroutine — periodically prints event counts."""
        while True:
            try:
                await asyncio.sleep(interval_s)
                self.print_counters(header=f"COUNTERS ({interval_s}s window)")
                self._counters.clear()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def print_counters(self, header: str = "COUNTERS") -> None:
        if not self._counters:
            return
        print(f"\n--- {header} ---", flush=True)
        for key, count in sorted(self._counters.items(), key=lambda x: -x[1]):
            print(f"  {key}: {count}", flush=True)
        print("", flush=True)


# ─── State Tracker ───────────────────────────────────────────────
class _StateTracker:
    """Tracks per-slot BotState for monitoring / dashboard display."""

    def __init__(self) -> None:
        self._states: dict[int, dict[str, Any]] = {}

    def enter(self, slot_id: int, state: BotState, **meta: Any) -> None:
        self._states[slot_id] = {"state": state, "ts": time.time(), **meta}

    def mark_error(self, slot_id: int, exc: Exception) -> None:
        if slot_id in self._states:
            self._states[slot_id]["error"] = str(exc)
            self._states[slot_id]["error_ts"] = time.time()

    def remove(self, slot_id: int) -> None:
        self._states.pop(slot_id, None)

    def get(self, slot_id: int) -> dict[str, Any] | None:
        return self._states.get(slot_id)

    def all_states(self) -> dict[int, dict[str, Any]]:
        return dict(self._states)


# ─── Singletons ──────────────────────────────────────────────────
log: _Logger = _Logger()
tracker: _StateTracker = _StateTracker()
