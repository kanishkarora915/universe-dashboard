"""
OI Minute Capture
─────────────────
Per-strike per-minute snapshots of CE/PE OI + LTP for both indices.
Enables Smart Money Detector to identify slow institutional accumulation
patterns (the 100-300 lots/min drip-fed positioning of FIIs/funds).

Captures every 60s. Each snapshot stores:
  ts, idx, strike, ce_oi, ce_ltp, pe_oi, pe_ltp

Retention: 1 trading day (auto-pruned at 9 AM IST). Heavy table — at
peak ~100 strikes × 2 indices × 375 minutes = 75,000 rows/day.

Used by:
  - smart_money_detector (analyzes per-strike trends)
  - Optionally: any other module needing per-minute OI history
"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = str(_DATA_DIR / "oi_minute.db")

# Capture only NTM range to keep DB small (ATM ±10 strikes covers ±1% move)
NTM_RANGE = 10
# Filter strikes with too-low OI (noise)
MIN_OI_TO_CAPTURE = 1000


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strike_minute (
            ts REAL,
            idx TEXT,
            strike INTEGER,
            ce_oi REAL,
            ce_ltp REAL,
            pe_oi REAL,
            pe_ltp REAL,
            spot REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_ts ON strike_minute(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_idx_strike ON strike_minute(idx, strike, ts)")
    conn.commit()
    conn.close()


def capture_pulse(engine):
    """Take a snapshot of NTM strikes for both indices. Call every 60s."""
    _init_db()
    now_ts = time.time()
    rows_inserted = 0
    conn = sqlite3.connect(DB_PATH)

    for idx in ("NIFTY", "BANKNIFTY"):
        try:
            spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
            spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
            chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
            if spot <= 0 or not chain:
                continue

            gap = 50 if idx == "NIFTY" else 100
            atm = round(spot / gap) * gap

            # Only NTM strikes (ATM ± NTM_RANGE)
            for offset in range(-NTM_RANGE, NTM_RANGE + 1):
                strike = atm + offset * gap
                sd = chain.get(strike) or chain.get(str(strike)) or {}
                if not isinstance(sd, dict):
                    continue
                ce_oi = sd.get("ce_oi", 0) or 0
                pe_oi = sd.get("pe_oi", 0) or 0
                ce_ltp = sd.get("ce_ltp", 0) or 0
                pe_ltp = sd.get("pe_ltp", 0) or 0
                # Skip if both sides empty
                if ce_oi < MIN_OI_TO_CAPTURE and pe_oi < MIN_OI_TO_CAPTURE:
                    continue
                conn.execute("""
                    INSERT INTO strike_minute
                    (ts, idx, strike, ce_oi, ce_ltp, pe_oi, pe_ltp, spot)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (now_ts, idx, int(strike), ce_oi, ce_ltp, pe_oi, pe_ltp, spot))
                rows_inserted += 1
        except Exception as e:
            print(f"[OI-MIN] {idx} capture err: {e}")

    # Prune anything older than 24 hrs
    conn.execute("DELETE FROM strike_minute WHERE ts < ?", (now_ts - 24 * 3600,))
    conn.commit()
    conn.close()
    return {"inserted": rows_inserted, "ts": now_ts}


# Per-strike-history cache (in-memory). Watcher pulse calls this every
# 3-30s per open trade — without cache that's a DB hit per pulse per
# trade. With 30s TTL the read load drops by ~10x.
_strike_hist_cache: Dict[str, Dict] = {}  # key: f"{idx}:{strike}:{minutes}" → {ts, data}
_STRIKE_HIST_TTL = 30  # seconds


def get_strike_history(idx: str, strike: int, minutes: int = 60) -> List[Dict]:
    """Per-strike per-minute history (last N minutes). Cached 30s."""
    cache_key = f"{idx.upper()}:{int(strike)}:{minutes}"
    cached = _strike_hist_cache.get(cache_key)
    now = time.time()
    if cached and (now - cached["ts"]) < _STRIKE_HIST_TTL:
        return cached["data"]

    _init_db()
    cutoff = now - minutes * 60
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT ts, ce_oi, ce_ltp, pe_oi, pe_ltp, spot
        FROM strike_minute
        WHERE idx=? AND strike=? AND ts >= ?
        ORDER BY ts ASC
    """, (idx.upper(), int(strike), cutoff)).fetchall()
    conn.close()
    data = [{
        "ts": r[0], "ce_oi": r[1], "ce_ltp": r[2],
        "pe_oi": r[3], "pe_ltp": r[4], "spot": r[5],
    } for r in rows]
    _strike_hist_cache[cache_key] = {"ts": now, "data": data}
    return data


def get_all_strikes_for_idx(idx: str, minutes: int = 30) -> Dict[int, List[Dict]]:
    """All strikes' history for an index (used by smart money analyzer)."""
    _init_db()
    cutoff = time.time() - minutes * 60
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT ts, strike, ce_oi, ce_ltp, pe_oi, pe_ltp, spot
        FROM strike_minute
        WHERE idx=? AND ts >= ?
        ORDER BY strike ASC, ts ASC
    """, (idx.upper(), cutoff)).fetchall()
    conn.close()
    by_strike: Dict[int, List[Dict]] = {}
    for r in rows:
        s = r[1]
        by_strike.setdefault(s, []).append({
            "ts": r[0], "ce_oi": r[2], "ce_ltp": r[3],
            "pe_oi": r[4], "pe_ltp": r[5], "spot": r[6],
        })
    return by_strike


def get_capture_stats() -> Dict:
    """Health check stats."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM strike_minute").fetchone()[0]
    last = conn.execute("SELECT MAX(ts) FROM strike_minute").fetchone()[0]
    by_idx = {}
    for idx in ("NIFTY", "BANKNIFTY"):
        n = conn.execute(
            "SELECT COUNT(DISTINCT strike) FROM strike_minute WHERE idx=? AND ts > ?",
            (idx, time.time() - 3600)
        ).fetchone()[0]
        by_idx[idx] = n
    conn.close()
    return {
        "total_rows": total,
        "last_capture_ts": last,
        "last_capture_age_sec": round(time.time() - last, 1) if last else None,
        "strikes_active_last_hour": by_idx,
    }
