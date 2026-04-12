"""watchdog.py — Scout watchdog and stuck-slot detector for Queue Farmer v2."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable


# ─── Scout Watchdog ──────────────────────────────────────────────
class ScoutWatchdog:
    """Monitors the scout slot (slot 0) and restarts it if it dies.

    Args:
        get_scout:        Zero-arg callable returning the current scout FarmSlot or None.
        restart_callback: Async callable to restart the scout (no args).
        logger:           resilience.Logger instance for structured logging.
    """

    # How often (seconds) to check the scout slot
    CHECK_INTERVAL_S: float = 30.0

    def __init__(
        self,
        get_scout: Callable[[], Any | None],
        restart_callback: Callable[[], Any],
        logger: Any,
    ) -> None:
        self._get_scout = get_scout
        self._restart = restart_callback
        self._log = logger
        self._restart_count: int = 0

    async def run(self) -> None:
        """Long-running coroutine — monitors the scout indefinitely."""
        while True:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL_S)

                scout = self._get_scout()
                if scout is None:
                    continue

                if scout.status == "dead":
                    self._restart_count += 1
                    self._log.warn(
                        "watchdog", "scout_dead",
                        restarts=self._restart_count,
                        error=scout.error or "unknown",
                    )
                    try:
                        await self._restart()
                        self._log.info("watchdog", "scout_restarted", attempt=self._restart_count)
                    except Exception as e:
                        self._log.error("watchdog", "restart_failed", error=str(e))

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error("watchdog", "watchdog_error", error=str(e))
                await asyncio.sleep(5)


# ─── Stuck-Slot Checker ──────────────────────────────────────────
_STUCK_THRESHOLD_S: float = 120.0   # seconds before a launching/navigating slot is "stuck"
_CHECK_INTERVAL_S:  float = 60.0    # how often to run the check


async def check_stuck_slots(
    slots: dict[int, Any],
    metrics: Any,
    log: Any,
    tracker: Any,
) -> None:
    """Background coroutine: log slots stuck in transitional states for too long.

    Does not kill slots — just warns so a human (or future logic) can act.
    """
    while True:
        try:
            await asyncio.sleep(_CHECK_INTERVAL_S)
            now = time.time()

            for slot in list(slots.values()):
                if slot.status not in ("launching", "navigating", "captcha"):
                    continue
                if not slot.launched_at:
                    continue
                elapsed = now - slot.launched_at
                if elapsed > _STUCK_THRESHOLD_S:
                    log.warn(
                        "watchdog", "stuck_slot",
                        page_idx=slot.id,
                        status=slot.status,
                        elapsed_s=int(elapsed),
                        account=slot.account,
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            try:
                log.error("watchdog", "stuck_check_error", error=str(e))
            except Exception:
                pass
            await asyncio.sleep(10)
