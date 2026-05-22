"""
Tests for early_move.oi_rotation — smart money positioning detector.

Built 2026-05-22 per user request ("OI rotation system bana do").

Tests the 5 sub-detectors:
  1. WALL_BUILD       — +1L+ OI added at a strike
  2. WALL_COLLAPSE    — -80k+ OI vanishes
  3. STRIKE_MIGRATION — net OI shift across strikes
  4. WRITER_FLIP      — CE/PE writer dominance reverses
  5. UNUSUAL_VELOCITY — OI change >2x typical
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in ["EARLY_MOVE_OI_ROTATION_ENABLED", "EARLY_MOVE_OI_ROTATION_SHADOW"]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_state():
    from early_move import oi_rotation
    oi_rotation.reset_history()


def _strike(strike, ce_oi=100000, pe_oi=100000, ce_change=0, pe_change=0):
    return {
        "strike": strike, "ce_oi": ce_oi, "pe_oi": pe_oi,
        "ce_change": ce_change, "pe_change": pe_change,
    }


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_off(self):
        from early_move.oi_rotation import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_OI_ROTATION_ENABLED", "on")
        from early_move.oi_rotation import is_enabled
        assert is_enabled() is True

    def test_shadow_default_on(self):
        from early_move.oi_rotation import is_shadow_enabled
        assert is_shadow_enabled() is True


# ── SNAPSHOT RECORDING ─────────────────────────────────────────────────

class TestSnapshotRecording:
    def test_record_snapshot(self):
        from early_move.oi_rotation import record_oi_snapshot, get_history_size
        record_oi_snapshot(idx="BANKNIFTY", strike=53000, ce_oi=100000, pe_oi=200000)
        sizes = get_history_size()
        assert "BANKNIFTY|53000" in sizes
        assert sizes["BANKNIFTY|53000"] == 1

    def test_negative_oi_ignored(self):
        from early_move.oi_rotation import record_oi_snapshot, get_history_size
        record_oi_snapshot(idx="BANKNIFTY", strike=53000, ce_oi=-1, pe_oi=200000)
        assert "BANKNIFTY|53000" not in get_history_size()


# ── DETECTOR 1: WALL_BUILD ──────────────────────────────────────────────

class TestWallBuild:
    def test_ce_wall_above_spot_bearish(self):
        """CE writers add 1L+ above spot → resistance → BEARISH."""
        from early_move.oi_rotation import detect_rotation
        strikes = [
            _strike(53000, ce_change=0, pe_change=0),
            _strike(54500, ce_change=150000, pe_change=0),  # CE wall above
        ]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        wall_signals = [s for s in result["signals"] if s["type"] == "WALL_BUILD"]
        assert len(wall_signals) >= 1
        ce_wall = [s for s in wall_signals if s["side"] == "CE"][0]
        assert ce_wall["direction"] == "BEARISH"
        assert ce_wall["strike"] == 54500

    def test_pe_wall_below_spot_bullish(self):
        """PE writers add 1L+ below spot → support → BULLISH."""
        from early_move.oi_rotation import detect_rotation
        strikes = [
            _strike(53000, ce_change=0, pe_change=180000),  # PE wall below
            _strike(54500, ce_change=0, pe_change=0),
        ]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        wall_signals = [s for s in result["signals"] if s["type"] == "WALL_BUILD"]
        pe_wall = [s for s in wall_signals if s["side"] == "PE"][0]
        assert pe_wall["direction"] == "BULLISH"

    def test_small_change_no_wall(self):
        """Change below threshold → no wall signal."""
        from early_move.oi_rotation import detect_rotation
        strikes = [_strike(54500, ce_change=20000, pe_change=0)]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        assert not any(s["type"] == "WALL_BUILD" for s in result["signals"])


# ── DETECTOR 2: WALL_COLLAPSE ──────────────────────────────────────────

class TestWallCollapse:
    def test_ce_collapse_above_bullish(self):
        """CE writers unwind 80k+ above spot → resistance gone → BULLISH."""
        from early_move.oi_rotation import detect_rotation
        strikes = [_strike(54500, ce_change=-120000, pe_change=0)]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        collapse = [s for s in result["signals"] if s["type"] == "WALL_COLLAPSE"]
        assert len(collapse) >= 1
        assert collapse[0]["direction"] == "BULLISH"

    def test_pe_collapse_below_bearish(self):
        """PE writers unwind 80k+ below spot → support gone → BEARISH."""
        from early_move.oi_rotation import detect_rotation
        strikes = [_strike(53000, ce_change=0, pe_change=-100000)]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        collapse = [s for s in result["signals"] if s["type"] == "WALL_COLLAPSE"]
        assert len(collapse) >= 1
        assert collapse[0]["direction"] == "BEARISH"


# ── DETECTOR 3: STRIKE_MIGRATION ───────────────────────────────────────

class TestStrikeMigration:
    def test_bullish_rotation_combined(self):
        """CE unwinding below + PE building below = BULLISH rotation."""
        from early_move.oi_rotation import detect_rotation
        strikes = [
            _strike(53000, ce_change=-200000, pe_change=200000),
            _strike(53500, ce_change=-150000, pe_change=180000),
        ]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        migration = [s for s in result["signals"] if s["type"] == "STRIKE_MIGRATION"]
        assert len(migration) >= 1
        assert any(s["direction"] == "BULLISH" for s in migration)

    def test_bearish_rotation_combined(self):
        """CE building above + PE unwinding above = BEARISH rotation."""
        from early_move.oi_rotation import detect_rotation
        strikes = [
            _strike(54500, ce_change=200000, pe_change=-200000),
            _strike(55000, ce_change=180000, pe_change=-150000),
        ]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        migration = [s for s in result["signals"] if s["type"] == "STRIKE_MIGRATION"]
        assert any(s["direction"] == "BEARISH" for s in migration)


# ── DETECTOR 4: WRITER_FLIP ────────────────────────────────────────────

class TestWriterFlip:
    def test_bullish_flip(self):
        """Net CE shrinking + PE growing = bullish sentiment flip."""
        from early_move.oi_rotation import detect_rotation
        strikes = [
            _strike(53000, ce_change=-150000, pe_change=150000),
            _strike(54000, ce_change=-100000, pe_change=100000),
        ]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        flips = [s for s in result["signals"] if s["type"] == "WRITER_FLIP"]
        assert len(flips) >= 1
        assert flips[0]["direction"] == "BULLISH"

    def test_bearish_flip(self):
        from early_move.oi_rotation import detect_rotation
        strikes = [
            _strike(53000, ce_change=150000, pe_change=-150000),
            _strike(54000, ce_change=100000, pe_change=-100000),
        ]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        flips = [s for s in result["signals"] if s["type"] == "WRITER_FLIP"]
        assert any(s["direction"] == "BEARISH" for s in flips)


# ── DETECTOR 5: UNUSUAL_VELOCITY ───────────────────────────────────────

class TestUnusualVelocity:
    def test_unusual_ce_velocity(self):
        """CE OI change 5x typical at strike above spot → unusual."""
        from early_move.oi_rotation import detect_rotation
        strikes = [{
            "strike": 54500, "ce_oi": 200000, "pe_oi": 50000,
            "ce_change": 150000, "pe_change": 0,
            "ce_typical_change": 30000, "pe_typical_change": 30000,
        }]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        vel = [s for s in result["signals"] if s["type"] == "UNUSUAL_VELOCITY"]
        assert len(vel) >= 1


# ── OVERALL AGGREGATION ────────────────────────────────────────────────

class TestOverallBias:
    def test_empty_data_neutral(self):
        from early_move.oi_rotation import detect_rotation
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=[])
        assert result["overall_bias"] == "NEUTRAL"
        assert result["signal_count"] == 0

    def test_strong_bullish_consensus(self):
        """Multiple bullish signals → overall BULLISH."""
        from early_move.oi_rotation import detect_rotation
        strikes = [
            _strike(53000, ce_change=-200000, pe_change=200000),
            _strike(53500, ce_change=-180000, pe_change=190000),
            _strike(54500, ce_change=-150000, pe_change=0),  # CE collapse above
        ]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        assert result["overall_bias"] == "BULLISH"
        assert result["signal_count"] >= 2

    def test_signals_have_required_fields(self):
        from early_move.oi_rotation import detect_rotation
        strikes = [_strike(54500, ce_change=150000, pe_change=0)]
        result = detect_rotation(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        for sig in result["signals"]:
            assert sig["signal"] == "EARLY_MOVE"
            assert sig["detector"] == "oi_rotation"
            assert sig["idx"] == "BANKNIFTY"
            assert "direction" in sig
            assert "confidence" in sig
            assert "rationale" in sig
            assert 0 <= sig["confidence"] <= 1.0


# ── check_and_log PUBLIC API ───────────────────────────────────────────

class TestCheckAndLog:
    def test_returns_result_dict(self):
        from early_move.oi_rotation import check_and_log
        strikes = [_strike(54500, ce_change=150000, pe_change=0)]
        result = check_and_log(idx="BANKNIFTY", spot=53975, strikes_data=strikes)
        assert "signals" in result
        assert "overall_bias" in result
