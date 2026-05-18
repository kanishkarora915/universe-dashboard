"""
Council — aggregates engine votes into a single verdict.

The "smart, not strict" core of the new mind.

ALGORITHM (the "2x rule" + dissent guard)

  1. Collect votes from all participating engines for one pulse.
  2. Sum conviction by direction:
       bull_strength = sum(v.conviction for v in votes if BULLISH)
       bear_strength = sum(v.conviction for v in votes if BEARISH)
       neutral_count = count NEUTRAL votes
  3. Dissent guard: if >40% engines NEUTRAL, verdict = MIXED → NO_TRADE.
     (Too much uncertainty across the team — no consensus possible.)
  4. 2x dominance rule:
       if bull_strength >= 2 * bear_strength and bull_strength >= MIN_DOMINANT:
           STRONG_BULLISH
       elif bear_strength >= 2 * bull_strength and bear_strength >= MIN_DOMINANT:
           STRONG_BEARISH
       elif abs(bull - bear) <= TIE_BAND:
           MIXED → NO_TRADE
       elif bull_strength > bear_strength:
           LEANING_BULL  (allow entry only on premium setups)
       else:
           LEANING_BEAR

  5. Confidence: dominant_strength / max_possible_strength.

KEY PRINCIPLE
  One side must DOMINATE 2:1 to be called "strong." Otherwise it's a
  weak lean or no trade. This prevents the 5-vs-4 close calls from
  triggering full conviction trades.
"""

import time
from datetime import datetime
from typing import Iterable, Optional

from .vote import (
    EngineVote, CouncilVerdict, Direction, Action,
)


# ── Tunable constants ────────────────────────────────────────────────

# A single direction must beat the other by at least this multiple to
# count as "STRONG" (the 2x rule).
DOMINANCE_RATIO = 2.0

# Below this absolute conviction sum, even a dominant side is weak.
# Prevents "3 vs 1 with low overall conviction" from firing strong verdicts.
MIN_DOMINANT_STRENGTH = 10.0

# If |bull - bear| < this, call it MIXED regardless of which is higher.
TIE_BAND = 3.0

# Fraction of engines voting NEUTRAL above which the council refuses
# to issue a directional verdict (dissent guard).
MAX_NEUTRAL_PCT = 0.40

# Confidence normalization — the theoretical max strength if every
# engine voted same direction at full conviction.
# Engines emit up to 10.0 conviction; with 11 engines max possible = 110.
MAX_POSSIBLE_STRENGTH = 110.0


class Council:
    """Aggregates engine votes into a single verdict.

    Stateless by design — every aggregate() call is independent. State
    (history, accuracy stats) lives in council.db, written separately.

    Usage:
        council = Council()
        verdict = council.aggregate(votes)
    """

    def __init__(
        self,
        dominance_ratio: float = DOMINANCE_RATIO,
        min_dominant_strength: float = MIN_DOMINANT_STRENGTH,
        tie_band: float = TIE_BAND,
        max_neutral_pct: float = MAX_NEUTRAL_PCT,
    ):
        self.dominance_ratio = dominance_ratio
        self.min_dominant_strength = min_dominant_strength
        self.tie_band = tie_band
        self.max_neutral_pct = max_neutral_pct

    def aggregate(
        self,
        votes: Iterable[EngineVote],
        pulse_id: Optional[str] = None,
    ) -> CouncilVerdict:
        """Compute a CouncilVerdict from a batch of engine votes.

        Args:
            votes: list of EngineVote objects from this pulse.
            pulse_id: optional unique identifier; auto-generated from
                      timestamp if not provided.

        Returns:
            CouncilVerdict with direction, confidence, action, and the
            underlying audit trail.
        """
        votes_list = list(votes)
        pulse_id = pulse_id or f"pulse_{int(time.time() * 1000)}"
        now = datetime.now()

        # Edge case: no votes
        if not votes_list:
            return CouncilVerdict(
                pulse_id=pulse_id,
                timestamp=now,
                direction=Direction.MIXED,
                confidence=0.0,
                action=Action.NO_TRADE,
                bull_strength=0.0,
                bear_strength=0.0,
                neutral_count=0,
                dissent_pct=0.0,
                votes=[],
                reasoning="No engine votes provided.",
            )

        total = len(votes_list)
        bull_strength = sum(
            v.conviction for v in votes_list if v.direction == Direction.BULLISH
        )
        bear_strength = sum(
            v.conviction for v in votes_list if v.direction == Direction.BEARISH
        )
        neutral_count = sum(
            1 for v in votes_list if v.direction == Direction.NEUTRAL
        )
        dissent_pct = neutral_count / total

        # Dissent guard: too many engines unsure
        if dissent_pct > self.max_neutral_pct:
            return CouncilVerdict(
                pulse_id=pulse_id,
                timestamp=now,
                direction=Direction.MIXED,
                confidence=0.0,
                action=Action.NO_TRADE,
                bull_strength=bull_strength,
                bear_strength=bear_strength,
                neutral_count=neutral_count,
                dissent_pct=dissent_pct,
                votes=votes_list,
                reasoning=(
                    f"Dissent too high: {neutral_count}/{total} engines NEUTRAL "
                    f"({dissent_pct:.0%} > {self.max_neutral_pct:.0%} threshold). "
                    f"No directional consensus."
                ),
            )

        # 2x dominance rule
        if (bull_strength >= self.dominance_ratio * bear_strength
                and bull_strength >= self.min_dominant_strength):
            direction = Direction.STRONG_BULLISH
            confidence = min(1.0, bull_strength / MAX_POSSIBLE_STRENGTH * 2)
            action = Action.ALLOW_ENTRY
            reasoning = (
                f"Strong bullish — bull {bull_strength:.1f} vs bear {bear_strength:.1f} "
                f"({bull_strength / max(bear_strength, 0.1):.1f}x dominance)."
            )
        elif (bear_strength >= self.dominance_ratio * bull_strength
                and bear_strength >= self.min_dominant_strength):
            direction = Direction.STRONG_BEARISH
            confidence = min(1.0, bear_strength / MAX_POSSIBLE_STRENGTH * 2)
            action = Action.ALLOW_ENTRY
            reasoning = (
                f"Strong bearish — bear {bear_strength:.1f} vs bull {bull_strength:.1f} "
                f"({bear_strength / max(bull_strength, 0.1):.1f}x dominance)."
            )
        elif abs(bull_strength - bear_strength) <= self.tie_band:
            direction = Direction.MIXED
            confidence = 0.0
            action = Action.NO_TRADE
            reasoning = (
                f"Tie — bull {bull_strength:.1f} vs bear {bear_strength:.1f} "
                f"(diff {abs(bull_strength - bear_strength):.1f} <= "
                f"{self.tie_band} tie band). No trade."
            )
        elif bull_strength > bear_strength:
            direction = Direction.LEANING_BULL
            # Weaker confidence than STRONG case
            confidence = min(0.6, (bull_strength - bear_strength) / MAX_POSSIBLE_STRENGTH * 4)
            action = Action.ALLOW_ENTRY
            reasoning = (
                f"Leaning bullish — bull {bull_strength:.1f} vs bear {bear_strength:.1f} "
                f"(diff {bull_strength - bear_strength:.1f}, no 2x dominance). "
                f"Premium setups only."
            )
        else:
            direction = Direction.LEANING_BEAR
            confidence = min(0.6, (bear_strength - bull_strength) / MAX_POSSIBLE_STRENGTH * 4)
            action = Action.ALLOW_ENTRY
            reasoning = (
                f"Leaning bearish — bear {bear_strength:.1f} vs bull {bull_strength:.1f} "
                f"(diff {bear_strength - bull_strength:.1f}, no 2x dominance). "
                f"Premium setups only."
            )

        return CouncilVerdict(
            pulse_id=pulse_id,
            timestamp=now,
            direction=direction,
            confidence=confidence,
            action=action,
            bull_strength=bull_strength,
            bear_strength=bear_strength,
            neutral_count=neutral_count,
            dissent_pct=dissent_pct,
            votes=votes_list,
            reasoning=reasoning,
        )

    def would_block_entry(
        self,
        votes: Iterable[EngineVote],
        side: str,  # "CE" or "PE"
    ) -> tuple:
        """Convenience helper — would the council BLOCK a given side's entry?

        Returns (would_block: bool, reason: str). Used for the observe-only
        mode to retrospectively check if council would have caught a bad
        trade.

        Logic:
          - CE entries blocked if verdict is STRONG_BEARISH (or LEANING_BEAR
            with high confidence).
          - PE entries blocked symmetrically.
          - MIXED verdict blocks all entries.
        """
        verdict = self.aggregate(votes)

        if verdict.direction == Direction.MIXED:
            return True, f"MIXED verdict: {verdict.reasoning}"

        side_upper = side.upper()
        if side_upper == "CE" and verdict.direction in (
            Direction.STRONG_BEARISH, Direction.LEANING_BEAR
        ):
            return True, (
                f"Council says {verdict.direction.value}: {verdict.reasoning}"
            )
        if side_upper == "PE" and verdict.direction in (
            Direction.STRONG_BULLISH, Direction.LEANING_BULL
        ):
            return True, (
                f"Council says {verdict.direction.value}: {verdict.reasoning}"
            )

        return False, f"Council aligned with {side_upper}: {verdict.reasoning}"
