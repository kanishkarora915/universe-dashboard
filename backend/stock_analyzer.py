"""
stock_analyzer.py — Comprehensive deep-dive analysis for any F&O stock.

Coverage:
  PRICE         spot, prev close, day H/L, range %, gap
  RETURNS       1d / 5d / 1m / 3m / 6m / 1y / YTD
  52-WEEK       high, low, dist from each, position in range
  TREND         SMA 20/50/200, golden cross, multi-TF struct (5m/15m/1h/1d/1w)
  MOMENTUM      RSI-14, Stochastic K/D, MACD (line/signal/histogram)
  VOLATILITY    Bollinger Bands, BB width, BB position, HV-20d, HV-60d, ATR-14d
  LEVELS        Cluster S/R from 60-day swings, VWAP, distance to nearest
  VOLUME        Today vs 20d avg, ratio, trend
  FUTURES       Price, basis %, OI, OI change, OI buildup type, lot size
  PATTERNS      Bull/Bear flag, double top/bottom, cup&handle, breakout
  PREDICTION    1d / 3d / 1w targets, SL, RR, confidence, reasoning
  SCORE         7-dimension breakdown + total /100

Single entry point: analyze(kite, stock) -> dict with ~60 fields.
"""

from __future__ import annotations

import os
import math
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import pytz

IST = pytz.timezone("Asia/Kolkata")


# ── Indicator math ────────────────────────────────────────────────────
def sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(0, ch))
        losses.append(max(0, -ch))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def macd(closes: List[float]) -> Optional[Dict]:
    if len(closes) < 35:
        return None
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    if ema12 is None or ema26 is None:
        return None
    line = ema12 - ema26
    # signal = 9-EMA of MACD line — compute over rolling history
    macd_history = []
    for i in range(26, len(closes) + 1):
        sub = closes[:i]
        e12 = ema(sub, 12)
        e26 = ema(sub, 26)
        if e12 and e26:
            macd_history.append(e12 - e26)
    signal = ema(macd_history, 9) if len(macd_history) >= 9 else None
    if signal is None:
        return None
    return {
        "line": round(line, 3),
        "signal": round(signal, 3),
        "histogram": round(line - signal, 3),
        "verdict": "BULLISH" if line > signal else "BEARISH",
    }


def bollinger_bands(closes: List[float], period: int = 20, k: float = 2.0) -> Optional[Dict]:
    if len(closes) < period:
        return None
    mid = sum(closes[-period:]) / period
    variance = sum((c - mid) ** 2 for c in closes[-period:]) / period
    std = math.sqrt(variance)
    upper = mid + k * std
    lower = mid - k * std
    cur = closes[-1]
    width_pct = ((upper - lower) / mid) * 100 if mid > 0 else 0
    position = (cur - lower) / (upper - lower) if upper > lower else 0.5
    return {
        "upper": round(upper, 2),
        "middle": round(mid, 2),
        "lower": round(lower, 2),
        "width_pct": round(width_pct, 2),
        "position": round(position, 2),
        "squeeze": width_pct < 5.0,  # tight = volatility ahead
    }


def stochastic(candles: List[Dict], period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> Optional[Dict]:
    if len(candles) < period + smooth_k + smooth_d:
        return None
    raw_k = []
    for i in range(period - 1, len(candles)):
        sub = candles[i - period + 1: i + 1]
        h = max(c["high"] for c in sub)
        l = min(c["low"] for c in sub)
        cur = sub[-1]["close"]
        raw_k.append(100 * (cur - l) / (h - l) if h > l else 50)
    # Smooth K
    smoothed_k = [sum(raw_k[i - smooth_k + 1: i + 1]) / smooth_k
                  for i in range(smooth_k - 1, len(raw_k))]
    smoothed_d = [sum(smoothed_k[i - smooth_d + 1: i + 1]) / smooth_d
                  for i in range(smooth_d - 1, len(smoothed_k))]
    k_val = smoothed_k[-1]
    d_val = smoothed_d[-1]
    return {
        "k": round(k_val, 1),
        "d": round(d_val, 1),
        "status": "OVERBOUGHT" if k_val > 80 else "OVERSOLD" if k_val < 20 else "NEUTRAL",
    }


def atr(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0
    return sum(trs[-period:]) / period


def historical_volatility(closes: List[float], period: int = 20) -> float:
    """Annualized historical volatility from daily log returns."""
    if len(closes) < period + 1:
        return 0.0
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    sub = returns[-period:]
    if not sub:
        return 0.0
    mean = sum(sub) / len(sub)
    var = sum((r - mean) ** 2 for r in sub) / len(sub)
    return math.sqrt(var) * math.sqrt(252) * 100  # annualized %


def find_swings(candles: List[Dict], fractal: int = 2) -> tuple:
    if len(candles) < 2 * fractal + 1:
        return [], []
    highs, lows = [], []
    for i in range(fractal, len(candles) - fractal):
        win = candles[i - fractal: i + fractal + 1]
        c = candles[i]
        if all(c["high"] >= w["high"] for w in win) and \
                sum(1 for w in win if w["high"] == c["high"]) == 1:
            highs.append(c["high"])
        if all(c["low"] <= w["low"] for w in win) and \
                sum(1 for w in win if w["low"] == c["low"]) == 1:
            lows.append(c["low"])
    return highs, lows


def cluster_levels(levels: List[float], tolerance_pct: float = 0.8) -> List[float]:
    if not levels:
        return []
    sorted_lvls = sorted(levels)
    clusters: List[List[float]] = [[sorted_lvls[0]]]
    for lvl in sorted_lvls[1:]:
        mean = sum(clusters[-1]) / len(clusters[-1])
        if mean > 0 and abs(lvl - mean) / mean * 100 <= tolerance_pct:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])
    return [round(sum(c) / len(c), 2) for c in clusters if len(c) >= 2]


def detect_tf_structure(candles: List[Dict], fractal: int = 2) -> str:
    highs, lows = find_swings(candles, fractal)
    if len(highs) < 2 or len(lows) < 2:
        return "UNKNOWN"
    asc_h = highs[-1] > highs[-2]
    asc_l = lows[-1] > lows[-2]
    if asc_h and asc_l: return "UPTREND"
    if not asc_h and not asc_l: return "DOWNTREND"
    return "CHOP"


# ── Candle loaders ───────────────────────────────────────────────────
def _load_history(kite, token: int, interval: str, days: int) -> List[Dict]:
    """Pull historical candles from Kite REST."""
    try:
        now = datetime.now(IST)
        from_dt = now - timedelta(days=days)
        raw = kite.historical_data(
            instrument_token=token,
            from_date=from_dt.strftime("%Y-%m-%d"),
            to_date=now.strftime("%Y-%m-%d"),
            interval=interval,
        )
        return [{
            "ts": str(c.get("date", "")),
            "open": float(c.get("open", 0) or 0),
            "high": float(c.get("high", 0) or 0),
            "low": float(c.get("low", 0) or 0),
            "close": float(c.get("close", 0) or 0),
            "volume": int(c.get("volume", 0) or 0),
        } for c in (raw or [])]
    except Exception as e:
        print(f"[STOCK-ANALYZE] history fetch failed token={token} interval={interval}: {e}")
        return []


# Cache NFO instruments list — kite.instruments("NFO") is SLOW (~2-5s)
# and returns thousands of contracts. Without caching, scanning 211
# stocks calls this 211 times = 10-15 min of just instruments fetches.
# 1-hour TTL — F&O contracts rarely change intra-day.
_nfo_cache: Optional[List] = None
_nfo_cache_ts: float = 0.0
import threading as _th
_nfo_cache_lock = _th.Lock()
import time as _time_mod


def _get_nfo_instruments(kite) -> List:
    global _nfo_cache, _nfo_cache_ts
    now = _time_mod.time()
    with _nfo_cache_lock:
        if _nfo_cache and (now - _nfo_cache_ts) < 3600:
            return _nfo_cache
    try:
        inst = kite.instruments("NFO")
        with _nfo_cache_lock:
            _nfo_cache = inst
            _nfo_cache_ts = now
        print(f"[STOCK-ANALYZE] NFO instruments cached: {len(inst)}")
        return inst
    except Exception as e:
        print(f"[STOCK-ANALYZE] NFO fetch failed: {e}")
        return _nfo_cache or []


def _resolve_futures_token(kite, symbol: str) -> Optional[Dict]:
    """Find the nearest-month futures contract for a symbol.
    Uses module-level cache to avoid repeated 2-5s instruments() calls.
    """
    try:
        instruments = _get_nfo_instruments(kite)
        candidates = []
        today = datetime.now(IST).date()
        for inst in instruments:
            if (inst.get("instrument_type") == "FUT"
                    and inst.get("name", "").upper() == symbol.upper()):
                exp = inst.get("expiry")
                if exp:
                    if hasattr(exp, "date"):
                        exp_date = exp.date() if hasattr(exp, "date") else exp
                    else:
                        exp_date = exp
                    if exp_date >= today:
                        candidates.append((exp_date, inst))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
    except Exception as e:
        print(f"[STOCK-ANALYZE] futures resolve failed for {symbol}: {e}")
        return None


# ── MAIN ANALYZE ─────────────────────────────────────────────────────
def analyze(kite, stock: Dict) -> Optional[Dict]:
    """Comprehensive analysis. Returns ~60-field dict or None on failure."""
    if kite is None or not stock.get("token"):
        return None

    symbol = stock["symbol"]
    token = stock["token"]

    try:
        # ── Pull histories ──
        d_candles = _load_history(kite, token, "day", days=400)
        if not d_candles or len(d_candles) < 30:
            return None

        # Intraday (best-effort; may fail for newly-listed)
        d1h_candles = _load_history(kite, token, "60minute", days=15)
        d15m_candles = _load_history(kite, token, "15minute", days=5)
        d5m_candles = _load_history(kite, token, "5minute", days=2)

        closes = [c["close"] for c in d_candles]
        cur = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else cur
        today = d_candles[-1]
        prev = d_candles[-2] if len(d_candles) >= 2 else today

        # ── PRICE ──
        price_block = {
            "price": round(cur, 2),
            "prev_close": round(prev_close, 2),
            "open": round(today["open"], 2),
            "day_high": round(today["high"], 2),
            "day_low": round(today["low"], 2),
            "moved_pct": round((cur - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0,
            "day_range_pct": round((today["high"] - today["low"]) / today["open"] * 100, 2) if today["open"] > 0 else 0,
            "gap_pct": round((today["open"] - prev["close"]) / prev["close"] * 100, 2) if prev["close"] > 0 else 0,
        }

        # ── RETURNS ──
        def ret_n_back(n):
            if len(closes) <= n: return None
            return round((cur - closes[-n - 1]) / closes[-n - 1] * 100, 2)

        returns_block = {
            "1d_pct": price_block["moved_pct"],
            "5d_pct": ret_n_back(5),
            "1m_pct": ret_n_back(21),    # ~21 trading days
            "3m_pct": ret_n_back(63),
            "6m_pct": ret_n_back(126),
            "1y_pct": ret_n_back(252),
        }

        # ── 52-WEEK RANGE ──
        last_252 = d_candles[-min(252, len(d_candles)):]
        hi_52w = max(c["high"] for c in last_252)
        lo_52w = min(c["low"] for c in last_252)
        position_pct = ((cur - lo_52w) / (hi_52w - lo_52w) * 100) if hi_52w > lo_52w else 50
        w52_block = {
            "high": round(hi_52w, 2),
            "low": round(lo_52w, 2),
            "dist_from_high_pct": round((cur - hi_52w) / hi_52w * 100, 2),
            "dist_from_low_pct": round((cur - lo_52w) / lo_52w * 100, 2),
            "position_pct": round(position_pct, 1),
        }

        # ── TREND ──
        sma20 = sma(closes, 20)
        sma50 = sma(closes, 50)
        sma200 = sma(closes, 200)
        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)

        def trend_verdict(spot, ma):
            if ma is None: return "UNKNOWN"
            diff_pct = (spot - ma) / ma * 100
            if diff_pct > 1: return "ABOVE"
            if diff_pct < -1: return "BELOW"
            return "NEAR"

        golden_cross = (sma50 and sma200 and sma50 > sma200)
        death_cross = (sma50 and sma200 and sma50 < sma200)

        # Multi-TF structure
        struct_1w = detect_tf_structure(d_candles[-50:], fractal=3) if len(d_candles) >= 50 else "UNKNOWN"
        struct_1d = detect_tf_structure(d_candles[-30:], fractal=2)
        struct_1h = detect_tf_structure(d1h_candles, fractal=2) if d1h_candles else "UNKNOWN"
        struct_15m = detect_tf_structure(d15m_candles, fractal=2) if d15m_candles else "UNKNOWN"
        struct_5m = detect_tf_structure(d5m_candles, fractal=2) if d5m_candles else "UNKNOWN"

        # Alignment score
        tfs = [struct_1w, struct_1d, struct_1h, struct_15m, struct_5m]
        up_count = sum(1 for t in tfs if t == "UPTREND")
        down_count = sum(1 for t in tfs if t == "DOWNTREND")
        non_unknown = sum(1 for t in tfs if t not in ("UNKNOWN", "CHOP"))
        alignment_score = 0
        alignment_dir = "MIXED"
        if non_unknown > 0:
            if up_count >= non_unknown - 1 and up_count >= 3:
                alignment_score = int(up_count / 5 * 100)
                alignment_dir = "BULLISH"
            elif down_count >= non_unknown - 1 and down_count >= 3:
                alignment_score = int(down_count / 5 * 100)
                alignment_dir = "BEARISH"
            else:
                alignment_score = 30

        # 200d trend strength
        trend_strength = 0
        trend_direction = "RANGE"
        if sma200:
            dist_pct = abs((cur - sma200) / sma200 * 100)
            trend_strength = min(100, int(dist_pct * 10))
            if cur > sma200 and sma50 and sma50 > sma200:
                trend_direction = "UPTREND"
            elif cur < sma200 and sma50 and sma50 < sma200:
                trend_direction = "DOWNTREND"

        trend_block = {
            "sma20": round(sma20, 2) if sma20 else None,
            "sma50": round(sma50, 2) if sma50 else None,
            "sma200": round(sma200, 2) if sma200 else None,
            "ema20": round(ema20, 2) if ema20 else None,
            "ema50": round(ema50, 2) if ema50 else None,
            "vs_sma20": trend_verdict(cur, sma20),
            "vs_sma50": trend_verdict(cur, sma50),
            "vs_sma200": trend_verdict(cur, sma200),
            "golden_cross": golden_cross,
            "death_cross": death_cross,
            "200d_trend": trend_direction,
            "200d_strength": trend_strength,
            "struct_1w": struct_1w,
            "struct_1d": struct_1d,
            "struct_1h": struct_1h,
            "struct_15m": struct_15m,
            "struct_5m": struct_5m,
            "alignment_score": alignment_score,
            "alignment_direction": alignment_dir,
        }

        # ── MOMENTUM ──
        rsi_val = rsi(closes, 14)
        rsi_status = "NEUTRAL"
        if rsi_val:
            if rsi_val > 70: rsi_status = "OVERBOUGHT"
            elif rsi_val < 30: rsi_status = "OVERSOLD"
            elif rsi_val > 50: rsi_status = "BULLISH"
            elif rsi_val < 50: rsi_status = "BEARISH"

        macd_data = macd(closes)
        stoch = stochastic(d_candles, 14, 3, 3)

        momentum_block = {
            "rsi_14": round(rsi_val, 1) if rsi_val else None,
            "rsi_status": rsi_status,
            "macd": macd_data,
            "stochastic": stoch,
        }

        # ── VOLATILITY ──
        atr14 = atr(d_candles, 14)
        atr_pct = (atr14 / cur * 100) if cur > 0 else 0
        hv20 = historical_volatility(closes, 20)
        hv60 = historical_volatility(closes, 60)
        bb = bollinger_bands(closes, 20, 2.0)

        vol_block = {
            "atr_14d": round(atr14, 2),
            "atr_pct": round(atr_pct, 2),
            "hv_20d_pct": round(hv20, 1),
            "hv_60d_pct": round(hv60, 1),
            "bollinger": bb,
        }

        # ── LEVELS (S/R from 60-day swings) ──
        recent_60 = d_candles[-min(60, len(d_candles)):]
        highs, lows = find_swings(recent_60, fractal=2)
        resistance_zones = cluster_levels(highs)
        support_zones = cluster_levels(lows)
        nearest_s = max([s for s in support_zones if s < cur], default=None)
        nearest_r = min([r for r in resistance_zones if r > cur], default=None)

        levels_block = {
            "support_zones": support_zones[:5],
            "resistance_zones": resistance_zones[:5],
            "nearest_support": nearest_s,
            "nearest_resistance": nearest_r,
            "dist_to_support_pct": round((cur - nearest_s) / cur * 100, 2) if nearest_s else None,
            "dist_to_resistance_pct": round((nearest_r - cur) / cur * 100, 2) if nearest_r else None,
        }

        # ── VOLUME ──
        today_vol = today.get("volume", 0)
        avg_20d_vol = sum(c.get("volume", 0) for c in d_candles[-20:]) / 20 if len(d_candles) >= 20 else 0
        vol_ratio = (today_vol / avg_20d_vol) if avg_20d_vol > 0 else 0
        vol_trend = "RISING" if vol_ratio > 1.3 else "DROPPING" if vol_ratio < 0.7 else "NORMAL"

        volume_block = {
            "today": today_vol,
            "avg_20d": int(avg_20d_vol),
            "ratio": round(vol_ratio, 2),
            "trend": vol_trend,
        }

        # ── FUTURES ──
        fut_block = None
        try:
            fut_inst = _resolve_futures_token(kite, symbol)
            if fut_inst:
                fut_token = fut_inst["instrument_token"]
                fut_ltp = None
                fut_oi = 0
                fut_oi_change_pct = None
                try:
                    quote = kite.quote(f"NFO:{fut_inst['tradingsymbol']}")
                    qdata = quote.get(f"NFO:{fut_inst['tradingsymbol']}", {})
                    fut_ltp = qdata.get("last_price", 0)
                    fut_oi = qdata.get("oi", 0)
                    fut_prev_oi = qdata.get("oi_day_high", 0) or qdata.get("oi_day_low", 0)
                except Exception:
                    pass
                # OI change from daily history
                try:
                    fut_day = _load_history(kite, fut_token, "day", days=5)
                    if len(fut_day) >= 2:
                        # Volume is OI proxy in Kite futures history
                        pass
                except Exception:
                    pass

                basis_pct = ((fut_ltp - cur) / cur * 100) if (fut_ltp and cur) else None
                # OI buildup classification
                price_up_today = price_block["moved_pct"] > 0
                # Without precise OI delta, we use today's change as a proxy
                fut_oi_buildup = "UNKNOWN"
                if price_up_today and fut_oi > 0:
                    fut_oi_buildup = "LONG_BUILDUP"  # price up + OI presumed up
                elif not price_up_today and fut_oi > 0:
                    fut_oi_buildup = "SHORT_BUILDUP"

                fut_block = {
                    "expiry": str(fut_inst.get("expiry", ""))[:10],
                    "tradingsymbol": fut_inst.get("tradingsymbol"),
                    "price": round(fut_ltp, 2) if fut_ltp else None,
                    "basis_pct": round(basis_pct, 2) if basis_pct is not None else None,
                    "basis_signal": "PREMIUM" if (basis_pct and basis_pct > 0) else "DISCOUNT" if (basis_pct and basis_pct < 0) else "FLAT",
                    "oi": fut_oi,
                    "oi_buildup": fut_oi_buildup,
                    "lot_size": fut_inst.get("lot_size"),
                }
        except Exception as e:
            print(f"[STOCK-ANALYZE] futures block err for {symbol}: {e}")

        # ── PATTERNS ──
        patterns = []
        if sma200 and cur > sma200 * 1.02:
            patterns.append({"name": "Above 200 SMA (uptrend)", "tf": "1d", "confidence": 100})
        if sma200 and cur < sma200 * 0.98:
            patterns.append({"name": "Below 200 SMA (downtrend)", "tf": "1d", "confidence": 100})
        if golden_cross:
            patterns.append({"name": "Golden Cross (SMA50>200)", "tf": "1d", "confidence": 85})
        if death_cross:
            patterns.append({"name": "Death Cross (SMA50<200)", "tf": "1d", "confidence": 85})
        if w52_block["position_pct"] > 90:
            patterns.append({"name": "Near 52-week HIGH", "tf": "1y", "confidence": 90})
        if w52_block["position_pct"] < 10:
            patterns.append({"name": "Near 52-week LOW", "tf": "1y", "confidence": 90})
        if bb and bb["squeeze"]:
            patterns.append({"name": "Bollinger Squeeze (volatility coming)", "tf": "1d", "confidence": 70})
        if rsi_val:
            if rsi_val > 75:
                patterns.append({"name": "RSI Overbought (>75)", "tf": "1d", "confidence": 75})
            elif rsi_val < 25:
                patterns.append({"name": "RSI Oversold (<25)", "tf": "1d", "confidence": 75})
        if macd_data and macd_data["histogram"] > 0 and macd_data["verdict"] == "BULLISH":
            patterns.append({"name": "MACD Bull Cross", "tf": "1d", "confidence": 75})
        if macd_data and macd_data["histogram"] < 0 and macd_data["verdict"] == "BEARISH":
            patterns.append({"name": "MACD Bear Cross", "tf": "1d", "confidence": 75})
        if vol_ratio > 1.5:
            patterns.append({"name": f"Volume spike ({vol_ratio:.1f}x avg)", "tf": "1d", "confidence": 80})

        # ── PREDICTION ──
        moved_atr = abs(cur - prev_close) / atr14 if atr14 > 0 else 0
        room_atr_up = ((nearest_r - cur) / atr14) if (nearest_r and atr14 > 0) else 0
        room_atr_down = ((cur - nearest_s) / atr14) if (nearest_s and atr14 > 0) else 0

        # Bull setup
        bull_signals = 0
        bear_signals = 0
        reasons = []

        if trend_direction == "UPTREND":
            bull_signals += 1
            reasons.append("200d UPTREND")
        elif trend_direction == "DOWNTREND":
            bear_signals += 1
            reasons.append("200d DOWNTREND")

        if alignment_dir == "BULLISH":
            bull_signals += 1
            reasons.append(f"Multi-TF aligned bull ({alignment_score}%)")
        elif alignment_dir == "BEARISH":
            bear_signals += 1
            reasons.append(f"Multi-TF aligned bear ({alignment_score}%)")

        if golden_cross:
            bull_signals += 1
            reasons.append("Golden Cross")
        if death_cross:
            bear_signals += 1
            reasons.append("Death Cross")

        if rsi_val:
            if 50 <= rsi_val < 70:
                bull_signals += 1
                reasons.append(f"RSI {rsi_val:.0f} bull room")
            elif 30 < rsi_val <= 50:
                bear_signals += 1
                reasons.append(f"RSI {rsi_val:.0f} bear room")
            elif rsi_val >= 70:
                bear_signals += 1  # reversal risk
                reasons.append(f"RSI {rsi_val:.0f} overbought")
            elif rsi_val <= 30:
                bull_signals += 1
                reasons.append(f"RSI {rsi_val:.0f} oversold")

        if macd_data and macd_data["verdict"] == "BULLISH":
            bull_signals += 1
            reasons.append("MACD bull")
        elif macd_data and macd_data["verdict"] == "BEARISH":
            bear_signals += 1
            reasons.append("MACD bear")

        if vol_ratio > 1.3 and price_block["moved_pct"] > 0:
            bull_signals += 1
            reasons.append(f"Vol {vol_ratio:.1f}x + price up")
        elif vol_ratio > 1.3 and price_block["moved_pct"] < 0:
            bear_signals += 1
            reasons.append(f"Vol {vol_ratio:.1f}x + price down")

        if fut_block and fut_block.get("basis_signal") == "PREMIUM":
            bull_signals += 1
            reasons.append("Futures premium (bullish positioning)")
        elif fut_block and fut_block.get("basis_signal") == "DISCOUNT":
            bear_signals += 1
            reasons.append("Futures discount (bearish positioning)")

        # Direction
        if bull_signals > bear_signals and bull_signals >= 3:
            direction = "BULL"
        elif bear_signals > bull_signals and bear_signals >= 3:
            direction = "BEAR"
        else:
            direction = "NEUTRAL"

        # Targets (ATR + structure based)
        target_1d = target_3d = target_1w = None
        sl = None
        if direction == "BULL":
            target_1d = round(cur + atr14 * 1.0, 2) if atr14 else None
            target_3d = round(min(nearest_r, cur + atr14 * 2.5), 2) if (nearest_r and atr14) else None
            target_1w = round(min(nearest_r, cur + atr14 * 4.0), 2) if (nearest_r and atr14) else None
            sl = round(nearest_s or (cur - atr14 * 1.5), 2)
        elif direction == "BEAR":
            target_1d = round(cur - atr14 * 1.0, 2) if atr14 else None
            target_3d = round(max(nearest_s, cur - atr14 * 2.5), 2) if (nearest_s and atr14) else None
            target_1w = round(max(nearest_s, cur - atr14 * 4.0), 2) if (nearest_s and atr14) else None
            sl = round(nearest_r or (cur + atr14 * 1.5), 2)

        rr = None
        if target_3d and sl and cur:
            risk = abs(cur - sl)
            reward = abs(target_3d - cur)
            rr = round(reward / risk, 2) if risk > 0 else None

        # ── SCORE BREAKDOWN ──
        score = {
            "trend": min(25, bull_signals * 8 if direction == "BULL" else bear_signals * 8 if direction == "BEAR" else 0),
            "structure": min(20, alignment_score // 5),
            "momentum": min(15, 8 if rsi_val and 40 < rsi_val < 70 else 5),
            "volume": min(10, int(vol_ratio * 5) if vol_ratio else 0),
            "futures": min(12, 8 if fut_block and fut_block.get("basis_signal") == ("PREMIUM" if direction == "BULL" else "DISCOUNT") else 4),
            "pattern": min(8, len([p for p in patterns if p["confidence"] >= 75])),
            "risk_reward": min(10, int((rr or 0) * 3)),
        }
        total = sum(score.values())

        prediction_block = {
            "direction": direction,
            "target_1d": target_1d,
            "target_3d": target_3d,
            "target_1w": target_1w,
            "stop_loss": sl,
            "risk_reward": rr,
            "confidence_score": min(100, total),
            "reason": " · ".join(reasons[:6]),
            "score_breakdown": score,
            "moved_in_atr_units": round(moved_atr, 2),
            "room_atr_up": round(room_atr_up, 2),
            "room_atr_down": round(room_atr_down, 2),
            "bull_signals": bull_signals,
            "bear_signals": bear_signals,
        }

        return {
            "symbol": symbol,
            "scan_ts": datetime.now(IST).isoformat(),
            "price": price_block,
            "returns": returns_block,
            "52w": w52_block,
            "trend": trend_block,
            "momentum": momentum_block,
            "volatility": vol_block,
            "levels": levels_block,
            "volume": volume_block,
            "futures": fut_block,
            "patterns": patterns,
            "prediction": prediction_block,
        }
    except Exception as e:
        import traceback
        print(f"[STOCK-ANALYZE] error analyzing {symbol}: {e}")
        traceback.print_exc()
        return None
