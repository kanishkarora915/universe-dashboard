"""
price_structure — HH/HL/LH/LL trend structure detection.

Built 2026-05-27 per user vision (Option Y, Whole System).

NOTE: this is DIFFERENT from `market_structure.py` (which scores
sweep/pin/stop-hunt patterns for the existing 11-engine verdict).
`price_structure` is the new Dow-Theory-based trend detector.

WHY THIS EXISTS

The system reads many signals (OI, IV, volume, capitulation, 11 engines)
but NONE of them directly answer "what is price doing right now?".
Trend structure — the 120-year-old Dow Theory concept — fills that gap.

CORE CONCEPTS

  Swing High: a candle whose high > N candles before AND N after
              (Bill Williams' fractal method, 1990s)
  Swing Low:  mirrored

  UPTREND   = last 2 swing highs ascending AND last 2 swing lows ascending
              (HH+HL — "stairs going up")
  DOWNTREND = last 2 swing highs descending AND last 2 swing lows descending
              (LH+LL — "stairs going down")
  CHOP      = neither pattern clean (mixed/sideways)
  BREAK     = was UPTREND but new LL formed (or vice versa)

USAGE

    from price_structure import detect_structure
    result = detect_structure(candles)   # candles = list of OHLCV dicts
    # result["verdict"] in ("UPTREND", "DOWNTREND", "CHOP", "UNKNOWN")

ENV — tunable knobs
  STRUCTURE_FRACTAL_BARS (default 2) — bars each side for swing point
  STRUCTURE_MIN_SWINGS   (default 2) — min swings each side for verdict

The pure module has NO side effects — caller decides how to use the
verdict. Integration into trade gates is intentionally separated so the
detection logic can be tested in isolation.
"""

from __future__ import annotations
from typing import List, Dict, Optional
import os


# ── Config ────────────────────────────────────────────────────────────


def _fractal_bars() -> int:
    """Bars each side of a swing point. 2 = classic Bill Williams fractal."""
    try:
        return max(1, int(os.environ.get("STRUCTURE_FRACTAL_BARS", "2") or 2))
    except Exception:
        return 2


def _min_swings() -> int:
    """Minimum number of swings each side required for a verdict."""
    try:
        return max(1, int(os.environ.get("STRUCTURE_MIN_SWINGS", "2") or 2))
    except Exception:
        return 2


# ── Swing point detection (fractal method) ────────────────────────────


def find_swing_highs(candles: List[Dict], bars: int = 2) -> List[Dict]:
    """Find swing high points using Bill Williams' fractal method.

    A candle at index i is a swing high if its high is STRICTLY greater
    than the highs of `bars` candles before AND `bars` candles after.
    Only candles in the range [bars, len-bars-1] can be evaluated —
    swings near the edges aren't yet "confirmed".

    Args:
        candles: list of dicts with at least "high" key
        bars: lookback/lookahead width (default 2 = 5-candle fractal)

    Returns:
        List of dicts: [{"index": int, "ts": str, "price": float}, ...]
        in chronological order.
    """
    if not candles or len(candles) < 2 * bars + 1:
        return []
    swings = []
    for i in range(bars, len(candles) - bars):
        high = candles[i].get("high", 0)
        if high <= 0:
            continue
        # Left side: all `bars` candles before must have lower high
        left_ok = all(
            candles[j].get("high", 0) < high for j in range(i - bars, i)
        )
        if not left_ok:
            continue
        # Right side: all `bars` candles after must have lower high
        right_ok = all(
            candles[j].get("high", 0) < high for j in range(i + 1, i + bars + 1)
        )
        if not right_ok:
            continue
        swings.append({
            "index": i,
            "ts": candles[i].get("ts"),
            "price": high,
            "type": "HIGH",
        })
    return swings


def find_swing_lows(candles: List[Dict], bars: int = 2) -> List[Dict]:
    """Mirror of find_swing_highs for lows."""
    if not candles or len(candles) < 2 * bars + 1:
        return []
    swings = []
    for i in range(bars, len(candles) - bars):
        low = candles[i].get("low", 0)
        if low <= 0:
            continue
        left_ok = all(
            candles[j].get("low", 0) > low for j in range(i - bars, i)
        )
        if not left_ok:
            continue
        right_ok = all(
            candles[j].get("low", 0) > low for j in range(i + 1, i + bars + 1)
        )
        if not right_ok:
            continue
        swings.append({
            "index": i,
            "ts": candles[i].get("ts"),
            "price": low,
            "type": "LOW",
        })
    return swings


# ── Structure verdict (Dow Theory) ────────────────────────────────────


def detect_structure(
    candles: List[Dict],
    fractal_bars: Optional[int] = None,
    min_swings: Optional[int] = None,
) -> Dict:
    """Detect trend structure (UPTREND/DOWNTREND/CHOP) from candle data.

    Args:
        candles: list of OHLCV dicts (keys: ts, open, high, low, close, volume)
        fractal_bars: override env STRUCTURE_FRACTAL_BARS
        min_swings: override env STRUCTURE_MIN_SWINGS

    Returns:
        {
          "verdict": "UPTREND" | "DOWNTREND" | "CHOP" | "UNKNOWN",
          "confidence": "HIGH" | "MEDIUM" | "LOW",
          "reason": str,                # human explanation
          "swing_highs": [...],         # chronological list
          "swing_lows": [...],
          "last_high": float | None,    # most recent swing high price
          "last_low": float | None,
          "prev_high": float | None,    # 2nd most recent
          "prev_low": float | None,
        }

    Pure function: no side effects, no I/O.
    """
    if fractal_bars is None:
        fractal_bars = _fractal_bars()
    if min_swings is None:
        min_swings = _min_swings()

    base_result = {
        "swing_highs": [],
        "swing_lows": [],
        "last_high": None,
        "last_low": None,
        "prev_high": None,
        "prev_low": None,
    }

    if not candles or len(candles) < 2 * fractal_bars + 1:
        return {
            **base_result,
            "verdict": "UNKNOWN",
            "confidence": "LOW",
            "reason": (
                f"Not enough candles ({len(candles) if candles else 0}, "
                f"need {2 * fractal_bars + 1}+)"
            ),
        }

    highs = find_swing_highs(candles, fractal_bars)
    lows = find_swing_lows(candles, fractal_bars)

    base_result["swing_highs"] = highs
    base_result["swing_lows"] = lows
    if highs:
        base_result["last_high"] = highs[-1]["price"]
    if len(highs) >= 2:
        base_result["prev_high"] = highs[-2]["price"]
    if lows:
        base_result["last_low"] = lows[-1]["price"]
    if len(lows) >= 2:
        base_result["prev_low"] = lows[-2]["price"]

    if len(highs) < min_swings or len(lows) < min_swings:
        return {
            **base_result,
            "verdict": "UNKNOWN",
            "confidence": "LOW",
            "reason": (
                f"Not enough swings (highs={len(highs)}, lows={len(lows)}, "
                f"need {min_swings}+ each)"
            ),
        }

    # Compare the last `min_swings` highs and lows
    recent_highs = highs[-min_swings:]
    recent_lows = lows[-min_swings:]

    highs_ascending = all(
        recent_highs[i]["price"] > recent_highs[i - 1]["price"]
        for i in range(1, len(recent_highs))
    )
    highs_descending = all(
        recent_highs[i]["price"] < recent_highs[i - 1]["price"]
        for i in range(1, len(recent_highs))
    )
    lows_ascending = all(
        recent_lows[i]["price"] > recent_lows[i - 1]["price"]
        for i in range(1, len(recent_lows))
    )
    lows_descending = all(
        recent_lows[i]["price"] < recent_lows[i - 1]["price"]
        for i in range(1, len(recent_lows))
    )

    if highs_ascending and lows_ascending:
        verdict = "UPTREND"
        confidence = "HIGH" if (len(highs) >= 3 and len(lows) >= 3) else "MEDIUM"
        reason = (
            f"HH+HL confirmed — last {min_swings} highs ascending, "
            f"last {min_swings} lows ascending"
        )
    elif highs_descending and lows_descending:
        verdict = "DOWNTREND"
        confidence = "HIGH" if (len(highs) >= 3 and len(lows) >= 3) else "MEDIUM"
        reason = (
            f"LH+LL confirmed — last {min_swings} highs descending, "
            f"last {min_swings} lows descending"
        )
    else:
        verdict = "CHOP"
        confidence = "LOW"
        reason = (
            f"Mixed — highs(↑={highs_ascending},↓={highs_descending}), "
            f"lows(↑={lows_ascending},↓={lows_descending})"
        )

    return {
        **base_result,
        "verdict": verdict,
        "confidence": confidence,
        "reason": reason,
    }


# ── Trend break detection ─────────────────────────────────────────────


def detect_trend_break(
    candles: List[Dict],
    current_structure: str,
    fractal_bars: Optional[int] = None,
) -> Optional[Dict]:
    """Detect if a previously confirmed trend has just been invalidated.

    UPTREND_BROKEN: new swing low forms BELOW the previous swing low
                   (the HL sequence is broken — it's now a LL)
    DOWNTREND_BROKEN: new swing high forms ABOVE the previous swing high

    Returns None if no break, else a dict describing the break.

    Used by structural-trail exits and to detect entry-signal invalidation.
    """
    if current_structure not in ("UPTREND", "DOWNTREND"):
        return None
    if fractal_bars is None:
        fractal_bars = _fractal_bars()
    if not candles or len(candles) < 2 * fractal_bars + 1:
        return None

    if current_structure == "UPTREND":
        lows = find_swing_lows(candles, fractal_bars)
        if len(lows) < 2:
            return None
        last_low = lows[-1]["price"]
        prev_low = lows[-2]["price"]
        if last_low < prev_low:
            return {
                "broken": True,
                "type": "UPTREND_BROKEN",
                "reason": f"New LL ({last_low} < prev low {prev_low})",
                "last_swing": last_low,
                "prev_swing": prev_low,
            }

    if current_structure == "DOWNTREND":
        highs = find_swing_highs(candles, fractal_bars)
        if len(highs) < 2:
            return None
        last_high = highs[-1]["price"]
        prev_high = highs[-2]["price"]
        if last_high > prev_high:
            return {
                "broken": True,
                "type": "DOWNTREND_BROKEN",
                "reason": f"New HH ({last_high} > prev high {prev_high})",
                "last_swing": last_high,
                "prev_swing": prev_high,
            }

    return None


# ── Multi-timeframe alignment ─────────────────────────────────────────


def align_timeframes(
    structures_by_tf: Dict[str, Dict],
) -> Dict:
    """Compute alignment verdict from multiple timeframe structures.

    Implements Alexander Elder's Triple Screen concept — bigger timeframe
    sets direction, smaller timeframes confirm.

    Args:
        structures_by_tf: e.g. {"5m": <result>, "15m": <result>, "1h": <result>}

    Returns:
        {
          "aligned": bool,
          "direction": "BULL" | "BEAR" | "MIXED",
          "conviction": "HIGH" | "MEDIUM" | "LOW",
          "breakdown": {"5m": "UPTREND", "15m": "UPTREND", "1h": "BULL"},
          "reason": str,
        }
    """
    if not structures_by_tf:
        return {
            "aligned": False, "direction": "MIXED", "conviction": "LOW",
            "breakdown": {}, "reason": "No timeframes provided",
        }

    breakdown = {}
    bull_count = 0
    bear_count = 0
    for tf, struct in structures_by_tf.items():
        v = struct.get("verdict") if struct else "UNKNOWN"
        breakdown[tf] = v
        if v == "UPTREND":
            bull_count += 1
        elif v == "DOWNTREND":
            bear_count += 1

    total = len(structures_by_tf)
    if bull_count == total:
        return {
            "aligned": True, "direction": "BULL", "conviction": "HIGH",
            "breakdown": breakdown,
            "reason": f"All {total} timeframes UPTREND",
        }
    if bear_count == total:
        return {
            "aligned": True, "direction": "BEAR", "conviction": "HIGH",
            "breakdown": breakdown,
            "reason": f"All {total} timeframes DOWNTREND",
        }
    if bull_count >= total - 1 and bear_count == 0:
        return {
            "aligned": True, "direction": "BULL", "conviction": "MEDIUM",
            "breakdown": breakdown,
            "reason": f"{bull_count}/{total} BULL, rest neutral",
        }
    if bear_count >= total - 1 and bull_count == 0:
        return {
            "aligned": True, "direction": "BEAR", "conviction": "MEDIUM",
            "breakdown": breakdown,
            "reason": f"{bear_count}/{total} BEAR, rest neutral",
        }
    if bull_count > 0 and bear_count > 0:
        return {
            "aligned": False, "direction": "MIXED", "conviction": "LOW",
            "breakdown": breakdown,
            "reason": f"Conflict — {bull_count} BULL vs {bear_count} BEAR",
        }
    return {
        "aligned": False, "direction": "MIXED", "conviction": "LOW",
        "breakdown": breakdown,
        "reason": "No clear direction (mostly UNKNOWN/CHOP)",
    }


# ── Diagnostics ───────────────────────────────────────────────────────


def diagnostics() -> Dict:
    """Module state snapshot for /api/structure/state debug."""
    return {
        "module": "price_structure",
        "fractal_bars": _fractal_bars(),
        "min_swings": _min_swings(),
        "description": (
            "Bill Williams fractal swing detection + Dow Theory verdict "
            "+ Elder's Triple Screen multi-timeframe alignment. "
            "Pure module — caller integrates verdicts into trade decisions."
        ),
    }
