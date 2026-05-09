"""
Tests for atr_targets.py — T1/T2/SL calculation logic.

Critical because: wrong T1/T2 means real money. Bugs here directly
hit the user's PnL. Today's commit (716cfa5) fixed bounds — these
tests lock in that behavior so future changes don't regress.

Run: pytest backend/tests/test_atr_targets.py -v
"""

import sys
from pathlib import Path

# Allow imports from backend/
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from atr_targets import calculate_targets


class TestCalculateTargetsHardBounds:
    """T1/T2/SL must always respect user-spec bounds:
        T1: 5-8%, T2: 10-15%, SL: 3-5%
    """

    def test_typical_atr_within_bounds(self):
        """Typical ATR (5%) → T1=8% (capped), T2=15% (capped), SL=5% (capped)"""
        r = calculate_targets(entry_price=100, atr_pct=0.05, vol_multiplier=1.0)
        assert r["sl_pct"] <= 5.0, "SL must be capped at 5%"
        assert 5.0 <= r["t1_pct"] <= 8.0, "T1 must be in 5-8% range"
        assert 10.0 <= r["t2_pct"] <= 15.0, "T2 must be in 10-15% range"

    def test_low_atr_uses_floor(self):
        """Tiny ATR (1%) → still gets minimum T1=5%, T2=10%, SL=3%"""
        r = calculate_targets(entry_price=100, atr_pct=0.01, vol_multiplier=1.0)
        assert r["sl_pct"] >= 3.0, "SL must have 3% floor"
        assert r["t1_pct"] >= 5.0, "T1 must have 5% floor"
        assert r["t2_pct"] >= 10.0, "T2 must have 10% floor"

    def test_high_atr_uses_ceiling(self):
        """Huge ATR (50%) → still capped at T1=8%, T2=15%, SL=5%"""
        r = calculate_targets(entry_price=100, atr_pct=0.50, vol_multiplier=1.0)
        assert r["sl_pct"] <= 5.0, "SL ceiling must hold"
        assert r["t1_pct"] <= 8.0, "T1 ceiling must hold"
        assert r["t2_pct"] <= 15.0, "T2 ceiling must hold"

    def test_vol_multiplier_high(self):
        """High-vol regime (vol_mult=1.5) — bounds still hold"""
        r = calculate_targets(entry_price=100, atr_pct=0.05, vol_multiplier=1.5)
        assert r["sl_pct"] <= 5.0
        assert r["t1_pct"] <= 8.0
        assert r["t2_pct"] <= 15.0

    def test_vol_multiplier_expiry(self):
        """Expiry day (vol_mult=0.7) — floors still hold"""
        r = calculate_targets(entry_price=100, atr_pct=0.05, vol_multiplier=0.7)
        assert r["sl_pct"] >= 3.0
        assert r["t1_pct"] >= 5.0
        assert r["t2_pct"] >= 10.0


class TestCalculateTargetsPriceCalculation:
    """Verify SL/T1/T2 prices are computed correctly from percentages."""

    def test_entry_100_typical(self):
        r = calculate_targets(entry_price=100, atr_pct=0.05)
        assert r["sl"] == round(100 * (1 - r["sl_pct"] / 100), 1)
        assert r["t1"] == round(100 * (1 + r["t1_pct"] / 100), 1)
        assert r["t2"] == round(100 * (1 + r["t2_pct"] / 100), 1)

    def test_entry_1000_typical(self):
        r = calculate_targets(entry_price=1000, atr_pct=0.05)
        # SL must be below entry, T1/T2 above
        assert r["sl"] < 1000
        assert r["t1"] > 1000
        assert r["t2"] > r["t1"]


class TestFallbackPath:
    """When ATR computation fails (atr_pct=0), fallback values must match spec."""

    def test_fallback_atr_zero(self):
        r = calculate_targets(entry_price=100, atr_pct=0)
        # User spec: -5% / +5% / +12% fallback
        assert r["method"] == "fallback"
        assert r["sl"] == round(100 * 0.95, 1)   # -5%
        assert r["t1"] == round(100 * 1.05, 1)   # +5%
        assert r["t2"] == round(100 * 1.12, 1)   # +12%

    def test_fallback_negative_entry(self):
        r = calculate_targets(entry_price=0, atr_pct=0.05)
        assert r["method"] == "fallback"


class TestNoFantasyTargets:
    """Regression test for the original bug: T1=+50%, T2=+100% fantasy targets.

    User's actual closed trades exited at +0.6% to +1.76% (REVERSAL_EXIT)
    while old config had T1=+50% / T2=+100% — fantasy that never hit.
    """

    def test_no_target_above_15_percent(self):
        """No matter what input, T2 must never exceed 15%."""
        for atr in [0.01, 0.05, 0.10, 0.20, 0.30, 0.50, 1.0]:
            for vol in [0.5, 1.0, 1.5, 2.0]:
                r = calculate_targets(entry_price=100, atr_pct=atr, vol_multiplier=vol)
                assert r["t2_pct"] <= 15.0, (
                    f"T2 {r['t2_pct']}% exceeds 15% cap "
                    f"(atr={atr}, vol={vol})"
                )

    def test_no_loss_below_5_percent(self):
        """Hard max-loss cap: -5% SL no matter what."""
        for atr in [0.01, 0.05, 0.10, 0.20, 0.50, 1.0]:
            for vol in [0.5, 1.0, 1.5, 2.0]:
                r = calculate_targets(entry_price=100, atr_pct=atr, vol_multiplier=vol)
                assert r["sl_pct"] <= 5.0, (
                    f"SL {r['sl_pct']}% exceeds 5% cap "
                    f"(atr={atr}, vol={vol})"
                )


class TestMethodFlag:
    """method='atr' on success, method='fallback' on bad input."""

    def test_atr_method(self):
        r = calculate_targets(entry_price=100, atr_pct=0.05)
        assert r["method"] == "atr"

    def test_fallback_method_bad_atr(self):
        r = calculate_targets(entry_price=100, atr_pct=0)
        assert r["method"] == "fallback"

    def test_fallback_method_bad_entry(self):
        r = calculate_targets(entry_price=-1, atr_pct=0.05)
        assert r["method"] == "fallback"
