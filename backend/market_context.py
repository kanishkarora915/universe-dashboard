"""
market_context.py — 200-day historical structure awareness.

PHILOSOPHY (2026-06-05, user-directed):
  Smart = ADDITIVE (boost confidence when chart agrees).
  Strict = ONLY on loss control (caps, breakeven, early cut).
  Never BLOCK a trade based on chart alone — just adjust the score.

WHAT IT DOES:
  Daily (run at engine boot + every 4 hr):
    1. Pull 200-day daily candles via Kite REST (cached 6 hr).
    2. Identify last 60-day swing highs/lows (Bill Williams fractal).
    3. Cluster these into 3-5 major Support / Resistance zones.
    4. Detect 200-day trend: UPTREND / DOWNTREND / RANGE
    5. Compute volatility regime: low/normal/high (ATR-based).
    6. Store in market_context cache.

  Per-trade (every signal):
    7. Compare current spot to S/R zones.
    8. Score: -10 to +10 based on position + trend alignment.
    9. Return as ADDITIVE bonus to existing verdict score.

OUTPUTS (for caller integration):
  get_context(idx, spot, action) -> {
    "trend_200d": "UPTREND"|"DOWNTREND"|"RANGE",
    "trend_strength": 0..100,
    "nearest_support": float,
    "nearest_resistance": float,
    "zone_position": "AT_SUPPORT"|"AT_RESISTANCE"|"MIDDLE",
    "alignment_bonus": int (-10 to +10),  # ADDED to verdict score
    "reason": str,
  }

  Higher bonus = chart confirms action.
  Negative bonus = chart contradicts action.
  But NEVER 0 unless data missing — even mild signals contribute.

INTEGRATION:
  Verdict layer (engine.py): add bonus to bull_score or bear_score.
  Exit layer: use trend_200d to decide if "let it run" or "cut tight".
  NEVER use this module to BLOCK an entry.

Env:
  MARKET_CONTEXT_ENABLED=on  (default on)
  MARKET_CONTEXT_LOOKBACK_DAYS=200
  MARKET_CONTEXT_REFRESH_HOURS=6
"""

from __future__ import annotations

import os
import time
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

import pytz

IST = pytz.timezone("Asia/Kolkata")

# Module-level cache — populated by daily fetcher, read by per-trade scoring
_context_cache: Dict[str, Dict] = {}
_cache_lock = threading.Lock()
_last_refresh_ts = 0.0


def _is_enabled() -> bool:
    return os.environ.get("MARKET_CONTEXT_ENABLED", "on").lower() != "off"


def _lookback_days() -> int:
    try:
        return int(os.environ.get("MARKET_CONTEXT_LOOKBACK_DAYS", "200"))
    except (TypeError, ValueError):
        return 200


def _refresh_hours() -> int:
    try:
        return int(os.environ.get("MARKET_CONTEXT_REFRESH_HOURS", "6"))
    except (TypeError, ValueError):
        return 6


# ── Daily-candle structure analysis ──────────────────────────────────
def _find_swing_highs_lows(candles: List[Dict], fractal: int = 2) -> Tuple[List[Dict], List[Dict]]:
    """Bill Williams fractal: a swing high is a candle whose HIGH is greater
    than the HIGHs of the `fractal` candles on either side. Symmetric for low.
    Returns (swing_highs, swing_lows) each as list of {ts, price}.
    """
    highs, lows = [], []
    if len(candles) < 2 * fractal + 1:
        return highs, lows
    for i in range(fractal, len(candles) - fractal):
        win = candles[i - fractal: i + fractal + 1]
        center = candles[i]
        if all(center["high"] >= c["high"] for c in win) and \
                sum(1 for c in win if c["high"] == center["high"]) == 1:
            highs.append({"ts": center["ts"], "price": center["high"]})
        if all(center["low"] <= c["low"] for c in win) and \
                sum(1 for c in win if c["low"] == center["low"]) == 1:
            lows.append({"ts": center["ts"], "price": center["low"]})
    return highs, lows


def _cluster_levels(levels: List[float], tolerance_pct: float = 0.5) -> List[float]:
    """Cluster nearby price levels (within tolerance%) and return centroids.
    Sorted ascending.
    """
    if not levels:
        return []
    sorted_lvls = sorted(levels)
    clusters: List[List[float]] = [[sorted_lvls[0]]]
    for lvl in sorted_lvls[1:]:
        last_cluster_mean = sum(clusters[-1]) / len(clusters[-1])
        if abs(lvl - last_cluster_mean) / max(last_cluster_mean, 1) * 100 <= tolerance_pct:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])
    # Return centroid of each cluster, only keep clusters with ≥ 2 touches
    return [sum(c) / len(c) for c in clusters if len(c) >= 2]


def _detect_200d_trend(candles: List[Dict]) -> Tuple[str, int]:
    """Determine 200-day trend via SMA cross.
    Returns (verdict, strength 0-100).
    """
    if len(candles) < 50:
        return "UNKNOWN", 0
    closes = [c["close"] for c in candles]
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / min(200, len(closes))
    current = closes[-1]
    # Strength = distance from 200-SMA, clamped 0-100
    distance_pct = abs((current - sma200) / sma200 * 100)
    strength = min(100, int(distance_pct * 10))  # 1% = 10 strength
    if current > sma200 and sma50 > sma200:
        return "UPTREND", strength
    if current < sma200 and sma50 < sma200:
        return "DOWNTREND", strength
    return "RANGE", strength


def _compute_atr(candles: List[Dict], period: int = 14) -> float:
    """Average True Range over `period` daily candles."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(len(candles) - period, len(candles)):
        if i == 0:
            continue
        high, low = candles[i]["high"], candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


# ── Refresh & cache ──────────────────────────────────────────────────
def refresh_context(kite, idx: str) -> Optional[Dict]:
    """Fetch 200d daily candles via Kite REST + compute structure.
    Caches the result. Returns the computed context dict.
    """
    if not _is_enabled():
        return None
    try:
        from historical_loader import load_index_history
        days = _lookback_days()
        candles = load_index_history(kite, idx, "day", days=days)
        if not candles or len(candles) < 30:
            print(f"[MARKET_CONTEXT] {idx}: insufficient candles ({len(candles)})")
            return None

        # Use last 60d for swing detection (recent + relevant)
        recent_60d = candles[-min(60, len(candles)):]
        highs, lows = _find_swing_highs_lows(recent_60d, fractal=2)

        # Cluster swing prices into S/R zones
        resistance_zones = _cluster_levels([h["price"] for h in highs])
        support_zones = _cluster_levels([l["price"] for l in lows])

        # 200-day trend + volatility
        trend, strength = _detect_200d_trend(candles)
        atr = _compute_atr(candles)
        current = candles[-1]["close"]

        context = {
            "idx": idx,
            "computed_at": datetime.now(IST).isoformat(),
            "candle_count": len(candles),
            "current_close": current,
            "trend_200d": trend,
            "trend_strength": strength,
            "atr_14d": round(atr, 2),
            "support_zones": [round(s, 2) for s in support_zones[:5]],
            "resistance_zones": [round(r, 2) for r in resistance_zones[:5]],
            "swing_highs_60d": len(highs),
            "swing_lows_60d": len(lows),
        }
        with _cache_lock:
            _context_cache[idx] = context
        print(f"[MARKET_CONTEXT] {idx} refreshed: trend={trend} "
              f"({strength}), S={support_zones[:3]}, R={resistance_zones[:3]}")
        return context
    except Exception as e:
        print(f"[MARKET_CONTEXT] refresh {idx} failed: {e}")
        return None


def get_cached_context(idx: str) -> Optional[Dict]:
    """Read cached context. Returns None if not yet computed."""
    with _cache_lock:
        return _context_cache.get(idx)


# ── Per-trade scoring (the ADDITIVE bonus) ───────────────────────────
def get_context(idx: str, spot: float, action: str) -> Dict:
    """Return chart-alignment context for a proposed trade.

    Bonus interpretation:
      +10  Strong tailwind — chart strongly confirms action
      +5   Mild tailwind
       0   Neutral / no data
      -5   Mild headwind (chart contradicts but signal can still win)
      -10  Strong headwind (chart strongly contradicts)

    Caller adds bonus to verdict score. NEVER blocks based on this.
    """
    ctx = get_cached_context(idx)
    if not ctx:
        return {
            "trend_200d": "UNKNOWN",
            "alignment_bonus": 0,
            "reason": "no context cached yet",
        }

    is_ce = "CE" in (action or "").upper()
    bonus = 0
    reasons = []

    # Trend alignment: ±5 based on 200d trend agreeing with action
    #
    # 2026-06-15 — ADAPTIVE BONUS (Fix E):
    # Data showed CE-aligned UP trades (n=15) NET LOST ₹10k while
    # PE-aligned DN won 100%. Root cause: when 200d=UPTREND, CE got +5
    # which pushed marginal CE entries through *even when intraday
    # move was already exhausted*. Make the CE bonus conditional on
    # intraday room remaining (move_since_open < 0.3% by default).
    # PE bonus in DOWNTREND remains unconditional (data shows it works).
    # Env: MARKET_CTX_CE_BONUS_GUARD_DISABLED=1, MARKET_CTX_CE_BONUS_MAX_MOVE=0.3
    trend = ctx.get("trend_200d", "UNKNOWN")
    strength = ctx.get("trend_strength", 0)
    # Compute intraday move proxy: spot vs last daily close cached in ctx
    # (refresh happens once per session; current_close is the prior session close)
    _ref_close = ctx.get("current_close") or 0
    if _ref_close > 0 and spot > 0:
        intraday_move = (spot - _ref_close) / _ref_close * 100
    else:
        intraday_move = ctx.get("move_since_open_pct", 0) or 0
    try:
        import os as _os_mc
        _ce_guard_off = _os_mc.environ.get("MARKET_CTX_CE_BONUS_GUARD_DISABLED", "").strip() in ("1","true","on")
        _ce_max_move = float(_os_mc.environ.get("MARKET_CTX_CE_BONUS_MAX_MOVE", "0.3"))
    except Exception:
        _ce_guard_off = False
        _ce_max_move = 0.3

    if trend == "UPTREND":
        if is_ce:
            if _ce_guard_off or abs(intraday_move) <= _ce_max_move:
                bonus += 5
                reasons.append(f"200d UPTREND aligns CE (+5)")
            else:
                # exhaustion guard — index already moved a lot today
                reasons.append(f"200d UPTREND CE bonus skipped (move {intraday_move:+.2f}% > {_ce_max_move}%, exhaustion risk)")
        else:
            bonus -= 5
            reasons.append(f"200d UPTREND against PE (-5)")
    elif trend == "DOWNTREND":
        if not is_ce:
            bonus += 5
            reasons.append(f"200d DOWNTREND aligns PE (+5)")
        else:
            bonus -= 5
            reasons.append(f"200d DOWNTREND against CE (-5)")
    else:
        reasons.append("200d RANGE — no trend bias")

    # Zone position: ±5 based on S/R proximity
    support_zones = ctx.get("support_zones", [])
    resistance_zones = ctx.get("resistance_zones", [])
    nearest_support = max([s for s in support_zones if s < spot], default=None)
    nearest_resistance = min([r for r in resistance_zones if r > spot], default=None)

    zone = "MIDDLE"
    if nearest_support and (spot - nearest_support) / spot * 100 < 0.5:
        zone = "AT_SUPPORT"
        if is_ce:
            bonus += 5
            reasons.append(f"At support {nearest_support} → CE bounce setup (+5)")
        else:
            bonus -= 3
            reasons.append(f"At support {nearest_support} → PE risk (-3)")
    elif nearest_resistance and (nearest_resistance - spot) / spot * 100 < 0.5:
        zone = "AT_RESISTANCE"
        if not is_ce:
            bonus += 5
            reasons.append(f"At resistance {nearest_resistance} → PE rejection setup (+5)")
        else:
            bonus -= 3
            reasons.append(f"At resistance {nearest_resistance} → CE risk (-3)")

    return {
        "trend_200d": trend,
        "trend_strength": strength,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "zone_position": zone,
        "alignment_bonus": bonus,
        "reason": " · ".join(reasons),
    }


# ── Background refresh thread ────────────────────────────────────────
def start_refresh_thread(kite_getter):
    """Spawn a daemon that refreshes context every `MARKET_CONTEXT_REFRESH_HOURS`.

    kite_getter is a callable returning the current KiteConnect instance
    (so we always use the latest token).
    """
    def _loop():
        global _last_refresh_ts
        # Initial fetch after 90s (let engine fully boot)
        time.sleep(90)
        while True:
            try:
                if not _is_enabled():
                    time.sleep(600)
                    continue
                kite = kite_getter()
                if kite is None:
                    time.sleep(60)
                    continue
                refresh_hrs = _refresh_hours()
                if time.time() - _last_refresh_ts > refresh_hrs * 3600:
                    for idx in ("NIFTY", "BANKNIFTY"):
                        refresh_context(kite, idx)
                    _last_refresh_ts = time.time()
                time.sleep(600)  # check every 10 min
            except Exception as e:
                print(f"[MARKET_CONTEXT] refresh loop error: {e}")
                time.sleep(60)

    t = threading.Thread(target=_loop, daemon=True, name="market-context-refresh")
    t.start()
    return t


def diagnostics() -> Dict:
    """Snapshot for API endpoint."""
    with _cache_lock:
        return {
            "enabled": _is_enabled(),
            "lookback_days": _lookback_days(),
            "refresh_hours": _refresh_hours(),
            "last_refresh_ts": _last_refresh_ts,
            "cached_indices": list(_context_cache.keys()),
            "contexts": dict(_context_cache),
        }
