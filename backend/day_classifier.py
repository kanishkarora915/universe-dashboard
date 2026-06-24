"""
day_classifier — day-level filters derived from 60d NIFTY OHLC analysis.

USER PROVIDED CSV 2026-06-23: NIFTY 50 daily for 2026-03-24 to 2026-06-23.
Cross-referenced with 320 main-mode trades.

DATA-DERIVED FINDINGS:

  DAY TYPE         n    WR     Net P&L     Avg/trade
  ─────────────────────────────────────────────────
  STRONG_TREND    10  10.0%   -₹48,622    -₹4,862  ← system FIGHTS strong trend
  DEAD            91  59.3%   -₹89,322    -₹982    ← theta vampire
  TREND           88  62.5%  +₹230,545    +₹2,620
  CHOP            90  54.4%  +₹166,639    +₹1,852
  MIXED           41  53.7%  +₹133,113    +₹3,247

  DIRECTION × SIDE:
  UP day + CE    n=181  WR 56%  +₹467,755  ✅
  DOWN day + PE  n= 52  WR 64%  +₹68,876   ✅
  DOWN day + CE  n= 72  WR 51%  -₹147,389  ❌ ← biggest leak
  UP day + PE    n= 15  WR 60%  +₹3,112

THIS MODULE ADDS 3 NEW GATES:

  1. DEAD_MARKET_HALT — block entries when 30min spot range < 0.2%
                       (eats ~91 trades/90d for -₹89k savings)

  2. STRONG_TREND_FADE_BLOCK — block counter-trend entries when 1h
                              candle shows STRONG_TREND direction
                              (saves the -₹48k STRONG_TREND bucket)

  3. DOWN_DAY_CE_PENALTY — raise win_pct threshold +10pts for CE
                          entries when spot is down ≥0.2% from open
                          (filters the -₹147k DOWN+CE bucket)

All three are env-overridable. Defaults set per data; tune via:
  DEAD_MARKET_HALT_DISABLED, DEAD_MARKET_RANGE_PCT
  STRONG_TREND_FADE_DISABLED, STRONG_TREND_BODY_PCT, STRONG_TREND_RANGE_PCT
  DOWN_DAY_CE_PENALTY_DISABLED, DOWN_DAY_THRESHOLD_BUMP, DOWN_DAY_TRIGGER_PCT
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import pytz

IST = pytz.timezone("Asia/Kolkata")


def _now_ist() -> datetime:
    return datetime.now(IST)


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, "").strip() or default)
    except Exception:
        return default


def _enabled(env: str, default: bool = True) -> bool:
    val = os.environ.get(env, "").strip().lower()
    if val in ("1", "true", "on"):
        return True
    if val in ("0", "false", "off"):
        return False
    return default


# ── Sensor: 30-min spot range ──────────────────────────────────────────


def get_recent_range_pct(engine, idx: str, minutes: int = 30) -> Optional[float]:
    """Return spot range % over last N minutes (None if insufficient data)."""
    if engine is None:
        return None
    hist = (getattr(engine, "_spot_history", {}) or {}).get(idx, [])
    if not hist:
        return None
    now = _now_ist()
    cutoff = now - timedelta(minutes=minutes)
    recent_prices = []
    for h in hist:
        try:
            t = datetime.fromisoformat(h["t"])
            if t.tzinfo is None:
                t = IST.localize(t)
            if t >= cutoff:
                ltp = h.get("ltp", 0)
                if ltp > 0:
                    recent_prices.append(ltp)
        except Exception:
            continue
    if len(recent_prices) < 5:  # need enough samples
        return None
    hi, lo = max(recent_prices), min(recent_prices)
    if lo <= 0:
        return None
    return (hi - lo) / lo * 100


def get_day_stats(engine, idx: str) -> Dict:
    """Return current day's OHLC-derived stats.

    Returns:
        {
          "day_open": float,        # earliest spot today
          "day_high": float,        # engine.day_high[idx]
          "day_low": float,         # engine.day_low[idx]
          "current": float,         # current spot
          "day_range_pct": float,   # (high-low)/current * 100
          "from_open_pct": float,   # (current-open)/open * 100  (negative = down day)
          "body_pct": float,        # abs(current-open) / (high-low) * 100
        }
    """
    out = {"day_open": 0.0, "day_high": 0.0, "day_low": 0.0,
           "current": 0.0, "day_range_pct": 0.0, "from_open_pct": 0.0,
           "body_pct": 0.0}
    if engine is None:
        return out
    try:
        tok = (getattr(engine, "spot_tokens", {}) or {}).get(idx)
        if tok:
            cur = (getattr(engine, "prices", {}) or {}).get(tok, {}).get("ltp", 0) or 0
            out["current"] = cur
        out["day_high"] = (getattr(engine, "day_high", {}) or {}).get(idx, 0) or 0
        out["day_low"] = (getattr(engine, "day_low", {}) or {}).get(idx, 0) or 0
        # Day open from earliest _spot_history record this session
        hist = (getattr(engine, "_spot_history", {}) or {}).get(idx, [])
        if hist:
            out["day_open"] = hist[0].get("ltp", 0) or 0
        if out["current"] > 0 and out["day_high"] > 0 and out["day_low"] > 0:
            out["day_range_pct"] = (out["day_high"] - out["day_low"]) / out["current"] * 100
        if out["day_open"] > 0 and out["current"] > 0:
            out["from_open_pct"] = (out["current"] - out["day_open"]) / out["day_open"] * 100
        if out["day_high"] > out["day_low"] and out["day_open"] > 0:
            body = abs(out["current"] - out["day_open"])
            out["body_pct"] = body / (out["day_high"] - out["day_low"]) * 100
    except Exception:
        pass
    return out


# ── GATE 1: DEAD_MARKET_HALT ──────────────────────────────────────────


def check_dead_market_halt(engine, idx: str) -> Tuple[bool, str]:
    """Block entries when BOTH the day and last 30min are very quiet.

    2026-06-24 BUG FIX: previous version checked ONLY 30-min range,
    using a 0.20% threshold. Real-world observation: NIFTY range_30min
    can dip to 0.15% during normal mid-session pauses on a day whose
    overall range is 0.6-1%+. Single-window check halted BOTH indices
    on 24-Jun, a tradeable day. 0 trades, both tabs.

    Now requires BOTH:
      - day_range_pct < DEAD_MARKET_DAY_RANGE_PCT (default 0.45%)
      - range_30min < DEAD_MARKET_RANGE_PCT (default 0.10%)

    Active after 10:00 IST (opening volatility is real).
    Disable: DEAD_MARKET_HALT_DISABLED=on
    """
    if not _enabled("DEAD_MARKET_HALT_ENABLED", default=True):
        return False, ""
    if os.environ.get("DEAD_MARKET_HALT_DISABLED", "").lower() in ("on", "1", "true"):
        return False, ""

    now = _now_ist()
    # Only active after 10:00 IST — opening 9:15-10:00 is high volatility
    if now.hour < 10:
        return False, ""

    range_pct = get_recent_range_pct(engine, idx, minutes=30)
    if range_pct is None:
        return False, ""

    # Day-level guard — only halt if the WHOLE day is dead, not just
    # a quiet 30-min window during a normal/trending day.
    day_range = get_day_stats(engine, idx).get("day_range_pct", 0)
    day_thresh = _f("DEAD_MARKET_DAY_RANGE_PCT", 0.45)
    if day_range >= day_thresh:
        return False, ""

    threshold = _f("DEAD_MARKET_RANGE_PCT", 0.10)
    if range_pct < threshold:
        return True, (
            f"DEAD_MARKET_HALT: day_range {day_range:.2f}% AND 30min {range_pct:.2f}% "
            f"both below thresholds ({day_thresh}% / {threshold}%) — "
            f"60d data: 91 truly-dead-day trades lost -₹89k"
        )
    return False, ""


# ── GATE 2: STRONG_TREND_FADE_BLOCK ────────────────────────────────────


def detect_strong_trend(engine, idx: str) -> Optional[str]:
    """Detect if day is forming a STRONG_TREND.

    Returns "UP", "DOWN", or None.

    Heuristic on session-so-far stats:
      - body_pct ≥ 60% of range_pct (high directionality)
      - day_range_pct ≥ STRONG_TREND_RANGE_PCT
      - from_open_pct same sign as body direction
    """
    if engine is None:
        return None
    stats = get_day_stats(engine, idx)
    range_pct = stats.get("day_range_pct", 0)
    body_pct = stats.get("body_pct", 0)
    from_open = stats.get("from_open_pct", 0)

    body_thresh = _f("STRONG_TREND_BODY_PCT", 60.0)
    range_thresh = _f("STRONG_TREND_RANGE_PCT", 0.80)

    if range_pct < range_thresh or body_pct < body_thresh:
        return None
    if abs(from_open) < 0.30:  # close to open = not directional
        return None
    return "UP" if from_open > 0 else "DOWN"


def check_strong_trend_fade(engine, idx: str, action: str) -> Tuple[bool, str]:
    """Block counter-trend entries when day shows STRONG_TREND.

    60d data: STRONG_TREND bucket = 10 trades, 10% WR, -₹48,622.
    System literally fades the trend and loses 90% of attempts.
    Block: CE on STRONG_DOWN, PE on STRONG_UP.

    Disable: STRONG_TREND_FADE_DISABLED=on
    """
    if not _enabled("STRONG_TREND_FADE_ENABLED", default=True):
        return False, ""
    if os.environ.get("STRONG_TREND_FADE_DISABLED", "").lower() in ("on", "1", "true"):
        return False, ""

    trend = detect_strong_trend(engine, idx)
    if not trend:
        return False, ""

    is_ce = "CE" in (action or "").upper()
    if trend == "DOWN" and is_ce:
        return True, (
            f"STRONG_TREND_FADE: blocking BUY CE on STRONG_DOWN day "
            f"(60d data: 10 fade trades, 10% WR, -₹48k)"
        )
    if trend == "UP" and not is_ce:
        return True, (
            f"STRONG_TREND_FADE: blocking BUY PE on STRONG_UP day "
            f"(60d data: counter-trend = -₹48k bucket)"
        )
    return False, ""


# ── GATE 3: DOWN_DAY_CE_PENALTY ────────────────────────────────────────


def down_day_ce_threshold_bump(engine, idx: str, action: str) -> int:
    """Return threshold bump (in win_pct points) for CE entries on down days.

    60d data: DOWN day + CE = 72 trades, 51% WR, -₹147,389.
    The 51% WR shows entries aren't directional-blind, but the average
    loss per trade is -₹2k. Raising the threshold filters the marginal
    50-55% prob signals where direction confluence is weakest.

    Returns 0 if not a down day or not CE.
    Returns DOWN_DAY_THRESHOLD_BUMP (default 10) if CE on down day.

    Disable: DOWN_DAY_CE_PENALTY_DISABLED=on
    """
    if not _enabled("DOWN_DAY_CE_PENALTY_ENABLED", default=True):
        return 0
    if os.environ.get("DOWN_DAY_CE_PENALTY_DISABLED", "").lower() in ("on", "1", "true"):
        return 0

    is_ce = "CE" in (action or "").upper()
    if not is_ce:
        return 0

    stats = get_day_stats(engine, idx)
    from_open = stats.get("from_open_pct", 0)
    trigger = _f("DOWN_DAY_TRIGGER_PCT", -0.20)  # -0.2% from open
    if from_open >= trigger:
        return 0

    bump = int(_f("DOWN_DAY_THRESHOLD_BUMP", 10))
    return bump


# ── Public helper: combined gate check ─────────────────────────────────


def check_day_gates(engine, idx: str, action: str) -> Tuple[bool, str]:
    """Run all three gates. Return (block, first_reason)."""
    blocked, reason = check_dead_market_halt(engine, idx)
    if blocked:
        return True, reason
    blocked, reason = check_strong_trend_fade(engine, idx, action)
    if blocked:
        return True, reason
    return False, ""


# ── Diagnostics for /api/admin/day-classifier ─────────────────────────


def diagnostics(engine=None) -> Dict:
    """Snapshot of day-classifier state per index."""
    out = {
        "dead_market_halt": _enabled("DEAD_MARKET_HALT_ENABLED", True)
            and not os.environ.get("DEAD_MARKET_HALT_DISABLED", "").lower() in ("on","1","true"),
        "strong_trend_fade": _enabled("STRONG_TREND_FADE_ENABLED", True)
            and not os.environ.get("STRONG_TREND_FADE_DISABLED", "").lower() in ("on","1","true"),
        "down_day_ce_penalty": _enabled("DOWN_DAY_CE_PENALTY_ENABLED", True)
            and not os.environ.get("DOWN_DAY_CE_PENALTY_DISABLED", "").lower() in ("on","1","true"),
        "thresholds": {
            "dead_market_range_pct": _f("DEAD_MARKET_RANGE_PCT", 0.10),
            "dead_market_day_range_pct": _f("DEAD_MARKET_DAY_RANGE_PCT", 0.45),
            "strong_trend_body_pct": _f("STRONG_TREND_BODY_PCT", 60.0),
            "strong_trend_range_pct": _f("STRONG_TREND_RANGE_PCT", 0.80),
            "down_day_trigger_pct": _f("DOWN_DAY_TRIGGER_PCT", -0.20),
            "down_day_threshold_bump": int(_f("DOWN_DAY_THRESHOLD_BUMP", 10)),
        },
        "indices": {},
    }
    if engine is not None:
        for idx in ("NIFTY", "BANKNIFTY"):
            stats = get_day_stats(engine, idx)
            r30 = get_recent_range_pct(engine, idx, minutes=30)
            trend = detect_strong_trend(engine, idx)
            day_range = stats.get("day_range_pct", 0)
            r30_thresh = _f("DEAD_MARKET_RANGE_PCT", 0.10)
            day_thresh = _f("DEAD_MARKET_DAY_RANGE_PCT", 0.45)
            is_dead = (r30 is not None and r30 < r30_thresh
                       and day_range < day_thresh)
            out["indices"][idx] = {
                "day_stats": {k: round(v, 3) if isinstance(v, float) else v
                              for k, v in stats.items()},
                "range_30min_pct": round(r30, 3) if r30 is not None else None,
                "strong_trend_dir": trend,
                "is_dead_now": is_dead,
            }
    return out
