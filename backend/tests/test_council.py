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

    def test_votes_from_engine_dict(self):
        """The shape main.py / engine.py will pass."""
        eng_dict = {
            "seller_positioning": +12,
            "oi_flow": -8,
            "vwap": +3,
            "fii_dii": 0,
            "unknown_engine": +5,  # not in COUNCIL_ENGINES — should be skipped
        }
        votes = votes_from_engine_dict(eng_dict, bull_score=15, bear_score=8)
        # Should produce votes only for engines in COUNCIL_ENGINES
        engine_names = [v.engine for v in votes]
        assert "seller_positioning" in engine_names
        assert "oi_flow" in engine_names
        assert "vwap" in engine_names
        assert "fii_dii" in engine_names
        assert "unknown_engine" not in engine_names

        # Directions correct
        sp = next(v for v in votes if v.engine == "seller_positioning")
        assert sp.direction == Direction.BULLISH
        of = next(v for v in votes if v.engine == "oi_flow")
        assert of.direction == Direction.BEARISH
        fii = next(v for v in votes if v.engine == "fii_dii")
        assert fii.direction == Direction.NEUTRAL  # net 0
