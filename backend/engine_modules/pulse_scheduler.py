"""
Pulse scheduler — drives engine's interval-based checks every 1s.

Extracted from engine.py (`_start_pulse_scheduler`).

Purpose:
  Pulse checks (position watcher, capitulation, forecast, OI capture,
  polarity flip, smart money, scalper) used to fire only when ticks
  arrived. During WS dead periods (Kite reconnect, lunch chop, 5+ min
  silence), they stale out.

  Independent 1Hz scheduler decouples pulse cadence from tick frequency.
  The engine still owns the dispatch logic (`_run_pulse_checks`); this
  module just calls it on a fixed cadence in a background thread.

Decoupled from engine.py for:
  • Independent testability
  • Clearer separation of concerns
  • Same scheduler could later drive other 1Hz cadences

Public API:
  PulseScheduler(engine_ref, interval=1.0, error_log_throttle=30)
    .start() → spawns background thread (idempotent)
    .stop()  → signals thread to exit
"""

import threading
import time
from typing import Any


class PulseScheduler:
    """Background thread that calls engine._run_pulse_checks() at fixed cadence.

    Args:
        engine:              the MarketEngine instance (must expose
                             `running` attr, `ticker` attr after connect,
                             and `_run_pulse_checks()` method)
        interval:            seconds between pulses (default 1.0)
        error_log_throttle:  min seconds between repeated error logs (30)
    """

    def __init__(
        self,
        engine: Any,
        interval: float = 1.0,
        error_log_throttle: int = 30,
    ) -> None:
        self.engine = engine
        self.interval = interval
        self.error_log_throttle = error_log_throttle

        self._thread: threading.Thread = None  # type: ignore[assignment]
        self._stop_signal = threading.Event()

    def start(self) -> None:
        """Spawn the scheduler thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_signal.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="pulse-scheduler",
        )
        self._thread.start()
        hz = (1.0 / self.interval) if self.interval > 0 else 0
        print(f"[PULSE-SCHED] Started ({hz:.0f}Hz, interval={self.interval}s)")

    def stop(self) -> None:
        """Signal thread to exit. Doesn't block."""
        self._stop_signal.set()

    def _loop(self) -> None:
        """Scheduler main loop."""
        last_error_log = 0.0

        while (
            not self._stop_signal.is_set()
            and getattr(self.engine, "running", False)
        ):
            try:
                # Original guard from engine.py: only fire pulses once the
                # ticker has been wired up (avoids race during start()).
                if hasattr(self.engine, "ticker"):
                    try:
                        self.engine._run_pulse_checks()
                    except Exception as e:
                        now_ts = time.time()
                        if now_ts - last_error_log > self.error_log_throttle:
                            print(f"[PULSE-SCHED] cycle err: {e}")
                            last_error_log = now_ts
            except Exception as e:
                # Outer errors (e.g. hasattr blew up) — always log.
                print(f"[PULSE-SCHED] outer err: {e}")

            time.sleep(self.interval)

        print("[PULSE-SCHED] Stopped")
