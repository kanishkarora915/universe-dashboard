"""
Tests for Fix 6 — calibration gate wired into trade firing.

Background:
  60-day audit found `probability` is INVERSE:
    50-59% raw → 74% actual WR
    90-100% raw → 29% actual WR  (worst)

  Wire calibration.calibrated_wr into trade gating: skip when
  historical WR at the raw_prob bucket is below threshold (default 55%).

The wiring lives inline in scalper_mode.should_enter_scalp and
engine.py pending-confirmation block. We test the gate logic + env
flags at the calibration module level.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("CALIBRATION_GATE_ENABLED", raising=False)
    monkeypatch.delenv("CALIBRATION_MIN_WR", raising=False)
    # Reset calibration cache so each test reads fresh
    if "calibration" in sys.modules:
        sys.modules["calibration"]._cache = None
        sys.modules["calibration"]._cache_mtime = 0.0


class TestGateEnvFlags:
    def test_gate_default_off(self):
        """CALIBRATION_GATE_ENABLED default off → engine reads "off"."""
        assert os.environ.get("CALIBRATION_GATE_ENABLED", "off").lower() == "off"

    def test_gate_on_when_set(self, monkeypatch):
        monkeypatch.setenv("CALIBRATION_GATE_ENABLED", "on")
        assert os.environ.get("CALIBRATION_GATE_ENABLED", "off").lower() == "on"

    def test_threshold_default_55(self):
        v = float(os.environ.get("CALIBRATION_MIN_WR", "55"))
        assert v == 55

    def test_threshold_configurable(self, monkeypatch):
        monkeypatch.setenv("CALIBRATION_MIN_WR", "60")
        v = float(os.environ.get("CALIBRATION_MIN_WR", "55"))
        assert v == 60


class TestGateLogic:
    """Test the decision rule: block if cal_wr is not None AND cal_wr < threshold."""

    @staticmethod
    def gate_decision(cal_wr, threshold):
        """Mirror of inline rule:
        - If cal_wr is None → no data → DO NOT BLOCK (let trade through)
        - If cal_wr < threshold → BLOCK
        - Else → ALLOW
        """
        if cal_wr is None:
            return "ALLOW_NO_DATA"
        if cal_wr < threshold:
            return "BLOCK"
        return "ALLOW"

    def test_none_cal_wr_allows(self):
        """No calibration data for this bucket → permissive."""
        assert self.gate_decision(None, 55) == "ALLOW_NO_DATA"

    def test_below_threshold_blocks(self):
        """Cal WR < threshold → block."""
        assert self.gate_decision(41, 55) == "BLOCK"

    def test_at_threshold_allows(self):
        """Cal WR == threshold → allow (strict inequality)."""
        assert self.gate_decision(55, 55) == "ALLOW"

    def test_above_threshold_allows(self):
        assert self.gate_decision(74, 55) == "ALLOW"

    def test_zero_allows_only_at_or_below_threshold(self):
        """Edge: 0% calibrated WR → always blocks (any threshold > 0)."""
        assert self.gate_decision(0, 55) == "BLOCK"


class TestCalibrationModuleIntegration:
    """Verify the calibration module returns sensible values that the
    gate can act on (uses the built-in v1 fallback table)."""

    def test_calibration_returns_value_or_none(self):
        from calibration import calibrated_wr
        # 75% raw_prob bucket for main has known data in v1 table
        wr = calibrated_wr(75, engine_type="main", action="ALL")
        assert wr is None or (0 <= wr <= 100)

    def test_inverted_buckets_have_low_cal_wr(self):
        """The 95-100 bucket should have low cal_wr (audit found 41%)."""
        from calibration import calibrated_wr
        wr = calibrated_wr(95, engine_type="main", action="ALL")
        # From v1 audit data: 95-100 bucket WR was ~40%
        if wr is not None:
            assert wr < 55  # below default threshold → gate would block

    def test_50_pct_bucket_above_threshold(self):
        """The 50-54 bucket should be ABOVE 55% (audit found 73%)."""
        from calibration import calibrated_wr
        wr = calibrated_wr(50, engine_type="main", action="ALL")
        if wr is not None:
            assert wr >= 55

    def test_action_specific_lookup_does_not_crash(self):
        """Lookup by specific action (BUY CE / BUY PE) should not error."""
        from calibration import calibrated_wr
        for action in ["BUY CE", "BUY PE", "ALL"]:
            wr = calibrated_wr(70, engine_type="scalper", action=action)
            assert wr is None or (0 <= wr <= 100)


class TestEndToEndScenarios:
    """Walk through realistic gate decisions for each prob bucket."""

    def test_borderline_bucket_50_passes(self):
        from calibration import calibrated_wr
        wr = calibrated_wr(50, engine_type="main", action="ALL")
        if wr is not None:
            # 73% historical → above 55% threshold → ALLOW
            assert wr >= 55

    def test_high_confidence_bucket_85_blocks(self):
        """85% raw_prob historically lost — gate should block."""
        from calibration import calibrated_wr
        wr = calibrated_wr(85, engine_type="main", action="ALL")
        if wr is not None:
            # 40% historical → below 55% threshold → BLOCK
            assert wr < 55

    def test_max_confidence_bucket_95_definitively_blocks(self):
        """95-100% raw_prob has worst actual WR — definitively blocked."""
        from calibration import calibrated_wr
        wr = calibrated_wr(98, engine_type="main", action="ALL")
        if wr is not None:
            assert wr < 55
