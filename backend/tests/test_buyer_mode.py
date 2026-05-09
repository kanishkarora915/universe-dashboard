"""
Tests for buyer_mode.py — trade-mode threshold definitions.

Critical because: changing reversal_exit_pct from -5% to -10% would
silently let trades bleed past max-loss cap. This test locks user's
explicit spec: max loss = -5%, T1 = +5%, T2 = +12%.

Run: pytest backend/tests/test_buyer_mode.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from buyer_mode import BUYER_DEFAULTS, HEDGER_DEFAULTS


class TestBuyerDefaultsRespectUserSpec:
    """User's explicit spec from session 2026-05-07:
        SL:  -5% max (hard cap, no overrides)
        T1:  +5% (achievable, locks profit)
        T2:  +12% (mid 10-15% range)
        BE:  +3% (early breakeven)
        Trail activate: +25% (post-T2 trailing)
    """

    def test_max_loss_5_percent(self):
        """reversal_exit_pct must be -5% (user spec)"""
        assert BUYER_DEFAULTS["reversal_exit_pct"] == -5.0, (
            "BUYER mode max loss must be -5% per user spec"
        )

    def test_t1_5_percent(self):
        """scalper_t1_pct must be 0.05 = +5%"""
        assert BUYER_DEFAULTS["scalper_t1_pct"] == 0.05, (
            "BUYER mode T1 must be +5% (was +50% — fantasy)"
        )

    def test_t2_12_percent(self):
        """scalper_t2_pct must be 0.12 = +12% (mid 10-15%)"""
        assert BUYER_DEFAULTS["scalper_t2_pct"] == 0.12, (
            "BUYER mode T2 must be +12% (was +100% — fantasy)"
        )

    def test_sl_5_percent(self):
        """scalper_sl_pct must be 0.05 = max -5%"""
        assert BUYER_DEFAULTS["scalper_sl_pct"] == 0.05, (
            "BUYER mode SL must be -5% (was -18%)"
        )

    def test_breakeven_3_percent(self):
        """breakeven_pct must be 3% (early lock once T1 territory)"""
        assert BUYER_DEFAULTS["breakeven_pct"] == 3.0

    def test_post_t2_lock_enabled(self):
        """post_t2_lock_t2 must be True for runner-capture logic"""
        assert BUYER_DEFAULTS.get("post_t2_lock_t2") is True, (
            "post-T2 ratchet trail must be enabled"
        )

    def test_early_neg_exit_pct_present(self):
        """early_neg_exit_pct enables 30-min trend-confirmed early exit"""
        assert "early_neg_exit_pct" in BUYER_DEFAULTS
        assert BUYER_DEFAULTS["early_neg_exit_pct"] == -3.0

    def test_min_hold_2min(self):
        """reversal_exit_min_hold_sec lowered from 600 (10min) → 120 (2min)
        for faster cuts on confirmed bad entries"""
        assert BUYER_DEFAULTS["reversal_exit_min_hold_sec"] == 120


class TestHedgerDefaultsRespectSpec:
    """HEDGER mode is for tighter scalping. Different thresholds OK
    but still must have hard SL cap."""

    def test_hedger_sl_capped(self):
        """HEDGER scalper_sl_pct should be ≤12% (was 8%)"""
        assert HEDGER_DEFAULTS["scalper_sl_pct"] <= 0.12, (
            "HEDGER SL must not exceed -12%"
        )

    def test_hedger_breakeven_low(self):
        """HEDGER hits BE faster (typical 2%)"""
        assert HEDGER_DEFAULTS["breakeven_pct"] <= 5.0


class TestBackwardCompatKeys:
    """Required keys must exist — used throughout trade_logger.py.
    If renamed/removed, KeyErrors break trade flow."""

    REQUIRED_KEYS = [
        "mode",
        "breakeven_pct",
        "trail_giveback_pct",
        "reversal_exit_pct",
        "reversal_exit_min_hold_sec",
        "scalper_sl_pct",
        "scalper_t1_pct",
        "scalper_t2_pct",
        "conviction_exit_enabled",
    ]

    def test_buyer_has_all_required_keys(self):
        for key in self.REQUIRED_KEYS:
            assert key in BUYER_DEFAULTS, f"BUYER_DEFAULTS missing required key: {key}"

    def test_hedger_has_all_required_keys(self):
        for key in self.REQUIRED_KEYS:
            assert key in HEDGER_DEFAULTS, f"HEDGER_DEFAULTS missing required key: {key}"


class TestNoFantasyTargets:
    """Regression: BUYER must never have T2 ≥ 50% or SL ≥ 15% again."""

    def test_buyer_t2_realistic(self):
        assert BUYER_DEFAULTS["scalper_t2_pct"] <= 0.15, (
            "T2 must be ≤15% (no more fantasy +100%)"
        )

    def test_buyer_sl_capped(self):
        assert BUYER_DEFAULTS["scalper_sl_pct"] <= 0.05, (
            "SL must be ≤5% (no more -18% bleed)"
        )

    def test_buyer_t1_achievable(self):
        assert BUYER_DEFAULTS["scalper_t1_pct"] <= 0.08, (
            "T1 must be ≤8% (must be hittable, not +50% fantasy)"
        )
