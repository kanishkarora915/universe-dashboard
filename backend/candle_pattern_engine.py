"""
Candle Pattern Engine
─────────────────────
Detects reversal patterns on the spot chart for OPEN positions.
Used by position_watcher to flag exits BEFORE SL hit.

Patterns detected (5-min candles, post-entry):
  • LOWER_HIGHS    — 3 consecutive lower highs (CE buy reversal)
  • HIGHER_LOWS    — 3 consecutive higher lows (PE buy reversal)
  • SHOOTING_STAR  — small body, long upper wick at top (CE killer)
  • HAMMER_INVERTED— at bottom (PE killer)
  • BEARISH_ENGULF — bearish candle engulfing prior green
  • BULLISH_ENGULF — bullish candle engulfing prior red
  • INSIDE_BAR_DEAD— momentum dying (3+ inside bars)
  • VOL_DIVERGENCE — price up, volume down (unsustainable)

Usage:
    from candle_pattern_engine import detect_patterns
    patterns = detect_patterns(candles_5min, position_action="BUY_CE")
    # returns: [{"name": "LOWER_HIGHS", "confidence": 0.8, "exit_signal": True}, ...]
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


def _is_red(c: Dict) -> bool:
    return c.get("close", 0) < c.get("open", 0)


def _is_green(c: Dict) -> bool:
    return c.get("close", 0) > c.get("open", 0)


def _body(c: Dict) -> float:
    return abs(c.get("close", 0) - c.get("open", 0))


def _range(c: Dict) -> float:
    return abs(c.get("high", 0) - c.get("low", 0))


def _upper_wick(c: Dict) -> float:
    return c.get("high", 0) - max(c.get("open", 0), c.get("close", 0))


def _lower_wick(c: Dict) -> float:
    return min(c.get("open", 0), c.get("close", 0)) - c.get("low", 0)


def detect_lower_highs(candles: List[Dict], lookback: int = 5) -> Optional[Dict]:
    """3 consecutive lower highs in last N candles → CE buy reversal warning."""
    if len(candles) < 3:
        return None
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    highs = [c.get("high", 0) for c in recent]
    # Look for monotonic decreasing in last 3
    last3 = highs[-3:] if len(highs) >= 3 else highs
    if len(last3) >= 3 and last3[0] > last3[1] > last3[2]:
        drop_pct = round((last3[0] - last3[2]) / last3[0] * 100, 2) if last3[0] > 0 else 0
        return {
            "name": "LOWER_HIGHS",
            "confidence": 0.85,
            "exit_signal": True,
            "applies_to": "CE",
            "detail": f"3 lower highs: {last3[0]:.1f} → {last3[1]:.1f} → {last3[2]:.1f} (-{drop_pct}%)",
        }
    return None


def detect_higher_lows(candles: List[Dict], lookback: int = 5) -> Optional[Dict]:
    """3 consecutive higher lows → PE buy reversal warning."""
    if len(candles) < 3:
        return None
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    lows = [c.get("low", 0) for c in recent]
    last3 = lows[-3:] if len(lows) >= 3 else lows
    if len(last3) >= 3 and last3[0] < last3[1] < last3[2]:
        rise_pct = round((last3[2] - last3[0]) / last3[0] * 100, 2) if last3[0] > 0 else 0
        return {
            "name": "HIGHER_LOWS",
            "confidence": 0.85,
            "exit_signal": True,
            "applies_to": "PE",
            "detail": f"3 higher lows: {last3[0]:.1f} → {last3[1]:.1f} → {last3[2]:.1f} (+{rise_pct}%)",
        }
    return None


def detect_shooting_star(candles: List[Dict]) -> Optional[Dict]:
    """Small body + long upper wick = top reversal. Kills CE longs."""
    if not candles:
        return None
    c = candles[-1]
    body = _body(c)
    rng = _range(c)
    upper = _upper_wick(c)
    lower = _lower_wick(c)
    if rng <= 0:
        return None
    if upper >= 2 * body and upper >= 0.6 * rng and lower <= 0.2 * rng and body > 0:
        return {
            "name": "SHOOTING_STAR",
            "confidence": 0.75,
            "exit_signal": True,
            "applies_to": "CE",
            "detail": f"Shooting star formed at {c.get('high', 0):.1f} — top rejection",
        }
    return None


def detect_inverted_hammer(candles: List[Dict]) -> Optional[Dict]:
    """Inverted hammer at bottom — kills PE longs."""
    if not candles:
        return None
    c = candles[-1]
    body = _body(c)
    rng = _range(c)
    lower = _lower_wick(c)
    upper = _upper_wick(c)
    if rng <= 0:
        return None
    if lower >= 2 * body and lower >= 0.6 * rng and upper <= 0.2 * rng and body > 0:
        return {
            "name": "HAMMER_REVERSAL",
            "confidence": 0.75,
            "exit_signal": True,
            "applies_to": "PE",
            "detail": f"Hammer at {c.get('low', 0):.1f} — bottom rejection",
        }
    return None


def detect_bearish_engulfing(candles: List[Dict]) -> Optional[Dict]:
    """Bearish candle engulfs prior green — strong CE-kill."""
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    if (_is_green(prev) and _is_red(curr)
            and curr.get("open", 0) >= prev.get("close", 0)
            and curr.get("close", 0) <= prev.get("open", 0)
            and _body(curr) > _body(prev) * 1.1):
        return {
            "name": "BEARISH_ENGULF",
            "confidence": 0.80,
            "exit_signal": True,
            "applies_to": "CE",
            "detail": f"Bearish engulfing: red body ₹{_body(curr):.1f} > prev green ₹{_body(prev):.1f}",
        }
    return None


def detect_bullish_engulfing(candles: List[Dict]) -> Optional[Dict]:
    """Bullish engulfing — strong PE-kill."""
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    if (_is_red(prev) and _is_green(curr)
            and curr.get("open", 0) <= prev.get("close", 0)
            and curr.get("close", 0) >= prev.get("open", 0)
            and _body(curr) > _body(prev) * 1.1):
        return {
            "name": "BULLISH_ENGULF",
            "confidence": 0.80,
            "exit_signal": True,
            "applies_to": "PE",
            "detail": f"Bullish engulfing: green body ₹{_body(curr):.1f} > prev red ₹{_body(prev):.1f}",
        }
    return None


def detect_inside_bars(candles: List[Dict], min_count: int = 3) -> Optional[Dict]:
    """3+ consecutive inside bars = momentum dead → premium decay accelerating."""
    if len(candles) < min_count + 1:
        return None
    inside_count = 0
    for i in range(len(candles) - 1, 0, -1):
        c = candles[i]
        prev = candles[i - 1]
        if (c.get("high", 0) <= prev.get("high", 0)
                and c.get("low", 0) >= prev.get("low", 0)):
            inside_count += 1
        else:
            break
    if inside_count >= min_count:
        return {
            "name": "INSIDE_BAR_DEAD",
            "confidence": 0.65,
            "exit_signal": True,
            "applies_to": "BOTH",
            "detail": f"{inside_count} inside bars — momentum dead, theta winning",
        }
    return None


def detect_volume_divergence(candles: List[Dict], lookback: int = 5) -> Optional[Dict]:
    """Price making higher highs but volume declining → unsustainable rally."""
    if len(candles) < lookback:
        return None
    recent = candles[-lookback:]
    highs = [c.get("high", 0) for c in recent]
    vols = [c.get("volume", 0) or 0 for c in recent]
    # Higher highs (last > first) but lower volume (avg of last 2 < avg of first 2)
    if highs[-1] > highs[0] and vols[0] > 0:
        first_half_vol = (vols[0] + vols[1]) / 2 if len(vols) >= 2 else vols[0]
        last_half_vol = (vols[-1] + vols[-2]) / 2 if len(vols) >= 2 else vols[-1]
        if last_half_vol < first_half_vol * 0.6:
            return {
                "name": "VOL_DIVERGENCE",
                "confidence": 0.70,
                "exit_signal": True,
                "applies_to": "CE",
                "detail": f"Price up but volume {last_half_vol:.0f} < {first_half_vol:.0f} avg — buying weak",
            }
    return None


def detect_patterns(
    candles: List[Dict],
    position_action: str = "BUY_CE",
    entry_time: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Run all pattern detectors on candle history. Filter to those applicable
    to the given position action.

    candles: list of dicts with {open, high, low, close, volume, timestamp}
    position_action: "BUY_CE" or "BUY_PE"
    entry_time: only consider candles AFTER entry (optional)

    Returns list of detected patterns sorted by confidence descending.
    """
    if not candles:
        return []

    # Filter to post-entry candles if entry_time given
    if entry_time:
        # Strip timezone for safe comparison with _to_dt() output (naive)
        et = entry_time.replace(tzinfo=None) if getattr(entry_time, "tzinfo", None) else entry_time
        candles = [c for c in candles if c.get("timestamp") and
                   _to_dt(c["timestamp"]) >= et]

    if not candles:
        return []

    is_ce = "CE" in position_action.upper()
    is_pe = "PE" in position_action.upper()

    results: List[Dict] = []

    # CE-killers
    if is_ce:
        for fn in (detect_lower_highs, detect_shooting_star,
                   detect_bearish_engulfing, detect_volume_divergence):
            r = fn(candles)
            if r:
                results.append(r)

    # PE-killers
    if is_pe:
        for fn in (detect_higher_lows, detect_inverted_hammer,
                   detect_bullish_engulfing):
            r = fn(candles)
            if r:
                results.append(r)

    # Both-side killers
    r = detect_inside_bars(candles)
    if r:
        results.append(r)

    return sorted(results, key=lambda x: x.get("confidence", 0), reverse=True)


def _to_dt(ts):
    """Normalize timestamp to datetime."""
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            try:
                return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return datetime.now()
    if isinstance(ts, (int, float)):
        # epoch ms or s
        try:
            return datetime.fromtimestamp(ts / 1000 if ts > 1e10 else ts)
        except Exception:
            return datetime.now()
    return datetime.now()


def build_candles_from_ticks(ticks: List[Dict], interval_min: int = 5) -> List[Dict]:
    """
    Aggregate raw spot ticks into OHLC candles.
    ticks: list of {ts (epoch ms or iso), price, volume?}
    """
    if not ticks:
        return []
    bucket: Dict[int, Dict] = {}
    for t in ticks:
        ts = t.get("ts") or t.get("timestamp")
        price = t.get("price") or t.get("ltp") or 0
        if not ts or price <= 0:
            continue
        dt = _to_dt(ts)
        # bucket key = minute floored to interval
        bucket_min = (dt.hour * 60 + dt.minute) // interval_min * interval_min
        key = dt.replace(hour=bucket_min // 60, minute=bucket_min % 60,
                         second=0, microsecond=0)
        ekey = int(key.timestamp())
        if ekey not in bucket:
            bucket[ekey] = {
                "timestamp": key.isoformat(),
                "open": price, "high": price, "low": price, "close": price,
                "volume": t.get("volume", 0) or 0,
            }
        else:
            b = bucket[ekey]
            b["high"] = max(b["high"], price)
            b["low"] = min(b["low"], price)
            b["close"] = price
            b["volume"] += t.get("volume", 0) or 0
    return sorted(bucket.values(), key=lambda x: x["timestamp"])
