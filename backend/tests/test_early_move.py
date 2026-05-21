"""
Tests for early_move detectors — leading-indicator system.

Built 2026-05-21 per user vision:
  "System har chiz late kyu samjhta hai? Make it catch moves EARLY
   like TradingView users do."

Tests:
  • premium_velocity — premium moving BEFORE spot = institutional leak
  • cross_asset — NIFTY ↔ BANKNIFTY lead-lag
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in [
        "EARLY_MOVE_VELOCITY_ENABLED",
        "EARLY_MOVE_VELOCITY_SHADOW",
        "EARLY_MOVE_CROSS_ASSET_ENABLED",
        "EARLY_MOVE_CROSS_ASSET_SHADOW",
    ]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_state():
    """Fresh tick history per test."""
    from early_move import premium_velocity, cross_asset
    premium_velocity.reset_history()
    cross_asset.reset_history()


# ══ PREMIUM VELOCITY DETECTOR ═════════════════════════════════════════

class TestPremiumVelocityEnv:
    def test_default_off(self):
        from early_move.premium_velocity import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_VELOCITY_ENABLED", "on")
        from early_move.premium_velocity import is_enabled
        assert is_enabled() is True

    def test_shadow_default_on(self):
        from early_move.premium_velocity import is_shadow_enabled
        assert is_shadow_enabled() is True


class TestPremiumVelocityRecord:
    def test_record_tick_stores_data(self):
        from early_move.premium_velocity import record_tick, get_history_size
        record_tick(idx="NIFTY", strike=24000, side="CE", premium=150, spot=24000)
        record_tick(idx="NIFTY", strike=24000, side="CE", premium=152, spot=24010)
        sizes = get_history_size()
        assert "NIFTY|24000|CE" in sizes
        assert sizes["NIFTY|24000|CE"] == 2

    def test_invalid_input_ignored(self):
        from early_move.premium_velocity import record_tick, get_history_size
        record_tick(idx="NIFTY", strike=24000, side="CE", premium=0, spot=24000)
        record_tick(idx="NIFTY", strike=24000, side="CE", premium=-1, spot=24000)
        sizes = get_history_size()
        assert "NIFTY|24000|CE" not in sizes or sizes["NIFTY|24000|CE"] == 0


class TestPremiumVelocityDetection:
    def test_no_signal_with_insufficient_data(self):
        """Less than 3 ticks → no detection."""
        from early_move.premium_velocity import record_tick, detect_divergence
        record_tick(idx="NIFTY", strike=24000, side="CE", premium=150, spot=24000)
        sig = detect_divergence(
            idx="NIFTY", strike=24000, side="CE", delta=0.5,
        )
        assert sig is None

    def test_no_signal_when_premium_matches_spot(self):
        """Premium move matches expected (no divergence) → no signal."""
        from early_move.premium_velocity import record_tick, detect_divergence
        now = time.time()
        # Over 60 sec: spot +10 pts, expected premium move = 10 × 0.5 = +5
        # Actual premium move = +5 (matches expectation = no divergence)
        record_tick(idx="NIFTY", strike=24000, side="CE",
                    premium=150, spot=24000, timestamp=now - 60)
        record_tick(idx="NIFTY", strike=24000, side="CE",
                    premium=152.5, spot=24005, timestamp=now - 30)
        record_tick(idx="NIFTY", strike=24000, side="CE",
                    premium=155, spot=24010, timestamp=now)
        sig = detect_divergence(
            idx="NIFTY", strike=24000, side="CE", delta=0.5,
        )
        # ratio ≈ 1.0 (matches expectation) → below 1.5 threshold
        assert sig is None

    def test_signal_when_premium_diverges_strongly(self):
        """Premium moves 3x more than spot can justify → SIGNAL."""
        from early_move.premium_velocity import record_tick, detect_divergence
        now = time.time()
        # Over 60s: spot +2 pts only, but CE premium +12 pts
        # Expected premium move: 2 × 0.5 = +1
        # Actual: +12 → ratio = 12 → strong signal
        record_tick(idx="NIFTY", strike=24000, side="CE",
                    premium=150, spot=24000, timestamp=now - 60)
        record_tick(idx="NIFTY", strike=24000, side="CE",
                    premium=158, spot=24001, timestamp=now - 30)
        record_tick(idx="NIFTY", strike=24000, side="CE",
                    premium=162, spot=24002, timestamp=now)
        sig = detect_divergence(
            idx="NIFTY", strike=24000, side="CE", delta=0.5,
        )
        assert sig is not None
        assert sig["signal"] == "EARLY_MOVE"
        assert sig["direction"] == "BULL"  # CE premium up = bullish
        assert sig["confidence"] >= 0.5
        assert sig["context"]["ratio"] >= 1.5

    def test_bearish_signal_for_pe_premium_rising(self):
        """PE premium up = bearish signal."""
        from early_move.premium_velocity import record_tick, detect_divergence
        now = time.time()
        record_tick(idx="NIFTY", strike=24000, side="PE",
                    premium=150, spot=24000, timestamp=now - 60)
        record_tick(idx="NIFTY", strike=24000, side="PE",
                    premium=158, spot=23999, timestamp=now - 30)
        record_tick(idx="NIFTY", strike=24000, side="PE",
                    premium=162, spot=23998, timestamp=now)
        sig = detect_divergence(
            idx="NIFTY", strike=24000, side="PE", delta=-0.5,
        )
        assert sig is not None
        assert sig["direction"] == "BEAR"

    def test_no_signal_for_deep_otm(self):
        """Delta < 0.15 = deep OTM = no meaningful signal."""
        from early_move.premium_velocity import record_tick, detect_divergence
        now = time.time()
        record_tick(idx="NIFTY", strike=24500, side="CE",
                    premium=5, spot=24000, timestamp=now - 60)
        record_tick(idx="NIFTY", strike=24500, side="CE",
                    premium=15, spot=24001, timestamp=now)
        sig = detect_divergence(
            idx="NIFTY", strike=24500, side="CE", delta=0.05,  # deep OTM
        )
        assert sig is None  # filtered out by delta check


# ══ CROSS-ASSET DETECTOR ══════════════════════════════════════════════

class TestCrossAssetEnv:
    def test_default_off(self):
        from early_move.cross_asset import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_CROSS_ASSET_ENABLED", "on")
        from early_move.cross_asset import is_enabled
        assert is_enabled() is True


class TestCrossAssetRecord:
    def test_record_tick_stores(self):
        from early_move.cross_asset import record_tick, _SPOT_HISTORY
        record_tick(idx="NIFTY", spot=24000)
        assert len(_SPOT_HISTORY["NIFTY"]) == 1


class TestCrossAssetDetection:
    def test_no_signal_below_threshold(self):
        """Both indices moved together → no divergence."""
        from early_move.cross_asset import record_tick, detect_divergence
        now = time.time()
        # NIFTY +0.2%, BANKNIFTY +0.17% (close to expected 0.2 × 0.85)
        record_tick(idx="NIFTY", spot=24000, timestamp=now - 60)
        record_tick(idx="NIFTY", spot=24048, timestamp=now)
        record_tick(idx="BANKNIFTY", spot=53000, timestamp=now - 60)
        record_tick(idx="BANKNIFTY", spot=53090, timestamp=now)
        # NIFTY +0.2%, BANKNIFTY +0.17%, expected from leader correlation = +0.17%
        # Divergence is tiny → no signal
        sig = detect_divergence()
        assert sig is None

    def test_signal_when_nifty_leads_banknifty(self):
        """NIFTY moves +0.5%, BANKNIFTY only +0.05% → BANKNIFTY laggard."""
        from early_move.cross_asset import record_tick, detect_divergence
        now = time.time()
        record_tick(idx="NIFTY", spot=24000, timestamp=now - 60)
        record_tick(idx="NIFTY", spot=24120, timestamp=now)  # +0.5%
        record_tick(idx="BANKNIFTY", spot=53000, timestamp=now - 60)
        record_tick(idx="BANKNIFTY", spot=53025, timestamp=now)  # +0.05%
        sig = detect_divergence()
        assert sig is not None
        assert sig["signal"] == "EARLY_MOVE"
        assert sig["target_index"] == "BANKNIFTY"  # the laggard
        assert sig["direction"] == "BULL"  # catch up direction
        assert sig["confidence"] >= 0.5

    def test_signal_bear_when_leader_goes_down(self):
        """BANKNIFTY drops, NIFTY hasn't yet → NIFTY likely follows down."""
        from early_move.cross_asset import record_tick, detect_divergence
        now = time.time()
        record_tick(idx="NIFTY", spot=24000, timestamp=now - 60)
        record_tick(idx="NIFTY", spot=23990, timestamp=now)  # -0.04%
        record_tick(idx="BANKNIFTY", spot=53000, timestamp=now - 60)
        record_tick(idx="BANKNIFTY", spot=52800, timestamp=now)  # -0.38%
        sig = detect_divergence()
        assert sig is not None
        assert sig["target_index"] == "NIFTY"  # NIFTY laggard
        assert sig["direction"] == "BEAR"  # catch down move

    def test_no_signal_with_no_data(self):
        from early_move.cross_asset import detect_divergence
        sig = detect_divergence()
        assert sig is None
