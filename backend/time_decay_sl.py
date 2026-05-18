"""
Time-Decay SL System (R:R Money Management Fix)
────────────────────────────────────────────────
Progressively tightens SL based on HOLD TIME for losers/flat trades.
Solves the "loser drifts to -12% before SL hits" problem.

LOGIC:
  Hold 0-5 min     →  SL cap = -10% (give room, fresh entry)
  Hold 5-15 min    →  SL cap = -8%  (tighten as theta accelerates)
  Hold 15-30 min   →  SL cap = -5%  (theta dominating)
  Hold 30+ min     →  SL cap = -3%  (exit zone)

WHY THIS MATTERS:
  Yesterday's BANKNIFTY 55400 CE: held 22 min, exited at -12% (-₹88k).
  With time-decay SL: would have exited at -5% (-₹37k). Saves ₹51k.

WORKS WITH PROFIT-TRAIL:
  - Profit-Trail handles profitable trades (locks gains)
  - Time-Decay handles losing/flat trades (caps further loss)
  - Both use max() against existing SL (only raise, never lower)
  - No conflict — whichever is higher wins

INVARIANTS (same as profit_trailing_sl):
  1. ONLY RAISES SL, never lowers
  2. NEVER above current price (1% buffer enforced)
  3. NEVER triggers exits — only updates sl_price
  4. ZERO interference with entry logic
"""

import sqlite3
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
LOG_DB_PATH = str(_DATA_DIR / "time_decay.db")


# ── Ladder config ─────────────────────────────────────────────────────

# (max_minutes, sl_multiplier_of_entry)
# Hold time ≤ X minutes → SL must be at least entry × multiplier
LADDER_MAIN: List[Tuple[float, float]] = [
    (5,    0.90),   # 0-5 min → cap -10% (fresh, give room)
    (15,   0.92),   # 5-15 min → cap -8%
    (30,   0.95),   # 15-30 min → cap -5%
    (999,  0.97),   # 30+ min → cap -3% (theta zone, exit fast)
]

# Scalper has tighter ladder (30-min max hold anyway)
LADDER_SCALPER: List[Tuple[float, float]] = [
    (3,    0.92),   # 0-3 min → -8%
    (8,    0.94),   # 3-8 min → -6%
    (15,   0.96),   # 8-15 min → -4%
    (999,  0.98),   # 15+ min → -2%
]

# Safety: SL must always be ≥1% below current price
MIN_GAP_FROM_CURRENT_PCT = 1.0


# ── DB init ───────────────────────────────────────────────────────────

def _init_db():
    conn = sqlite3.connect(LOG_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS time_decay_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            source TEXT,
            trade_id INTEGER,
            idx TEXT,
            action TEXT,
            strike INTEGER,
            entry_price REAL,
            current_premium REAL,
            hold_minutes REAL,
            profit_pct REAL,
            old_sl REAL,
            new_sl REAL,
            stage_minutes REAL,
            cap_pct REAL,
            reason TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_td_ts ON time_decay_log(ts)")
    conn.commit()
    conn.close()


# ── Core calculation ─────────────────────────────────────────────────

def calculate_decay_sl(
    entry_price: float,
    current_price: float,
    current_sl: float,
    hold_minutes: float,
    ladder: List[Tuple[float, float]],
) -> Optional[Dict]:
    """
    Compute the time-decay SL based on hold time.

    Returns:
      None       if no change needed (current_sl already ≥ time-decay floor)
      dict       with {new_sl, hold_minutes, cap_pct, stage_minutes} on raise
    """
    if entry_price <= 0 or current_price <= 0 or hold_minutes < 0:
        return None

    profit_pct = (current_price - entry_price) / entry_price * 100

    # Find applicable stage based on hold time
    cap_multiplier = None
    stage_minutes = None
    for max_min, mult in ladder:
        if hold_minutes <= max_min:
            cap_multiplier = mult
            stage_minutes = max_min
            break

    if cap_multiplier is None:
        return None

    # Calculate the SL floor based on time-decay
    candidate_sl = round(entry_price * cap_multiplier, 2)

    # Safety: must be at least 1% below current price (no instant exit)
    safe_max = round(current_price * (1 - MIN_GAP_FROM_CURRENT_PCT / 100), 2)
    if candidate_sl > safe_max:
        candidate_sl = safe_max

    # Only raise SL — if existing SL already higher, do nothing
    if candidate_sl <= current_sl:
        return None

    # Final sanity check: SL must be below current price
    if candidate_sl >= current_price:
        return None

    cap_pct = round((cap_multiplier - 1) * 100, 1)

    return {
        "new_sl": candidate_sl,
        "hold_minutes": round(hold_minutes, 1),
        "profit_pct": round(profit_pct, 2),
        "stage_minutes": stage_minutes,
        "cap_pct": cap_pct,
    }


# ── Logging ──────────────────────────────────────────────────────────

def _log_decay(source: str, trade: Dict, calc: Dict, current_premium: float, reason: str):
    try:
        _init_db()
        conn = sqlite3.connect(LOG_DB_PATH)
        conn.execute("""
            INSERT INTO time_decay_log
            (ts, source, trade_id, idx, action, strike, entry_price, current_premium,
             hold_minutes, profit_pct, old_sl, new_sl, stage_minutes, cap_pct, reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            time.time(), source, trade.get("id"),
            trade.get("idx"), trade.get("action"), trade.get("strike"),
            trade.get("entry_price"), current_premium,
            calc["hold_minutes"], calc["profit_pct"],
            trade.get("sl_price"), calc["new_sl"],
            calc["stage_minutes"], calc["cap_pct"], reason,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TIME-DECAY] log err: {e}")


# ── Main P&L trade time-decay ─────────────────────────────────────────

def update_main_decay(trade: Dict, current_premium: float) -> Optional[Dict]:
    """Update time-decay SL for main P&L trade.

    Args:
      trade: dict with id, entry_price, sl_price, entry_time, idx, action, strike
      current_premium: live LTP

    Returns:
      None if no update made, else dict with details

    Side effect: updates trades.db sl_price field on raise
    """
    entry = trade.get("entry_price", 0) or 0
    current_sl = trade.get("sl_price", 0) or 0
    if entry <= 0 or current_premium <= 0:
        return None

    # Compute hold time
    entry_iso = trade.get("entry_time", "")
    if not entry_iso:
        return None
    try:
        entry_dt = datetime.fromisoformat(entry_iso)
        if entry_dt.tzinfo is not None:
            entry_dt = entry_dt.replace(tzinfo=None)
        hold_min = (datetime.now() - entry_dt).total_seconds() / 60.0
    except Exception:
        return None

    if hold_min < 0:
        return None

    calc = calculate_decay_sl(entry, current_premium, current_sl, hold_min, LADDER_MAIN)
    if not calc:
        return None

    # Apply to DB
    try:
        from trade_logger import _conn
        conn = _conn()
        row = conn.execute(
            "SELECT sl_price, status FROM trades WHERE id=?", (trade["id"],)
        ).fetchone()
        if not row or row[1] != "OPEN":
            conn.close()
            return None
        latest_sl = row[0] or 0
        if calc["new_sl"] <= latest_sl:
            conn.close()
            return None

        reason = (f"TIME_DECAY: hold {calc['hold_minutes']:.1f}m "
                  f"→ stage ≤{calc['stage_minutes']}m "
                  f"→ SL ₹{calc['new_sl']} (cap {calc['cap_pct']:+.1f}%)")
        conn.execute("""
            UPDATE trades SET sl_price=?,
                alerts=COALESCE(alerts,'') || ?
            WHERE id=? AND status='OPEN'
        """, (calc["new_sl"], f" | {reason}", trade["id"]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TIME-DECAY] main update err: {e}")
        return None

    _log_decay("MAIN", trade, calc, current_premium, "auto-tighten")
    print(f"[TIME-DECAY] MAIN #{trade.get('id')} {trade.get('idx')} {trade.get('action')} "
          f"{trade.get('strike')}: hold {calc['hold_minutes']:.1f}m, "
          f"SL ₹{current_sl} → ₹{calc['new_sl']} (cap {calc['cap_pct']:.1f}%)")
    return calc


# ── Scalper trade time-decay ──────────────────────────────────────────

def update_scalper_decay(trade: Dict, current_premium: float) -> Optional[Dict]:
    """Update time-decay SL for scalper trade. Tighter ladder."""
    entry = trade.get("entry_price", 0) or 0
    current_sl = trade.get("sl_price", 0) or 0
    if entry <= 0 or current_premium <= 0:
        return None

    entry_iso = trade.get("entry_time", "")
    if not entry_iso:
        return None
    try:
        entry_dt = datetime.fromisoformat(entry_iso)
        if entry_dt.tzinfo is not None:
            entry_dt = entry_dt.replace(tzinfo=None)
        hold_min = (datetime.now() - entry_dt).total_seconds() / 60.0
    except Exception:
        return None

    if hold_min < 0:
        return None

    calc = calculate_decay_sl(entry, current_premium, current_sl, hold_min, LADDER_SCALPER)
    if not calc:
        return None

    try:
        import scalper_mode
        conn = scalper_mode._conn()
        row = conn.execute(
            "SELECT sl_price, status FROM scalper_trades WHERE id=?", (trade["id"],)
        ).fetchone()
        if not row or row[1] != "OPEN":
            conn.close()
            return None
        latest_sl = row[0] or 0
        if calc["new_sl"] <= latest_sl:
            conn.close()
            return None

        # For scalper, also update smart_sl_value
        conn.execute("""
            UPDATE scalper_trades SET sl_price=?,
                smart_sl_value=COALESCE(MAX(smart_sl_value, ?), ?)
            WHERE id=? AND status='OPEN'
        """, (calc["new_sl"], calc["new_sl"], calc["new_sl"], trade["id"]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TIME-DECAY] scalper update err: {e}")
        return None

    _log_decay("SCALPER", trade, calc, current_premium, "auto-tighten")
    print(f"[TIME-DECAY] SCALPER #{trade.get('id')} {trade.get('idx')} {trade.get('action')} "
          f"{trade.get('strike')}: hold {calc['hold_minutes']:.1f}m, "
          f"SL ₹{current_sl} → ₹{calc['new_sl']} (cap {calc['cap_pct']:.1f}%)")
    return calc


# ── Read helpers for API/UI ──────────────────────────────────────────

def get_decay_status(trade: Dict, current_premium: float, mode: str = "MAIN") -> Dict:
    """Return current time-decay state for UI rendering — does NOT update DB."""
    entry = trade.get("entry_price", 0) or 0
    current_sl = trade.get("sl_price", 0) or 0
    ladder = LADDER_SCALPER if mode == "SCALPER" else LADDER_MAIN

    if entry <= 0 or current_premium <= 0:
        return {"active": False, "ladder": []}

    entry_iso = trade.get("entry_time", "")
    hold_min = 0
    if entry_iso:
        try:
            entry_dt = datetime.fromisoformat(entry_iso)
            if entry_dt.tzinfo is not None:
                entry_dt = entry_dt.replace(tzinfo=None)
            hold_min = (datetime.now() - entry_dt).total_seconds() / 60.0
        except Exception:
            pass

    profit_pct = (current_premium - entry) / entry * 100 if entry > 0 else 0
    locked_pct = ((current_sl - entry) / entry * 100) if current_sl > 0 else None

    current_stage = None
    next_stage = None
    next_stage_at_min = None
    stages = []
    for max_min, mult in ladder:
        sl_at_stage = round(entry * mult, 2)
        cap_pct = round((mult - 1) * 100, 1)
        active = hold_min <= max_min
        stages.append({
            "max_minutes": max_min,
            "sl_target": sl_at_stage,
            "cap_pct": cap_pct,
            "active": active,
        })
        if current_stage is None and active:
            current_stage = max_min

    # Find next stage transition
    for max_min, mult in ladder:
        if hold_min < max_min and (next_stage_at_min is None or max_min < next_stage_at_min):
            next_stage = max_min
            next_stage_at_min = max_min
            break

    return {
        "active": True,
        "hold_minutes": round(hold_min, 1),
        "profit_pct": round(profit_pct, 2),
        "current_sl": current_sl,
        "current_stage": current_stage,
        "next_stage_at_min": next_stage_at_min,
        "minutes_to_next_stage": round(next_stage_at_min - hold_min, 1) if next_stage_at_min else None,
        "ladder": stages,
    }


def get_decay_log_today(source: Optional[str] = None, limit: int = 100) -> List[Dict]:
    _init_db()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    conn = sqlite3.connect(LOG_DB_PATH)
    if source:
        rows = conn.execute("""
            SELECT ts, source, trade_id, idx, action, strike, entry_price, current_premium,
                   hold_minutes, profit_pct, old_sl, new_sl, stage_minutes, cap_pct
            FROM time_decay_log WHERE source=? AND ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (source.upper(), today_start, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT ts, source, trade_id, idx, action, strike, entry_price, current_premium,
                   hold_minutes, profit_pct, old_sl, new_sl, stage_minutes, cap_pct
            FROM time_decay_log WHERE ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (today_start, limit)).fetchall()
    conn.close()
    return [{
        "ts": r[0], "source": r[1], "trade_id": r[2], "idx": r[3],
        "action": r[4], "strike": r[5], "entry_price": r[6],
        "current_premium": r[7], "hold_minutes": r[8], "profit_pct": r[9],
        "old_sl": r[10], "new_sl": r[11], "stage_minutes": r[12], "cap_pct": r[13],
    } for r in rows]


def get_ladder_config(mode: str = "MAIN") -> List[Dict]:
    ladder = LADDER_SCALPER if mode == "SCALPER" else LADDER_MAIN
    return [
        {"max_minutes": m, "sl_multiplier": mult, "cap_pct": round((mult - 1) * 100, 1)}
        for m, mult in ladder
    ]
