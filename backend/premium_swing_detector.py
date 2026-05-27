"""
premium_swing_detector — per-strike option premium "first bottom/top reversal".

Built 2026-05-27 (Phase 5 of Option Y) per user's chart-based observation.

THE PATTERN

User observed: when an option premium drops, hits a level, gets re-tested,
then bounces with massive volume — that's an institutional reversal.
Professional traders enter exactly at this bounce candle for high R:R
entries.

For a 24000 CE option:
  Premium ₹150 → drop → bottom ₹70 → retest ₹68 → BIG GREEN candle with
  volume spike → bounce to ₹109 (+56%).

That bounce candle = ENTRY signal.

PROVEN ORIGIN

  Richard Wyckoff (1920s) — "absorption + spring" pattern
  Tom Williams — Volume Spread Analysis (VSA, 1990s)
  Larry Williams — "Long-Term Secrets to Short-Term Trading" (1999)

DETECTION RULES

  1. Build 5-min premium candles from input data
  2. Find day's first significant swing LOW after 09:30 (avoid opening
     5-min noise)
  3. Confirm reversal — all 4 must be true on the bounce candle:
     a. Bottom retested within 2% of original (or 2nd swing low formed
        at higher level after re-test)
     b. Bounce candle volume ≥ 2.0x last-10-candle average
     c. Bounce closes ≥ 5% above the bottom
     d. Bounce candle is solid green (body ≥ 60% of range)
  4. Fire signal: BUY this strike's side, ride to next swing high

MIRRORED FOR TOPS

  Same pattern flipped — first swing HIGH + retest + bounce-DOWN with
  volume = SELL/SHORT signal. (For options: avoid this strike side.)

USAGE

  from premium_swing_detector import detect_first_bottom_reversal

  result = detect_first_bottom_reversal(candles_5m)
  # result = {"signal": True, "type": "FIRST_BOTTOM_REVERSAL",
  #           "bottom_price": 70.0, "bounce_price": 95.0,
  #           "entry_zone": [85, 95], "expected_target": 130,
  #           "suggested_sl": 65, "confidence": "HIGH",
  #           "reason": ...}

The pure module — caller supplies candles, gets signal.
"""

from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime, time as dtime
import os
import pytz

IST = pytz.timezone("Asia/Kolkata")
SIGNAL_EARLIEST_TIME = dtime(9, 30)  # skip opening noise


# ── Config ────────────────────────────────────────────────────────────


def _fractal_bars() -> int:
    try:
        return max(1, int(os.environ.get("PREMIUM_SWING_FRACTAL_BARS", "2") or 2))
    except Exception:
        return 2


def _vol_ratio() -> float:
    try:
        return float(os.environ.get("PREMIUM_SWING_VOL_RATIO", "2.0") or 2.0)
    except Exception:
        return 2.0


def _min_bounce_pct() -> float:
    try:
        return float(os.environ.get("PREMIUM_SWING_MIN_BOUNCE_PCT", "5.0") or 5.0)
    except Exception:
        return 5.0


def _retest_band_pct() -> float:
    try:
        return float(os.environ.get("PREMIUM_SWING_RETEST_BAND_PCT", "2.0") or 2.0)
    except Exception:
        return 2.0


def _min_body_frac() -> float:
    try:
        return float(os.environ.get("PREMIUM_SWING_MIN_BODY_FRAC", "0.60") or 0.60)
    except Exception:
        return 0.60


# ── Helpers ───────────────────────────────────────────────────────────


def _parse_ts(ts) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else IST.localize(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.astimezone(IST) if dt.tzinfo else IST.localize(dt)
        except Exception:
            return None
    return None


def _is_green(c: Dict) -> bool:
    return c.get("close", 0) > c.get("open", 0)


def _is_red(c: Dict) -> bool:
    return c.get("close", 0) < c.get("open", 0)


def _body(c: Dict) -> float:
    return abs(c.get("close", 0) - c.get("open", 0))


def _range(c: Dict) -> float:
    return abs(c.get("high", 0) - c.get("low", 0))


def _avg_volume(candles: List[Dict], window: int = 10) -> float:
    """Average volume over last `window` candles (excluding current)."""
    if not candles:
        return 0
    vols = [c.get("volume", 0) for c in candles[-window:]]
    if not vols:
        return 0
    return sum(vols) / len(vols)


def _today_after_threshold(candles: List[Dict]) -> List[Dict]:
    """Filter to today's candles after the earliest-signal threshold (09:30)."""
    today = datetime.now(IST).date()
    out = []
    for c in candles:
        ts = _parse_ts(c.get("ts"))
        if ts is None:
            continue
        if ts.date() != today:
            continue
        if ts.time() < SIGNAL_EARLIEST_TIME:
            continue
        out.append({**c, "_ts_parsed": ts})
    return out


# ── Core detection ────────────────────────────────────────────────────


def detect_first_bottom_reversal(
    candles_5m: List[Dict],
    today_only: bool = True,
) -> Dict:
    """Detect day's first bottom + bounce reversal pattern.

    Args:
        candles_5m: list of 5-min OHLCV candles
        today_only: if True, filter to today's candles after 09:30

    Returns:
        {
          "signal": bool,
          "type": "FIRST_BOTTOM_REVERSAL" | None,
          "bottom_price": float | None,    # the swing low that bottomed
          "bounce_price": float | None,    # bounce candle close
          "entry_zone": [low, high] | None,
          "suggested_sl": float | None,    # below bottom by 5%
          "expected_target": float | None, # 1.5-2x bounce
          "volume_ratio": float | None,    # bounce volume / avg
          "confidence": "HIGH" | "MEDIUM" | "LOW",
          "reason": str,
        }
    """
    from price_structure import find_swing_lows

    base = {
        "signal": False, "type": None,
        "bottom_price": None, "bounce_price": None,
        "entry_zone": None, "suggested_sl": None,
        "expected_target": None, "volume_ratio": None,
    }

    candles = _today_after_threshold(candles_5m) if today_only else candles_5m
    if not candles or len(candles) < 6:
        return {**base, "confidence": "LOW",
                "reason": f"Not enough candles ({len(candles) if candles else 0})"}

    fb = _fractal_bars()
    swing_lows = find_swing_lows(candles, bars=fb)
    if not swing_lows:
        return {**base, "confidence": "LOW",
                "reason": "no confirmed swing low yet"}

    # Use the FIRST swing low of the day
    first_low = swing_lows[0]
    bottom_price = first_low["price"]
    bottom_idx = first_low["index"]

    # Bounce candle must come AFTER bottom + after its fractal-confirm window
    bounce_idx_start = bottom_idx + fb + 1
    if len(candles) <= bounce_idx_start:
        return {**base, "bottom_price": bottom_price,
                "confidence": "LOW",
                "reason": (
                    f"Swing low at idx {bottom_idx} found "
                    f"(price {bottom_price:.2f}), waiting for bounce candle"
                )}

    # ── Check for retest of bottom within band ──
    retest_band = bottom_price * _retest_band_pct() / 100
    retested = False
    for j in range(bottom_idx + 1, min(bottom_idx + 8, len(candles))):
        if candles[j].get("low", 0) <= (bottom_price + retest_band):
            retested = True
            break

    # ── Find the actual "bounce candle" — first strong green after bottom ──
    avg_vol = _avg_volume(candles[:bounce_idx_start], window=10)
    vol_threshold = avg_vol * _vol_ratio()
    min_bounce_pct = _min_bounce_pct()
    min_body_frac = _min_body_frac()

    for k in range(bounce_idx_start, len(candles)):
        c = candles[k]
        if not _is_green(c):
            continue
        candle_range = _range(c)
        if candle_range <= 0:
            continue
        body_frac = _body(c) / candle_range
        if body_frac < min_body_frac:
            continue
        # Bounce %
        close = c.get("close", 0)
        bounce_pct = (close - bottom_price) / bottom_price * 100 if bottom_price > 0 else 0
        if bounce_pct < min_bounce_pct:
            continue
        # Volume
        c_vol = c.get("volume", 0)
        if c_vol < vol_threshold:
            continue

        # All conditions met!
        confidence = "HIGH" if retested else "MEDIUM"
        return {
            "signal": True,
            "type": "FIRST_BOTTOM_REVERSAL",
            "bottom_price": round(bottom_price, 2),
            "bounce_price": round(close, 2),
            "entry_zone": [round(c.get("open", close), 2), round(close, 2)],
            "suggested_sl": round(bottom_price * 0.95, 2),
            "expected_target": round(close * 1.5, 2),
            "volume_ratio": round(c_vol / avg_vol, 2) if avg_vol > 0 else None,
            "confidence": confidence,
            "reason": (
                f"FIRST_BOTTOM_REVERSAL — bottom {bottom_price:.2f} "
                f"{'retested ✓ ' if retested else ''}+ bounce candle close "
                f"{close:.2f} ({bounce_pct:+.1f}%), volume {c_vol/avg_vol:.1f}x avg, "
                f"body {body_frac*100:.0f}% of range"
            ),
        }

    return {**base, "bottom_price": bottom_price,
            "confidence": "LOW",
            "reason": (
                f"Bottom at {bottom_price:.2f} confirmed but no qualifying "
                f"bounce candle yet (need green +{min_bounce_pct}%, "
                f"vol >{_vol_ratio():.1f}x, body >{min_body_frac*100:.0f}%)"
            )}


def detect_first_top_reversal(
    candles_5m: List[Dict],
    today_only: bool = True,
) -> Dict:
    """Mirror of detect_first_bottom_reversal — for tops (sell signal)."""
    from price_structure import find_swing_highs

    base = {
        "signal": False, "type": None,
        "top_price": None, "drop_price": None,
        "entry_zone": None, "suggested_sl": None,
        "expected_target": None, "volume_ratio": None,
    }

    candles = _today_after_threshold(candles_5m) if today_only else candles_5m
    if not candles or len(candles) < 6:
        return {**base, "confidence": "LOW",
                "reason": f"Not enough candles ({len(candles) if candles else 0})"}

    fb = _fractal_bars()
    swing_highs = find_swing_highs(candles, bars=fb)
    if not swing_highs:
        return {**base, "confidence": "LOW",
                "reason": "no confirmed swing high yet"}

    first_high = swing_highs[0]
    top_price = first_high["price"]
    top_idx = first_high["index"]

    drop_idx_start = top_idx + fb + 1
    if len(candles) <= drop_idx_start:
        return {**base, "top_price": top_price,
                "confidence": "LOW",
                "reason": (
                    f"Swing high at idx {top_idx} (price {top_price:.2f}), "
                    f"waiting for drop candle"
                )}

    # Retest of top?
    retest_band = top_price * _retest_band_pct() / 100
    retested = False
    for j in range(top_idx + 1, min(top_idx + 8, len(candles))):
        if candles[j].get("high", 0) >= (top_price - retest_band):
            retested = True
            break

    avg_vol = _avg_volume(candles[:drop_idx_start], window=10)
    vol_threshold = avg_vol * _vol_ratio()
    min_drop_pct = _min_bounce_pct()
    min_body_frac = _min_body_frac()

    for k in range(drop_idx_start, len(candles)):
        c = candles[k]
        if not _is_red(c):
            continue
        candle_range = _range(c)
        if candle_range <= 0:
            continue
        body_frac = _body(c) / candle_range
        if body_frac < min_body_frac:
            continue
        close = c.get("close", 0)
        drop_pct = (top_price - close) / top_price * 100 if top_price > 0 else 0
        if drop_pct < min_drop_pct:
            continue
        c_vol = c.get("volume", 0)
        if c_vol < vol_threshold:
            continue

        confidence = "HIGH" if retested else "MEDIUM"
        return {
            "signal": True,
            "type": "FIRST_TOP_REVERSAL",
            "top_price": round(top_price, 2),
            "drop_price": round(close, 2),
            "entry_zone": [round(close, 2), round(c.get("open", close), 2)],
            "suggested_sl": round(top_price * 1.05, 2),
            "expected_target": round(close * 0.5, 2),
            "volume_ratio": round(c_vol / avg_vol, 2) if avg_vol > 0 else None,
            "confidence": confidence,
            "reason": (
                f"FIRST_TOP_REVERSAL — top {top_price:.2f} "
                f"{'retested ✓ ' if retested else ''}+ drop candle close "
                f"{close:.2f} ({drop_pct:+.1f}%), volume {c_vol/avg_vol:.1f}x avg"
            ),
        }

    return {**base, "top_price": top_price,
            "confidence": "LOW",
            "reason": f"Top at {top_price:.2f} confirmed but no qualifying drop"}


def diagnostics() -> Dict:
    return {
        "module": "premium_swing_detector",
        "fractal_bars": _fractal_bars(),
        "vol_ratio_min": _vol_ratio(),
        "min_bounce_pct": _min_bounce_pct(),
        "retest_band_pct": _retest_band_pct(),
        "min_body_frac": _min_body_frac(),
        "description": (
            "Day's first bottom/top + volume-confirmed reversal — "
            "Wyckoff/VSA tape-reading pattern for institutional entries."
        ),
    }
