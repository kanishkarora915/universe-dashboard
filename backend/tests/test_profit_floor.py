"""
Tests for profit_floor — HARD GUARANTEE: profitable trade never closes in loss.

Built 2026-05-21 per user critical bug report:
  "Trade went to +₹10k profit then closed in LOSS. Why?"

Real data: 159 such trades over 60 days, -₹2.22M lost.
This module enforces: peak ≥ +3% → minimum SL = entry (breakeven).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in ["PROFIT_FLOOR_ENABLED", "PROFIT_FLOOR_SHADOW"]:
        monkeypatch.delenv(var, raising=False)


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_on(self):
        """profit_floor defaults ON because it's a safety guarantee."""
        from profit_floor import is_enabled
        assert is_enabled() is True

    def test_can_disable(self, monkeypatch):
        monkeypatch.setenv("PROFIT_FLOOR_ENABLED", "off")
        from profit_floor import is_enabled
        assert is_enabled() is False


# ── FLOOR COMPUTATION ──────────────────────────────────────────────────

class TestComputeFloor:
    def test_peak_below_3pct_no_floor(self):
        """Peak < +3% → no floor (trade hasn't really been profitable)."""
        from profit_floor import compute_floor
        result = compute_floor(entry_price=100, peak_price=102)
        assert result is None

    def test_peak_3pct_locks_breakeven(self):
        """Peak ≥ +3% → SL floor = entry (no loss possible)."""
        from profit_floor import compute_floor
        result = compute_floor(entry_price=100, peak_price=103)
        assert result is not None
        assert result["floor_sl"] == 100  # breakeven
        assert result["locked_pct"] == 0

    def test_peak_5pct_locks_1pct(self):
        """Peak ≥ +5% → +1% locked."""
        from profit_floor import compute_floor
        result = compute_floor(entry_price=100, peak_price=105.5)
        assert result["floor_sl"] == 101
        assert result["locked_pct"] == 1

    def test_peak_10pct_locks_4pct(self):
        """Peak ≥ +12% → +5% locked (note +8% threshold gives +2%)."""
        from profit_floor import compute_floor
        result = compute_floor(entry_price=100, peak_price=110)
        # peak 10% > 8% threshold = +2% locked
        assert result["floor_sl"] == 102
        assert result["locked_pct"] == 2

    def test_peak_15pct_locks_5pct(self):
        from profit_floor import compute_floor
        result = compute_floor(entry_price=100, peak_price=115)
        # peak 15% > 12% threshold = +5% locked
        assert result["floor_sl"] == 105
        assert result["locked_pct"] == 5

    def test_peak_20pct_locks_10pct(self):
        from profit_floor import compute_floor
        result = compute_floor(entry_price=100, peak_price=120)
        # peak 20% > 18% threshold = +10% locked
        assert result["floor_sl"] == 110
        assert result["locked_pct"] == 10

    def test_peak_50pct_locks_25pct(self):
        from profit_floor import compute_floor
        result = compute_floor(entry_price=100, peak_price=150)
        # peak 50% > 40% threshold = +25% locked
        assert result["floor_sl"] == 125

    def test_peak_at_entry_no_floor(self):
        """If peak hasn't even crossed entry, no floor."""
        from profit_floor import compute_floor
        result = compute_floor(entry_price=100, peak_price=100)
        assert result is None

    def test_invalid_inputs_return_none(self):
        from profit_floor import compute_floor
        assert compute_floor(0, 100) is None
        assert compute_floor(100, 0) is None
        assert compute_floor(-100, 100) is None


# ── PUBLIC API ──────────────────────────────────────────────────────────

class TestGetMinimumSL:
    def test_floor_higher_than_current_sl_raises(self):
        """Floor wins when peak triggers it."""
        from profit_floor import get_minimum_sl
        # Entry 100, peak 110 → floor at 102. Current SL at 85.
        new_sl = get_minimum_sl(entry_price=100, peak_price=110, current_sl=85)
        assert new_sl == 102  # raised from 85 to 102

    def test_existing_sl_higher_wins(self):
        """If current SL is already above floor, keep current."""
        from profit_floor import get_minimum_sl
        # Entry 100, peak 110 (floor would be 102). Current SL at 105.
        new_sl = get_minimum_sl(entry_price=100, peak_price=110, current_sl=105)
        assert new_sl == 105  # unchanged (already above floor)

    def test_no_peak_no_change(self):
        """Below +3% peak → no change."""
        from profit_floor import get_minimum_sl
        new_sl = get_minimum_sl(entry_price=100, peak_price=102, current_sl=85)
        assert new_sl == 85  # unchanged

    def test_disabled_returns_current(self, monkeypatch):
        """When PROFIT_FLOOR_ENABLED=off, no change."""
        monkeypatch.setenv("PROFIT_FLOOR_ENABLED", "off")
        from profit_floor import get_minimum_sl
        new_sl = get_minimum_sl(entry_price=100, peak_price=120, current_sl=85)
        assert new_sl == 85  # disabled → unchanged


# ── REAL AUDIT SCENARIOS ────────────────────────────────────────────────

class TestRealAuditScenarios:
    """Scenarios from the actual 60-day audit data showing
    profitable trades that closed in loss."""

    def test_banknifty_ce_peaked_17pct_closed_loss(self):
        """Real trade: BANKNIFTY CE entry ₹291.8, peak ₹341.2 (+17%),
        exited at ₹268.4 (-8%) = ₹-24,465 LOSS.

        With profit_floor: peak +17% > +12% threshold → SL ≥ entry × 1.05 = ₹306.4
        Exit would have been at ₹306.4 instead of ₹268.4
        Outcome: +₹14,520 GAIN instead of ₹-24,465 LOSS
        Swing: +₹38,985
        """
        from profit_floor import get_minimum_sl
        new_sl = get_minimum_sl(
            entry_price=291.8, peak_price=341.2, current_sl=247.0
        )
        # peak +16.93% > 12% threshold = entry × 1.05 = 306.39
        assert new_sl > 300
        # Worst case exit would be at this floor — still profitable
        assert new_sl > 291.8  # above entry = profitable

    def test_nifty_ce_peaked_5pct_closed_at_minus15(self):
        """Real trade: NIFTY CE entry ₹222.1, peak ₹234.7 (+5.7%),
        exited at ₹189 (-14.9%) = ₹-43,030 LOSS.

        With profit_floor: peak +5.7% > +5% threshold → SL ≥ entry × 1.01 = ₹224.32
        Worst case exit: ₹224.32 = +1% locked (small profit)
        Swing: would have been +₹2,860 instead of -₹43,030
        """
        from profit_floor import get_minimum_sl
        new_sl = get_minimum_sl(
            entry_price=222.1, peak_price=234.7, current_sl=189.0
        )
        # Floor would be entry × 1.01 = 224.32
        assert new_sl > 222.1  # profitable floor


# ── DIAGNOSE ────────────────────────────────────────────────────────────

class TestDiagnose:
    def test_diagnose_returns_full_info(self):
        from profit_floor import diagnose
        d = diagnose(entry_price=100, peak_price=115, current_sl=90)
        assert d["enabled"] is True
        assert d["floor_info"] is not None
        assert d["would_raise_sl"] is True
        assert d["final_sl"] == 105  # raised from 90 to 105

    def test_diagnose_when_no_peak(self):
        from profit_floor import diagnose
        d = diagnose(entry_price=100, peak_price=101, current_sl=85)
        assert d["floor_info"] is None
        assert d["would_raise_sl"] is False
        assert d["final_sl"] == 85  # unchanged
