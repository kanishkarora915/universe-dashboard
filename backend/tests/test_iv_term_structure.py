"""
Tests for early_move.iv_term_structure — volatility-timing detector.

Built 2026-05-22. Detects:
  • IV EXPANSION  — IV rising fast → move coming, vega friendly
  • IV CRUSH      — IV falling fast → don't buy, vega against you
  • IV INVERSION  — near-month IV > next-month → imminent move
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in ["EARLY_MOVE_IV_ENABLED", "EARLY_MOVE_IV_SHADOW"]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_state():
    from early_move import iv_term_structure
    iv_term_structure.reset_history()


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_off(self):
        from early_move.iv_term_structure import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_IV_ENABLED", "on")
        from early_move.iv_term_structure import is_enabled
        assert is_enabled() is True

    def test_shadow_default_on(self):
        from early_move.iv_term_structure import is_shadow_enabled
        assert is_shadow_enabled() is True


# ── RECORDING ──────────────────────────────────────────────────────────

class TestRecording:
    def test_record_iv(self):
        from early_move.iv_term_structure import record_iv, get_history_size
        record_iv(idx="NIFTY", atm_iv=15.0)
        record_iv(idx="NIFTY", atm_iv=15.5)
        sizes = get_history_size()
        assert sizes.get("iv|NIFTY") == 2

    def test_invalid_iv_ignored(self):
        from early_move.iv_term_structure import record_iv, get_history_size
        record_iv(idx="NIFTY", atm_iv=0)
        record_iv(idx="NIFTY", atm_iv=-5)
        assert get_history_size().get("iv|NIFTY", 0) == 0


# ── IV EXPANSION ───────────────────────────────────────────────────────

class TestIVExpansion:
    def test_expansion_detected(self):
        """IV rising 14% → 17% (+21% relative) → IV_EXPANSION."""
        from early_move.iv_term_structure import record_iv, detect_iv_signal
        now = time.time()
        record_iv(idx="NIFTY", atm_iv=14.0, timestamp=now - 600)
        record_iv(idx="NIFTY", atm_iv=15.5, timestamp=now - 300)
        record_iv(idx="NIFTY", atm_iv=17.0, timestamp=now)
        sig = detect_iv_signal(idx="NIFTY")
        assert sig is not None
        assert sig["type"] == "IV_EXPANSION"
        assert sig["vega_friendly"] is True
        assert sig["direction"] == "NEUTRAL"

    def test_small_iv_rise_no_signal(self):
        """IV up only 5% relative → below 15% threshold → no signal."""
        from early_move.iv_term_structure import record_iv, detect_iv_signal
        now = time.time()
        record_iv(idx="NIFTY", atm_iv=15.0, timestamp=now - 600)
        record_iv(idx="NIFTY", atm_iv=15.3, timestamp=now - 300)
        record_iv(idx="NIFTY", atm_iv=15.7, timestamp=now)
        sig = detect_iv_signal(idx="NIFTY")
        assert sig is None


# ── IV CRUSH ───────────────────────────────────────────────────────────

class TestIVCrush:
    def test_crush_detected(self):
        """IV falling 19% → 14% (-26% relative) → IV_CRUSH → AVOID."""
        from early_move.iv_term_structure import record_iv, detect_iv_signal
        now = time.time()
        record_iv(idx="BANKNIFTY", atm_iv=19.0, timestamp=now - 600)
        record_iv(idx="BANKNIFTY", atm_iv=16.5, timestamp=now - 300)
        record_iv(idx="BANKNIFTY", atm_iv=14.0, timestamp=now)
        sig = detect_iv_signal(idx="BANKNIFTY")
        assert sig is not None
        assert sig["type"] == "IV_CRUSH"
        assert sig["direction"] == "AVOID"
        assert sig["vega_friendly"] is False


# ── IV INVERSION ───────────────────────────────────────────────────────

class TestIVInversion:
    def test_inversion_detected(self):
        """Near-month IV 22% > next-month 17% → IV_INVERSION."""
        from early_move.iv_term_structure import record_term_structure, detect_inversion
        record_term_structure(idx="NIFTY", near_iv=22.0, next_iv=17.0)
        sig = detect_inversion(idx="NIFTY")
        assert sig is not None
        assert sig["type"] == "IV_INVERSION"
        assert sig["vega_friendly"] is True

    def test_normal_term_structure_no_signal(self):
        """Near 15% < next 17% (normal) → no inversion."""
        from early_move.iv_term_structure import record_term_structure, detect_inversion
        record_term_structure(idx="NIFTY", near_iv=15.0, next_iv=17.0)
        sig = detect_inversion(idx="NIFTY")
        assert sig is None


# ── detect_all ─────────────────────────────────────────────────────────

class TestDetectAll:
    def test_no_data_empty_result(self):
        from early_move.iv_term_structure import detect_all
        result = detect_all(idx="NIFTY")
        assert result["signal_count"] == 0
        assert result["vega_friendly"] is None

    def test_crush_makes_vega_unfriendly(self):
        from early_move.iv_term_structure import record_iv, detect_all
        now = time.time()
        record_iv(idx="NIFTY", atm_iv=20.0, timestamp=now - 600)
        record_iv(idx="NIFTY", atm_iv=17.0, timestamp=now - 300)
        record_iv(idx="NIFTY", atm_iv=14.0, timestamp=now)
        result = detect_all(idx="NIFTY")
        assert result["vega_friendly"] is False

    def test_check_and_log_returns_dict(self):
        from early_move.iv_term_structure import check_and_log
        result = check_and_log(idx="NIFTY")
        assert "signals" in result
        assert "vega_friendly" in result
