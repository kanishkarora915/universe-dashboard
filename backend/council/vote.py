"""
Schemas for engine votes and council verdicts.

These are passive data containers — no logic. The Council class in
aggregator.py uses them.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class Direction(str, Enum):
    """The direction an engine or council can vote for."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

    # Council-only refinements (engines emit only the three above):
    STRONG_BULLISH = "STRONG_BULLISH"
    LEANING_BULL = "LEANING_BULL"
    MIXED = "MIXED"
    LEANING_BEAR = "LEANING_BEAR"
    STRONG_BEARISH = "STRONG_BEARISH"


class Horizon(str, Enum):
    """Time horizon an engine's signal is meaningful over."""
    INTRADAY = "INTRADAY"      # next 5 min - 1 hr
    EOD = "EOD"                # rest of today
    OVERNIGHT = "OVERNIGHT"    # tomorrow's open


class Action(str, Enum):
    """What the council recommends to do based on the verdict."""
    ALLOW_ENTRY = "ALLOW_ENTRY"
    NO_TRADE = "NO_TRADE"
    EXIT_NOW = "EXIT_NOW"
    SCALPER_ONLY = "SCALPER_ONLY"


@dataclass
class EngineVote:
    """One engine's opinion on market direction for a single pulse.

    Engines emit a vote per pulse cycle (~1 Hz). Multiple votes per
    engine over time form an accuracy track record.

    Attributes:
        engine:     Engine key, e.g. "seller_positioning".
        direction:  BULLISH | BEARISH | NEUTRAL (engines only emit these
                    three; refinements happen at council level).
        conviction: 0.0 (no opinion) to 10.0 (high conviction).
        reasoning:  One-line explanation for the audit log.
        timestamp:  When the vote was emitted (IST).
        horizon:    Time scope of the prediction.
        raw_score:  Original engine output for traceability.
    """
    engine: str
    direction: Direction
    conviction: float
    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    horizon: Horizon = Horizon.INTRADAY
    raw_score: Optional[dict] = None

    def __post_init__(self):
        # Normalize / validate
        if isinstance(self.direction, str):
            self.direction = Direction(self.direction)
        if isinstance(self.horizon, str):
            self.horizon = Horizon(self.horizon)
        # Clamp conviction
        self.conviction = max(0.0, min(10.0, float(self.conviction)))
        # Engines may only emit the basic three — protect council semantics
        allowed = {Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL}
        if self.direction not in allowed:
            raise ValueError(
                f"Engine vote direction must be one of "
                f"{[d.value for d in allowed]}, got {self.direction.value}"
            )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["horizon"] = self.horizon.value
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class CouncilVerdict:
    """Aggregated council decision for a single pulse.

    Built from a batch of EngineVotes via Council.aggregate(votes).

    Attributes:
        pulse_id:       Unique identifier for this pulse (timestamp-derived).
        timestamp:      When the verdict was computed.
        direction:      One of STRONG_BULLISH / LEANING_BULL / MIXED /
                        LEANING_BEAR / STRONG_BEARISH.
        confidence:     0.0 (no conviction, MIXED) to 1.0 (unanimous).
        action:         What to actually do per the verdict.
        bull_strength:  Sum of conviction across BULLISH-voting engines.
        bear_strength:  Sum of conviction across BEARISH-voting engines.
        neutral_count:  How many engines voted NEUTRAL.
        dissent_pct:    neutral_count / total_engines.
        votes:          The underlying engine votes (for audit).
        reasoning:      Short explanation of why this verdict.
    """
    pulse_id: str
    direction: Direction
    confidence: float
    action: Action
    bull_strength: float
    bear_strength: float
    neutral_count: int
    dissent_pct: float
    timestamp: datetime = field(default_factory=datetime.now)
    votes: list = field(default_factory=list)
    reasoning: str = ""

    def __post_init__(self):
        if isinstance(self.direction, str):
            self.direction = Direction(self.direction)
        if isinstance(self.action, str):
            self.action = Action(self.action)
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict:
        return {
            "pulse_id": self.pulse_id,
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction.value,
            "confidence": round(self.confidence, 3),
            "action": self.action.value,
            "bull_strength": round(self.bull_strength, 2),
            "bear_strength": round(self.bear_strength, 2),
            "neutral_count": self.neutral_count,
            "dissent_pct": round(self.dissent_pct, 3),
            "reasoning": self.reasoning,
            "votes": [v.to_dict() if hasattr(v, "to_dict") else v
                      for v in self.votes],
        }

    @property
    def is_high_conviction(self) -> bool:
        """True if confidence ≥ 0.7 — suitable for entry."""
        return self.confidence >= 0.7

    @property
    def is_actionable(self) -> bool:
        """True if council recommends taking action (not NO_TRADE)."""
        return self.action != Action.NO_TRADE
