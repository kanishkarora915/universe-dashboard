"""
Tests for the council package — vote schema, aggregator logic, and
engine registry adapters.

These tests verify Phase 1 scaffold correctness BEFORE wiring into
the live engine. Council aggregation must be deterministic + safe
across edge cases.
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from council.vote import EngineVote, CouncilVerdict, Direction, Action, Horizon
from council.aggregator import (
    Council,
    DOMINANCE_RATIO,
    MIN_DOMINANT_STRENGTH,
    TIE_BAND,
    MAX_NEUTRAL_PCT,
)
from council.engines_registry import (
    COUNCIL_ENGINES,
    score_to_vote,
    votes_from_engine_dict,
)
from council import storage
from council.observer import observe_verdict_cycle, get_observer_health


# Helper to point storage at a temp DB for tests
import tempfile
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def temp_db(monkeypatch):
    """Each test gets a fresh isolated council.db."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)
    monkeypatch.setattr(storage, "_resolve_db_path", lambda: tmp_path)
    # Reset BOTH cached init flags — module-level _schema_applied in
    # storage, AND _db_initialized in observer — so each test starts
    # from a clean state.
    monkeypatch.setattr(storage, "_schema_applied", False)
    import council.observer as obs_mod
    monkeypatch.setattr(obs_mod, "_db_initialized", False)
    storage.init_db()
    yield tmp_path
    tmp_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────
# EngineVote schema
# ─────────────────────────────────────────────────────────────────────

class TestEngineVote:
    def test_basic_construction(self):
        v = EngineVote(
            engine="oi_flow",
            direction=Direction.BULLISH,
            conviction=7.5,
            reasoning="CE writers unwinding",
        )
        assert v.engine == "oi_flow"
        assert v.direction == Direction.BULLISH
        assert v.conviction == 7.5

    def test_conviction_clamped_high(self):
        v = EngineVote(engine="x", direction=Direction.BULLISH, conviction=999)
        assert v.conviction == 10.0  # clamped

    def test_conviction_clamped_low(self):
        v = EngineVote(engine="x", direction=Direction.BULLISH, conviction=-5)
        assert v.conviction == 0.0  # clamped

    def test_string_direction_converted_to_enum(self):
        v = EngineVote(engine="x", direction="BEARISH", conviction=5)
        assert v.direction == Direction.BEARISH  # enum, not string

    def test_engine_cannot_emit_council_only_direction(self):
        # Engines only emit BULLISH/BEARISH/NEUTRAL. Refinements like
        # STRONG_BULLISH are council-only outputs.
        with pytest.raises(ValueError):
            EngineVote(engine="x", direction=Direction.STRONG_BULLISH, conviction=10)
        with pytest.raises(ValueError):
            EngineVote(engine="x", direction=Direction.MIXED, conviction=5)

    def test_to_dict_serializable(self):
        v = EngineVote(engine="vwap", direction=Direction.BEARISH, conviction=6.0)
        d = v.to_dict()
        assert d["engine"] == "vwap"
        assert d["direction"] == "BEARISH"
        assert d["conviction"] == 6.0
        assert "timestamp" in d  # ISO string


# ─────────────────────────────────────────────────────────────────────
# Council aggregator
# ─────────────────────────────────────────────────────────────────────

class TestCouncilEdgeCases:
    def test_empty_votes_gives_mixed_no_trade(self):
        council = Council()
        v = council.aggregate([])
        assert v.direction == Direction.MIXED
        assert v.action == Action.NO_TRADE
        assert v.confidence == 0.0

    def test_all_neutral_triggers_dissent_guard(self):
        council = Council()
        votes = [
            EngineVote(engine=f"e{i}", direction=Direction.NEUTRAL, conviction=0)
            for i in range(5)
        ]
        v = council.aggregate(votes)
        assert v.direction == Direction.MIXED
        assert v.action == Action.NO_TRADE
        assert "Dissent too high" in v.reasoning

    def test_50pct_neutral_triggers_dissent_guard(self):
        # 50% > 40% MAX_NEUTRAL_PCT threshold
        council = Council()
        votes = [
            EngineVote(engine="e1", direction=Direction.NEUTRAL, conviction=0),
            EngineVote(engine="e2", direction=Direction.NEUTRAL, conviction=0),
            EngineVote(engine="e3", direction=Direction.BULLISH, conviction=10),
            EngineVote(engine="e4", direction=Direction.BULLISH, conviction=10),
        ]
        v = council.aggregate(votes)
        assert v.direction == Direction.MIXED


class TestCouncilStrongVerdicts:
    def test_unanimous_bullish_high_conviction(self):
        """5 engines all bullish at 8.0 conviction → STRONG_BULLISH."""
        council = Council()
        votes = [
            EngineVote(engine=f"e{i}", direction=Direction.BULLISH, conviction=8.0)
            for i in range(5)
        ]
        v = council.aggregate(votes)
        assert v.direction == Direction.STRONG_BULLISH
        assert v.action == Action.ALLOW_ENTRY
        assert v.bull_strength == 40.0
        assert v.bear_strength == 0.0
        assert v.is_high_conviction

    def test_unanimous_bearish_high_conviction(self):
        council = Council()
        votes = [
            EngineVote(engine=f"e{i}", direction=Direction.BEARISH, conviction=8.0)
            for i in range(5)
        ]
        v = council.aggregate(votes)
        assert v.direction == Direction.STRONG_BEARISH
        assert v.action == Action.ALLOW_ENTRY

    def test_2x_dominance_triggers_strong(self):
        """20 bull vs 8 bear → 2.5x dominance → STRONG_BULLISH."""
        council = Council()
        votes = [
            EngineVote(engine="b1", direction=Direction.BULLISH, conviction=10),
            EngineVote(engine="b2", direction=Direction.BULLISH, conviction=10),
            EngineVote(engine="r1", direction=Direction.BEARISH, conviction=4),
            EngineVote(engine="r2", direction=Direction.BEARISH, conviction=4),
        ]
        v = council.aggregate(votes)
        assert v.direction == Direction.STRONG_BULLISH
        assert v.bull_strength == 20.0
        assert v.bear_strength == 8.0

    def test_dominance_but_low_strength_is_only_leaning(self):
        """Even at 3x dominance, if absolute strength < MIN_DOMINANT, only LEANING."""
        council = Council()
        votes = [
            EngineVote(engine="b1", direction=Direction.BULLISH, conviction=3),
            EngineVote(engine="b2", direction=Direction.BULLISH, conviction=3),
            EngineVote(engine="r1", direction=Direction.BEARISH, conviction=1),
        ]
        # bull 6 > 3x bear 1, but bull < MIN_DOMINANT_STRENGTH (10)
        v = council.aggregate(votes)
        assert v.direction == Direction.LEANING_BULL
        assert not v.is_high_conviction


class TestCouncilLeaningAndTie:
    def test_close_call_in_tie_band_is_mixed(self):
        """bull=10, bear=8 → diff=2 < TIE_BAND=3 → MIXED."""
        council = Council()
        votes = [
            EngineVote(engine="b", direction=Direction.BULLISH, conviction=10),
            EngineVote(engine="r1", direction=Direction.BEARISH, conviction=4),
            EngineVote(engine="r2", direction=Direction.BEARISH, conviction=4),
        ]
        v = council.aggregate(votes)
        assert v.direction == Direction.MIXED
        assert v.action == Action.NO_TRADE

    def test_clear_lean_but_no_2x_is_leaning(self):
        """bull=15, bear=10 → 1.5x dominance → LEANING_BULL."""
        council = Council()
        votes = [
            EngineVote(engine="b1", direction=Direction.BULLISH, conviction=8),
            EngineVote(engine="b2", direction=Direction.BULLISH, conviction=7),
            EngineVote(engine="r1", direction=Direction.BEARISH, conviction=5),
            EngineVote(engine="r2", direction=Direction.BEARISH, conviction=5),
        ]
        v = council.aggregate(votes)
        assert v.direction == Direction.LEANING_BULL
        assert v.action == Action.ALLOW_ENTRY  # but premium setups only
        assert v.confidence < 0.7  # not high conviction

    def test_leaning_bear_symmetric(self):
        council = Council()
        votes = [
            EngineVote(engine="b1", direction=Direction.BULLISH, conviction=5),
            EngineVote(engine="b2", direction=Direction.BULLISH, conviction=5),
            EngineVote(engine="r1", direction=Direction.BEARISH, conviction=8),
            EngineVote(engine="r2", direction=Direction.BEARISH, conviction=7),
        ]
        v = council.aggregate(votes)
        assert v.direction == Direction.LEANING_BEAR


class TestCouncilBlockEntry:
    """The would_block_entry helper — what observe-only mode uses."""

    def test_strong_bearish_blocks_ce(self):
        council = Council()
        votes = [
            EngineVote(engine=f"e{i}", direction=Direction.BEARISH, conviction=8.0)
            for i in range(5)
        ]
        blocked, reason = council.would_block_entry(votes, "CE")
        assert blocked is True
        assert "STRONG_BEARISH" in reason

    def test_strong_bullish_blocks_pe(self):
        council = Council()
        votes = [
            EngineVote(engine=f"e{i}", direction=Direction.BULLISH, conviction=8.0)
            for i in range(5)
        ]
        blocked, reason = council.would_block_entry(votes, "PE")
        assert blocked is True
        assert "STRONG_BULLISH" in reason

    def test_strong_bullish_allows_ce(self):
        council = Council()
        votes = [
            EngineVote(engine=f"e{i}", direction=Direction.BULLISH, conviction=8.0)
            for i in range(5)
        ]
        blocked, _ = council.would_block_entry(votes, "CE")
        assert blocked is False

    def test_mixed_verdict_blocks_both(self):
        """If council can't decide → no entry either way."""
        council = Council()
        votes = [
            EngineVote(engine="b", direction=Direction.BULLISH, conviction=5),
            EngineVote(engine="r", direction=Direction.BEARISH, conviction=5),
        ]
        blocked_ce, _ = council.would_block_entry(votes, "CE")
        blocked_pe, _ = council.would_block_entry(votes, "PE")
        assert blocked_ce is True
        assert blocked_pe is True

    def test_simulates_may_12_bearish_day(self):
        """Real-world test: today's session if council had been live.

        Engines on 2026-05-12 (NIFTY -1.83%):
          - seller_positioning: heavy CE writing → BEARISH 8
          - oi_flow: writers dominating → BEARISH 8
          - price_action: 5 lower closes → BEARISH 9
          - market_context: below MAs → BEARISH 7
          - vwap: below VWAP falling → BEARISH 8
          - multi_timeframe: all TFs down → BEARISH 7
          - fii_dii: FII -1847Cr → BEARISH 6
          - global_cues: weak overnight → BEARISH 5

        Council verdict: STRONG_BEARISH, conviction 7+/10.
        → All 10 reversal_zone CE entries would have been BLOCKED.
        """
        council = Council()
        votes = [
            EngineVote(engine="seller_positioning", direction=Direction.BEARISH, conviction=8),
            EngineVote(engine="oi_flow",            direction=Direction.BEARISH, conviction=8),
            EngineVote(engine="price_action",       direction=Direction.BEARISH, conviction=9),
            EngineVote(engine="market_context",     direction=Direction.BEARISH, conviction=7),
            EngineVote(engine="vwap",               direction=Direction.BEARISH, conviction=8),
            EngineVote(engine="multi_timeframe",    direction=Direction.BEARISH, conviction=7),
            EngineVote(engine="fii_dii",            direction=Direction.BEARISH, conviction=6),
            EngineVote(engine="global_cues",        direction=Direction.BEARISH, conviction=5),
        ]
        verdict = council.aggregate(votes)
        assert verdict.direction == Direction.STRONG_BEARISH
        assert verdict.is_high_conviction
        # CE entries: should be BLOCKED
        blocked_ce, reason = council.would_block_entry(votes, "CE")
        assert blocked_ce is True
        # PE entries: should be ALLOWED (aligned with verdict)
        blocked_pe, _ = council.would_block_entry(votes, "PE")
        assert blocked_pe is False


# ─────────────────────────────────────────────────────────────────────
# engines_registry adapters
# ─────────────────────────────────────────────────────────────────────

class TestEngineRegistry:
    def test_council_engines_list_nonempty(self):
        assert len(COUNCIL_ENGINES) >= 9
        assert "seller_positioning" in COUNCIL_ENGINES
        assert "oi_flow" in COUNCIL_ENGINES

    def test_score_to_vote_bullish(self):
        v = score_to_vote("oi_flow", bull_score=12, bear_score=2, reasoning="test")
        assert v.direction == Direction.BULLISH
        assert v.conviction == 10.0  # capped (12-2=10)
        assert v.engine == "oi_flow"

    def test_score_to_vote_bearish(self):
        v = score_to_vote("vwap", bull_score=1, bear_score=8)
        assert v.direction == Direction.BEARISH
        assert v.conviction == 7.0  # |1-8| = 7

    def test_score_to_vote_neutral_when_small_net(self):
        # |net| < 1.0 → NEUTRAL
        v = score_to_vote("global_cues", bull_score=3, bear_score=2.5)
        assert v.direction == Direction.NEUTRAL
        assert v.conviction == 0.0

    def test_votes_from_engine_dict_uses_reasons_for_direction(self):
        """The _eng dict in engine.py stores MAGNITUDE only. Direction
        must be inferred from which reasons list (bull/bear) the
        engine's reasoning string lives in.

        Real-world reasoning strings from engine.py have distinctive
        prefixes per engine (FII, VWAP, Multi-TF, etc), so keyword
        matching is reliable when reasoning is realistic. Generic
        overlap (e.g. "writers" appears in multiple engines) falls
        through to NEUTRAL as safe default.
        """
        eng_dict = {
            "price_action": 3,
            "fii_dii": 10,
            "global_cues": 5,
            "unknown_engine": 5,
        }
        bull_reasons = [
            "Higher close + higher low — price action bullish [3pts]",
        ]
        bear_reasons = [
            "FII net: -1959Cr (STRONG_BEAR) [10pts]",
            "Global BEARISH: Dow -1.2% [5pts]",
        ]
        votes = votes_from_engine_dict(
            eng_dict, bull_score=3, bear_score=15,
            bull_reasons=bull_reasons, bear_reasons=bear_reasons,
        )
        engine_names = [v.engine for v in votes]
        assert "unknown_engine" not in engine_names

        # price_action: in bull_reasons → BULLISH
        pa = next(v for v in votes if v.engine == "price_action")
        assert pa.direction == Direction.BULLISH

        # fii_dii: in bear_reasons → BEARISH (this is THE bug we fixed)
        fii = next(v for v in votes if v.engine == "fii_dii")
        assert fii.direction == Direction.BEARISH
        assert fii.conviction == 10.0

        # global_cues: in bear_reasons → BEARISH
        gc = next(v for v in votes if v.engine == "global_cues")
        assert gc.direction == Direction.BEARISH

    def test_engine_with_positive_value_but_bear_reason_is_bearish(self):
        """THIS is the bug we just fixed.

        engine.py emits `_eng["fii_dii"] = +10` even when FII is BEARISH
        (because the formula is `bull_delta + bear_delta`, always >=0).
        Without checking reasons, we'd wrongly call this BULLISH.
        """
        eng_dict = {"fii_dii": 10}
        bull_reasons = []
        bear_reasons = [
            "FII net: -1959Cr (STRONG_BEAR) [10pts]"
        ]
        votes = votes_from_engine_dict(
            eng_dict, bull_score=0, bear_score=10,
            bull_reasons=bull_reasons, bear_reasons=bear_reasons,
        )
        fii = next(v for v in votes if v.engine == "fii_dii")
        # Bug fix: must read BEARISH, not BULLISH
        assert fii.direction == Direction.BEARISH
        assert fii.conviction == 10.0

    def test_engine_with_no_matching_reason_defaults_neutral(self):
        """If we can't determine direction from reasons, vote NEUTRAL
        rather than guess wrong."""
        eng_dict = {"oi_flow": 5}
        votes = votes_from_engine_dict(
            eng_dict, bull_score=5, bear_score=0,
            bull_reasons=["something unrelated"],
            bear_reasons=["something else unrelated"],
        )
        of = next(v for v in votes if v.engine == "oi_flow")
        assert of.direction == Direction.NEUTRAL
        assert of.conviction == 0.0


# ─────────────────────────────────────────────────────────────────────
# Storage (council.db)
# ─────────────────────────────────────────────────────────────────────

class TestStorage:
    def test_init_db_creates_tables(self, temp_db):
        # init_db is idempotent + creates all required tables
        import sqlite3
        conn = sqlite3.connect(str(temp_db))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        conn.close()
        assert "engine_votes" in tables
        assert "council_verdicts" in tables
        assert "daily_briefings" in tables
        assert "engine_accuracy" in tables

    def test_save_and_retrieve_verdict(self, temp_db):
        council = Council()
        votes = [
            EngineVote(engine="oi_flow", direction=Direction.BEARISH, conviction=8),
            EngineVote(engine="vwap",    direction=Direction.BEARISH, conviction=7),
            EngineVote(engine="fii_dii", direction=Direction.BEARISH, conviction=6),
            EngineVote(engine="global_cues", direction=Direction.BEARISH, conviction=5),
        ]
        verdict = council.aggregate(votes, pulse_id="test_pulse_001")
        storage.save_verdict(verdict)

        latest = storage.get_latest_verdict()
        assert latest is not None
        assert latest["pulse_id"] == "test_pulse_001"
        assert latest["direction"] == "STRONG_BEARISH"
        assert latest["confidence"] > 0
        assert len(latest["votes"]) == 4

    def test_get_recent_verdicts(self, temp_db):
        council = Council()
        for i in range(5):
            votes = [
                EngineVote(engine="e1", direction=Direction.BULLISH, conviction=5),
                EngineVote(engine="e2", direction=Direction.BULLISH, conviction=5),
            ]
            v = council.aggregate(votes, pulse_id=f"pulse_{i:03d}")
            storage.save_verdict(v)

        recent = storage.get_recent_verdicts(limit=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0]["pulse_id"] == "pulse_004"

    def test_mark_trade_outcome(self, temp_db):
        council = Council()
        votes = [EngineVote(engine="e1", direction=Direction.BULLISH, conviction=10)]
        v = council.aggregate(votes, pulse_id="outcome_test")
        storage.save_verdict(v)

        storage.mark_trade_outcome("outcome_test", trade_fired=True, pnl=1500.5)

        # Verify update
        records = storage.get_recent_verdicts(limit=1)
        assert records[0]["actual_trade_fired"] == 1
        assert records[0]["actual_outcome_pnl"] == 1500.5

    def test_summary_stats(self, temp_db):
        council = Council()
        # Mix of verdicts
        verdicts_data = [
            [EngineVote(engine="e1", direction=Direction.BULLISH, conviction=10),
             EngineVote(engine="e2", direction=Direction.BULLISH, conviction=10)],
            [EngineVote(engine="e1", direction=Direction.BEARISH, conviction=10),
             EngineVote(engine="e2", direction=Direction.BEARISH, conviction=10)],
            [EngineVote(engine="e1", direction=Direction.BULLISH, conviction=5),
             EngineVote(engine="e2", direction=Direction.BEARISH, conviction=5)],
        ]
        for i, votes in enumerate(verdicts_data):
            v = council.aggregate(votes, pulse_id=f"sum_{i}")
            storage.save_verdict(v)

        summary = storage.summary_stats(days=1)
        assert summary["total_verdicts"] == 3
        # one STRONG_BULLISH, one STRONG_BEARISH, one MIXED
        assert "STRONG_BULLISH" in summary["by_direction"]
        assert "STRONG_BEARISH" in summary["by_direction"]


# ─────────────────────────────────────────────────────────────────────
# Observer (engine.py bridge)
# ─────────────────────────────────────────────────────────────────────

class TestObserver:
    def test_observe_returns_verdict(self, temp_db):
        # _eng dict in engine.py stores MAGNITUDE only (always >=0).
        # Direction comes from which reasons list mentions the engine.
        eng_dict = {
            "oi_flow": 12,
            "vwap": 8,
            "fii_dii": 6,
            "global_cues": 5,
            "seller_positioning": 10,
        }
        bear_reasons = [
            "OI flow strongly bearish — CE writers add 5L [12pts]",
            "VWAP below + falling, spot rejected from VWAP [8pts]",
            "FII net -1500Cr (STRONG_BEAR) [6pts]",
            "Global cues bearish: Dow -1.2% Asian red [5pts]",
            "CE writers stacking 5L at 23500 (resistance) [10pts]",
        ]
        verdict = observe_verdict_cycle(
            index="NIFTY",
            eng_dict=eng_dict,
            bull_score=0,
            bear_score=41,
            bull_reasons=[],
            bear_reasons=bear_reasons,
        )
        assert verdict is not None
        assert verdict.direction == Direction.STRONG_BEARISH

    def test_observe_disabled_returns_none(self, temp_db, monkeypatch):
        # Patch the COUNCIL_ENABLED flag IN the observer module (where it's imported)
        import council.observer as obs_mod
        monkeypatch.setattr(obs_mod, "COUNCIL_ENABLED", False)
        result = observe_verdict_cycle(
            index="NIFTY", eng_dict={}, bull_score=0, bear_score=0,
        )
        assert result is None

    def test_observe_handles_empty_dict(self, temp_db):
        # Edge case — no engine scores yet
        verdict = observe_verdict_cycle(
            index="NIFTY",
            eng_dict={},
            bull_score=0,
            bear_score=0,
        )
        # Should still produce a verdict (MIXED with no votes)
        assert verdict is not None
        assert verdict.direction == Direction.MIXED

    def test_observer_health(self, temp_db):
        # Save one verdict first so health has something to report
        eng_dict = {"vwap": -5, "fii_dii": -4}
        observe_verdict_cycle(
            index="NIFTY", eng_dict=eng_dict, bull_score=0, bear_score=9,
        )
        # Let the background save thread finish
        import time
        time.sleep(0.3)
        health = get_observer_health()
        assert health["enabled"] is True
        assert "last_24h" in health
