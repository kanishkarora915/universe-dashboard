"""
Tests for the anti-stop-hunt smart SL placement module.

Audit (2026-05-19) showed 37 STOP_HUNT main trades, 0 wins, -₹351,707 over 60 days.
Cause: SL placed at predictable round-number levels (entry × 0.85 = round ₹).
This module:
  • Adds ATR-scaled SL distance (when ATR available)
  • Rounds to NSE tick size (0.05) instead of integer rupee
  • Nudges SL off multiples of 5 (institutional sweep targets)
  • Env-flag gated (default OFF) — shadow-logs comparison either way
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Each test starts with SMART_SL_ENABLED unset."""
    monkeypatch.delenv("SMART_SL_ENABLED", raising=False)
    monkeypatch.delenv("SMART_SL_SHADOW", raising=False)


class TestEnvFlags:
    def test_default_disabled(self):
        from smart_sl import is_smart_sl_enabled
        assert is_smart_sl_enabled() is False

    def test_enabled_when_set_on(self, monkeypatch):
        monkeypatch.setenv("SMART_SL_ENABLED", "on")
        from smart_sl import is_smart_sl_enabled
        assert is_smart_sl_enabled() is True

    def test_disabled_for_other_values(self, monkeypatch):
        monkeypatch.setenv("SMART_SL_ENABLED", "yes")
        from smart_sl import is_smart_sl_enabled
        # only literal "on" enables
        assert is_smart_sl_enabled() is False

    def test_shadow_logging_default_on(self):
        from smart_sl import is_shadow_logging_enabled
        assert is_shadow_logging_enabled() is True

    def test_shadow_logging_off_when_set_off(self, monkeypatch):
        monkeypatch.setenv("SMART_SL_SHADOW", "off")
        from smart_sl import is_shadow_logging_enabled
        assert is_shadow_logging_enabled() is False


class TestRoundToTick:
    def test_rounds_to_0_05(self):
        from smart_sl import _round_to_tick
        assert _round_to_tick(85.23) == 85.25
        assert _round_to_tick(85.27) == 85.25
        assert _round_to_tick(85.21) == 85.20

    def test_zero_input(self):
        from smart_sl import _round_to_tick
        assert _round_to_tick(0) == 0

    def test_negative_safe(self):
        from smart_sl import _round_to_tick
        assert _round_to_tick(-1) == 0


class TestAvoidRoundNumber:
    def test_nudges_off_multiple_of_5(self):
        """SL=85.00 should be nudged below 85 (off the round level)."""
        from smart_sl import _avoid_round_number
        result = _avoid_round_number(85.0, entry_price=100.0)
        assert result < 85.0
        assert result >= 84.5  # nudged 0.25-0.45 below
        assert result != 85.0
        # Result should NOT be on a multiple of 5
        assert result % 5 != 0

    def test_keeps_non_round_value(self):
        """SL=85.30 is already non-round — should be unchanged."""
        from smart_sl import _avoid_round_number
        result = _avoid_round_number(85.30, entry_price=100.0)
        assert result == 85.30

    def test_deterministic_per_entry(self):
        """Same entry_price should produce same offset."""
        from smart_sl import _avoid_round_number
        a = _avoid_round_number(85.0, entry_price=100.0)
        b = _avoid_round_number(85.0, entry_price=100.0)
        assert a == b

    def test_different_entries_get_different_offsets(self):
        """Different entry prices should produce different SL values
        (so all stops aren't clustered)."""
        from smart_sl import _avoid_round_number
        offsets = set()
        for entry in [100, 101, 102, 103, 104]:
            offsets.add(_avoid_round_number(85.0, entry_price=entry))
        # At least 3 distinct offsets across 5 entries
        assert len(offsets) >= 3


class TestComputeSmartSL:
    def test_atr_scaled_path(self):
        from smart_sl import compute_smart_sl
        # entry=100, ATR=5% → SL distance = 7.5%, SL = ~92.5
        # Will get nudged off 92.5 (multiple of 2.5)
        r = compute_smart_sl(entry_price=100.0, atr_pct=0.05, direction="BUY CE")
        assert r["method"] == "atr_scaled"
        assert r["atr_used"] == 0.05
        # SL should be in the 90-93 range after 1.5×ATR + tick rounding + nudge
        assert 88 < r["sl"] < 95

    def test_atr_scaled_min_clamp(self):
        """Very low ATR (1%) should clamp to 8% min SL distance."""
        from smart_sl import compute_smart_sl
        r = compute_smart_sl(entry_price=100.0, atr_pct=0.01, direction="BUY CE")
        assert r["method"] == "atr_scaled"
        assert r["guardrails_hit"] is not None
        assert "min_clamped" in r["guardrails_hit"]
        # SL should be at ~92 (8% below 100)
        assert 88 < r["sl"] < 93

    def test_atr_scaled_max_clamp(self):
        """Very high ATR (30%) should clamp to 22% max SL distance."""
        from smart_sl import compute_smart_sl
        r = compute_smart_sl(entry_price=100.0, atr_pct=0.30, direction="BUY CE")
        assert r["method"] == "atr_scaled"
        assert r["guardrails_hit"] is not None
        assert "max_clamped" in r["guardrails_hit"]
        # SL should be at ~78 (22% below 100)
        assert 76 < r["sl"] < 81

    def test_non_round_fallback_path(self):
        """No ATR but legacy SL given → use legacy with non-round adjustment."""
        from smart_sl import compute_smart_sl
        r = compute_smart_sl(
            entry_price=100.0, atr_pct=None, direction="BUY CE", legacy_sl=85.0
        )
        assert r["method"] == "non_round_fallback"
        assert r["sl"] < 85.0  # nudged off the round number

    def test_default_path(self):
        """No ATR and no legacy_sl → 15% default with non-round."""
        from smart_sl import compute_smart_sl
        r = compute_smart_sl(entry_price=100.0, atr_pct=None, direction="BUY CE")
        assert r["method"] == "default_15pct"
        # SL should be near 85 but offset
        assert 84 < r["sl"] < 86

    def test_zero_entry_safe(self):
        from smart_sl import compute_smart_sl
        r = compute_smart_sl(entry_price=0, atr_pct=0.05, direction="BUY CE")
        assert r["sl"] == 0
        assert r["method"] == "invalid_entry"


class TestSmartSLOrLegacy:
    def test_returns_legacy_when_disabled(self):
        """SMART_SL_ENABLED unset → return legacy SL unchanged."""
        from smart_sl import smart_sl_or_legacy
        sl = smart_sl_or_legacy(
            entry_price=100.0,
            legacy_sl=85.0,
            atr_pct=0.05,
            direction="BUY CE",
            source="test",
        )
        assert sl == 85.0  # unchanged

    def test_returns_smart_when_enabled(self, monkeypatch):
        """SMART_SL_ENABLED=on → return smart SL (different from legacy)."""
        monkeypatch.setenv("SMART_SL_ENABLED", "on")
        from smart_sl import smart_sl_or_legacy
        sl = smart_sl_or_legacy(
            entry_price=100.0,
            legacy_sl=85.0,
            atr_pct=0.05,
            direction="BUY CE",
            source="test",
        )
        assert sl != 85.0  # changed to smart SL

    def test_no_atr_still_modifies_when_enabled(self, monkeypatch):
        """Even without ATR, smart SL should at least non-round-adjust
        the legacy SL when flag is on."""
        monkeypatch.setenv("SMART_SL_ENABLED", "on")
        from smart_sl import smart_sl_or_legacy
        sl = smart_sl_or_legacy(
            entry_price=100.0,
            legacy_sl=85.0,
            atr_pct=None,
            direction="BUY CE",
            source="test",
        )
        # Should be off the round number
        assert sl != 85.0
        assert sl < 85.0  # nudged below the obvious level
        assert sl >= 84.5


class TestStopHuntScenarios:
    """Scenarios from the actual 2026-05-19 audit — verify smart SL
    would have placed stops where institutional sweeps wouldn't catch them.
    """

    def test_banknifty_call_stop_hunt_scenario_with_atr(self, monkeypatch):
        """Real trade from audit:
          entry: ₹851.9 (BANKNIFTY CE)
          legacy SL: ₹826.3 (3% below — institutional flush target)
          got stop-hunted to ₹826.3, then reversed to ₹849.6

        With ATR-scaled smart SL, the distance becomes 1.5×ATR (here
        ATR=5%, so SL distance = 7.5% = ₹64 away). This moves the SL
        to ~₹788 — WAY further from the ₹826 institutional level.
        """
        monkeypatch.setenv("SMART_SL_ENABLED", "on")
        from smart_sl import smart_sl_or_legacy
        sl = smart_sl_or_legacy(
            entry_price=851.9,
            legacy_sl=826.3,
            atr_pct=0.05,  # 5% ATR → SL distance = 7.5%
            direction="BUY CE",
            source="test",
        )
        # SL should be WELL below the 826 institutional sweep zone
        assert sl < 800
        # But not so deep that it eats capital — within 22% (max clamp)
        assert sl > 660  # 851.9 × (1 - 0.22) = 664

    def test_legacy_sl_on_round_5_gets_nudged(self, monkeypatch):
        """Without ATR, smart SL still helps when legacy is on a multiple
        of 5 (the most common sweep target)."""
        monkeypatch.setenv("SMART_SL_ENABLED", "on")
        from smart_sl import smart_sl_or_legacy
        sl = smart_sl_or_legacy(
            entry_price=100.0,
            legacy_sl=85.0,  # exact multiple of 5 — sweep target
            atr_pct=None,
            direction="BUY CE",
            source="test",
        )
        # SL nudged below 85 (off the sweep zone)
        assert sl < 85.0
        assert sl >= 84.5

    def test_cheap_option_sl_not_too_tight(self, monkeypatch):
        """For cheap options (entry < 50), don't go below 8% SL distance."""
        monkeypatch.setenv("SMART_SL_ENABLED", "on")
        from smart_sl import compute_smart_sl
        r = compute_smart_sl(entry_price=30.0, atr_pct=0.04, direction="BUY CE")
        # 1.5 × 4% = 6%, but clamped to 8% min
        assert r["guardrails_hit"] is not None
        # SL distance should be ~8% → SL near 27.5
        assert 26 < r["sl"] < 29
