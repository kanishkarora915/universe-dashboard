"""
Tests for early_move.volume_profile — breakout confirmation detector.

Built 2026-05-22. Detects:
  • VOLUME_BREAKOUT  — new extreme + volume spike → real momentum
  • FAKEOUT_WARNING  — new extreme + low volume → likely reversal
  • VOLUME_EXHAUSTION — spike then collapse → move ending
  • VOLUME_NODE      — high-volume price levels (support/resistance)
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in ["EARLY_MOVE_VOLUME_ENABLED", "EARLY_MOVE_VOLUME_SHADOW"]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_state():
    from early_move import volume_profile
    volume_profile.reset_history()


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_off(self):
        from early_move.volume_profile import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_VOLUME_ENABLED", "on")
        from early_move.volume_profile import is_enabled
        assert is_enabled() is True

    def test_shadow_default_on(self):
        from early_move.volume_profile import is_shadow_enabled
        assert is_shadow_enabled() is True


# ── RECORDING ──────────────────────────────────────────────────────────

class TestRecording:
    def test_record_tick(self):
        from early_move.volume_profile import record_tick, get_history_size
        record_tick(idx="BANKNIFTY", spot=53000, volume_proxy=10000)
        record_tick(idx="BANKNIFTY", spot=53050, volume_proxy=12000)
        assert get_history_size().get("BANKNIFTY") == 2

    def test_invalid_input_ignored(self):
        from early_move.volume_profile import record_tick, get_history_size
        record_tick(idx="BANKNIFTY", spot=0, volume_proxy=10000)
        record_tick(idx="BANKNIFTY", spot=53000, volume_proxy=-5)
        assert get_history_size().get("BANKNIFTY", 0) == 0


# ── VOLUME BREAKOUT ────────────────────────────────────────────────────

class TestVolumeBreakout:
    def test_breakout_up_detected(self):
        """New session high + volume spike → VOLUME_BREAKOUT BULL."""
        from early_move.volume_profile import record_tick, detect_volume_signal
        now = time.time()
        # Build session: flat at 53000 with typical 10k volume
        for i in range(10):
            record_tick(idx="BANKNIFTY", spot=53000 + i,
                        volume_proxy=10000, timestamp=now - 600 + i * 30)
        # Now break to new high with 3x volume
        for i in range(4):
            record_tick(idx="BANKNIFTY", spot=53100 + i,
                        volume_proxy=30000, timestamp=now - 90 + i * 30)
        sig = detect_volume_signal(idx="BANKNIFTY")
        assert sig is not None
        assert sig["type"] == "VOLUME_BREAKOUT"
        assert sig["direction"] == "BULL"

    def test_breakout_down_detected(self):
        """New session low + volume spike → VOLUME_BREAKOUT BEAR."""
        from early_move.volume_profile import record_tick, detect_volume_signal
        now = time.time()
        for i in range(10):
            record_tick(idx="BANKNIFTY", spot=53000 - i,
                        volume_proxy=10000, timestamp=now - 600 + i * 30)
        for i in range(4):
            record_tick(idx="BANKNIFTY", spot=52900 - i,
                        volume_proxy=30000, timestamp=now - 90 + i * 30)
        sig = detect_volume_signal(idx="BANKNIFTY")
        assert sig is not None
        assert sig["type"] == "VOLUME_BREAKOUT"
        assert sig["direction"] == "BEAR"


# ── FAKEOUT WARNING ────────────────────────────────────────────────────

class TestFakeoutWarning:
    def test_fakeout_low_volume_at_high(self):
        """New high but LOW volume → FAKEOUT_WARNING."""
        from early_move.volume_profile import record_tick, detect_volume_signal
        now = time.time()
        # Session with typical 20k volume
        for i in range(10):
            record_tick(idx="BANKNIFTY", spot=53000 + i,
                        volume_proxy=20000, timestamp=now - 600 + i * 30)
        # New high but only 5k volume (0.25x typical)
        for i in range(4):
            record_tick(idx="BANKNIFTY", spot=53100 + i,
                        volume_proxy=5000, timestamp=now - 90 + i * 30)
        sig = detect_volume_signal(idx="BANKNIFTY")
        assert sig is not None
        assert sig["type"] == "FAKEOUT_WARNING"
        assert sig["direction"] == "AVOID"


# ── VOLUME NODES ───────────────────────────────────────────────────────

class TestVolumeNodes:
    def test_high_volume_node_identified(self):
        """Price level with most volume = top node."""
        from early_move.volume_profile import record_tick, get_volume_nodes
        now = time.time()
        # Heavy volume at 53000, light elsewhere
        for i in range(20):
            record_tick(idx="BANKNIFTY", spot=53000,
                        volume_proxy=50000, timestamp=now - 600 + i * 20)
        for i in range(5):
            record_tick(idx="BANKNIFTY", spot=53200,
                        volume_proxy=5000, timestamp=now - 100 + i * 20)
        nodes = get_volume_nodes("BANKNIFTY", top_n=2)
        assert len(nodes) >= 1
        # Top node should be near 53000
        assert abs(nodes[0]["price"] - 53000) <= 25


# ── detect_all ─────────────────────────────────────────────────────────

class TestDetectAll:
    def test_no_data_empty(self):
        from early_move.volume_profile import detect_all
        result = detect_all(idx="BANKNIFTY")
        assert result["signal_count"] == 0

    def test_check_and_log_returns_dict(self):
        from early_move.volume_profile import check_and_log
        result = check_and_log(idx="BANKNIFTY")
        assert "signals" in result
        assert "volume_nodes" in result

    def test_signals_have_required_fields(self):
        from early_move.volume_profile import record_tick, detect_all
        now = time.time()
        for i in range(10):
            record_tick(idx="BANKNIFTY", spot=53000 + i,
                        volume_proxy=10000, timestamp=now - 600 + i * 30)
        for i in range(4):
            record_tick(idx="BANKNIFTY", spot=53100 + i,
                        volume_proxy=35000, timestamp=now - 90 + i * 30)
        result = detect_all(idx="BANKNIFTY")
        for sig in result["signals"]:
            assert sig["signal"] == "EARLY_MOVE"
            assert sig["detector"] == "volume_profile"
            assert "direction" in sig
            assert 0 <= sig["confidence"] <= 1.0
