"""
Tests for early_move.entry_gate — wires aggregator verdict into entry path.

Built 2026-05-22 (Week 4 — final piece).

Modes:
  off   — shadow only, never affects trades
  veto  — aggregator can BLOCK (crush/fakeout/opposite direction)
  full  — veto + confirm
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in ["EARLY_MOVE_ENTRY_MODE", "EARLY_MOVE_ENTRY_SHADOW",
                "EARLY_MOVE_AGGREGATOR_ENABLED", "EARLY_MOVE_SCALPER_FIRE"]:
        monkeypatch.delenv(var, raising=False)


class _FakeEngine:
    """Minimal engine stub — no chains, so aggregator returns NO_TRADE."""
    spot_tokens = {}
    prices = {}
    chains = {}


# ── MODE PARSING ───────────────────────────────────────────────────────

class TestModeParsing:
    def test_default_off(self):
        from early_move.entry_gate import entry_mode
        assert entry_mode() == "off"

    def test_veto_mode(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_ENTRY_MODE", "veto")
        from early_move.entry_gate import entry_mode
        assert entry_mode() == "veto"

    def test_full_mode(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_ENTRY_MODE", "full")
        from early_move.entry_gate import entry_mode
        assert entry_mode() == "full"

    def test_invalid_falls_back_off(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_ENTRY_MODE", "garbage")
        from early_move.entry_gate import entry_mode
        assert entry_mode() == "off"


# ── OFF MODE — never affects trades ────────────────────────────────────

class TestOffMode:
    def test_off_always_allows(self):
        from early_move.entry_gate import evaluate_entry
        result = evaluate_entry(
            engine=_FakeEngine(), idx="BANKNIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert result["allow"] is True
        assert result["mode"] == "off"


# ── VETO MODE — aggregate decisions ────────────────────────────────────

class TestVetoMode:
    def test_veto_allows_when_aggregator_no_trade(self, monkeypatch):
        """Empty data → aggregator NO_TRADE → entry allowed."""
        monkeypatch.setenv("EARLY_MOVE_ENTRY_MODE", "veto")
        from early_move.entry_gate import evaluate_entry
        result = evaluate_entry(
            engine=_FakeEngine(), idx="BANKNIFTY",
            proposed_action="BUY CE", source="test",
        )
        # No chain data → aggregator NO_TRADE → allow
        assert result["allow"] is True

    def test_veto_blocks_on_aggregator_blocked(self, monkeypatch):
        """When aggregator returns BLOCKED → entry blocked."""
        monkeypatch.setenv("EARLY_MOVE_ENTRY_MODE", "veto")
        import early_move.entry_gate as eg
        # Monkeypatch aggregator.get_verdict to return BLOCKED
        import early_move.aggregator as agg
        monkeypatch.setattr(agg, "get_verdict", lambda **kw: {
            "verdict": "BLOCKED", "blocked_by": "IV_CRUSH",
            "action": "BLOCKED — IV crush", "direction": None,
        })
        result = eg.evaluate_entry(
            engine=_FakeEngine(), idx="BANKNIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert result["allow"] is False
        assert "IV_CRUSH" in result["reason"]

    def test_veto_blocks_on_opposite_fire(self, monkeypatch):
        """Aggregator FIRE BEAR but trade is BUY CE (BULL) → conflict → block."""
        monkeypatch.setenv("EARLY_MOVE_ENTRY_MODE", "veto")
        import early_move.entry_gate as eg
        import early_move.aggregator as agg
        monkeypatch.setattr(agg, "get_verdict", lambda **kw: {
            "verdict": "FIRE", "direction": "BEAR",
            "detectors_agreed": 3, "confidence": 0.8, "action": "FIRE BUY PE",
        })
        result = eg.evaluate_entry(
            engine=_FakeEngine(), idx="BANKNIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert result["allow"] is False
        assert "conflict" in result["reason"].lower()

    def test_veto_allows_on_same_direction_fire(self, monkeypatch):
        """Aggregator FIRE BULL + trade BUY CE → agree → allow."""
        monkeypatch.setenv("EARLY_MOVE_ENTRY_MODE", "veto")
        import early_move.entry_gate as eg
        import early_move.aggregator as agg
        monkeypatch.setattr(agg, "get_verdict", lambda **kw: {
            "verdict": "FIRE", "direction": "BULL",
            "detectors_agreed": 3, "confidence": 0.85, "action": "FIRE BUY CE",
        })
        result = eg.evaluate_entry(
            engine=_FakeEngine(), idx="BANKNIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert result["allow"] is True
        assert "CONFIRM" in result["reason"]


# ── SAFETY — aggregator errors never block ─────────────────────────────

class TestSafety:
    def test_aggregator_exception_allows_trade(self, monkeypatch):
        """If aggregator throws, entry must still be ALLOWED (fail-open)."""
        monkeypatch.setenv("EARLY_MOVE_ENTRY_MODE", "veto")
        import early_move.entry_gate as eg
        import early_move.aggregator as agg

        def _boom(**kw):
            raise RuntimeError("aggregator broke")

        monkeypatch.setattr(agg, "get_verdict", _boom)
        result = eg.evaluate_entry(
            engine=_FakeEngine(), idx="BANKNIFTY",
            proposed_action="BUY CE", source="test",
        )
        # Aggregator error → verdict NO_TRADE → allow
        assert result["allow"] is True


# ── INDEPENDENT FIRE — evaluate_fire ───────────────────────────────────

class TestEvaluateFire:
    def test_fire_default_is_shadow(self):
        from early_move.entry_gate import fire_mode
        assert fire_mode() == "shadow"

    def test_fire_off_never_fires(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_SCALPER_FIRE", "off")
        from early_move.entry_gate import evaluate_fire
        r = evaluate_fire(engine=_FakeEngine(), idx="BANKNIFTY")
        assert r["fire"] is False
        assert r["mode"] == "off"

    def test_fire_shadow_never_trades(self, monkeypatch):
        """Shadow mode: even on a FIRE verdict, fire stays False."""
        monkeypatch.setenv("EARLY_MOVE_SCALPER_FIRE", "shadow")
        import early_move.entry_gate as eg
        import early_move.aggregator as agg
        monkeypatch.setattr(agg, "get_verdict", lambda **kw: {
            "verdict": "FIRE", "direction": "BULL",
            "detectors_agreed": 3, "confidence": 0.8,
        })
        r = eg.evaluate_fire(engine=_FakeEngine(), idx="BANKNIFTY")
        assert r["fire"] is False
        assert r["mode"] == "shadow"

    def test_fire_live_fires_on_bull(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_SCALPER_FIRE", "live")
        import early_move.entry_gate as eg
        import early_move.aggregator as agg
        monkeypatch.setattr(agg, "get_verdict", lambda **kw: {
            "verdict": "FIRE", "direction": "BULL",
            "detectors_agreed": 3, "confidence": 0.85,
        })
        r = eg.evaluate_fire(engine=_FakeEngine(), idx="NIFTY")
        assert r["fire"] is True
        assert r["action"] == "BUY CE"

    def test_fire_live_fires_on_bear(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_SCALPER_FIRE", "live")
        import early_move.entry_gate as eg
        import early_move.aggregator as agg
        monkeypatch.setattr(agg, "get_verdict", lambda **kw: {
            "verdict": "FIRE", "direction": "BEAR",
            "detectors_agreed": 4, "confidence": 0.9,
        })
        r = eg.evaluate_fire(engine=_FakeEngine(), idx="NIFTY")
        assert r["fire"] is True
        assert r["action"] == "BUY PE"

    def test_fire_live_no_trade_does_not_fire(self, monkeypatch):
        monkeypatch.setenv("EARLY_MOVE_SCALPER_FIRE", "live")
        import early_move.entry_gate as eg
        import early_move.aggregator as agg
        monkeypatch.setattr(agg, "get_verdict", lambda **kw: {
            "verdict": "NO_TRADE", "direction": None,
        })
        r = eg.evaluate_fire(engine=_FakeEngine(), idx="NIFTY")
        assert r["fire"] is False

    def test_fire_aggregator_exception_fails_safe(self, monkeypatch):
        """Unvalidated trigger must fail SAFE — aggregator error → no fire."""
        monkeypatch.setenv("EARLY_MOVE_SCALPER_FIRE", "live")
        import early_move.entry_gate as eg
        import early_move.aggregator as agg

        def _boom(**kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(agg, "get_verdict", _boom)
        r = eg.evaluate_fire(engine=_FakeEngine(), idx="NIFTY")
        assert r["fire"] is False


# ── DIRECTION HELPERS ──────────────────────────────────────────────────

class TestDirectionHelpers:
    def test_action_to_direction(self):
        from early_move.entry_gate import _action_to_direction
        assert _action_to_direction("BUY CE") == "BULL"
        assert _action_to_direction("BUY PE") == "BEAR"
        assert _action_to_direction("") is None

    def test_opposite(self):
        from early_move.entry_gate import _opposite
        assert _opposite("BULL", "BEAR") is True
        assert _opposite("BULL", "BULL") is False
        assert _opposite("BEAR", "NEUTRAL") is False


# ── DIAGNOSTICS ────────────────────────────────────────────────────────

class TestDiagnostics:
    def test_diagnostics_shape(self):
        from early_move.entry_gate import diagnostics
        d = diagnostics()
        assert d["mode"] == "off"
        assert "modes_available" in d
        assert set(d["modes_available"]) == {"off", "veto", "full"}
        assert d["fire_mode"] == "shadow"
        assert set(d["fire_modes_available"]) == {"off", "shadow", "live"}
