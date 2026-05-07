"""
Entry Filters — 4 quality gates for both main and scalper trades.

  1. check_5min_trend()       Block CE/PE entries against 5-min spot trend
  2. check_greeks_gate()      Only allow ATM-ish delta (0.30-0.70 abs)
  3. check_tick_velocity()    Detect momentum spike → fast entry
  4. detect_market_regime()   CHOP / BREAKOUT / TRENDING / NORMAL

All filters consume engine state (ltp_history, spot history, chains).
Designed to be cheap to compute (no DB hits, all in-memory).

Usage from engine.py / scalper_mode.py:
    from entry_filters import check_all_filters
    allowed, reason, regime = check_all_filters(engine, idx, strike, action)
    if not allowed:
        return False  # reject entry

Returns:
    (True,  reason, regime_dict)   if all gates pass
    (False, reason, regime_dict)   if any gate fails
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


# ───────────────────────────────────────────────────────────
# FILTER 1: 5-min spot trend agreement
# ───────────────────────────────────────────────────────────

def check_5min_trend(spot_history: List[Dict], action: str) -> Tuple[bool, str]:
    """Block entry if last 5 min of spot disagrees with trade direction.

    Trade direction:
      BUY CE → need bullish or neutral 5-min spot trend
      BUY PE → need bearish or neutral 5-min spot trend

    Threshold: spot move ±0.3% in last 5 min (significant for index).
    """
    if not spot_history or len(spot_history) < 5:
        return True, "5-min trend: insufficient history (allow)"

    cutoff = ist_now() - timedelta(minutes=5)
    recent = []
    for h in spot_history:
        try:
            t = datetime.fromisoformat(h["t"]) if isinstance(h["t"], str) else h["t"]
            if t.tzinfo is None:
                t = IST.localize(t)
            if t >= cutoff:
                recent.append(h)
        except Exception:
            continue

    if len(recent) < 5:
        return True, "5-min trend: <5 ticks in window (allow)"

    first = recent[0]["ltp"]
    last = recent[-1]["ltp"]
    if first <= 0:
        return True, "5-min trend: bad data (allow)"

    change_pct = ((last - first) / first) * 100

    is_ce = "CE" in (action or "")
    if is_ce:
        if change_pct < -0.3:
            return False, f"5-min trend BEARISH ({change_pct:+.2f}%) — block CE"
    else:
        if change_pct > 0.3:
            return False, f"5-min trend BULLISH ({change_pct:+.2f}%) — block PE"

    return True, f"5-min trend OK ({change_pct:+.2f}%)"


# ───────────────────────────────────────────────────────────
# FILTER 2: Greeks gate (delta range)
# ───────────────────────────────────────────────────────────

def check_greeks_gate(chain_data: Dict, strike: int, action: str,
                      delta_min: float = 0.30, delta_max: float = 0.70) -> Tuple[bool, str]:
    """Block deep-OTM (lottery) and deep-ITM (low-leverage) trades.

    Sweet spot for scalpers/buyers: |delta| 0.30 - 0.70.
    Below 0.30  → low probability of moving up enough to profit
    Above 0.70  → tracks underlying 1:1 (no leverage advantage)
    """
    side = "ce" if "CE" in (action or "") else "pe"
    strike_data = chain_data.get(strike, {}) if chain_data else {}
    greeks = strike_data.get(f"{side}_greeks", {})
    raw_delta = greeks.get("delta", 0)

    if raw_delta == 0:
        return True, "Greeks gate: no delta data (allow)"

    delta = abs(raw_delta)  # PE delta is negative
    if delta < delta_min:
        return False, f"Delta {delta:.2f} too OTM (< {delta_min}, lottery ticket — block)"
    if delta > delta_max:
        return False, f"Delta {delta:.2f} too ITM (> {delta_max}, low leverage — block)"

    return True, f"Delta {delta:.2f} OK"


# ───────────────────────────────────────────────────────────
# FILTER 3: Tick velocity (momentum detector)
# ───────────────────────────────────────────────────────────

def check_tick_velocity(option_history: List[Dict],
                        velocity_pct: float = 3.0,
                        window_sec: int = 30) -> Tuple[bool, str, float]:
    """Detect option premium momentum spike.

    Returns: (is_momentum, reason, velocity_pct)

    Used to SKIP standard 30-second confirmation when premium is moving
    fast — agar premium 30 sec mein 3% move kar raha → instant entry.
    """
    if not option_history or len(option_history) < 3:
        return False, "tick velocity: insufficient history", 0.0

    cutoff = ist_now() - timedelta(seconds=window_sec)
    recent = []
    for h in option_history:
        try:
            t = datetime.fromisoformat(h["t"]) if isinstance(h["t"], str) else h["t"]
            if t.tzinfo is None:
                t = IST.localize(t)
            if t >= cutoff:
                recent.append(h)
        except Exception:
            continue

    if len(recent) < 3:
        return False, "tick velocity: <3 ticks in window", 0.0

    first = recent[0]["ltp"]
    last = recent[-1]["ltp"]
    if first <= 0:
        return False, "tick velocity: bad data", 0.0

    move_pct = ((last - first) / first) * 100

    if move_pct >= velocity_pct:
        return True, f"momentum spike +{move_pct:.2f}% in {window_sec}s", move_pct

    return False, f"no spike ({move_pct:+.2f}%)", move_pct


# ───────────────────────────────────────────────────────────
# FILTER 4: Smart breakout detector (regime-aware)
# ───────────────────────────────────────────────────────────

def detect_market_regime(spot_history: List[Dict],
                         tight_range_pct: float = 0.4,
                         breakout_candle_pct: float = 1.5) -> Dict:
    """Detect market regime from last 20 min of spot.

    Returns: {
      "regime":   'CHOP' | 'BREAKOUT' | 'TRENDING' | 'NORMAL',
      "range_pct": float,      # range % over 20 min
      "candle_pct": float,     # last 1-min candle size
      "tight_before": bool,    # was tight before recent candle
      "reason":   str,
    }

    BREAKOUT (golden):
      Was tight (range < 0.4% in last 20 min)
      AND latest 1-min candle > 1.5% move
      → IMMEDIATE entry, skip 30s wait

    CHOP:
      Tight range + no breakout candle → block standard entries

    TRENDING:
      Range > 1% with directional bias → allow + bigger size

    NORMAL:
      Default state, allow standard entries.
    """
    if not spot_history or len(spot_history) < 10:
        return {
            "regime": "NORMAL",
            "range_pct": 0,
            "candle_pct": 0,
            "tight_before": False,
            "reason": "insufficient history (default NORMAL)",
        }

    # Last 20 minutes of ticks
    cutoff_20 = ist_now() - timedelta(minutes=20)
    recent_20 = []
    for h in spot_history:
        try:
            t = datetime.fromisoformat(h["t"]) if isinstance(h["t"], str) else h["t"]
            if t.tzinfo is None:
                t = IST.localize(t)
            if t >= cutoff_20:
                recent_20.append(h)
        except Exception:
            continue

    if len(recent_20) < 10:
        return {
            "regime": "NORMAL",
            "range_pct": 0,
            "candle_pct": 0,
            "tight_before": False,
            "reason": "<10 ticks in 20min (default NORMAL)",
        }

    ltps_20 = [h["ltp"] for h in recent_20 if h.get("ltp", 0) > 0]
    if not ltps_20:
        return {
            "regime": "NORMAL",
            "range_pct": 0,
            "candle_pct": 0,
            "tight_before": False,
            "reason": "no valid ltps",
        }

    high_20 = max(ltps_20)
    low_20 = min(ltps_20)
    avg_20 = sum(ltps_20) / len(ltps_20)
    if avg_20 <= 0:
        return {"regime": "NORMAL", "range_pct": 0, "candle_pct": 0, "tight_before": False,
                "reason": "bad avg"}

    range_pct = ((high_20 - low_20) / avg_20) * 100

    # Last 1-min candle size
    cutoff_1m = ist_now() - timedelta(minutes=1)
    last_1m = []
    for h in spot_history:
        try:
            t = datetime.fromisoformat(h["t"]) if isinstance(h["t"], str) else h["t"]
            if t.tzinfo is None:
                t = IST.localize(t)
            if t >= cutoff_1m:
                last_1m.append(h)
        except Exception:
            continue

    candle_pct = 0.0
    if last_1m and len(last_1m) >= 2:
        c_first = last_1m[0]["ltp"]
        c_last = last_1m[-1]["ltp"]
        if c_first > 0:
            candle_pct = ((c_last - c_first) / c_first) * 100

    # Was the period BEFORE the recent candle tight?
    cutoff_pre_candle = ist_now() - timedelta(minutes=20)
    cutoff_pre_candle_end = ist_now() - timedelta(minutes=1)
    pre_candle = []
    for h in spot_history:
        try:
            t = datetime.fromisoformat(h["t"]) if isinstance(h["t"], str) else h["t"]
            if t.tzinfo is None:
                t = IST.localize(t)
            if cutoff_pre_candle <= t < cutoff_pre_candle_end:
                pre_candle.append(h)
        except Exception:
            continue

    tight_before = False
    if pre_candle and len(pre_candle) >= 5:
        pre_ltps = [h["ltp"] for h in pre_candle if h.get("ltp", 0) > 0]
        if pre_ltps:
            pre_avg = sum(pre_ltps) / len(pre_ltps)
            if pre_avg > 0:
                pre_range_pct = ((max(pre_ltps) - min(pre_ltps)) / pre_avg) * 100
                tight_before = pre_range_pct < tight_range_pct

    # Decide regime
    if tight_before and abs(candle_pct) >= breakout_candle_pct:
        regime = "BREAKOUT"
        reason = (f"BREAKOUT: tight 20min ({range_pct:.2f}% range) + "
                  f"explosive 1-min candle ({candle_pct:+.2f}%)")
    elif range_pct < tight_range_pct and abs(candle_pct) < 0.3:
        regime = "CHOP"
        reason = (f"CHOP: tight range ({range_pct:.2f}%) + small candle "
                  f"({candle_pct:+.2f}%)")
    elif range_pct > 1.0:
        regime = "TRENDING"
        reason = f"TRENDING: range {range_pct:.2f}% in 20min"
    else:
        regime = "NORMAL"
        reason = f"NORMAL: range {range_pct:.2f}%, candle {candle_pct:+.2f}%"

    return {
        "regime": regime,
        "range_pct": round(range_pct, 3),
        "candle_pct": round(candle_pct, 3),
        "tight_before": tight_before,
        "reason": reason,
    }


# ───────────────────────────────────────────────────────────
# COMBINED — single call from engine / scalper
# ───────────────────────────────────────────────────────────

def check_all_filters(engine, idx: str, strike: int, action: str,
                      enable_trend: bool = True,
                      enable_greeks: bool = True,
                      enable_regime: bool = True) -> Tuple[bool, str, Dict]:
    """Run all entry filters. Returns (allowed, reason, regime_info).

    `engine` must have:
      - engine.chains[idx][strike] for greeks
      - engine._spot_history[idx]  for trend + regime  (added separately)
      - engine.ltp_history[(idx, strike, OPT)] for tick velocity

    If filters can't run (missing data), they default to ALLOW (don't block on
    missing data — that would be too restrictive on cold start).
    """
    spot_history = []
    if hasattr(engine, "_spot_history"):
        spot_history = engine._spot_history.get(idx, [])

    chain_data = {}
    if hasattr(engine, "chains"):
        chain_data = engine.chains.get(idx, {})

    regime_info = detect_market_regime(spot_history) if enable_regime else {
        "regime": "NORMAL", "reason": "regime check disabled"
    }

    # CHOP regime → block UNLESS verdict has special override (handled by caller)
    if enable_regime and regime_info["regime"] == "CHOP":
        return False, f"CHOP regime — {regime_info['reason']}", regime_info

    # 5-min trend filter
    if enable_trend:
        trend_ok, trend_reason = check_5min_trend(spot_history, action)
        if not trend_ok:
            return False, trend_reason, regime_info

    # Greeks gate
    if enable_greeks:
        greeks_ok, greeks_reason = check_greeks_gate(chain_data, strike, action)
        if not greeks_ok:
            return False, greeks_reason, regime_info

    return True, "all filters pass", regime_info


def is_breakout_skip_confirmation(regime_info: Dict) -> bool:
    """Helper: should we skip the 30-second confirmation window?
    YES on BREAKOUT regime — fire entry immediately.
    """
    return regime_info.get("regime") == "BREAKOUT"
