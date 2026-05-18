"""
Profit-Lock Trailing SL System
──────────────────────────────
Auto-raises stop-loss as profit grows, locking gains progressively.
Solves the "winner becomes loser" reversal problem.

8-STAGE LADDER (default for both PnL + Scalper):
  Profit ≥ +3%   → SL = entry × 0.92  (cap -8% loss)
  Profit ≥ +5%   → SL = entry × 0.95  (cap -5% loss)
  Profit ≥ +7%   → SL = entry × 1.00  (BREAKEVEN — zero loss)
  Profit ≥ +10%  → SL = entry × 1.03  (lock +3%)
  Profit ≥ +15%  → SL = entry × 1.05  (lock +5%)
  Profit ≥ +20%  → SL = entry × 1.08  (lock +8%)
  Profit ≥ +30%  → SL = entry × 1.12  (lock +12%)
  Profit ≥ +40%  → SL = entry × 1.20  (lock +20%)

CRITICAL INVARIANTS:
  1. ONLY RAISES SL, NEVER LOWERS — uses max() against existing SL
  2. NEVER above current price — keeps 1% buffer (instant-exit guard)
  3. NEVER triggers exits itself — only updates sl_price field
     (existing exit logic handles when current_ltp ≤ sl_price)
  4. ZERO INTERFERENCE with entry logic — read-only on trade entries
  5. WORKS ALONGSIDE existing systems (smart SL ladder, breakeven_active,
     position watcher tight SL) — uses max() so highest wins, no conflict

INTEGRATION:
  - trade_logger.check_and_update() calls update_main_trail() per cycle
  - scalper_mode.check_scalper_exits() calls update_scalper_trail() per cycle
  - Both fire AFTER existing SL adjustments, so trailing takes precedence
    when it raises higher

LOGGING:
  Every successful trail action writes to trail_log table for audit.
"""

import sqlite3
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
LOG_DB_PATH = str(_DATA_DIR / "profit_trail.db")


# ── Ladder config ─────────────────────────────────────────────────────

# (profit_threshold_pct, sl_multiplier_of_entry)
# When profit ≥ threshold, SL ≥ entry × multiplier
LADDER_MAIN: List[Tuple[float, float]] = [
    (3.0,  0.92),   # +3% profit → max -8% loss
    (5.0,  0.95),   # +5% → max -5%
    (7.0,  1.00),   # +7% → BREAKEVEN
    (10.0, 1.03),   # +10% → lock +3%
    (15.0, 1.05),   # +15% → lock +5%
    (20.0, 1.08),   # +20% → lock +8%
    (30.0, 1.12),   # +30% → lock +12%
    (40.0, 1.20),   # +40% → lock +20%
    (60.0, 1.35),   # +60% → lock +35% (runners)
    (80.0, 1.50),   # +80% → lock +50% (big runners)
]

# Scalper uses slightly faster trail (smaller windows of opportunity)
LADDER_SCALPER: List[Tuple[float, float]] = [
    (2.0,  0.95),   # +2% → max -5%
    (4.0,  1.00),   # +4% → BREAKEVEN (faster than main)
    (6.0,  1.02),   # +6% → lock +2%
    (10.0, 1.05),   # +10% → lock +5%
    (15.0, 1.08),   # +15% → lock +8%
    (20.0, 1.12),   # +20% → lock +12%
    (30.0, 1.18),   # +30% → lock +18%
    (40.0, 1.25),   # +40% → lock +25%
    (60.0, 1.40),
    (80.0, 1.55),
]

# Safety: SL must always be at least this % below current price
# Prevents instant-exit if SL is set too close to LTP
MIN_GAP_FROM_CURRENT_PCT = 1.0


# ── DB init ───────────────────────────────────────────────────────────

def _init_db():
    conn = sqlite3.connect(LOG_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trail_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            source TEXT,           -- 'MAIN' or 'SCALPER'
            trade_id INTEGER,
            idx TEXT,
            action TEXT,
            strike INTEGER,
            entry_price REAL,
            current_premium REAL,
            profit_pct REAL,
            old_sl REAL,
            new_sl REAL,
            stage_threshold REAL,    -- which threshold tripped
            locked_pct REAL,         -- (new_sl - entry) / entry * 100
            reason TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trail_ts ON trail_log(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trail_trade ON trail_log(source, trade_id)")
    conn.commit()
    conn.close()


# ── Core calculation ─────────────────────────────────────────────────

def calculate_trail_sl(
    entry_price: float,
    current_price: float,
    current_sl: float,
    ladder: List[Tuple[float, float]],
) -> Optional[Dict]:
    """
    Compute the new trailing SL based on ladder.

    Returns:
      None       if no change should be made
      dict       with {new_sl, profit_pct, locked_pct, stage_threshold} otherwise
    """
    if entry_price <= 0 or current_price <= 0:
        return None
    profit_pct = (current_price - entry_price) / entry_price * 100

    # Walk ladder bottom-up, find highest threshold met
    candidate_sl = current_sl
    stage_hit = None
    for threshold, multiplier in ladder:
        if profit_pct >= threshold:
            candidate = round(entry_price * multiplier, 2)
            if candidate > candidate_sl:
                candidate_sl = candidate
                stage_hit = threshold

    # No threshold met above current SL
    if stage_hit is None:
        return None

    # Safety: never set SL within 1% of current price (instant-exit guard)
    safe_max = round(current_price * (1 - MIN_GAP_FROM_CURRENT_PCT / 100), 2)
    if candidate_sl > safe_max:
        candidate_sl = safe_max

    # Final check: must actually raise SL
    if candidate_sl <= current_sl:
        return None

    locked_pct = (candidate_sl - entry_price) / entry_price * 100
    return {
        "new_sl": candidate_sl,
        "profit_pct": round(profit_pct, 2),
        "locked_pct": round(locked_pct, 2),
        "stage_threshold": stage_hit,
    }


# ── Logging ──────────────────────────────────────────────────────────

def _log_trail(source: str, trade: Dict, calc: Dict, current_premium: float, reason: str):
    try:
        _init_db()
        conn = sqlite3.connect(LOG_DB_PATH)
        conn.execute("""
            INSERT INTO trail_log
            (ts, source, trade_id, idx, action, strike, entry_price, current_premium,
             profit_pct, old_sl, new_sl, stage_threshold, locked_pct, reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            time.time(), source, trade.get("id"),
            trade.get("idx"), trade.get("action"), trade.get("strike"),
            trade.get("entry_price"), current_premium,
            calc["profit_pct"], trade.get("sl_price"), calc["new_sl"],
            calc["stage_threshold"], calc["locked_pct"], reason,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[PROFIT-TRAIL] log err: {e}")


# ── Main P&L trade trail ──────────────────────────────────────────────

def update_main_trail(trade: Dict, current_premium: float) -> Optional[Dict]:
    """Update trailing SL for a main P&L trade.

    Args:
      trade: dict with id, entry_price, sl_price, idx, action, strike
      current_premium: live LTP

    Returns:
      None if no update made, else dict with details

    Side effect: updates trades.db sl_price field on raise
    """
    entry = trade.get("entry_price", 0) or 0
    current_sl = trade.get("sl_price", 0) or 0
    if entry <= 0 or current_premium <= 0:
        return None

    calc = calculate_trail_sl(entry, current_premium, current_sl, LADDER_MAIN)
    if not calc:
        return None

    # Apply to DB
    try:
        from trade_logger import _conn
        conn = _conn()
        # Idempotent guard: re-check current sl_price before update
        row = conn.execute(
            "SELECT sl_price, status FROM trades WHERE id=?", (trade["id"],)
        ).fetchone()
        if not row or row[1] != "OPEN":
            conn.close()
            return None
        latest_sl = row[0] or 0
        # Don't lower under any circumstance
        if calc["new_sl"] <= latest_sl:
            conn.close()
            return None

        reason = (f"PROFIT_TRAIL: profit {calc['profit_pct']:+.2f}% "
                  f"→ stage +{calc['stage_threshold']}% "
                  f"→ SL ₹{calc['new_sl']} (locked {calc['locked_pct']:+.2f}%)")
        conn.execute("""
            UPDATE trades SET sl_price=?,
                alerts=COALESCE(alerts,'') || ?
            WHERE id=? AND status='OPEN'
        """, (calc["new_sl"], f" | {reason}", trade["id"]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[PROFIT-TRAIL] main update err: {e}")
        return None

    _log_trail("MAIN", trade, calc, current_premium, "auto-raise")
    print(f"[TRAIL] MAIN #{trade.get('id')} {trade.get('idx')} {trade.get('action')} "
          f"{trade.get('strike')}: profit {calc['profit_pct']:+.1f}%, "
          f"SL ₹{current_sl} → ₹{calc['new_sl']} (locked {calc['locked_pct']:+.1f}%)")
    return calc


# ── Scalper trade trail ───────────────────────────────────────────────

def update_scalper_trail(trade: Dict, current_premium: float) -> Optional[Dict]:
    """Update trailing SL for a scalper trade. Mirrors update_main_trail."""
    entry = trade.get("entry_price", 0) or 0
    current_sl = trade.get("sl_price", 0) or 0
    if entry <= 0 or current_premium <= 0:
        return None

    calc = calculate_trail_sl(entry, current_premium, current_sl, LADDER_SCALPER)
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

        # For scalper, also update smart_sl_value so existing logic respects this
        conn.execute("""
            UPDATE scalper_trades SET sl_price=?,
                smart_sl_value=COALESCE(MAX(smart_sl_value, ?), ?)
            WHERE id=? AND status='OPEN'
        """, (calc["new_sl"], calc["new_sl"], calc["new_sl"], trade["id"]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[PROFIT-TRAIL] scalper update err: {e}")
        return None

    _log_trail("SCALPER", trade, calc, current_premium, "auto-raise")
    print(f"[TRAIL] SCALPER #{trade.get('id')} {trade.get('idx')} {trade.get('action')} "
          f"{trade.get('strike')}: profit {calc['profit_pct']:+.1f}%, "
          f"SL ₹{current_sl} → ₹{calc['new_sl']} (locked {calc['locked_pct']:+.1f}%)")
    return calc


# ── Read helpers for API/UI ──────────────────────────────────────────

def get_trail_status(trade: Dict, current_premium: float, mode: str = "MAIN") -> Dict:
    """Return current trail state for UI rendering — does NOT update DB.

    Returns:
      {
        active: bool (any stage hit),
        profit_pct,
        current_stage: highest threshold met,
        locked_pct,
        next_stage: next threshold to hit,
        next_stage_at_premium: premium needed to reach next stage,
        ladder: [...stages with status]
      }
    """
    entry = trade.get("entry_price", 0) or 0
    current_sl = trade.get("sl_price", 0) or 0
    ladder = LADDER_SCALPER if mode == "SCALPER" else LADDER_MAIN

    if entry <= 0 or current_premium <= 0:
        return {"active": False, "profit_pct": 0, "ladder": []}

    profit_pct = (current_premium - entry) / entry * 100
    locked_pct = ((current_sl - entry) / entry * 100) if current_sl > 0 else None

    current_stage = None
    next_stage = None
    next_stage_premium = None
    stages = []
    for threshold, mult in ladder:
        sl_at_stage = round(entry * mult, 2)
        hit = profit_pct >= threshold
        stages.append({
            "threshold": threshold,
            "sl_target": sl_at_stage,
            "lock_pct": round((mult - 1) * 100, 1),
            "hit": hit,
            "current": current_sl >= sl_at_stage if current_sl > 0 else False,
        })
        if hit:
            current_stage = threshold
        elif next_stage is None:
            next_stage = threshold
            next_stage_premium = round(entry * (1 + threshold / 100), 2)

    return {
        "active": current_stage is not None,
        "profit_pct": round(profit_pct, 2),
        "current_stage": current_stage,
        "locked_pct": round(locked_pct, 2) if locked_pct is not None else None,
        "next_stage": next_stage,
        "next_stage_at_premium": next_stage_premium,
        "current_sl": current_sl,
        "ladder": stages,
    }


def get_trail_log_today(source: Optional[str] = None, limit: int = 100) -> List[Dict]:
    """Today's trail-raise events for audit/UI."""
    _init_db()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    conn = sqlite3.connect(LOG_DB_PATH)
    if source:
        rows = conn.execute("""
            SELECT ts, source, trade_id, idx, action, strike, entry_price, current_premium,
                   profit_pct, old_sl, new_sl, stage_threshold, locked_pct
            FROM trail_log WHERE source=? AND ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (source.upper(), today_start, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT ts, source, trade_id, idx, action, strike, entry_price, current_premium,
                   profit_pct, old_sl, new_sl, stage_threshold, locked_pct
            FROM trail_log WHERE ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (today_start, limit)).fetchall()
    conn.close()
    return [{
        "ts": r[0], "source": r[1], "trade_id": r[2], "idx": r[3],
        "action": r[4], "strike": r[5], "entry_price": r[6],
        "current_premium": r[7], "profit_pct": r[8], "old_sl": r[9],
        "new_sl": r[10], "stage_threshold": r[11], "locked_pct": r[12],
    } for r in rows]


def get_ladder_config(mode: str = "MAIN") -> List[Dict]:
    """Return active ladder for given mode (for UI display)."""
    ladder = LADDER_SCALPER if mode == "SCALPER" else LADDER_MAIN
    return [
        {"threshold": t, "sl_multiplier": m, "lock_pct": round((m - 1) * 100, 1)}
        for t, m in ladder
    ]
