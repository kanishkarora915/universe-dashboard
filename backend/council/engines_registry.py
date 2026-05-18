"""
engines_registry — adapters that translate existing engine outputs
into the council's EngineVote schema.

WHY THIS FILE EXISTS

The existing engines in engine.py emit per-engine scores into a `_eng`
dict (per the _compute_verdict logic around line 2900). Each engine
contributes to bull_score / bear_score separately.

This registry wraps each engine's output and emits a normalized
EngineVote that the council can aggregate.

CURRENT STATUS (Phase 1 scaffold)

Adapters are PLACEHOLDERS. They define the interface but return
NEUTRAL votes for now. The real signal extraction will be wired up
once we hook into the engine's verdict cycle in Phase 1 implementation.

This keeps the package importable + testable without touching
production engine code yet.
"""

from typing import Optional, Callable
from datetime import datetime

from .vote import EngineVote, Direction, Horizon


# ── Registry of engines we want to include in the council ────────────

# Order matters for display but not for aggregation.
COUNCIL_ENGINES = [
    "seller_positioning",
    "trap_fingerprints",
    "price_action",
    "oi_flow",
    "market_context",
    "vwap",
    "multi_timeframe",
    "fii_dii",
    "global_cues",
    "predictive",
    "smart_money",
]


# ── Score → vote translation ─────────────────────────────────────────

def score_to_vote(
    engine_name: str,
    bull_score: float,
    bear_score: float,
    reasoning: str = "",
    horizon: Horizon = Horizon.INTRADAY,
    raw_score: Optional[dict] = None,
) -> EngineVote:
    """Convert a (bull, bear) score pair into a single EngineVote.

    Args:
        engine_name:  Engine key (e.g. "seller_positioning")
        bull_score:   Engine's contribution to bull side (0+)
        bear_score:   Engine's contribution to bear side (0+)
        reasoning:    One-line explanation
        horizon:      Time scope
        raw_score:    Original engine output for audit

    Logic:
        Net score = bull - bear.
        If |net| < 1.0 → NEUTRAL (engine had no opinion).
        If net > 0 → BULLISH with conviction = min(10, net).
        If net < 0 → BEARISH with conviction = min(10, |net|).

    Note: conviction is capped at 10.0 even if engine has higher
    point allocation, to keep all engines on the same 0-10 scale.
    """
    net = bull_score - bear_score
    abs_net = abs(net)

    if abs_net < 1.0:
        direction = Direction.NEUTRAL
        conviction = 0.0
    elif net > 0:
        direction = Direction.BULLISH
        conviction = min(10.0, net)
    else:
        direction = Direction.BEARISH
        conviction = min(10.0, abs_net)

    return EngineVote(
        engine=engine_name,
        direction=direction,
        conviction=conviction,
        reasoning=reasoning,
        timestamp=datetime.now(),
        horizon=horizon,
        raw_score=raw_score,
    )


def votes_from_engine_dict(
    eng_dict: dict,
    bull_score: float,
    bear_score: float,
    bull_reasons: Optional[list] = None,
    bear_reasons: Optional[list] = None,
) -> list:
    """Build council votes from engine.py's _eng dict + bull/bear reasons.

    IMPORTANT: The _eng dict in engine.py stores MAGNITUDE only:
        _eng["fii_dii"] = (bull_score - _bs0) + (bear_score - _be0)
                          ^ always >=0 regardless of direction
    So we CANNOT determine direction from the value alone.

    Instead, we use bull_reasons / bear_reasons membership to determine
    direction:
      • If the engine's reasoning string is in bull_reasons → BULLISH
      • If in bear_reasons → BEARISH
      • If in both / neither → NEUTRAL (conflicted or no clear signal)

    The _eng value gives us the conviction magnitude.

    Returns one EngineVote per engine in COUNCIL_ENGINES (where data
    is available). Missing engines are silently skipped.
    """
    bull_reasons = bull_reasons or []
    bear_reasons = bear_reasons or []
    votes = []

    for name in COUNCIL_ENGINES:
        if name not in eng_dict:
            continue

        magnitude = abs(eng_dict[name])  # _eng value is always >=0

        # Determine direction from which reasons list mentions this engine.
        in_bull = _engine_mentioned_in(name, bull_reasons)
        in_bear = _engine_mentioned_in(name, bear_reasons)

        if magnitude < 1.0 or (in_bull and in_bear):
            # No conviction OR conflicting signals → NEUTRAL
            direction = Direction.NEUTRAL
            conviction = 0.0
            engine_bull = 0.0
            engine_bear = 0.0
        elif in_bull:
            direction = Direction.BULLISH
            conviction = min(10.0, magnitude)
            engine_bull = magnitude
            engine_bear = 0.0
        elif in_bear:
            direction = Direction.BEARISH
            conviction = min(10.0, magnitude)
            engine_bull = 0.0
            engine_bear = magnitude
        else:
            # Engine contributed but no matching reasoning — direction
            # unknown. Mark as NEUTRAL to avoid wrong-direction trades.
            direction = Direction.NEUTRAL
            conviction = 0.0
            engine_bull = 0.0
            engine_bear = 0.0

        # Best-effort reasoning extraction
        reasoning = _find_reasoning_for(name, bull_reasons, bear_reasons)

        # Build vote DIRECTLY (bypass score_to_vote so we can set
        # direction explicitly — score_to_vote would re-infer from
        # bull/bear scores which is what we're avoiding).
        from datetime import datetime
        vote = EngineVote(
            engine=name,
            direction=direction,
            conviction=conviction,
            reasoning=reasoning,
            timestamp=datetime.now(),
            horizon=Horizon.INTRADAY,
            raw_score={
                "magnitude": magnitude,
                "in_bull_reasons": in_bull,
                "in_bear_reasons": in_bear,
                "engine_bull": engine_bull,
                "engine_bear": engine_bear,
            },
        )
        votes.append(vote)

    return votes


def _engine_mentioned_in(engine_name: str, reasons: list) -> bool:
    """Return True if any reason string in the list contains a keyword
    associated with this engine.
    """
    keyword_map = {
        "seller_positioning": ("writ", "selling", "seller", "ce writ", "pe writ"),
        "trap_fingerprints":  ("trap", "gex"),
        "price_action":       ("price action", "lower close", "higher close",
                               "support", "resistance", "lower high", "higher low"),
        "oi_flow":            ("oi flow", "oi flip", "open interest", "unwind"),
        "market_context":     ("context", "ma", "moving avg", "ce premium", "pe premium"),
        "vwap":               ("vwap",),
        "multi_timeframe":    ("multi-tf", "multi tf", "5m+15m", "timeframe"),
        "fii_dii":            ("fii ", "fii:", "dii", "fii net"),
        "global_cues":        ("dow", "sgx", "global", "asian"),
        "predictive":         ("momentum", "velocity", "divergence", "exhaustion",
                               "predictive"),
        "smart_money":        ("smart money", "institutional", "iceberg", "block",
                               "itm pe volume", "itm ce volume"),
    }
    keywords = keyword_map.get(engine_name, ())
    for r in reasons:
        rl = str(r).lower()
        for kw in keywords:
            if kw.lower() in rl:
                return True
    return False


def _find_reasoning_for(
    engine_name: str,
    bull_reasons: list,
    bear_reasons: list,
) -> str:
    """Try to find a reasoning string that mentions this engine.

    Reasons come in as strings like:
        "FII net: +1500Cr (BULL) [5pts]"
        "Heavy CE writing at 23700 [8pts]"

    We do a best-effort substring match by engine-key keywords. If
    none match, returns empty string.
    """
    keyword_map = {
        "seller_positioning": ("writ", "selling", "seller"),
        "trap_fingerprints":  ("trap",),
        "price_action":       ("price action", "lower close", "higher close", "support", "resistance"),
        "oi_flow":            ("oi", "open interest", "unwind"),
        "market_context":     ("context", "ma", "moving avg"),
        "vwap":               ("vwap",),
        "multi_timeframe":    ("timeframe", "5m", "15m", "1h"),
        "fii_dii":            ("fii", "dii"),
        "global_cues":        ("dow", "sgx", "global"),
        "predictive":         ("momentum", "velocity", "divergence", "exhaustion"),
        "smart_money":        ("smart money", "institutional", "iceberg", "block"),
    }
    keywords = keyword_map.get(engine_name, ())
    for r in bull_reasons + bear_reasons:
        rl = r.lower()
        for kw in keywords:
            if kw.lower() in rl:
                return r[:120]  # truncate to keep DB rows manageable
    return ""
