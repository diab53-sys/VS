"""metrics.py — FarmerMetrics dataclass, live dashboard, and crash-state restore."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass  # avoids circular import with queue_farmer


# ─── Status display helpers ──────────────────────────────────────
_STATUS_ICON: dict[str, str] = {
    "pending":    "·",
    "launching":  "⟳",
    "navigating": "→",
    "captcha":    "C",
    "in_queue":   "Q",
    "ready":      "✓",
    "logging_in": "L",
    "in_shop":    "S",
    "dead":       "✗",
}


# ─── Metrics ─────────────────────────────────────────────────────
@dataclass
class FarmerMetrics:
    """Aggregated counters for a farming session."""

    shop_entries:    int = 0
    slot_deaths:     int = 0
    keepalive_ok:    int = 0
    keepalive_fail:  int = 0
    nan_reloads:     int = 0
    akamai_detected: int = 0
    akamai_solved:   int = 0
    scout_restarts:  int = 0

    _shop_entry_times: list[float] = field(default_factory=list, repr=False)

    def record_shop_entry(self, slot_id: int, launched_at: float) -> None:
        self.shop_entries += 1
        if launched_at:
            self._shop_entry_times.append(time.time() - launched_at)

    def record_slot_death(self) -> None:
        self.slot_deaths += 1

    def record_keepalive(self, *, success: bool) -> None:
        if success:
            self.keepalive_ok += 1
        else:
            self.keepalive_fail += 1

    def avg_shop_time(self) -> float:
        if not self._shop_entry_times:
            return 0.0
        return sum(self._shop_entry_times) / len(self._shop_entry_times)

    def summary_line(self) -> str:
        return (
            f"shops={self.shop_entries}  deaths={self.slot_deaths}  "
            f"keepalive={self.keepalive_ok}/{self.keepalive_ok + self.keepalive_fail}  "
            f"nan_reloads={self.nan_reloads}  "
            f"akamai={self.akamai_solved}/{self.akamai_detected}  "
            f"scout_restarts={self.scout_restarts}  "
            f"avg_shop_s={self.avg_shop_time():.0f}"
        )


# ─── Live Dashboard ──────────────────────────────────────────────
async def run_dashboard(
    slots: dict[int, Any],
    total: int,
    metrics: "FarmerMetrics",
    log: Any,
) -> None:
    """Periodically print a live status table for all slots."""
    from constants import DASHBOARD_INTERVAL_S

    while True:
        try:
            await asyncio.sleep(DASHBOARD_INTERVAL_S)
            _print_dashboard(slots, total, metrics)
        except asyncio.CancelledError:
            break
        except Exception as e:
            try:
                log.debug("dashboard", "render_error", error=str(e))
            except Exception:
                pass


def _print_dashboard(slots: dict[int, Any], total: int, metrics: "FarmerMetrics") -> None:
    width = 60
    now = time.strftime("%H:%M:%S")
    print(f"\n{'=' * width}", flush=True)
    print(f"  QUEUE FARMER DASHBOARD  —  {now}", flush=True)
    print(f"{'─' * width}", flush=True)

    # Slot grid (10 per row)
    icons: list[str] = []
    for i in range(total):
        slot = slots.get(i)
        if slot is None:
            icons.append("·")
        else:
            icons.append(_STATUS_ICON.get(slot.status, "?"))

    row_size = 10
    for row_start in range(0, total, row_size):
        row = icons[row_start:row_start + row_size]
        row_label = f"{row_start:>2}-{min(row_start + row_size - 1, total - 1):<2}"
        print(f"  [{row_label}]  {' '.join(row)}", flush=True)

    print(f"{'─' * width}", flush=True)

    # Status counts
    counts: dict[str, int] = {}
    for slot in slots.values():
        counts[slot.status] = counts.get(slot.status, 0) + 1

    status_parts = [f"{s}={c}" for s, c in sorted(counts.items())]
    print(f"  Status: {', '.join(status_parts)}", flush=True)
    print(f"  {metrics.summary_line()}", flush=True)
    print(f"{'=' * width}\n", flush=True)


# ─── Crash-State Restore ─────────────────────────────────────────
def restore_state() -> dict[str, Any] | None:
    """Check the SQLite DB for a recent interrupted session.

    Returns a dict with at least ``age_s`` if found, else ``None``.
    """
    try:
        from db import get_all_slots, DB_PATH
        import os
        import sqlite3

        if not os.path.exists(DB_PATH):
            return None

        rows = get_all_slots()
        if not rows:
            return None

        # Find the most-recently-updated non-dead row
        live_rows = [r for r in rows if r["status"] not in ("dead", "pending")]
        if not live_rows:
            return None

        latest_ts: float = max(r["updated_at"] for r in live_rows)
        age_s = time.time() - latest_ts

        # Only treat as recoverable if < 30 min old
        if age_s > 1800:
            return None

        return {
            "age_s":     age_s,
            "row_count": len(live_rows),
            "latest_ts": latest_ts,
        }

    except Exception:
        return None
