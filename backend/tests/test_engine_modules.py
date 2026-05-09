"""
Tests for engine_modules — extracted submodules from engine.py.

These tests verify the EXTRACTED logic works in isolation, before
we wire it back into engine.py. Run before/after wiring to catch
regressions during the modularization refactor.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine_modules.price_action import (
    record_spot_tick,
    prune_history,
    get_recent_window,
)
from engine_modules.watchdog import WSWatchdog
from engine_modules.cache_populator import CachePopulator


# ── price_action ──────────────────────────────────────────────────────────

class TestPriceAction:
    def test_record_spot_tick_basic(self):
        history = {}
        record_spot_tick(history, "NIFTY", 24500.5, "2026-05-09T12:00:00")
        assert "NIFTY" in history
        assert len(history["NIFTY"]) == 1
        assert history["NIFTY"][0]["ltp"] == 24500.5
        assert history["NIFTY"][0]["t"] == "2026-05-09T12:00:00"

    def test_record_multiple_ticks(self):
        history = {}
        for i in range(5):
            record_spot_tick(history, "NIFTY", 24500 + i, f"2026-05-09T12:00:0{i}")
        assert len(history["NIFTY"]) == 5

    def test_record_separate_indices(self):
        history = {}
        record_spot_tick(history, "NIFTY", 24500, "t1")
        record_spot_tick(history, "BANKNIFTY", 55000, "t2")
        assert len(history) == 2
        assert history["NIFTY"][0]["ltp"] == 24500
        assert history["BANKNIFTY"][0]["ltp"] == 55000

    def test_prune_to_max_entries(self):
        history = {"NIFTY": []}
        for i in range(10):
            record_spot_tick(history, "NIFTY", 100 + i, f"t{i}", max_entries=5)
        # Should keep only last 5
        assert len(history["NIFTY"]) == 5
        # Last 5 = ticks 5-9
        assert history["NIFTY"][0]["ltp"] == 105
        assert history["NIFTY"][-1]["ltp"] == 109

    def test_prune_history_global(self):
        history = {
            "NIFTY":     [{"t": f"t{i}", "ltp": i} for i in range(800)],
            "BANKNIFTY": [{"t": f"t{i}", "ltp": i} for i in range(600)],
        }
        prune_history(history, max_entries=500)
        assert len(history["NIFTY"]) == 500
        assert len(history["BANKNIFTY"]) == 500

    def test_get_recent_window(self):
        history = {"NIFTY": [{"t": f"t{i}", "ltp": i} for i in range(100)]}
        window = get_recent_window(history, "NIFTY", 10)
        assert len(window) == 10
        assert window[-1]["ltp"] == 99

    def test_get_recent_window_empty(self):
        history = {}
        window = get_recent_window(history, "NIFTY", 10)
        assert window == []


# ── watchdog ──────────────────────────────────────────────────────────────

class TestWSWatchdog:
    def test_init_defaults(self):
        engine = MagicMock()
        wd = WSWatchdog(engine)
        assert wd.check_interval == 30
        assert wd.stale_threshold == 60
        assert wd.consecutive_for_reconnect == 2

    def test_init_custom(self):
        engine = MagicMock()
        wd = WSWatchdog(
            engine,
            check_interval=10,
            stale_threshold=30,
            consecutive_for_reconnect=3,
        )
        assert wd.check_interval == 10
        assert wd.stale_threshold == 30
        assert wd.consecutive_for_reconnect == 3

    def test_start_stop_thread(self):
        """Watchdog start spawns thread; stop signals exit."""
        engine = MagicMock()
        engine.running = False  # so loop exits quickly
        wd = WSWatchdog(engine, check_interval=1)
        wd.start()
        assert wd._thread is not None
        assert wd._thread.is_alive()
        wd.stop()
        # Give thread a moment to exit (it should respect stop signal next tick)
        time.sleep(2)

    def test_start_idempotent(self):
        """Calling start twice doesn't spawn duplicate thread."""
        engine = MagicMock()
        engine.running = False
        wd = WSWatchdog(engine, check_interval=1)
        wd.start()
        first_thread = wd._thread
        wd.start()  # second call should not spawn new thread
        assert wd._thread is first_thread
        wd.stop()


# ── cache_populator ────────────────────────────────────────────────────────

class TestCachePopulator:
    def test_init(self):
        engine = MagicMock()
        cp = CachePopulator(engine, interval=5.0)
        assert cp.engine is engine
        assert cp.interval == 5.0

    def test_simple_keys_constant(self):
        """SIMPLE_ENGINE_KEYS should be a list of (key, method) tuples."""
        for entry in CachePopulator.SIMPLE_ENGINE_KEYS:
            assert len(entry) == 2
            key, method = entry
            assert isinstance(key, str)
            assert isinstance(method, str)
            assert method.startswith("get_")

    def test_safe_set_swallows_errors(self):
        """_safe_set should not raise even if getter throws."""
        engine = MagicMock()
        cp = CachePopulator(engine)

        cache_set = MagicMock()
        bad_getter = MagicMock(side_effect=ValueError("boom"))

        # Should not raise
        cp._safe_set(cache_set, "test_key", bad_getter, cycle=1)
        # cache_set should not have been called (getter failed)
        cache_set.assert_not_called()

    def test_safe_set_calls_cache_on_success(self):
        engine = MagicMock()
        cp = CachePopulator(engine)

        cache_set = MagicMock()
        good_getter = MagicMock(return_value={"data": 42})

        cp._safe_set(cache_set, "test_key", good_getter, cycle=1)
        cache_set.assert_called_once_with("test_key", {"data": 42})


# ── Module imports work ───────────────────────────────────────────────────

class TestPackageImports:
    def test_engine_modules_re_exports(self):
        """__init__.py must re-export public names."""
        import engine_modules
        assert hasattr(engine_modules, "record_spot_tick")
        assert hasattr(engine_modules, "prune_history")
        assert hasattr(engine_modules, "WSWatchdog")
        assert hasattr(engine_modules, "CachePopulator")
