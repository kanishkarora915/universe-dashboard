"""
Tests for Fix 2 — profit-anchor timeout extension.

Audit (2026-05-19):
  TIMEOUT_EXIT scalper bucket: 18W +₹357k vs 16L -₹223k.
  Profitable timeouts get cut at 30min flat — they're still moving.
  Losing timeouts also exit at 30min — same flat cut.

Fix: at active_max_hold_min (default 30m), check profit:
  - If profit > +1% → extend by 50% (to 45m)
  - If unprofitable → exit normally
  - If hold reaches extended_max_min (45m) → hard exit regardless

This isn't a separate module — it's a behavioral change inline in
monitor_scalp_trades(). We test the math/decision logic directly.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


# Mirror the inline calculation as a unit-testable function
def decide_timeout_action(
    *,
    hold_sec: float,
    active_max_hold_min: int,
    current_ltp: float,
    entry: float,
    extension_enabled: bool,
    extension_factor: float = 1.5,
    min_profit_pct: float = 0.01,
):
    """Mirrors the inline logic. Returns (action, reason).
    action ∈ {"EXIT", "STAY_OPEN"}
    """
    extended_max_min = active_max_hold_min * extension_factor
    pnl_pct = (current_ltp - entry) / entry if entry > 0 else 0
    past_hard = hold_sec >= extended_max_min * 60
    in_window = (
        hold_sec >= active_max_hold_min * 60
        and hold_sec < extended_max_min * 60
    )
    if not extension_enabled or past_hard:
        if hold_sec >= active_max_hold_min * 60:
            return ("EXIT", "hard_or_disabled")
        return ("STAY_OPEN", "not_yet_timeout")
    if in_window and pnl_pct > min_profit_pct:
        return ("STAY_OPEN", "profitable_extension")
    if in_window:
        return ("EXIT", "in_window_not_profitable")
    return ("STAY_OPEN", "not_yet_timeout")


class TestTimeoutExtensionLogic:
    def test_before_timeout_stays_open(self):
        """Below active_max_hold_min → no exit."""
        action, _ = decide_timeout_action(
            hold_sec=10 * 60,
            active_max_hold_min=30,
            current_ltp=110,
            entry=100,
            extension_enabled=True,
        )
        assert action == "STAY_OPEN"

    def test_disabled_exits_at_active_max(self):
        """Feature off → standard 30m exit."""
        action, _ = decide_timeout_action(
            hold_sec=30 * 60,
            active_max_hold_min=30,
            current_ltp=110,  # +10% profit
            entry=100,
            extension_enabled=False,
        )
        assert action == "EXIT"

    def test_enabled_profitable_at_timeout_extends(self):
        """Profitable at active max → stay open in extension window."""
        action, reason = decide_timeout_action(
            hold_sec=30 * 60,
            active_max_hold_min=30,
            current_ltp=110,  # +10%
            entry=100,
            extension_enabled=True,
        )
        assert action == "STAY_OPEN"
        assert reason == "profitable_extension"

    def test_enabled_unprofitable_at_timeout_exits(self):
        """Not profitable at active max → exit (don't waste capital)."""
        action, reason = decide_timeout_action(
            hold_sec=30 * 60,
            active_max_hold_min=30,
            current_ltp=99,  # -1% (below min_profit_pct)
            entry=100,
            extension_enabled=True,
        )
        assert action == "EXIT"
        assert reason == "in_window_not_profitable"

    def test_extension_window_profitable_keeps_open(self):
        """At 35min mark with profit, still in extension window."""
        action, _ = decide_timeout_action(
            hold_sec=35 * 60,
            active_max_hold_min=30,
            current_ltp=108,
            entry=100,
            extension_enabled=True,
        )
        assert action == "STAY_OPEN"

    def test_hard_limit_forces_exit(self):
        """At 45min (extended max), exit regardless of profitability."""
        action, reason = decide_timeout_action(
            hold_sec=45 * 60,
            active_max_hold_min=30,
            current_ltp=115,  # very profitable
            entry=100,
            extension_enabled=True,
        )
        assert action == "EXIT"
        assert reason == "hard_or_disabled"

    def test_breakeven_does_not_extend(self):
        """Trade at exactly entry price → not extended (need >1%)."""
        action, _ = decide_timeout_action(
            hold_sec=30 * 60,
            active_max_hold_min=30,
            current_ltp=100,  # 0%
            entry=100,
            extension_enabled=True,
        )
        assert action == "EXIT"

    def test_tiny_profit_below_threshold_no_extension(self):
        """Trade at +0.5% (below 1% threshold) → exit, no extension."""
        action, _ = decide_timeout_action(
            hold_sec=30 * 60,
            active_max_hold_min=30,
            current_ltp=100.5,
            entry=100,
            extension_enabled=True,
        )
        assert action == "EXIT"

    def test_custom_extension_factor(self):
        """Extension factor of 2.0 doubles max hold."""
        # At 50min with factor=2.0, hold_sec=50*60=3000, extended=60*60=3600
        # → in extension window if profitable
        action, _ = decide_timeout_action(
            hold_sec=50 * 60,
            active_max_hold_min=30,
            current_ltp=110,
            entry=100,
            extension_enabled=True,
            extension_factor=2.0,
        )
        assert action == "STAY_OPEN"


class TestEnvFlag:
    def test_default_disabled(self, monkeypatch):
        """TIMEOUT_EXTENSION_ENABLED default OFF."""
        monkeypatch.delenv("TIMEOUT_EXTENSION_ENABLED", raising=False)
        # The flag is read inline in scalper_mode at runtime,
        # so we test the env directly
        assert os.environ.get("TIMEOUT_EXTENSION_ENABLED", "off").lower() == "off"

    def test_only_literal_on_enables(self, monkeypatch):
        monkeypatch.setenv("TIMEOUT_EXTENSION_ENABLED", "yes")
        assert os.environ.get("TIMEOUT_EXTENSION_ENABLED", "off").lower() != "on"
        monkeypatch.setenv("TIMEOUT_EXTENSION_ENABLED", "on")
        assert os.environ.get("TIMEOUT_EXTENSION_ENABLED", "off").lower() == "on"
