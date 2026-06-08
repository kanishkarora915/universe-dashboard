"""
fno_scanner.py — 1-3 day swing trade scanner for NSE F&O universe.

For each F&O stock (~190), pulls 200-day daily history + intraday TFs,
computes structure + S/R + ATR-based move projection, and ranks by
predicted opportunity for the next 1-3 sessions.

OUTPUT (per stock):
  {
    "symbol": "RELIANCE",
    "current_price": 2890.50,
    "prev_close": 2872.10,
    "moved_today_pct": 0.64,

    "trend_200d": "UPTREND",
    "trend_strength": 65,
    "atr_14d": 38.5,
    "atr_pct": 1.33,

    "nearest_support": 2810.0,
    "nearest_resistance": 2950.0,
    "dist_to_support_pct": -2.78,
    "dist_to_resistance_pct": 2.06,

    "structure": {"5m": "UPTREND", "15m": "UPTREND", "1h": "UPTREND"},
    "structure_alignment": "ALL_BULLISH",

    "moved_in_atr_units": 0.48,   # how much price moved today in ATR units
    "remaining_atr_room": 1.5,    # ATR available before hitting next S/R

    "predicted_direction": "BULL",
    "predicted_target": 2992.0,
    "predicted_move_pct": 3.5,
    "predicted_sl": 2810.0,
    "risk_reward": 1.85,
    "confidence_score": 78,

    "reason": "200d UP + 5m+15m+1h aligned bull + 1.5 ATR room to R + RR 1.85",
    "scan_ts_iso": "2026-06-08T08:00:00+05:30",
  }

USAGE:
  Once-per-day scan triggered at 08:00 IST by background daemon
  (also after deploy + manual /api/fno/scan).
  Result cached in memory + persisted to fno_scan.db.

Env:
  FNO_SCAN_ENABLED=on            (default on)
  FNO_SCAN_HOUR_IST=8            (when daily scan fires)
  FNO_TOP_N=15                   (how many bull + bear to return)
"""

from __future__ import annotations

import os
import time
import threading
import sqlite3
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from pathlib import Path

import pytz

IST = pytz.timezone("Asia/Kolkata")


# ── State ─────────────────────────────────────────────────────────────
_scan_cache: Dict = {"results": [], "scan_ts": None, "scan_count": 0}
_cache_lock = threading.Lock()


def _data_dir() -> Path:
    if Path("/data").is_dir():
        return Path("/data")
    return Path(__file__).parent


_FNO_DB = _data_dir() / "fno_scan.db"


def _init_db():
    conn = sqlite3.connect(str(_FNO_DB), timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            scan_ts TEXT,
            symbol TEXT,
            current_price REAL,
            prev_close REAL,
            trend_200d TEXT,
            structure_alignment TEXT,
            predicted_direction TEXT,
            predicted_target REAL,
            predicted_move_pct REAL,
            risk_reward REAL,
            confidence_score INTEGER,
            reason TEXT,
            payload TEXT,
            PRIMARY KEY (scan_ts, symbol)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_ts ON scans(scan_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON scans(symbol)")
    conn.commit()
    conn.close()


_init_db()


# ── Math helpers ──────────────────────────────────────────────────────
def _atr(candles: List[Dict], period: int = 14) -> float:
    """Average True Range (Wilder)."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    return sum(trs[-period:]) / period


def _sma(candles: List[Dict], period: int) -> float:
    if len(candles) < period:
        return 0.0
    closes = [c["close"] for c in candles[-period:]]
    return sum(closes) / len(closes)


def _detect_trend_200d(candles: List[Dict]) -> tuple:
    """Returns (verdict, strength 0-100)."""
    if len(candles) < 50:
        return "UNKNOWN", 0
    closes = [c["close"] for c in candles]
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-min(200, len(closes)):]) / min(200, len(closes))
    cur = closes[-1]
    if sma200 == 0:
        return "UNKNOWN", 0
    dist_pct = abs((cur - sma200) / sma200 * 100)
    strength = min(100, int(dist_pct * 10))
    if cur > sma200 and sma50 > sma200:
        return "UPTREND", strength
    if cur < sma200 and sma50 < sma200:
        return "DOWNTREND", strength
    return "RANGE", strength


def _find_swings(candles: List[Dict], fractal: int = 2) -> tuple:
    """Bill Williams fractal swing detection. Returns (highs, lows) as price lists."""
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


def _cluster_levels(levels: List[float], tolerance_pct: float = 0.8) -> List[float]:
    """Cluster nearby price levels within tolerance%. Returns centroids."""
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
    # Keep only clusters with 2+ touches (confirmed S/R)
    return [round(sum(c) / len(c), 2) for c in clusters if len(c) >= 2]


def _detect_tf_structure(candles: List[Dict], fractal: int = 2) -> str:
    """Returns UPTREND / DOWNTREND / CHOP based on last few swing highs/lows."""
    highs, lows = _find_swings(candles, fractal)
    if len(highs) < 2 or len(lows) < 2:
        return "UNKNOWN"
    last_2_highs_asc = highs[-1] > highs[-2]
    last_2_lows_asc = lows[-1] > lows[-2]
    if last_2_highs_asc and last_2_lows_asc:
        return "UPTREND"
    if not last_2_highs_asc and not last_2_lows_asc:
        return "DOWNTREND"
    return "CHOP"


# ── Per-stock scan ────────────────────────────────────────────────────
def scan_one_deep(kite, stock: Dict, fast: bool = True) -> Optional[Dict]:
    """Comprehensive deep-dive analysis via stock_analyzer.
    Returns ~60-field dict — Use scan_one() for legacy summary format.
    fast=True (default for bulk scan): skips intraday TF + futures fetch.
    """
    try:
        from stock_analyzer import analyze
        return analyze(kite, stock, fast=fast)
    except Exception as e:
        print(f"[FNO-SCAN] deep analyze error for {stock.get('symbol')}: {e}")
        return None


def scan_one(kite, stock: Dict) -> Optional[Dict]:
    """Scan one stock — pull history, compute everything, return result dict.
    Legacy summary format. Use scan_one_deep for full analysis."""
    try:
        from historical_loader import load_index_history

        symbol = stock["symbol"]
        token = stock["token"]
        if not token:
            return None

        # Override SPOT_INSTRUMENT_TOKENS isn't possible; we call kite directly
        now_ist = datetime.now(IST)
        from_dt = now_ist - timedelta(days=300)  # 300 calendar days ≈ 200 trading

        try:
            raw_day = kite.historical_data(
                instrument_token=token,
                from_date=from_dt.strftime("%Y-%m-%d"),
                to_date=now_ist.strftime("%Y-%m-%d"),
                interval="day",
            )
        except Exception as e:
            print(f"[FNO-SCAN] {symbol}: day fetch failed: {e}")
            return None

        if not raw_day or len(raw_day) < 30:
            return None

        candles_day = [{
            "ts": str(c.get("date", "")),
            "open": float(c.get("open", 0) or 0),
            "high": float(c.get("high", 0) or 0),
            "low": float(c.get("low", 0) or 0),
            "close": float(c.get("close", 0) or 0),
            "volume": int(c.get("volume", 0) or 0),
        } for c in raw_day]

        # Trend (200d)
        trend, strength = _detect_trend_200d(candles_day)

        # ATR-14d
        atr = _atr(candles_day, 14)
        cur = candles_day[-1]["close"]
        prev_close = candles_day[-2]["close"] if len(candles_day) >= 2 else cur
        atr_pct = (atr / cur * 100) if cur > 0 else 0
        moved_today_pct = ((cur - prev_close) / prev_close * 100) if prev_close > 0 else 0

        # S/R zones from last 60 days
        recent_60 = candles_day[-min(60, len(candles_day)):]
        highs, lows = _find_swings(recent_60, fractal=2)
        resistance_zones = _cluster_levels(highs)
        support_zones = _cluster_levels(lows)

        # Nearest S/R
        nearest_s = max([s for s in support_zones if s < cur], default=None)
        nearest_r = min([r for r in resistance_zones if r > cur], default=None)
        dist_s_pct = ((cur - nearest_s) / cur * 100) if nearest_s else None
        dist_r_pct = ((nearest_r - cur) / cur * 100) if nearest_r else None

        # Multi-TF structure — re-use day candles for 15m proxy doesn't make sense.
        # For F&O scanner the day-level structure is enough for 1-3 day swing.
        # We compute structure on day candles (recent 30) as a proxy for "trend"
        # and use the 200d SMA for direction confirmation.
        day_struct = _detect_tf_structure(candles_day[-30:])

        # ATR-based move quantification
        # How much has it moved today, in ATR units?
        moved_today_atr = abs(cur - prev_close) / atr if atr > 0 else 0
        # How much room before hitting next S/R, in ATR units?
        if trend == "UPTREND" and nearest_r:
            remaining_atr = (nearest_r - cur) / atr if atr > 0 else 0
        elif trend == "DOWNTREND" and nearest_s:
            remaining_atr = (cur - nearest_s) / atr if atr > 0 else 0
        else:
            remaining_atr = 0

        # Predict
        # Bull setup: UPTREND + struct UPTREND + room to resistance + ATR not exhausted
        # Bear setup: DOWNTREND + struct DOWNTREND + room to support + ATR not exhausted
        direction = "NEUTRAL"
        target = None
        sl = None
        confidence = 0
        reasons = []

        if trend == "UPTREND" and day_struct == "UPTREND":
            direction = "BULL"
            confidence += 30
            reasons.append("200d UPTREND + recent struct UP")
            if remaining_atr >= 1.0 and nearest_r:
                target = nearest_r
                sl = nearest_s or (cur - atr * 1.5)
                confidence += 20
                reasons.append(f"{remaining_atr:.1f} ATR room to R")
            if moved_today_atr < 1.0:
                confidence += 15
                reasons.append("today move < 1 ATR (not exhausted)")
            if strength >= 50:
                confidence += 15
                reasons.append(f"trend strength {strength}")

        elif trend == "DOWNTREND" and day_struct == "DOWNTREND":
            direction = "BEAR"
            confidence += 30
            reasons.append("200d DOWNTREND + recent struct DOWN")
            if remaining_atr >= 1.0 and nearest_s:
                target = nearest_s
                sl = nearest_r or (cur + atr * 1.5)
                confidence += 20
                reasons.append(f"{remaining_atr:.1f} ATR room to S")
            if moved_today_atr < 1.0:
                confidence += 15
                reasons.append("today move < 1 ATR (not exhausted)")
            if strength >= 50:
                confidence += 15
                reasons.append(f"trend strength {strength}")

        # Risk-reward
        rr = None
        predicted_move_pct = None
        if target and sl and cur > 0:
            risk = abs(cur - sl)
            reward = abs(target - cur)
            if risk > 0:
                rr = round(reward / risk, 2)
                if rr >= 1.5:
                    confidence += 10
                    reasons.append(f"RR {rr}")
            predicted_move_pct = ((target - cur) / cur * 100)

        confidence = min(100, confidence)

        return {
            "symbol": symbol,
            "current_price": round(cur, 2),
            "prev_close": round(prev_close, 2),
            "moved_today_pct": round(moved_today_pct, 2),
            "trend_200d": trend,
            "trend_strength": strength,
            "atr_14d": round(atr, 2),
            "atr_pct": round(atr_pct, 2),
            "nearest_support": nearest_s,
            "nearest_resistance": nearest_r,
            "dist_to_support_pct": round(dist_s_pct, 2) if dist_s_pct is not None else None,
            "dist_to_resistance_pct": round(dist_r_pct, 2) if dist_r_pct is not None else None,
            "day_structure": day_struct,
            "moved_in_atr_units": round(moved_today_atr, 2),
            "remaining_atr_room": round(remaining_atr, 2),
            "predicted_direction": direction,
            "predicted_target": round(target, 2) if target else None,
            "predicted_move_pct": round(predicted_move_pct, 2) if predicted_move_pct else None,
            "predicted_sl": round(sl, 2) if sl else None,
            "risk_reward": rr,
            "confidence_score": confidence,
            "reason": " · ".join(reasons),
        }
    except Exception as e:
        print(f"[FNO-SCAN] error scanning {stock.get('symbol')}: {e}")
        return None


# ── Full scan ─────────────────────────────────────────────────────────
def run_full_scan(kite, max_symbols: Optional[int] = None) -> Dict:
    """Run a full scan of the F&O universe. Returns ranked results."""
    if kite is None:
        return {"error": "no kite", "results": []}

    from fno_universe import get_fno_symbols
    universe = get_fno_symbols(kite)
    if not universe:
        return {"error": "no universe", "results": []}

    if max_symbols:
        universe = universe[:max_symbols]

    use_deep = os.environ.get("FNO_DEEP_ANALYSIS", "on").lower() != "off"
    print(f"[FNO-SCAN] starting {'DEEP' if use_deep else 'summary'} scan of {len(universe)} stocks")
    start = time.time()
    results = []

    for i, stock in enumerate(universe):
        if use_deep:
            r_deep = scan_one_deep(kite, stock)
            # Flatten deep analysis into the summary format for ranking compatibility
            if r_deep:
                pred = r_deep.get("prediction", {})
                trend = r_deep.get("trend", {})
                lvl = r_deep.get("levels", {})
                p = r_deep.get("price", {})
                vol = r_deep.get("volatility", {})
                r = {
                    "symbol": r_deep["symbol"],
                    "current_price": p.get("price"),
                    "prev_close": p.get("prev_close"),
                    "moved_today_pct": p.get("moved_pct"),
                    "trend_200d": trend.get("200d_trend", "RANGE"),
                    "trend_strength": trend.get("200d_strength", 0),
                    "atr_14d": vol.get("atr_14d"),
                    "atr_pct": vol.get("atr_pct"),
                    "nearest_support": lvl.get("nearest_support"),
                    "nearest_resistance": lvl.get("nearest_resistance"),
                    "dist_to_support_pct": lvl.get("dist_to_support_pct"),
                    "dist_to_resistance_pct": lvl.get("dist_to_resistance_pct"),
                    "day_structure": trend.get("struct_1d", "UNKNOWN"),
                    "moved_in_atr_units": pred.get("moved_in_atr_units"),
                    "remaining_atr_room": pred.get("room_atr_up") or pred.get("room_atr_down"),
                    "predicted_direction": pred.get("direction"),
                    "predicted_target": pred.get("target_3d"),
                    "predicted_move_pct": round(((pred.get("target_3d") or 0) - p.get("price", 0)) / p.get("price", 1) * 100, 2) if pred.get("target_3d") and p.get("price") else None,
                    "predicted_sl": pred.get("stop_loss"),
                    "risk_reward": pred.get("risk_reward"),
                    "confidence_score": pred.get("confidence_score"),
                    "reason": pred.get("reason"),
                    "_deep": r_deep,  # full data attached
                }
                results.append(r)
        else:
            r = scan_one(kite, stock)
            if r:
                results.append(r)
        if (i + 1) % 20 == 0:
            print(f"[FNO-SCAN] progress: {i+1}/{len(universe)}")
        # Tiny pause to not hammer Kite REST
        time.sleep(0.05)

    duration = time.time() - start
    scan_ts = datetime.now(IST).isoformat()

    # Persist to DB
    try:
        conn = sqlite3.connect(str(_FNO_DB), timeout=10)
        for r in results:
            import json
            conn.execute("""
                INSERT OR REPLACE INTO scans
                (scan_ts, symbol, current_price, prev_close, trend_200d,
                 structure_alignment, predicted_direction, predicted_target,
                 predicted_move_pct, risk_reward, confidence_score, reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                scan_ts, r["symbol"], r["current_price"], r["prev_close"],
                r["trend_200d"], r.get("day_structure", ""),
                r["predicted_direction"], r["predicted_target"],
                r["predicted_move_pct"], r["risk_reward"],
                r["confidence_score"], r["reason"], json.dumps(r),
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[FNO-SCAN] db persist failed: {e}")

    with _cache_lock:
        _scan_cache["results"] = results
        _scan_cache["scan_ts"] = scan_ts
        _scan_cache["scan_count"] = _scan_cache.get("scan_count", 0) + 1

    print(f"[FNO-SCAN] complete: {len(results)} stocks scanned in {duration:.0f}s")

    # Telegram alert
    try:
        import telegram_alerts
        if telegram_alerts.is_enabled():
            top_n = int(os.environ.get("FNO_TOP_N", "10"))
            ranked = ranked_watchlist(top_n=top_n)
            bull = ranked["bullish"][:5]
            bear = ranked["bearish"][:5]
            msg = "🔍 *F&O Scan Complete*\n"
            msg += f"Scanned: {len(results)} stocks\n\n"
            if bull:
                msg += "🟢 *Top Bullish:*\n"
                for s in bull:
                    msg += f"  • {s['symbol']} @ ₹{s['current_price']} → ₹{s['predicted_target']} ({s['confidence_score']}%)\n"
            if bear:
                msg += "\n🔴 *Top Bearish:*\n"
                for s in bear:
                    msg += f"  • {s['symbol']} @ ₹{s['current_price']} → ₹{s['predicted_target']} ({s['confidence_score']}%)\n"
            telegram_alerts.send(msg, key="fno_scan_done")
    except Exception:
        pass

    return {
        "scan_ts": scan_ts,
        "duration_sec": round(duration, 1),
        "scanned": len(results),
        "results": results,
    }


def ranked_watchlist(top_n: int = 15) -> Dict:
    """Return ranked top-N bullish + bearish from latest scan."""
    with _cache_lock:
        results = _scan_cache.get("results") or []
        scan_ts = _scan_cache.get("scan_ts")

    if not results:
        return {"bullish": [], "bearish": [], "scan_ts": None}

    bullish = sorted(
        [r for r in results if r["predicted_direction"] == "BULL" and r["confidence_score"] >= 40],
        key=lambda r: (-r["confidence_score"], -(r.get("risk_reward") or 0)),
    )[:top_n]
    bearish = sorted(
        [r for r in results if r["predicted_direction"] == "BEAR" and r["confidence_score"] >= 40],
        key=lambda r: (-r["confidence_score"], -(r.get("risk_reward") or 0)),
    )[:top_n]
    return {"bullish": bullish, "bearish": bearish, "scan_ts": scan_ts}


def get_stock_detail(symbol: str) -> Optional[Dict]:
    """Get detail for one symbol from latest scan."""
    symbol = symbol.upper()
    with _cache_lock:
        results = _scan_cache.get("results") or []
    for r in results:
        if r["symbol"] == symbol:
            return r
    return None


def latest_scan_meta() -> Dict:
    """Metadata about the latest scan."""
    with _cache_lock:
        return {
            "scan_ts": _scan_cache.get("scan_ts"),
            "scanned_count": len(_scan_cache.get("results") or []),
            "total_scans": _scan_cache.get("scan_count", 0),
        }


# ── Daily background scan ────────────────────────────────────────────
def start_daily_scan_thread(kite_getter):
    """Spawn a daemon that runs the full scan once per day at 08:00 IST."""
    def _loop():
        last_scan_date = None
        print(f"[FNO-SCAN] daemon armed — fires at 08:00 IST daily")
        # Initial scan after 2 min (let engine boot fully)
        time.sleep(120)
        try:
            kite = kite_getter()
            if kite is not None:
                print("[FNO-SCAN] initial post-boot scan")
                run_full_scan(kite)
                last_scan_date = datetime.now(IST).strftime("%Y-%m-%d")
        except Exception as e:
            print(f"[FNO-SCAN] initial scan err: {e}")

        scan_hour = int(os.environ.get("FNO_SCAN_HOUR_IST", "8"))
        while True:
            try:
                now = datetime.now(IST)
                today_iso = now.strftime("%Y-%m-%d")
                if (now.hour == scan_hour
                        and 0 <= now.minute < 10
                        and last_scan_date != today_iso):
                    kite = kite_getter()
                    if kite is not None:
                        print(f"[FNO-SCAN] daily {scan_hour}:00 scan firing")
                        run_full_scan(kite)
                        last_scan_date = today_iso
            except Exception as e:
                print(f"[FNO-SCAN] loop err: {e}")
            time.sleep(60)

    t = threading.Thread(target=_loop, daemon=True, name="fno-scanner")
    t.start()
    return t
