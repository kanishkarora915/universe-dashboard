"""
Position Health Score (0-10)
────────────────────────────
Composite real-time score for an OPEN position. Combines 7 components:
  • Direct drawdown from entry (25%)  ← critical safety net
  • Candle patterns post-entry (15%)
  • VIX velocity (15%)
  • Premium velocity (15%)             ← directional + theta
  • Day-high/low proximity (10%)
  • Time-since-entry decay risk (10%)
  • Today's similar-setup loss count (10%)

Output:
  9-10: STRONG    — let it run
  6-8 : HEALTHY   — normal monitoring
  4-5 : WARNING   — tight SL recommended
  0-3 : CRITICAL  — auto-exit recommended

Why drawdown is FIRST:
A trade losing money RIGHT NOW must always weigh on the score —
indirect signals (theta, IV crush, patterns) only catch some failure
modes. -4% loss is bad regardless of why.
"""

import time
from datetime import datetime
from typing import Dict, List, Optional, Any

from candle_pattern_engine import detect_patterns
from vix_velocity import assess_for_position as vix_assess
from premium_velocity import assess as prem_assess


# ──────────────────────────────────────────────────────────────
# Component scorers — each returns a dict with score_delta (negative = bad)
# Base score = 10. Each component subtracts up to its max penalty.
# ──────────────────────────────────────────────────────────────


def score_drawdown(profit_pct: float) -> Dict:
    """
    Direct drawdown penalty — the most important safety net.
    A trade currently losing money MUST drag the score down regardless of
    whether other engines (candle/VIX/etc) caught the move yet.
    Max penalty: -2.5 (25% weight).
    """
    if profit_pct >= 0:
        return {"penalty": 0, "reason": None}
    abs_loss = abs(profit_pct)
    if abs_loss >= 8:
        return {"penalty": 2.5, "reason": f"Position {profit_pct:+.1f}% — CRITICAL drawdown"}
    if abs_loss >= 5:
        return {"penalty": 2.0, "reason": f"Position {profit_pct:+.1f}% — heavy drawdown"}
    if abs_loss >= 3:
        return {"penalty": 1.5, "reason": f"Position {profit_pct:+.1f}% — bleeding"}
    if abs_loss >= 1.5:
        return {"penalty": 0.8, "reason": f"Position {profit_pct:+.1f}% — concerning drift"}
    return {"penalty": 0, "reason": None}


def score_candle_patterns(candles: List[Dict], action: str,
                          entry_time: Optional[datetime] = None) -> Dict:
    """Max penalty: -1.5 (15% weight) — was 25%, rebalanced for drawdown."""
    patterns = detect_patterns(candles, action, entry_time)
    if not patterns:
        return {"penalty": 0, "patterns": [], "reason": None}
    # Take strongest pattern
    top = patterns[0]
    confidence = top.get("confidence", 0)
    penalty = round(1.5 * confidence, 2)  # max 1.5 (was 2.5)
    return {
        "penalty": penalty,
        "patterns": [p["name"] for p in patterns[:3]],
        "reason": top.get("detail"),
        "top_pattern": top["name"],
    }


def score_vix(action: str) -> Dict:
    """Max penalty: -1.5 (15% weight) — was 20%, rebalanced for drawdown."""
    a = vix_assess(action)
    raw = a.get("score_penalty", 0)  # 0-3
    penalty = min(1.5, raw * 1.5 / 3.0)
    return {
        "penalty": round(penalty, 2),
        "severity": a.get("severity"),
        "delta_15m": a.get("delta_15m"),
        "current_vix": a.get("current_vix"),
        "reason": a.get("warning"),
    }


def score_premium(trade_id, action: str) -> Dict:
    """Max penalty: -1.5 (15% weight) — directional + theta combined."""
    a = prem_assess(trade_id, action)
    raw = a.get("score_penalty", 0)
    penalty = min(1.5, raw * 1.5 / 3.0)
    return {
        "penalty": round(penalty, 2),
        "severity": a.get("severity"),
        "spot_change_10m": a.get("spot_change_10m_pct"),
        "premium_change_10m": a.get("premium_change_10m_pct"),
        "theta_winning": a.get("theta_winning"),
        "reason": a.get("warning"),
    }


def score_day_extreme_proximity(spot: float, day_high: float, day_low: float,
                                action: str, profit_pct: float,
                                hold_minutes: float) -> Dict:
    """
    CE near day-high without profit = trapped at top.
    PE near day-low without profit = trapped at bottom.
    Max penalty: -1.0 (10% weight) — was 15%, rebalanced for drawdown
    """
    out = {"penalty": 0, "reason": None}
    if not spot or not day_high or not day_low:
        return out

    is_ce = "CE" in action.upper()
    is_pe = "PE" in action.upper()

    if is_ce and day_high > 0:
        dist_pct = (day_high - spot) / day_high * 100
        if dist_pct <= 0.2 and profit_pct < 5 and hold_minutes >= 30:
            out["penalty"] = 1.0
            out["reason"] = (f"CE trapped at top: spot {spot:.1f} within {dist_pct:.2f}% of "
                             f"day-high {day_high:.1f}, no profit after {hold_minutes:.0f}m")
        elif dist_pct <= 0.4 and profit_pct < 2 and hold_minutes >= 15:
            out["penalty"] = 0.5
            out["reason"] = f"CE near day-high {day_high:.1f}, momentum dying"

    if is_pe and day_low > 0:
        dist_pct = (spot - day_low) / day_low * 100
        if dist_pct <= 0.2 and profit_pct < 5 and hold_minutes >= 30:
            out["penalty"] = 1.0
            out["reason"] = (f"PE trapped at bottom: spot {spot:.1f} within {dist_pct:.2f}% of "
                             f"day-low {day_low:.1f}, no profit after {hold_minutes:.0f}m")
        elif dist_pct <= 0.4 and profit_pct < 2 and hold_minutes >= 15:
            out["penalty"] = 0.5
            out["reason"] = f"PE near day-low {day_low:.1f}, no follow-through"

    return out


def score_time_decay(hold_minutes: float, profit_pct: float,
                     entry_hour: int = 12) -> Dict:
    """
    The longer we hold without profit, the worse. Theta accelerates after lunch.
    Max penalty: -1.0 (10% weight).
    """
    out = {"penalty": 0, "reason": None}
    if hold_minutes <= 0:
        return out

    # Post-lunch entries: stricter time penalty
    post_lunch = entry_hour >= 13

    if hold_minutes >= 60 and profit_pct < 5:
        out["penalty"] = 1.0
        out["reason"] = f"Held {hold_minutes:.0f}m, peak <5% — timeout incoming"
    elif hold_minutes >= 45 and profit_pct < 3:
        out["penalty"] = 0.7
        out["reason"] = f"45m+ hold, only {profit_pct:.1f}% profit"
    elif post_lunch and hold_minutes >= 30 and profit_pct < 2:
        out["penalty"] = 0.5
        out["reason"] = f"Post-lunch: 30m hold, no momentum"
    elif hold_minutes >= 30 and profit_pct < 0:
        out["penalty"] = 0.4
        out["reason"] = f"30m hold, still negative"

    return out


def score_pattern_loser(today_similar_losses: int) -> Dict:
    """
    Today already had N similar-setup losses → block more.
    Max penalty: -1.0 (10% weight).
    """
    out = {"penalty": 0, "reason": None}
    if today_similar_losses >= 3:
        out["penalty"] = 1.0
        out["reason"] = f"{today_similar_losses} similar losses today — pattern not working"
    elif today_similar_losses == 2:
        out["penalty"] = 0.5
        out["reason"] = f"2 losses today on similar setup — caution"
    return out


# ──────────────────────────────────────────────────────────────
# Master scorer
# ──────────────────────────────────────────────────────────────

def compute_health(
    *,
    trade_id: Any,
    action: str,
    entry_price: float,
    current_premium: float,
    entry_spot: float,
    current_spot: float,
    day_high: float,
    day_low: float,
    candles_5min: List[Dict],
    entry_time: Optional[datetime],
    today_similar_losses: int = 0,
) -> Dict:
    """
    Compute the full health score for a position.

    Returns:
      {
        score: 0.0-10.0,
        verdict: "STRONG"|"HEALTHY"|"WARNING"|"CRITICAL",
        components: {candle, vix, premium, proximity, time, pattern},
        reasons: [list of negative-impact reason strings],
        exit_recommended: bool,
        tighten_sl: bool,
        suggested_action: str,
      }
    """
    base = 10.0
    profit_pct = ((current_premium - entry_price) / entry_price * 100) if entry_price > 0 else 0
    hold_min = 0.0
    if entry_time:
        # Defensive: strip timezone so subtraction with naive datetime.now() works
        if getattr(entry_time, "tzinfo", None) is not None:
            entry_time = entry_time.replace(tzinfo=None)
        hold_min = (datetime.now() - entry_time).total_seconds() / 60.0
    entry_hour = entry_time.hour if entry_time else 12

    # Component scores
    c_drawdown = score_drawdown(profit_pct)  # ← NEW: direct loss penalty
    c_candle = score_candle_patterns(candles_5min, action, entry_time)
    c_vix = score_vix(action)
    c_prem = score_premium(trade_id, action)
    c_prox = score_day_extreme_proximity(
        current_spot, day_high, day_low, action, profit_pct, hold_min
    )
    c_time = score_time_decay(hold_min, profit_pct, entry_hour)
    c_pat = score_pattern_loser(today_similar_losses)

    total_penalty = (
        c_drawdown["penalty"]
        + c_candle["penalty"] + c_vix["penalty"] + c_prem["penalty"]
        + c_prox["penalty"] + c_time["penalty"] + c_pat["penalty"]
    )
    score = max(0.0, round(base - total_penalty, 1))

    # Reasons (drawdown FIRST so it's most visible)
    reasons = []
    for c in (c_drawdown, c_prem, c_candle, c_vix, c_prox, c_time, c_pat):
        if c.get("reason"):
            reasons.append(c["reason"])

    # Verdict bands
    if score >= 9:
        verdict = "STRONG"
    elif score >= 6:
        verdict = "HEALTHY"
    elif score >= 4:
        verdict = "WARNING"
    else:
        verdict = "CRITICAL"

    exit_recommended = score < 4
    tighten_sl = 4 <= score < 6

    # Suggested action
    if exit_recommended:
        suggested_action = "EXIT_NOW"
    elif tighten_sl:
        suggested_action = "TIGHTEN_SL"
    elif score < 8:
        suggested_action = "MONITOR"
    else:
        suggested_action = "HOLD"

    return {
        "score": score,
        "verdict": verdict,
        "exit_recommended": exit_recommended,
        "tighten_sl": tighten_sl,
        "suggested_action": suggested_action,
        "reasons": reasons,
        "profit_pct": round(profit_pct, 2),
        "hold_minutes": round(hold_min, 1),
        "components": {
            "drawdown": c_drawdown,  # NEW
            "candle": c_candle,
            "vix": c_vix,
            "premium": c_prem,
            "proximity": c_prox,
            "time": c_time,
            "pattern": c_pat,
        },
        "ts": time.time(),
    }
