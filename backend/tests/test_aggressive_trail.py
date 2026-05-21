"""
Tests for aggressive_trail — peak-anchored tight trail SL.

Built 2026-05-21 per user vision:
  "Why focus only on losses? I want BIG PROFITS too."

Captures more of the move by trailing tighter from PEAK (not entry).
At +51% peak, instead of locking +25% (entry-anchored), locks +44%
(peak-anchored 5% giveback).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in ["AGGRESSIVE_TRAIL_ENABLED", "AGGRESSIVE_TRAIL_SHADOW"]:
        monkeypatch.delenv(var, raising=False)


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_off(self):
        from aggressive_trail import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("AGGRESSIVE_TRAIL_ENABLED", "on")
        from aggressive_trail import is_enabled
        assert is_enabled() is True

    def test_shadow_default_on(self):
        from aggressive_trail import is_shadow_enabled
        assert is_shadow_enabled() is True


# ── CORE CALCULATION ────────────────────────────────────────────────────

class TestAggressiveCalculation:
    def test_below_5pct_no_trail(self):
        """Peak < +5% → no aggressive trail yet."""
        from aggressive_trail import calculate_aggressive_trail
        result = calculate_aggressive_trail(
            entry_price=100, peak_price=102, current_price=101, current_sl=90
        )
        assert result is None

    def test_5_to_10_locks_breakeven(self):
        """Peak +5% to +10% → SL = entry (breakeven)."""
        from aggressive_trail import calculate_aggressive_trail
        result = calculate_aggressive_trail(
            entry_price=100, peak_price=108, current_price=105, current_sl=90
        )
        assert result is not None
        assert result["new_sl"] == 100  # breakeven

    def test_10_to_20_8pct_giveback(self):
        """Peak +10% to +20% → SL = peak × 0.92 (8% giveback)."""
        from aggressive_trail import calculate_aggressive_trail
        result = calculate_aggressive_trail(
            entry_price=100, peak_price=115, current_price=112, current_sl=100
        )
        # peak=115, 8% giveback = 115*0.92 = 105.8
        assert result is not None
        # SL clamped to current × 0.995 if needed (105.8 < 111.44 so OK)
        assert abs(result["new_sl"] - 105.8) < 0.5

    def test_20_to_40_6pct_giveback(self):
        """Peak +20% to +40% → SL = peak × 0.94 (6% giveback)."""
        from aggressive_trail import calculate_aggressive_trail
        result = calculate_aggressive_trail(
            entry_price=100, peak_price=130, current_price=128, current_sl=105
        )
        # peak=130, 6% giveback = 130*0.94 = 122.2
        assert result is not None
        assert abs(result["new_sl"] - 122.2) < 0.5

    def test_40_to_70_runner_mode_5pct(self):
        """Peak +40% to +70% → SL = peak × 0.95 (5% giveback — runner)."""
        from aggressive_trail import calculate_aggressive_trail
        result = calculate_aggressive_trail(
            entry_price=100, peak_price=150, current_price=148, current_sl=122
        )
        # peak=150, 5% giveback = 142.5
        assert result is not None
        assert abs(result["new_sl"] - 142.5) < 0.5

    def test_above_70_moonshot_4pct(self):
        """Peak >+70% → SL = peak × 0.96 (4% giveback — moonshot)."""
        from aggressive_trail import calculate_aggressive_trail
        result = calculate_aggressive_trail(
            entry_price=100, peak_price=180, current_price=175, current_sl=142
        )
        # peak=180, 4% giveback = 172.8
        assert result is not None
        # Could be clamped to safe_max = 175 × 0.995 = 174.125
        # Either way, much tighter than 25% locked (which would be 125)
        assert result["new_sl"] > 170

    def test_dont_lower_sl(self):
        """If new SL would be below current SL, return None."""
        from aggressive_trail import calculate_aggressive_trail
        # Peak +51% but SL already at +35%
        result = calculate_aggressive_trail(
            entry_price=100, peak_price=151, current_price=148, current_sl=135
        )
        # peak=151, 5% giveback = 143.45, current_sl=135 → raises to 143.45
        # That's higher than 135, so should return value
        assert result is not None

        # Now try with current_sl already at 144 (above what aggressive would set)
        result2 = calculate_aggressive_trail(
            entry_price=100, peak_price=151, current_price=148, current_sl=144
        )
        # aggressive would set ~143.45, but current_sl=144 is higher → None
        assert result2 is None

    def test_invalid_inputs_return_none(self):
        from aggressive_trail import calculate_aggressive_trail
        assert calculate_aggressive_trail(0, 100, 100, 80) is None
        assert calculate_aggressive_trail(100, 0, 100, 80) is None
        assert calculate_aggressive_trail(100, 100, 0, 80) is None


# ── PUBLIC API ──────────────────────────────────────────────────────────

class TestGetOrLegacy:
    def test_disabled_returns_legacy(self):
        """When AGGRESSIVE_TRAIL_ENABLED=off, return legacy SL."""
        from aggressive_trail import get_or_legacy
        sl = get_or_legacy(
            entry_price=100, peak_price=150, current_price=148,
            current_sl=110, legacy_sl=125,
        )
        assert sl == 125  # unchanged

    def test_enabled_uses_aggressive(self, monkeypatch):
        monkeypatch.setenv("AGGRESSIVE_TRAIL_ENABLED", "on")
        from aggressive_trail import get_or_legacy
        sl = get_or_legacy(
            entry_price=100, peak_price=150, current_price=148,
            current_sl=110, legacy_sl=125,
        )
        # peak=150, 5% giveback = 142.5, higher than legacy 125
        assert sl > 125  # aggressive won

    def test_legacy_wins_if_higher(self, monkeypatch):
        """If legacy SL happens to be higher than aggressive, keep legacy."""
        monkeypatch.setenv("AGGRESSIVE_TRAIL_ENABLED", "on")
        from aggressive_trail import get_or_legacy
        sl = get_or_legacy(
            entry_price=100, peak_price=120, current_price=118,
            current_sl=100, legacy_sl=115,  # legacy is very high
        )
        # peak=120, 8% giveback = 110.4
        # legacy=115 is higher → keep legacy
        assert sl == 115


# ── DELTA VS LEGACY ─────────────────────────────────────────────────────

class TestDeltaVsLegacy:
    def test_aggressive_beats_legacy_on_big_winners(self):
        """The bigger the peak, the more aggressive trail wins vs legacy.

        Real data scenario: Win #1 NIFTY PE entry ₹207, peak ₹312 (+51%)
        Legacy would lock at ~entry × 1.25 = ₹259 (+25%)
        Aggressive locks at peak × 0.95 = ₹296 (+43%)
        Delta: +₹37 per qty (more profit captured)
        """
        from aggressive_trail import calculate_aggressive_trail
        result = calculate_aggressive_trail(
            entry_price=207, peak_price=312, current_price=308, current_sl=240,
        )
        assert result is not None
        # Aggressive should give ~296 (peak × 0.95)
        assert result["new_sl"] > 290
        assert result["locked_pct"] > 40  # vs legacy ~+25%

    def test_compare_with_legacy_returns_full_dict(self):
        from aggressive_trail import compare_with_legacy
        c = compare_with_legacy(
            entry_price=100, peak_price=130, current_price=128,
            current_sl=110, legacy_sl=115,
        )
        assert "aggressive_new_sl" in c
        assert "delta_vs_legacy" in c
        # Aggressive 6% giveback at 130 = 122.2, legacy 115, delta = 7.2
        assert c["delta_vs_legacy"] is not None
        assert c["delta_vs_legacy"] > 0
