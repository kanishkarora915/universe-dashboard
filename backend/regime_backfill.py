"""
regime_backfill — one-time script to backfill regime_at_entry data for
historical trades (both scalper_trades and trades tables).

For each trade with empty regime_at_entry:
  1. Parse entry_time
  2. Fetch Kite minute candles for ~22 min before entry (20-min window + buffer)
  3. Convert candles to "spot_history" tick-like list expected by
     entry_filters.detect_market_regime()
  4. Fetch 5m + 15m + 1h candles around entry day for price_structure
  5. Run detect_market_regime() + detect_structure() at the entry time
  6. UPDATE the trade row with regime, range_pct, candle_pct, structure_5m/15m/1h

Run from backend dir:
  python3 regime_backfill.py
"""
from __future__ import annotations
import os
import sys
import sqlite3
import time as _time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")


# ───────────────────────────────────────────────────────────
# Kite setup — reuse existing auth flow
# ───────────────────────────────────────────────────────────

def _make_kite():
    """Build authenticated Kite client using the same access_token file the
    main app uses (handled by autologin / shared session)."""
    try:
        from kiteconnect import KiteConnect
    except Exception as e:
        print(f"[BACKFILL] kiteconnect import failed: {e}")
        return None

    api_key = os.environ.get("KITE_API_KEY")
    if not api_key:
        print("[BACKFILL] KITE_API_KEY not set in env")
        return None
    kc = KiteConnect(api_key=api_key)

    # Token file paths — match what main app uses
    token_paths = [
        "/data/access_token.json",
        os.path.join(os.path.dirname(__file__), "access_token.json"),
    ]
    token = None
    for p in token_paths:
        if os.path.exists(p):
            try:
                import json
                with open(p) as f:
                    blob = json.load(f)
                token = blob.get("access_token") or blob.get("token")
                if token:
                    print(f"[BACKFILL] loaded access_token from {p}")
                    break
            except Exception as e:
                print(f"[BACKFILL] could not read {p}: {e}")
    if not token:
        print("[BACKFILL] no access_token found — set KITE_ACCESS_TOKEN or place /data/access_token.json")
        env_tok = os.environ.get("KITE_ACCESS_TOKEN")
        if env_tok:
            token = env_tok
        else:
            return None
    kc.set_access_token(token)
    return kc


SPOT_TOKENS = {"NIFTY": 256265, "BANKNIFTY": 260105}


# ───────────────────────────────────────────────────────────
# Per-day Kite minute history cache (one fetch per (idx, date) pair)
# ───────────────────────────────────────────────────────────

_day_cache: Dict = {}  # (idx, date_str) -> list of candle dicts


def _fetch_day_minute(kite, idx: str, day: datetime) -> List[Dict]:
    """Fetch ALL 1-minute candles for one trading day for `idx`."""
    key = (idx, day.strftime("%Y-%m-%d"))
    if key in _day_cache:
        return _day_cache[key]
    token = SPOT_TOKENS.get(idx)
    if not token:
        return []
    try:
        # Fetch 9:00-15:30 IST window
        from_dt = day.replace(hour=9, minute=0, second=0, microsecond=0)
        to_dt = day.replace(hour=15, minute=30, second=0, microsecond=0)
        raw = kite.historical_data(
            instrument_token=token,
            from_date=from_dt.strftime("%Y-%m-%d %H:%M:%S"),
            to_date=to_dt.strftime("%Y-%m-%d %H:%M:%S"),
            interval="minute",
        )
        candles = []
        for c in raw or []:
            ts_val = c.get("date")
            ts_str = ts_val.isoformat() if hasattr(ts_val, "isoformat") else str(ts_val)
            candles.append({
                "ts": ts_str,
                "open": float(c.get("open", 0) or 0),
                "high": float(c.get("high", 0) or 0),
                "low": float(c.get("low", 0) or 0),
                "close": float(c.get("close", 0) or 0),
                "volume": int(c.get("volume", 0) or 0),
            })
        _day_cache[key] = candles
        print(f"[BACKFILL] fetched {len(candles)} 1m candles for {idx} {key[1]}")
        # Light rate-limit guard
        _time.sleep(0.3)
        return candles
    except Exception as e:
        print(f"[BACKFILL] fetch failed {idx} {key[1]}: {e}")
        _day_cache[key] = []
        return []


_struct_cache: Dict = {}  # (idx, interval, date_str) -> candles


def _fetch_struct_candles(kite, idx: str, interval: str, ref_day: datetime, lookback_days: int) -> List[Dict]:
    """Fetch larger-timeframe candles (5min/15min/60min) ending at ref_day."""
    key = (idx, interval, ref_day.strftime("%Y-%m-%d"))
    if key in _struct_cache:
        return _struct_cache[key]
    token = SPOT_TOKENS.get(idx)
    if not token:
        return []
    try:
        from_dt = ref_day - timedelta(days=lookback_days)
        to_dt = ref_day + timedelta(days=1)
        raw = kite.historical_data(
            instrument_token=token,
            from_date=from_dt.strftime("%Y-%m-%d"),
            to_date=to_dt.strftime("%Y-%m-%d"),
            interval=interval,
        )
        candles = []
        for c in raw or []:
            ts_val = c.get("date")
            ts_str = ts_val.isoformat() if hasattr(ts_val, "isoformat") else str(ts_val)
            candles.append({
                "ts": ts_str,
                "open": float(c.get("open", 0) or 0),
                "high": float(c.get("high", 0) or 0),
                "low": float(c.get("low", 0) or 0),
                "close": float(c.get("close", 0) or 0),
                "volume": int(c.get("volume", 0) or 0),
            })
        _struct_cache[key] = candles
        _time.sleep(0.3)
        return candles
    except Exception as e:
        print(f"[BACKFILL] fetch struct failed {idx} {interval} {key[2]}: {e}")
        _struct_cache[key] = []
        return []


# ───────────────────────────────────────────────────────────
# Regime computation for a specific entry timestamp
# ───────────────────────────────────────────────────────────

def _candles_to_spot_history(candles: List[Dict], entry_dt: datetime, window_min: int = 22) -> List[Dict]:
    """Convert 1-min OHLC candles into the tick-like spot_history list
    expected by detect_market_regime. Uses close prices, timestamps as 't'."""
    cutoff = entry_dt - timedelta(minutes=window_min)
    out = []
    for c in candles:
        try:
            ts = datetime.fromisoformat(c["ts"]) if isinstance(c["ts"], str) else c["ts"]
            if ts.tzinfo is None:
                ts = IST.localize(ts)
            if cutoff <= ts <= entry_dt:
                out.append({"t": ts.isoformat(), "ltp": c["close"]})
        except Exception:
            continue
    return out


def _compute_regime_at(entry_dt: datetime, spot_history: List[Dict],
                       tight_range_pct: float = 0.4,
                       breakout_candle_pct: float = 1.5) -> Dict:
    """REPLICATE detect_market_regime() but use entry_dt as "now" instead of
    ist_now(). The live function compares against current time which kills
    historical backfill — every old tick fails cutoff and returns NORMAL.
    """
    if not spot_history or len(spot_history) < 10:
        return {"regime": "NORMAL", "range_pct": 0, "candle_pct": 0,
                "tight_before": False, "reason": "insufficient history"}

    cutoff_20 = entry_dt - timedelta(minutes=20)
    recent_20 = []
    for h in spot_history:
        try:
            t = datetime.fromisoformat(h["t"]) if isinstance(h["t"], str) else h["t"]
            if t.tzinfo is None:
                t = IST.localize(t)
            if cutoff_20 <= t <= entry_dt:
                recent_20.append(h)
        except Exception:
            continue
    if len(recent_20) < 10:
        return {"regime": "NORMAL", "range_pct": 0, "candle_pct": 0,
                "tight_before": False, "reason": f"<10 ticks ({len(recent_20)}) in 20min before entry"}

    ltps_20 = [h["ltp"] for h in recent_20 if h.get("ltp", 0) > 0]
    if not ltps_20:
        return {"regime": "NORMAL", "range_pct": 0, "candle_pct": 0,
                "tight_before": False, "reason": "no valid ltps"}

    high_20 = max(ltps_20)
    low_20 = min(ltps_20)
    avg_20 = sum(ltps_20) / len(ltps_20)
    if avg_20 <= 0:
        return {"regime": "NORMAL", "range_pct": 0, "candle_pct": 0,
                "tight_before": False, "reason": "bad avg"}
    range_pct = ((high_20 - low_20) / avg_20) * 100

    cutoff_1m = entry_dt - timedelta(minutes=1)
    last_1m = []
    for h in spot_history:
        try:
            t = datetime.fromisoformat(h["t"]) if isinstance(h["t"], str) else h["t"]
            if t.tzinfo is None:
                t = IST.localize(t)
            if cutoff_1m <= t <= entry_dt:
                last_1m.append(h)
        except Exception:
            continue
    candle_pct = 0.0
    if last_1m and len(last_1m) >= 2:
        c_first = last_1m[0]["ltp"]
        c_last = last_1m[-1]["ltp"]
        if c_first > 0:
            candle_pct = ((c_last - c_first) / c_first) * 100

    cutoff_pre_start = entry_dt - timedelta(minutes=20)
    cutoff_pre_end = entry_dt - timedelta(minutes=1)
    pre_candle = []
    for h in spot_history:
        try:
            t = datetime.fromisoformat(h["t"]) if isinstance(h["t"], str) else h["t"]
            if t.tzinfo is None:
                t = IST.localize(t)
            if cutoff_pre_start <= t < cutoff_pre_end:
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

    if tight_before and abs(candle_pct) >= breakout_candle_pct:
        regime = "BREAKOUT"
    elif range_pct < tight_range_pct and abs(candle_pct) < 0.3:
        regime = "CHOP"
    elif range_pct > 1.0:
        regime = "TRENDING"
    else:
        regime = "NORMAL"
    return {
        "regime": regime,
        "range_pct": round(range_pct, 3),
        "candle_pct": round(candle_pct, 3),
        "tight_before": tight_before,
        "reason": "computed at entry_dt",
    }


def _candles_before(candles: List[Dict], ref_dt: datetime, count_back: int) -> List[Dict]:
    """Return the most recent `count_back` candles strictly before ref_dt."""
    keep = []
    for c in candles:
        try:
            ts = datetime.fromisoformat(c["ts"]) if isinstance(c["ts"], str) else c["ts"]
            if ts.tzinfo is None:
                ts = IST.localize(ts)
            if ts <= ref_dt:
                keep.append(c)
        except Exception:
            continue
    return keep[-count_back:] if len(keep) > count_back else keep


# ───────────────────────────────────────────────────────────
# Main backfill loop
# ───────────────────────────────────────────────────────────

def backfill_one_db(db_path: str, table: str):
    """Backfill a single SQLite DB / table."""
    if not os.path.exists(db_path):
        print(f"[BACKFILL] DB not found: {db_path} — skipping")
        return
    print(f"\n========== {db_path} :: {table} ==========")
    kite = _make_kite()
    if kite is None:
        print("[BACKFILL] no Kite client — aborting")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Make sure new columns exist (idempotent ALTERs)
    new_cols = [
        ("regime_at_entry", "TEXT DEFAULT ''"),
        ("range_pct_at_entry", "REAL DEFAULT 0"),
        ("candle_pct_at_entry", "REAL DEFAULT 0"),
        ("structure_5m", "TEXT DEFAULT ''"),
        ("structure_15m", "TEXT DEFAULT ''"),
        ("structure_1h", "TEXT DEFAULT ''"),
    ]
    existing = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    for col, defn in new_cols:
        if col not in existing:
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
                print(f"[BACKFILL] added column {col}")
            except Exception as e:
                print(f"[BACKFILL] ALTER {col} failed: {e}")
    conn.commit()

    # Reset rows polluted by the v1 bug (regime='NORMAL' with range_pct=0 came
    # from detect_market_regime using ist_now() against historical timestamps).
    try:
        cur.execute(
            f"UPDATE {table} SET regime_at_entry='' "
            f"WHERE regime_at_entry='NORMAL' AND range_pct_at_entry=0 "
            f"AND status NOT IN ('OPEN','PENDING')"
        )
        n_reset = cur.rowcount
        conn.commit()
        if n_reset:
            print(f"[BACKFILL] reset {n_reset} v1-bug rows for re-compute")
    except Exception as e:
        print(f"[BACKFILL] reset step failed (ignoring): {e}")

    rows = cur.execute(
        f"SELECT id, entry_time, idx FROM {table} "
        f"WHERE (regime_at_entry IS NULL OR regime_at_entry='') "
        f"AND entry_time IS NOT NULL ORDER BY id"
    ).fetchall()
    total = len(rows)
    print(f"[BACKFILL] {total} rows need backfill")
    if total == 0:
        conn.close()
        return

    # Defer imports until after DB open so script can run even if backend not importable
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import price_structure as ps

    ok = 0
    fail = 0
    for i, row in enumerate(rows, 1):
        try:
            entry_iso = row["entry_time"]
            idx = row["idx"]
            entry_dt = datetime.fromisoformat(entry_iso)
            if entry_dt.tzinfo is None:
                entry_dt = IST.localize(entry_dt)
            day_anchor = entry_dt.replace(hour=0, minute=0, second=0, microsecond=0)

            # Per-trade work
            minute_candles = _fetch_day_minute(kite, idx, day_anchor)
            spot_hist = _candles_to_spot_history(minute_candles, entry_dt)
            regime_info = _compute_regime_at(entry_dt, spot_hist) if spot_hist else {
                "regime": "", "range_pct": 0, "candle_pct": 0}

            # Structure 5m / 15m / 1h
            s5 = ps.detect_structure(_candles_before(
                _fetch_struct_candles(kite, idx, "5minute", day_anchor, lookback_days=2),
                entry_dt, 60))
            s15 = ps.detect_structure(_candles_before(
                _fetch_struct_candles(kite, idx, "15minute", day_anchor, lookback_days=2),
                entry_dt, 30))
            s1h = ps.detect_structure(_candles_before(
                _fetch_struct_candles(kite, idx, "60minute", day_anchor, lookback_days=5),
                entry_dt, 20))

            cur.execute(
                f"UPDATE {table} SET regime_at_entry=?, range_pct_at_entry=?, "
                f"candle_pct_at_entry=?, structure_5m=?, structure_15m=?, structure_1h=? "
                f"WHERE id=?",
                (
                    regime_info.get("regime", ""),
                    float(regime_info.get("range_pct") or 0),
                    float(regime_info.get("candle_pct") or 0),
                    s5.get("verdict", ""),
                    s15.get("verdict", ""),
                    s1h.get("verdict", ""),
                    row["id"],
                )
            )
            if i % 25 == 0:
                conn.commit()
                print(f"[BACKFILL] {i}/{total} done — last: id={row['id']} idx={idx} "
                      f"regime={regime_info.get('regime')} s5={s5.get('verdict')} "
                      f"s15={s15.get('verdict')} s1h={s1h.get('verdict')}")
            ok += 1
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"[BACKFILL] row id={row['id']} failed: {e}")

    conn.commit()
    conn.close()
    print(f"[BACKFILL] done — ok={ok} fail={fail}")


# ───────────────────────────────────────────────────────────
# Entry point
# ───────────────────────────────────────────────────────────

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    # Prefer /data (Render persistent disk) when present, fall back to backend dir
    candidates = [
        ("/data/trades.db", "trades"),
        (os.path.join(base, "trades.db"), "trades"),
        ("/data/scalper_trades.db", "scalper_trades"),
        (os.path.join(base, "scalper_trades.db"), "scalper_trades"),
    ]
    seen = set()
    for path, table in candidates:
        if path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            backfill_one_db(path, table)
        else:
            print(f"[BACKFILL] skip missing: {path}")


if __name__ == "__main__":
    main()
