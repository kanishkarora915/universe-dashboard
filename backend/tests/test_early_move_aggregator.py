"""
Tests for early_move.aggregator — combines 5 detectors into one verdict.

Built 2026-05-22. The aggregator is the "jury":
  • 2+ detectors agree on direction → FIRE
  • only 1 → NO_TRADE
  • IV crush / fakeout / exhaustion → BLOCKED (veto)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in ["EARLY_MOVE_AGGREGATOR_ENABLED", "EARLY_MOVE_MIN_DETECTORS"]:
        monkeypatch.delenv(var, raising=False)


def _sig(detector, direction, confidence=0.7, sig_type="", **extra):
    d = {
        "detector": detector,
        "direction": direction,
        "confidence": confidence,
        "type": sig_type,
        "rationale": f"{detector} says {direction}",
    }
    d.update(extra)
    return d


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_off(self):
        from early_move.aggregator import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_AGGREGATOR_ENABLED", "on")
        from early_move.aggregator import is_enabled
        assert is_enabled() is True

    def test_min_detectors_default_2(self):
        from early_move.aggregator import min_detectors
        assert min_detectors() == 2

    def test_min_detectors_configurable(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_MIN_DETECTORS", "3")
        from early_move.aggregator import min_detectors
        assert min_detectors() == 3

    def test_min_detectors_floor_is_2(self, monkeypatch):
        """Even if env says 1, minimum enforced is 2."""
        monkeypatch.setenv("EARLY_MOVE_MIN_DETECTORS", "1")
        from early_move.aggregator import min_detectors
        assert min_detectors() == 2


# ── EMPTY / NO SIGNALS ─────────────────────────────────────────────────

class TestNoSignals:
    def test_empty_signals_no_trade(self):
        from early_move.aggregator import aggregate
        v = aggregate([])
        assert v["verdict"] == "NO_TRADE"
        assert v["direction"] is None
        assert v["detectors_agreed"] == 0


# ── FIRE — 2+ DETECTORS AGREE ──────────────────────────────────────────

class TestFireVerdict:
    def test_two_bull_detectors_fire(self):
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BULL", 0.75),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "FIRE"
        assert v["direction"] == "BULL"
        assert v["detectors_agreed"] == 2
        assert "BUY CE" in v["action"]

    def test_two_bear_detectors_fire(self):
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BEAR", 0.8),
            _sig("premium_velocity", "BEAR", 0.7),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "FIRE"
        assert v["direction"] == "BEAR"
        assert "BUY PE" in v["action"]

    def test_three_detectors_high_conviction(self):
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BULL", 0.85),
            _sig("premium_velocity", "BULL", 0.75),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "FIRE"
        assert v["detectors_agreed"] == 3
        assert v["conviction"] == "HIGH"

    def test_single_detector_no_trade(self):
        """Only 1 detector → NO_TRADE (need 2+)."""
        from early_move.aggregator import aggregate
        signals = [_sig("oi_rotation", "BULL", 0.9)]
        v = aggregate(signals)
        assert v["verdict"] == "NO_TRADE"
        assert v["detectors_agreed"] == 1

    def test_same_detector_twice_counts_once(self):
        """One detector emitting 2 BULL signals = still 1 detector."""
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8, sig_type="WALL_BUILD"),
            _sig("oi_rotation", "BULL", 0.7, sig_type="STRIKE_MIGRATION"),
        ]
        v = aggregate(signals)
        # Only 1 distinct detector → NO_TRADE
        assert v["verdict"] == "NO_TRADE"
        assert v["detectors_agreed"] == 1


# ── CONFLICTING DIRECTIONS ─────────────────────────────────────────────

class TestConflict:
    def test_tie_no_trade(self):
        """1 bull + 1 bear = tie → NO_TRADE."""
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BEAR", 0.8),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "NO_TRADE"

    def test_majority_wins(self):
        """2 bull + 1 bear → BULL wins (2 agree)."""
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BULL", 0.75),
            _sig("cross_asset", "BEAR", 0.6),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "FIRE"
        assert v["direction"] == "BULL"
        assert v["detectors_agreed"] == 2


# ── VETO — IV CRUSH / FAKEOUT / EXHAUSTION ─────────────────────────────

class TestVeto:
    def test_iv_crush_blocks_even_with_agreement(self):
        """2 bull detectors agree BUT IV crush → BLOCKED."""
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BULL", 0.75),
            _sig("iv_term_structure", "AVOID", 0.7, sig_type="IV_CRUSH"),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "BLOCKED"
        assert v["blocked_by"] == "IV_CRUSH"

    def test_fakeout_blocks(self):
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("premium_velocity", "BULL", 0.75),
            _sig("volume_profile", "AVOID", 0.7, sig_type="FAKEOUT_WARNING"),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "BLOCKED"
        assert v["blocked_by"] == "FAKEOUT_WARNING"

    def test_exhaustion_blocks(self):
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BEAR", 0.8),
            _sig("premium_velocity", "BEAR", 0.75),
            _sig("volume_profile", "EXIT", 0.7, sig_type="VOLUME_EXHAUSTION"),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "BLOCKED"
        assert v["blocked_by"] == "VOLUME_EXHAUSTION"


# ── NEUTRAL SIGNALS ────────────────────────────────────────────────────

class TestNeutralSignals:
    def test_neutral_doesnt_count_as_direction(self):
        """IV expansion is NEUTRAL — adds context but doesn't pick side.
        2 bull + 1 neutral → still fires BULL on the 2."""
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BULL", 0.75),
            _sig("iv_term_structure", "NEUTRAL", 0.7,
                 sig_type="IV_EXPANSION", vega_friendly=True),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "FIRE"
        assert v["direction"] == "BULL"
        assert v["vega_friendly"] is True

    def test_only_neutral_signals_no_trade(self):
        """All NEUTRAL → no directional agreement → NO_TRADE."""
        from early_move.aggregator import aggregate
        signals = [
            _sig("iv_term_structure", "NEUTRAL", 0.7, sig_type="IV_EXPANSION"),
        ]
        v = aggregate(signals)
        assert v["verdict"] == "NO_TRADE"


# ── min_agree OVERRIDE ─────────────────────────────────────────────────

class TestMinAgreeOverride:
    def test_min_agree_3_blocks_2detector_fire(self):
        """With min_agree=3, two detectors isn't enough."""
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BULL", 0.75),
        ]
        v = aggregate(signals, min_agree=3)
        assert v["verdict"] == "NO_TRADE"

    def test_min_agree_3_fires_with_3(self):
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BULL", 0.75),
            _sig("premium_velocity", "BULL", 0.7),
        ]
        v = aggregate(signals, min_agree=3)
        assert v["verdict"] == "FIRE"


# ── OUTPUT CONTRACT ────────────────────────────────────────────────────

class TestOutputContract:
    def test_verdict_has_required_fields(self):
        from early_move.aggregator import aggregate
        signals = [
            _sig("oi_rotation", "BULL", 0.8),
            _sig("volume_profile", "BULL", 0.75),
        ]
        v = aggregate(signals)
        for field in ("verdict", "direction", "confidence",
                      "detectors_agreed", "contributing", "action", "all_signals"):
            assert field in v
        assert 0 <= v["confidence"] <= 1.0
        assert isinstance(v["contributing"], list)
