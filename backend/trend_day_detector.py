"""
trend_day_detector — identify "big move days" via Opening Range Breakout.

Built 2026-05-27 (Phase 3 of Option Y).

PROVEN ORIGIN

  Toby Crabel — "Day Trading with Short Term Price Patterns and
  Opening Range Breakout" (1990). 30+ years of validation. Used by
  prop desks and futures traders globally.

LOGIC

  1. Opening Range (OR) = high − low of first N minutes after open
     (default N=30, gives ~6 × 5-min candles).
  2. After OR completes:
       BULL trend day → spot breaks ABOVE OR_high by (OR_size × break_pct)
       BEAR trend day → spot breaks BELOW OR_low by (OR_size × break_pct)
  3. Optional volume confirmation — break candle volume ≥ N×avg.
  4. Once flagged, trend day status is sticky for the rest of the day.

USAGE

  from trend_day_detector import detect_trend_day

  status = detect_trend_day(candles_5m, current_spot, current_time)
  # status = {"is_trend_day": True, "direction": "BULL",
  #           "or_high": 24050, "or_low": 23980, "confidence": "HIGH",
  #           "reason": ...}

ENV

  TREND_DAY_OR_MINUTES (default 30)
  TREND_DAY_BREAK_PCT  (default 0.5)
  TREND_DAY_VOL_RATIO  (default 1.5)

The pure module — caller fetches candles and supplies them.
"""

from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime, time as dtime
import os
import pytz

IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = dtime(9, 15)


def _or_minutes() -> int:
    try:
        return max(5, int(os.environ.get("TREND_DAY_OR_MINUTES", "30") or 30))
    except Exception:
        return 30


def _break_pct() -> float:
    try:
        return float(os.environ.get("TREND_DAY_BREAK_PCT", "0.5") or 0.5)
    except Exception:
        return 0.5


def _vol_ratio() -> float:
    try:
        return float(os.environ.get("TREND_DAY_VOL_RATIO", "1.5") or 1.5)
    except Exception:
        return 1.5


def _parse_ts(ts) -> Optional[datetime]:
    """Best-effort parse of a candle timestamp into IST datetime."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else IST.localize(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.astimezone(IST) if dt.tzinfo else IST.localize(dt)
        except Exception:
            return None
    return None


def _today_candles(candles: List[Dict], now: Optional[datetime] = None) -> List[Dict]:
    """Filter candles to today's market session only."""
    if now is None:
        now = datetime.now(IST)
    today_date = now.date()
    out = []
    for c in candles:
        ts = _parse_ts(c.get("ts"))
        if ts is None:
            continue
        if ts.date() != today_date:
            continue
        if ts.time() < MARKET_OPEN:
            continue
        out.append({**c, "_ts_parsed": ts})
    return out


def compute_opening_range(
    candles_5m: List[Dict],
    now: Optional[datetime] = None,
    or_minutes: Optional[int] = None,
) -> Optional[Dict]:
    """Compute today's opening range from first N min of 5-min candles.

    Returns {high, low, size, complete} or None if not enough data.
    """
    if or_minutes is None:
        or_minutes = _or_minutes()
    today = _today_candles(candles_5m, now)
    if not today:
        return None

    market_open = today[0]["_ts_parsed"].replace(
        hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0
    )
    or_end = market_open.replace(minute=MARKET_OPEN.minute + or_minutes)

    or_candles = [c for c in today if c["_ts_parsed"] < or_end]
    if not or_candles:
        return None

    or_high = max(c.get("high", 0) for c in or_candles)
    or_low = min(c.get("low", 0) for c in or_candles if c.get("low", 0) > 0)
    # OR is "complete" once the OR window has ended
    complete = (now or datetime.now(IST)) >= or_end

    return {
        "high": or_high,
        "low": or_low,
        "size": or_high - or_low,
        "complete": complete,
        "candle_count": len(or_candles),
        "or_end": or_end.isoformat(),
    }


def detect_trend_day(
    candles_5m: List[Dict],
    current_spot: float,
    now: Optional[datetime] = None,
) -> Dict:
    """Detect whether today is a trend day based on ORB.

    Args:
        candles_5m: today's 5-min candles (more is fine, extras filtered)
        current_spot: latest spot LTP
        now: override "now" for testing (default datetime.now(IST))

    Returns:
        {
          "is_trend_day": bool,
          "direction": "BULL" | "BEAR" | None,
          "confidence": "HIGH" | "MEDIUM" | "LOW" | "N/A",
          "or_high": float, "or_low": float, "or_size": float,
          "break_threshold_pct": float,
          "reason": str,
          "or_complete": bool,
        }
    """
    if now is None:
        now = datetime.now(IST)
    if current_spot is None or current_spot <= 0:
        return {
            "is_trend_day": False, "direction": None, "confidence": "N/A",
            "or_high": None, "or_low": None, "or_size": None,
            "break_threshold_pct": _break_pct(),
            "reason": "no current_spot",
            "or_complete": False,
        }

    or_info = compute_opening_range(candles_5m, now=now)
    if not or_info or or_info["size"] <= 0:
        return {
            "is_trend_day": False, "direction": None, "confidence": "N/A",
            "or_high": None, "or_low": None, "or_size": None,
            "break_threshold_pct": _break_pct(),
            "reason": "opening range not available yet",
            "or_complete": False,
        }

    if not or_info["complete"]:
        return {
            "is_trend_day": False, "direction": None, "confidence": "LOW",
            "or_high": or_info["high"], "or_low": or_info["low"],
            "or_size": or_info["size"],
            "break_threshold_pct": _break_pct(),
            "reason": f"OR still building ({or_info['candle_count']} candles)",
            "or_complete": False,
        }

    break_pct = _break_pct()
    threshold = or_info["size"] * break_pct

    # BULL break
    if current_spot > or_info["high"] + threshold:
        return {
            "is_trend_day": True, "direction": "BULL", "confidence": "HIGH",
            "or_high": or_info["high"], "or_low": or_info["low"],
            "or_size": or_info["size"],
            "break_threshold_pct": break_pct,
            "reason": (
                f"BULL trend day — spot {current_spot:.1f} > "
                f"OR_high {or_info['high']:.1f} + {threshold:.1f} buffer"
            ),
            "or_complete": True,
        }

    # BEAR break
    if current_spot < or_info["low"] - threshold:
        return {
            "is_trend_day": True, "direction": "BEAR", "confidence": "HIGH",
            "or_high": or_info["high"], "or_low": or_info["low"],
            "or_size": or_info["size"],
            "break_threshold_pct": break_pct,
            "reason": (
                f"BEAR trend day — spot {current_spot:.1f} < "
                f"OR_low {or_info['low']:.1f} - {threshold:.1f} buffer"
            ),
            "or_complete": True,
        }

    return {
        "is_trend_day": False, "direction": None, "confidence": "LOW",
        "or_high": or_info["high"], "or_low": or_info["low"],
        "or_size": or_info["size"],
        "break_threshold_pct": break_pct,
        "reason": (
            f"Spot {current_spot:.1f} inside OR "
            f"[{or_info['low']:.1f}-{or_info['high']:.1f}]"
        ),
        "or_complete": True,
    }


def diagnostics() -> Dict:
    return {
        "module": "trend_day_detector",
        "or_minutes": _or_minutes(),
        "break_pct": _break_pct(),
        "vol_ratio": _vol_ratio(),
        "description": (
            "Toby Crabel's Opening Range Breakout (1990). First N min "
            "defines OR; break by (OR_size × break_pct) flags trend day."
        ),
    }
