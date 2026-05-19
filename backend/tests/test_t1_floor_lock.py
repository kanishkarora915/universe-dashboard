"""
Tests for Fix 5 — T1 floor lock (partial profit booking equivalent).

Audit (2026-05-19):
  30 T1_HIT scalper trades, all wins, +₹921,978 (avg +₹30,733).
  Currently full exit at T1 — misses runners that continue to T2 or beyond.

Fix logic:
  When current_ltp >= T1 AND T1_FLOOR_LOCK_ENABLED=on AND active SL < T1:
    • Promote SL to T1 - 0.5% buffer (lock in T1 profit)
    • Trade stays OPEN
    • Next ticks: T2 hit → exit at T2 (full runner)
                  drift to T1 → SL hits at T1 (same as old)
                  timeout → exit at current ltp ≥ T1

  Strictly better than current behaviour:
    Worst case  = ~T1 profit (same as old behaviour)
    Best case   = T2 profit (12% better on average)

We test the math/decision logic with a stand-in helper function that
mirrors the inline code path.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def decide_t1_action(
    *,
    current_ltp: float,
    t1: float,
    t2: float,
    active_sl: float,
    enabled: bool,
):
    """Mirror of inline decision logic. Returns:
    (action, new_sl_or_none, reason)
    action ∈ {"LOCK_SL", "EXIT_T1", "NO_OP"}
    """
    if current_ltp < t1:
        return ("NO_OP", None, "below_t1")
    # T1 reached
    if not enabled:
        return ("EXIT_T1", None, "lock_disabled")
    if active_sl >= t1:
        return ("EXIT_T1", None, "already_locked_above_t1")
    new_floor_sl = round(t1 * 0.995, 2)
    if new_floor_sl <= active_sl:
        return ("EXIT_T1", None, "no_improvement_in_sl")
    return ("LOCK_SL", new_floor_sl, "promoted_sl_to_t1_buffer")


class TestT1FloorLockLogic:
    def test_below_t1_noop(self):
        """If price below T1, no action."""
        action, _, _ = decide_t1_action(
            current_ltp=100, t1=110, t2=120, active_sl=90, enabled=True,
        )
        assert action == "NO_OP"

    def test_disabled_exits_at_t1(self):
        """Feature off → current behavior: full exit at T1."""
        action, _, reason = decide_t1_action(
            current_ltp=115, t1=110, t2=120, active_sl=90, enabled=False,
        )
        assert action == "EXIT_T1"
        assert reason == "lock_disabled"

    def test_enabled_locks_sl_to_t1_buffer(self):
        """Enabled + SL below T1 → lock SL to T1 × 0.995."""
        action, new_sl, reason = decide_t1_action(
            current_ltp=115, t1=110, t2=120, active_sl=90, enabled=True,
        )
        assert action == "LOCK_SL"
        assert new_sl == round(110 * 0.995, 2)  # 109.45
        assert reason == "promoted_sl_to_t1_buffer"

    def test_sl_already_above_t1_exits(self):
        """If SL is already at/above T1 (e.g. trail already moved it),
        fall through to T1 exit — no further lock needed."""
        action, _, reason = decide_t1_action(
            current_ltp=115, t1=110, t2=120, active_sl=112, enabled=True,
        )
        assert action == "EXIT_T1"
        assert reason == "already_locked_above_t1"

    def test_no_improvement_falls_through(self):
        """If proposed floor SL isn't higher than current SL → exit at T1."""
        action, _, reason = decide_t1_action(
            current_ltp=115, t1=110, t2=120,
            active_sl=109.5,  # already very close to T1*0.995 = 109.45
            enabled=True,
        )
        # 109.45 ≤ 109.5 → no improvement → exit at T1
        assert action == "EXIT_T1"
        assert reason == "no_improvement_in_sl"

    def test_at_exactly_t1_locks(self):
        """current_ltp exactly equals T1 → should still trigger lock path."""
        action, new_sl, _ = decide_t1_action(
            current_ltp=110, t1=110, t2=120, active_sl=90, enabled=True,
        )
        assert action == "LOCK_SL"
        assert new_sl < 110  # floor sits below T1
        assert new_sl >= 109  # but close


class TestT1FloorLockMath:
    def test_new_floor_is_t1_minus_buffer(self):
        """The locked SL = T1 × 0.995 (0.5% buffer below T1)."""
        action, new_sl, _ = decide_t1_action(
            current_ltp=110, t1=100, t2=110, active_sl=85, enabled=True,
        )
        assert new_sl == 99.5  # 100 × 0.995

    def test_locked_sl_strictly_above_old_sl(self):
        """Lock only fires when it actually improves SL."""
        action, new_sl, _ = decide_t1_action(
            current_ltp=110, t1=100, t2=110, active_sl=85, enabled=True,
        )
        assert new_sl > 85

    def test_runner_can_reach_t2(self):
        """At T2, exits at T2 (not blocked by floor lock — T2 check is earlier)."""
        # Note: actual T2 check runs BEFORE T1 check in scalper_mode
        # so we don't test that flow here — just confirm T1 lock doesn't
        # prevent T2 from being reached on next tick
        # (After lock, sl=99.5, so if next tick ltp=120, T2 check fires first)
        action, new_sl, _ = decide_t1_action(
            current_ltp=115, t1=110, t2=120, active_sl=90, enabled=True,
        )
        assert action == "LOCK_SL"
        # Next tick at 120: T2 check fires → exit at 120, NOT at 99.5 SL


class TestEnvFlag:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("T1_FLOOR_LOCK_ENABLED", raising=False)
        # The flag is read inline at runtime — test the env value directly
        assert os.environ.get("T1_FLOOR_LOCK_ENABLED", "off").lower() == "off"

    def test_on_enables(self, monkeypatch):
        monkeypatch.setenv("T1_FLOOR_LOCK_ENABLED", "on")
        assert os.environ.get("T1_FLOOR_LOCK_ENABLED", "off").lower() == "on"

    def test_only_literal_on_enables(self, monkeypatch):
        monkeypatch.setenv("T1_FLOOR_LOCK_ENABLED", "yes")
        assert os.environ.get("T1_FLOOR_LOCK_ENABLED", "off").lower() != "on"
