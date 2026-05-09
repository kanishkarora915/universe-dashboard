"""
WebSocket watchdog — auto-detects stale ticks and force-reconnects.

Extracted from engine.py (`_start_ws_watchdog`).

Purpose:
  Critical reliability layer. The "engine running but ticks frozen"
  bug used to require manual Render restart. This watchdog runs in
  a background thread, monitors `last_tick_time`, and if no ticks
  for 60+ seconds during market hours, force-reconnects the ticker.

Decoupled from engine.py for:
  • Independent testability
  • Clearer separation of concerns
  • Same logic could later be applied to other WS streams

Public API:
  WSWatchdog(engine_ref, interval=30, stale_threshold=60)
    .start()  → spawns background thread
    .stop()   → signals thread to exit
"""

import threading
import time
from typing import Any


class WSWatchdog:
    """Monitor WS tick health and trigger reconnect on staleness.

    Args:
        engine:           the MarketEngine instance (read self.running,
                          self._last_tick_time; call self._restart_ticker())
        check_interval:   seconds between health checks (default 30)
        stale_threshold:  seconds of no ticks before considered stale (60)
        consecutive_for_reconnect: stale checks needed before reconnecting (2)
        min_reconnect_gap: minimum seconds between reconnect attempts (300)
    """

    def __init__(
        self,
        engine: Any,
        check_interval: int = 30,
        stale_threshold: int = 60,
        consecutive_for_reconnect: int = 2,
        min_reconnect_gap: int = 300,
    ) -> None:
        self.engine = engine
        self.check_interval = check_interval
        self.stale_threshold = stale_threshold
        self.consecutive_for_reconnect = consecutive_for_reconnect
        self.min_reconnect_gap = min_reconnect_gap

        self._thread: threading.Thread = None  # type: ignore[assignment]
        self._stop_signal = threading.Event()

    def start(self) -> None:
        """Spawn the watchdog thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_signal.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="ws-watchdog",
        )
        self._thread.start()
        print(f"[WS-WATCHDOG] Started (interval={self.check_interval}s, "
              f"stale={self.stale_threshold}s)")

    def stop(self) -> None:
        """Signal thread to exit. Doesn't block."""
        self._stop_signal.set()

    def _loop(self) -> None:
        """Watchdog main loop."""
        consecutive_stale = 0
        last_restart_attempt = 0

        while not self._stop_signal.is_set():
            try:
                if not getattr(self.engine, "running", False):
                    time.sleep(self.check_interval)
                    continue

                if not self._is_market_hours():
                    consecutive_stale = 0  # reset between sessions
                    time.sleep(self.check_interval)
                    continue

                last_tick_time = getattr(self.engine, "_last_tick_time", 0)
                now = time.time()
                last_tick_age = now - last_tick_time if last_tick_time else 999

                if last_tick_age > self.stale_threshold:
                    consecutive_stale += 1
                    print(f"[WS-WATCHDOG] STALE detected: no ticks for "
                          f"{last_tick_age:.0f}s (threshold={self.stale_threshold}s) "
                          f"[consecutive={consecutive_stale}]")

                    if (consecutive_stale >= self.consecutive_for_reconnect
                            and (now - last_restart_attempt) > self.min_reconnect_gap):
                        print(f"[WS-WATCHDOG] FORCE RECONNECT: WS confirmed "
                              f"dead, restarting ticker...")
                        self._log_force_reconnect(last_tick_age, consecutive_stale)
                        last_restart_attempt = now
                        try:
                            self.engine._restart_ticker()
                            consecutive_stale = 0
                            self.engine._last_tick_time = now
                            print(f"[WS-WATCHDOG] Ticker restart complete — "
                                  f"watching for fresh ticks...")
                            self._log_reconnect_success()
                        except Exception as e:
                            print(f"[WS-WATCHDOG] Restart FAILED: {e}")
                            import traceback
                            traceback.print_exc()
                            self._log_reconnect_failed(str(e))
                else:
                    consecutive_stale = 0  # ticks flowing — reset

            except Exception as e:
                print(f"[WS-WATCHDOG] cycle err: {e}")

            time.sleep(self.check_interval)

        print("[WS-WATCHDOG] Stopped")

    def _is_market_hours(self) -> bool:
        """True if current IST time is during NSE market hours (9:15-15:30, Mon-Fri)."""
        try:
            from datetime import datetime
            import pytz
            ist = pytz.timezone("Asia/Kolkata")
            now = datetime.now(ist)
            if now.weekday() > 4:  # Sat/Sun
                return False
            t = now.hour * 60 + now.minute
            return 9 * 60 + 15 <= t <= 15 * 60 + 30
        except Exception:
            return False

    # ── Structured logging helpers (best-effort, non-fatal) ───────────────

    def _log_force_reconnect(self, last_tick_age: float, consecutive: int) -> None:
        try:
            from structured_logger import log
            log.warn(
                "ws_force_reconnect",
                last_tick_age_sec=round(last_tick_age, 1),
                consecutive_stale=consecutive,
            )
        except Exception:
            pass

    def _log_reconnect_success(self) -> None:
        try:
            from structured_logger import log
            log.info("ws_reconnect_success")
        except Exception:
            pass

    def _log_reconnect_failed(self, error: str) -> None:
        try:
            from structured_logger import log
            log.error("ws_reconnect_failed", error=error)
        except Exception:
            pass
