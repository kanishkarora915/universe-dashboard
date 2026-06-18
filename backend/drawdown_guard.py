"""
drawdown_guard — protect today's profits from "give-back" pattern.

USER OBSERVATION 2026-06-18:
  "PnL tab ne morning mein ache profits banae, fir loss pe loss
   krke sab khaarab krdiya, sirf 20k profit bacha."

PATTERN:
  09:22-09:55 — Morning rally caught, ₹+1L gross
  09:55-10:40 — Trend exhausted but kept buying same direction
  Result    — Gave back ₹80k, net ₹+20k

FIX:
  Track today's PEAK realized P&L per mode.
  If current P&L falls below PEAK × KEEP_PCT:
    → Block new entries for the day
    → Lock in remaining gains

  Default KEEP_PCT = 0.65 (lock 65% of peak)
  Threshold activates only after PEAK ≥ MIN_PEAK_TRIGGER (₹20k default)

EFFECT:
  Day hits +₹100k peak → stops at +₹65k
  Day hits +₹50k peak → stops at +₹32.5k
  Day never goes positive → no effect (HARD_LOSS_CAP handles)

This protects PROFITABLE DAYS from being destroyed.
"""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")
_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_PEAK_CACHE_DB = _DATA_DIR / "drawdown_guard.db"


def _ist_now() -> datetime:
    return datetime.now(IST)


def _trades_db_path(mode: str) -> str:
    if mode == "main":
        p = _DATA_DIR / "trades.db"
    else:
        p = _DATA_DIR / "scalper_trades.db"
    return str(p)


def _init_db():
    conn = sqlite3.connect(str(_PEAK_CACHE_DB), timeout=10.0)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_peak (
            date TEXT NOT NULL,
            mode TEXT NOT NULL,
            peak_pnl REAL NOT NULL DEFAULT 0,
            last_updated TEXT,
            PRIMARY KEY (date, mode)
        )
    """)
    conn.commit()
    conn.close()


def _today_pnl(mode: str) -> float:
    """Sum of pnl_rupees for today's CLOSED trades in given mode."""
    db_path = _trades_db_path(mode)
    if not os.path.exists(db_path):
        return 0.0
    today = _ist_now().strftime("%Y-%m-%d")
    table = "trades" if mode == "main" else "scalper_trades"
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        cur = conn.execute(
            f"SELECT COALESCE(SUM(pnl_rupees), 0) FROM {table} "
            f"WHERE substr(entry_time,1,10) = ? AND status NOT IN ('OPEN','PENDING')",
            (today,)
        ).fetchone()
        conn.close()
        return float(cur[0] or 0)
    except Exception as e:
        print(f"[DRAWDOWN_GUARD] today_pnl error ({mode}): {e}")
        return 0.0


def _get_peak(mode: str) -> float:
    """Read today's recorded peak P&L from cache."""
    _init_db()
    today = _ist_now().strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(_PEAK_CACHE_DB), timeout=10.0)
        cur = conn.execute(
            "SELECT peak_pnl FROM daily_peak WHERE date=? AND mode=?",
            (today, mode)
        ).fetchone()
        conn.close()
        return float(cur[0]) if cur else 0.0
    except Exception:
        return 0.0


def _save_peak(mode: str, peak: float):
    today = _ist_now().strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(_PEAK_CACHE_DB), timeout=10.0)
        conn.execute(
            "INSERT OR REPLACE INTO daily_peak (date, mode, peak_pnl, last_updated) "
            "VALUES (?, ?, ?, ?)",
            (today, mode, peak, _ist_now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DRAWDOWN_GUARD] save_peak error: {e}")


def check_drawdown_block(mode: str) -> Tuple[bool, str]:
    """Return (should_block, reason).

    True = block new entry, False = allow.
    """
    # Env kill switch
    if os.environ.get("DRAWDOWN_GUARD_DISABLED", "").strip() in ("1","true","on"):
        return False, ""

    try:
        keep_pct = float(os.environ.get("DRAWDOWN_KEEP_PCT", "0.65"))
        min_peak_trigger = float(os.environ.get("DRAWDOWN_MIN_PEAK", "20000"))
    except Exception:
        keep_pct = 0.65
        min_peak_trigger = 20000.0

    current_pnl = _today_pnl(mode)
    cached_peak = _get_peak(mode)

    # Update peak if current is higher (ratchet)
    if current_pnl > cached_peak:
        cached_peak = current_pnl
        _save_peak(mode, cached_peak)

    # Only protect after a meaningful peak
    if cached_peak < min_peak_trigger:
        return False, ""

    lock_threshold = cached_peak * keep_pct

    if current_pnl < lock_threshold:
        return True, (
            f"DRAWDOWN_LOCK: today's peak ₹{cached_peak:+,.0f}, "
            f"now ₹{current_pnl:+,.0f} (< {keep_pct*100:.0f}% of peak). "
            f"Locking remaining gains. Resume tomorrow."
        )
    return False, ""


def diagnostics() -> dict:
    """Return current state of drawdown guard for both modes."""
    out = {}
    for mode in ("main", "scalper"):
        current = _today_pnl(mode)
        peak = _get_peak(mode)
        if current > peak:
            peak = current
        keep_pct = float(os.environ.get("DRAWDOWN_KEEP_PCT", "0.65"))
        min_peak = float(os.environ.get("DRAWDOWN_MIN_PEAK", "20000"))
        active = peak >= min_peak
        lock = peak * keep_pct if active else None
        blocked = active and current < lock
        out[mode] = {
            "current_pnl": round(current, 0),
            "peak_pnl": round(peak, 0),
            "keep_pct": keep_pct,
            "min_peak_trigger": min_peak,
            "active": active,
            "lock_threshold": round(lock, 0) if lock else None,
            "blocking_entries": blocked,
        }
    return out
