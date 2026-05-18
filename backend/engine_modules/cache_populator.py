"""
Cache populator — pre-computes hot endpoint responses every 3s.

Extracted from engine.py (`_start_cache_populator`).

Purpose:
  Without this, API endpoints recomputed responses on every request
  (100-400ms each). Tab open fired 30-40 requests → 5-10s of laggy UI.

  This populator does the heavy compute ONCE per cycle and writes
  results to api_cache. Endpoints then read from cache (sub-ms).

Design:
  • Background thread, polls every 3 seconds
  • Per-key try/except — one failure doesn't kill the loop
  • Errors throttled (logged once per minute, not every cycle)
  • All cache writes via api_cache.cache_set()
  • Reads engine state — does NOT modify it

Public API:
  CachePopulator(engine_ref, interval=3.0)
    .start() → spawns background thread
    .stop()  → signals thread to exit
"""

import threading
import time
from typing import Any


class CachePopulator:
    """Background thread that pre-computes hot endpoint responses."""

    # Tuples: (cache key, engine method name)
    # Used for the simple "call engine.method() → cache_set(key, result)" pattern
    SIMPLE_ENGINE_KEYS = [
        ("oi_summary",     "get_oi_change_summary"),
        ("unusual",        "get_unusual"),
        ("sellers",        "get_sellers"),
        ("trap_verdict",   "get_trap_verdict"),
        ("signals",        "get_signals"),
        ("seller_summary", "get_seller_summary"),
        ("trade_analysis", "get_trade_analysis"),
        ("hidden_shift",   "get_hidden_shift"),
        ("price_action",   "get_price_action"),
        ("intraday",       "get_intraday"),
        ("nextday",        "get_nextday"),
        ("multi_tf",       "get_multi_timeframe"),
    ]

    def __init__(self, engine: Any, interval: float = 3.0) -> None:
        self.engine = engine
        self.interval = interval
        self._thread: threading.Thread = None  # type: ignore[assignment]
        self._stop_signal = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_signal.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="cache-populator",
        )
        self._thread.start()
        print(f"[CACHE-POP] Started — pre-computing hot endpoints every {self.interval}s")

    def stop(self) -> None:
        self._stop_signal.set()

    def _loop(self) -> None:
        try:
            from api_cache import cache_set
        except ImportError as e:
            print(f"[CACHE-POP] api_cache import failed: {e}")
            return

        cycle = 0
        while not self._stop_signal.is_set() and getattr(self.engine, "running", False):
            cycle += 1
            try:
                self._populate_one_cycle(cache_set, cycle)
            except Exception as e:
                print(f"[CACHE-POP] outer error: {e}")
            time.sleep(self.interval)

        print("[CACHE-POP] Stopped")

    def _populate_one_cycle(self, cache_set: Any, cycle: int) -> None:
        """One pass of populate. Public for testability."""
        # Live data (heaviest hit endpoint, polled every 5s by frontend)
        self._safe_set(cache_set, "live", lambda: self.engine.get_live_data(), cycle)

        # Option chains (called per index, polled by multiple tabs)
        for idx in ["NIFTY", "BANKNIFTY"]:
            self._safe_set(
                cache_set,
                f"chain_{idx}",
                lambda i=idx: self.engine.get_option_chain(i),
                cycle,
            )

        # Simple "call method, cache result" entries
        for key, method_name in self.SIMPLE_ENGINE_KEYS:
            if hasattr(self.engine, method_name):
                getter = getattr(self.engine, method_name)
                self._safe_set(cache_set, key, getter, cycle)

        # Open trades (PnL tab — heavy SQLite read)
        if hasattr(self.engine, "trade_manager") and self.engine.trade_manager:
            self._safe_set(
                cache_set,
                "trades_open",
                lambda: self.engine.trade_manager.get_open_trades(),
                cycle,
            )

        # Forecast live
        try:
            from forecast_engine import get_live_state as _fc_state
            self._safe_set(cache_set, "forecast_live", _fc_state, cycle)
        except ImportError:
            pass

        # Watcher status (header indicator, polled often)
        self._safe_set(
            cache_set,
            "watcher_status",
            self._compute_watcher_status,
            cycle,
        )

        # Smart money detector
        try:
            from smart_money_detector import get_live_state as _sm_state
            self._safe_set(cache_set, "smart_money_live", _sm_state, cycle)
        except ImportError:
            pass

        # Reversal/capitulation live (used by Reversal tab)
        try:
            from capitulation_engine import get_live_state as _cap_state
            self._safe_set(cache_set, "reversal_live", _cap_state, cycle)
        except ImportError:
            pass

        # Positions aggregate health
        try:
            from position_watcher import get_last_health
            self._safe_set(cache_set, "positions_health", get_last_health, cycle)
        except ImportError:
            pass

    def _safe_set(self, cache_set: Any, key: str, getter: Any, cycle: int) -> None:
        """Wrap one cache_set call with try/except + throttled error log."""
        try:
            value = getter()
            cache_set(key, value)
        except Exception as e:
            if cycle % 20 == 0:  # ~once per minute (3s cycle × 20)
                print(f"[CACHE-POP] {key} error: {e}")

    def _compute_watcher_status(self) -> dict:
        """Build watcher_status payload from position_watcher state."""
        from position_watcher import _last_health_cache
        cached_h = list(_last_health_cache.values())
        last_pulse_ts = max([h.get("ts", 0) for h in cached_h], default=0)
        now_ts = time.time()
        age = (now_ts - last_pulse_ts) if last_pulse_ts else None
        return {
            "live": bool(age is not None and age < 90),
            "last_pulse_age_sec": round(age, 1) if age is not None else None,
            "cached_positions": len(cached_h),
            "main_count": len([h for h in cached_h if h.get("source") == "MAIN"]),
            "scalper_count": len([h for h in cached_h if h.get("source") == "SCALPER"]),
            "stub_count": len([h for h in cached_h if h.get("stub")]),
        }
