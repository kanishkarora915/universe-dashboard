"""
Tests for tick_watchdog — isolated tick-freshness monitor.

Contract these tests protect:
  1. Module is fully self-contained — no trading imports required.
  2. Missing engine → skip cycle, don't crash.
  3. Missing telegram/structured_logger → silent skip.
  4. Env-controlled thresholds override.
  5. State snapshot is thread-safe.
  6. Recovery actions escalate correctly.
  7. Kill action is opt-out-able via env.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Each test gets a clean slate — env cleared + module state reset."""
    for key in list(sys.modules):
        if key == "tick_watchdog":
            del sys.modules[key]
    for env_key in [
        "TICK_WATCHDOG_DISABLED",
        "TICK_WATCHDOG_CHECK_INTERVAL_SEC",
        "TICK_STALE_WARN_SEC",
        "TICK_STALE_RECONNECT_SEC",
        "TICK_STALE_RESTART_SEC",
        "TICK_STALE_KILL_SEC",
        "TICK_WATCHDOG_STAGE_COOLDOWN_SEC",
        "TICK_WATCHDOG_KILL_ENABLED",
        "TICK_WATCHDOG_MARKET_ONLY",
    ]:
        monkeypatch.delenv(env_key, raising=False)


# ── Module-level contract ────────────────────────────────────────────


class TestIsolation:
    def test_import_does_not_pull_trading_modules(self):
        """Importing tick_watchdog must not import trade_logger or scalper_mode."""
        prev = {k: v for k, v in sys.modules.items()}
        import tick_watchdog  # noqa: F401
        new_mods = set(sys.modules) - set(prev)
        forbidden = {
            "trade_logger", "scalper_mode", "position_watcher",
            "structure_gate", "day_classifier", "drawdown_guard",
        }
        leaked = forbidden & new_mods
        assert not leaked, f"tick_watchdog leaked trading imports: {leaked}"

    def test_module_has_public_api(self):
        import tick_watchdog as tw
        assert hasattr(tw, "start_watchdog")
        assert hasattr(tw, "stop_watchdog")
        assert hasattr(tw, "diagnostics")


# ── Env / defaults ───────────────────────────────────────────────────


class TestDefaults:
    def test_default_thresholds(self):
        import tick_watchdog as tw
        d = tw.diagnostics()
        t = d["thresholds"]
        assert t["warn_sec"] == 20.0
        assert t["reconnect_sec"] == 45.0
        assert t["restart_sec"] == 90.0
        assert t["kill_sec"] == 180.0
        assert t["kill_enabled"] is True
        assert t["market_only"] is True
        assert t["disabled"] is False

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("TICK_STALE_WARN_SEC", "10")
        monkeypatch.setenv("TICK_STALE_KILL_SEC", "300")
        monkeypatch.setenv("TICK_WATCHDOG_KILL_ENABLED", "off")
        import tick_watchdog as tw
        d = tw.diagnostics()
        assert d["thresholds"]["warn_sec"] == 10.0
        assert d["thresholds"]["kill_sec"] == 300.0
        assert d["thresholds"]["kill_enabled"] is False

    def test_disabled_env_prevents_start(self, monkeypatch):
        monkeypatch.setenv("TICK_WATCHDOG_DISABLED", "on")
        import tick_watchdog as tw
        started = tw.start_watchdog(lambda: None)
        assert started is False


# ── Diagnostics shape ────────────────────────────────────────────────


class TestDiagnostics:
    def test_diagnostics_shape(self):
        import tick_watchdog as tw
        d = tw.diagnostics()
        for key in ("started", "current_stage", "cycles",
                    "stage_1_fired", "stage_2_fired", "stage_3_fired",
                    "stage_4_fired", "recoveries", "thread_alive",
                    "in_market_hours", "thresholds"):
            assert key in d, f"missing key: {key}"

    def test_tick_age_is_none_when_never_seen(self):
        import tick_watchdog as tw
        d = tw.diagnostics()
        assert d["tick_age_sec"] is None


# ── Engine defensive reads ───────────────────────────────────────────


class TestEngineDefensive:
    def test_read_last_tick_ts_handles_missing_attr(self):
        import tick_watchdog as tw
        engine = MagicMock(spec=[])  # no attributes
        v = tw._read_last_tick_ts(engine)
        assert v == 0.0

    def test_read_last_tick_ts_handles_none(self):
        import tick_watchdog as tw
        engine = MagicMock()
        engine._last_tick_time = None
        v = tw._read_last_tick_ts(engine)
        assert v == 0.0

    def test_read_last_tick_ts_handles_bad_type(self):
        import tick_watchdog as tw
        engine = MagicMock()
        engine._last_tick_time = "not-a-number"
        v = tw._read_last_tick_ts(engine)
        assert v == 0.0

    def test_engine_running_false_when_no_attr(self):
        import tick_watchdog as tw
        engine = MagicMock(spec=[])
        assert tw._engine_running(engine) is False

    def test_engine_running_true(self):
        import tick_watchdog as tw
        engine = MagicMock()
        engine.running = True
        assert tw._engine_running(engine) is True


# ── Escalation logic (unit tests, no threading) ──────────────────────


class TestEscalation:
    """Feed the loop a known engine state → verify stage escalation.

    We drive by manipulating _last_tick_time on a mock engine and calling
    the loop with a stop-signal that fires immediately after one cycle.
    """

    def _one_cycle(self, engine, monkeypatch):
        """Run one cycle of the loop by setting _stop after first sleep."""
        import tick_watchdog as tw
        # Force market hours to be TRUE so watchdog runs
        monkeypatch.setattr(tw, "_in_market_hours", lambda: True)
        # Interrupt after first wait so we exit fast
        original_wait = tw._stop_event.wait

        def _quick_wait(_secs):
            tw._stop_event.set()
            return True
        monkeypatch.setattr(tw._stop_event, "wait", _quick_wait)
        try:
            tw._loop(lambda: engine)
        finally:
            tw._stop_event.clear()

    def test_healthy_stays_stage_0(self, monkeypatch):
        engine = MagicMock()
        engine.running = True
        engine._last_tick_time = time.time() - 5  # 5s ago = healthy
        self._one_cycle(engine, monkeypatch)
        import tick_watchdog as tw
        assert tw._state["current_stage"] == 0

    def test_stale_20s_fires_stage_1(self, monkeypatch):
        engine = MagicMock()
        engine.running = True
        engine._last_tick_time = time.time() - 25  # 25s stale
        # Mute telegram/log
        monkeypatch.setenv("TICK_WATCHDOG_STAGE_COOLDOWN_SEC", "0")
        self._one_cycle(engine, monkeypatch)
        import tick_watchdog as tw
        assert tw._state["current_stage"] == 1
        assert tw._state["stage_1_fired"] == 1

    def test_stale_50s_fires_stage_2_and_calls_restart_ticker(self, monkeypatch):
        engine = MagicMock()
        engine.running = True
        engine._last_tick_time = time.time() - 50  # 50s stale
        monkeypatch.setenv("TICK_WATCHDOG_STAGE_COOLDOWN_SEC", "0")
        self._one_cycle(engine, monkeypatch)
        import tick_watchdog as tw
        assert tw._state["current_stage"] == 2
        assert engine._restart_ticker.called

    def test_stale_100s_fires_stage_3_engine_restart(self, monkeypatch):
        engine = MagicMock()
        engine.running = True
        engine._last_tick_time = time.time() - 100
        monkeypatch.setenv("TICK_WATCHDOG_STAGE_COOLDOWN_SEC", "0")
        self._one_cycle(engine, monkeypatch)
        import tick_watchdog as tw
        assert tw._state["current_stage"] == 3
        assert engine.stop.called
        assert engine.start.called

    def test_stale_200s_kill_disabled_env_prevents_exit(self, monkeypatch):
        engine = MagicMock()
        engine.running = True
        engine._last_tick_time = time.time() - 200
        monkeypatch.setenv("TICK_WATCHDOG_STAGE_COOLDOWN_SEC", "0")
        monkeypatch.setenv("TICK_WATCHDOG_KILL_ENABLED", "off")
        # If os._exit gets called, test would abort. If disabled works,
        # we return normally.
        self._one_cycle(engine, monkeypatch)
        import tick_watchdog as tw
        assert tw._state["current_stage"] == 4


# ── Optional side-channel absence (no telegram/log) ──────────────────


class TestOptionalDeps:
    def test_missing_telegram_does_not_crash(self, monkeypatch):
        """Simulate telegram_alerts import failing."""
        monkeypatch.setitem(sys.modules, "telegram_alerts", None)
        import tick_watchdog as tw
        # This should not raise
        tw._send_telegram("test message", key="test")

    def test_missing_structured_logger_does_not_crash(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "structured_logger", None)
        import tick_watchdog as tw
        tw._log_event("test_event", foo="bar")
