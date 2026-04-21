"""
Scalper Mode — Aggressive quick-trade engine.

Different from default swing mode:
  - 15 trades/day cap (vs 6)
  - Tighter SL: -8% (vs -15%)
  - Smaller targets: T1 +12%, T2 +25% (vs +30%/+60%)
  - 15s confirmation (vs 60s)
  - Lower threshold: 45% (vs 50%)
  - Smaller position: 1.5% risk (vs 3%)
  - Quick exits — max 30 min hold
  - Separate trades table for paper tracking

Philosophy: Many small wins, accept many small losses.
Win rate target: 50-55%, R:R 1:1.5
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
SCALPER_DB = _data_dir / "scalper_trades.db"

# SCALPER CONFIG (tuned after whipsaw losses)
SCALPER_THRESHOLD = 55         # Raised 45→55 (avoid weak signals)
SCALPER_DAILY_CAP = 15         # 15 trades/day
SCALPER_SL_PCT = 0.12          # 12% SL (was 8% — too tight, whipsaw)
SCALPER_T1_PCT = 0.20          # 20% T1 (R:R 1:1.67) — was 12%
SCALPER_T2_PCT = 0.40          # 40% T2 (R:R 1:3.3)
SCALPER_RISK_PCT = 1.0         # 1.0% risk (smaller size — 4 hard losses = -4% max)
SCALPER_CONFIRM_SEC = 30       # 30s confirmation (was 15 — avoid noise)
SCALPER_MAX_HOLD_MIN = 30      # 30 min max hold

# WHIPSAW GUARDS
COOLDOWN_SAME_STRIKE_MIN = 10  # No re-entry same strike for 10 min after exit
COOLDOWN_FLIP_DIRECTION_MIN = 15  # No CE→PE or PE→CE same strike for 15 min
MAX_SL_HITS_SAME_STRIKE = 2    # After 2 SL hits on same strike, pause that strike for day


def ist_now():
    return datetime.now(IST)


def init_scalper_db():
    """Init scalper trades table — separate from main trades."""
    conn = sqlite3.connect(str(SCALPER_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scalper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            idx TEXT NOT NULL,
            action TEXT NOT NULL,
            strike INTEGER NOT NULL,
            expiry TEXT,
            entry_price REAL NOT NULL,
            sl_price REAL,
            t1_price REAL,
            t2_price REAL,
            current_ltp REAL,
            peak_ltp REAL,
            exit_price REAL DEFAULT 0,
            lots INTEGER,
            lot_size INTEGER,
            qty INTEGER,
            pnl_pts REAL DEFAULT 0,
            pnl_rupees REAL DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            exit_reason TEXT,
            probability INTEGER,
            hold_seconds INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'SCALPER'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scalper_status ON scalper_trades(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scalper_date ON scalper_trades(entry_time)")
    conn.commit()
    conn.close()


def _conn():
    init_scalper_db()
    conn = sqlite3.connect(str(SCALPER_DB))
    conn.row_factory = sqlite3.Row
    return conn


def should_enter_scalp(idx, verdict_data, scalper_enabled=True, atm_strike=None):
    """Scalper entry rules with WHIPSAW GUARDS.

    Rules:
      1. Scalper mode enabled (toggle)
      2. Market hours
      3. Probability >= 55% (raised from 45)
      4. Daily 15 trade cap
      5. No duplicate same idx+action open
      6. COOLDOWN: No re-entry same strike for 10 min after ANY exit
      7. FLIP GUARD: No CE↔PE flip on same strike for 15 min
      8. WHIPSAW BLOCK: 2+ SL hits on strike today → pause that strike
    """
    if not scalper_enabled:
        return False

    if not verdict_data or verdict_data.get("action") == "NO TRADE":
        return False

    now = ist_now()

    # Market hours
    if now.weekday() > 4:
        return False
    market_open = (now.hour == 9 and now.minute >= 20) or (10 <= now.hour <= 14) or (now.hour == 15 and now.minute <= 15)
    if not market_open:
        return False

    # Threshold
    win_pct = verdict_data.get("winProbability", 0)
    if win_pct < SCALPER_THRESHOLD:
        return False

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    conn = _conn()

    # Daily cap
    today_count = conn.execute(
        "SELECT COUNT(*) FROM scalper_trades WHERE entry_time > ?",
        (today_start,)
    ).fetchone()[0]
    if today_count >= SCALPER_DAILY_CAP:
        conn.close()
        return False

    # No duplicate open
    action_str = verdict_data.get("action", "")
    dup_open = conn.execute(
        "SELECT COUNT(*) FROM scalper_trades WHERE status='OPEN' AND idx=? AND action=?",
        (idx, action_str)
    ).fetchone()[0]
    if dup_open > 0:
        conn.close()
        return False

    # ── WHIPSAW GUARDS ──
    if atm_strike is None:
        conn.close()
        return False

    # Guard 1: Cooldown after ANY exit on same strike
    cooldown_cutoff = (now - timedelta(minutes=COOLDOWN_SAME_STRIKE_MIN)).isoformat()
    recent_same_strike = conn.execute(
        """SELECT COUNT(*) FROM scalper_trades
           WHERE idx=? AND strike=? AND status!='OPEN' AND exit_time > ?""",
        (idx, int(atm_strike), cooldown_cutoff)
    ).fetchone()[0]
    if recent_same_strike > 0:
        conn.close()
        return False

    # Guard 2: Flip direction block (CE→PE or PE→CE on same strike)
    flip_cutoff = (now - timedelta(minutes=COOLDOWN_FLIP_DIRECTION_MIN)).isoformat()
    opposite = "BUY PE" if "CE" in action_str else "BUY CE"
    recent_opposite = conn.execute(
        """SELECT COUNT(*) FROM scalper_trades
           WHERE idx=? AND strike=? AND action=? AND entry_time > ?""",
        (idx, int(atm_strike), opposite, flip_cutoff)
    ).fetchone()[0]
    if recent_opposite > 0:
        conn.close()
        return False

    # Guard 3: 2+ SL hits on same strike today → pause that strike
    sl_hits_today = conn.execute(
        """SELECT COUNT(*) FROM scalper_trades
           WHERE idx=? AND strike=? AND status='SL_HIT' AND entry_time > ?""",
        (idx, int(atm_strike), today_start)
    ).fetchone()[0]
    if sl_hits_today >= MAX_SL_HITS_SAME_STRIKE:
        conn.close()
        return False

    conn.close()
    return True


def calc_scalper_size(entry_price, sl_price, running_capital=1000000):
    """Smaller position for scalper — 1.5% risk per trade."""
    if entry_price <= 0:
        return 1, 25, 25

    max_risk = running_capital * SCALPER_RISK_PCT / 100
    risk_per_unit = max(entry_price - sl_price, 1) if sl_price > 0 else entry_price * 0.08
    risk_per_unit = max(risk_per_unit, 1)

    max_qty = int(max_risk / risk_per_unit)
    return max_qty


def log_scalp_trade(idx, action, strike, entry_price, probability, expiry=""):
    """Create new scalper trade with tight SL/T1/T2."""
    if entry_price <= 0:
        return None

    sl_price = round(entry_price * (1 - SCALPER_SL_PCT))
    t1_price = round(entry_price * (1 + SCALPER_T1_PCT))
    t2_price = round(entry_price * (1 + SCALPER_T2_PCT))

    # Lot size lookup
    lot_sizes = {"NIFTY": 25, "BANKNIFTY": 15}
    lot_size = lot_sizes.get(idx, 25)
    max_qty = calc_scalper_size(entry_price, sl_price)
    lots = max(1, max_qty // lot_size)
    qty = lots * lot_size

    now = ist_now()
    conn = _conn()
    cursor = conn.execute("""
        INSERT INTO scalper_trades (entry_time, idx, action, strike, expiry,
            entry_price, sl_price, t1_price, t2_price,
            current_ltp, peak_ltp, lots, lot_size, qty,
            status, probability)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?)
    """, (now.isoformat(), idx, action, strike, expiry,
          entry_price, sl_price, t1_price, t2_price,
          entry_price, entry_price, lots, lot_size, qty,
          probability))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()

    print(f"[SCALPER] OPENED #{trade_id}: {action} {idx} {strike} @ ₹{entry_price} | qty {qty} | SL ₹{sl_price} T1 ₹{t1_price}")
    return trade_id


def check_scalper_exits(chains):
    """Monitor open scalper trades — quick exit logic."""
    conn = _conn()
    open_trades = conn.execute(
        "SELECT * FROM scalper_trades WHERE status='OPEN'"
    ).fetchall()
    conn.close()

    now = ist_now()

    for t in open_trades:
        t = dict(t)
        idx = t["idx"]
        strike = t["strike"]
        action = t["action"]
        chain = chains.get(idx, {})
        sd = chain.get(strike, {})

        opt_key = "ce_ltp" if "CE" in action else "pe_ltp"
        current_ltp = sd.get(opt_key, 0)
        if current_ltp <= 0:
            continue

        entry = t["entry_price"]
        sl = t["sl_price"]
        t1 = t["t1_price"]
        t2 = t["t2_price"]
        peak = max(t.get("peak_ltp", entry), current_ltp)

        # Hold time check
        try:
            entry_time = datetime.fromisoformat(t["entry_time"])
            hold_sec = (now - entry_time).total_seconds()
        except Exception:
            hold_sec = 0

        new_status = "OPEN"
        exit_reason = None
        exit_price = 0

        # Exit logic (priority order)
        if current_ltp <= sl:
            new_status = "SL_HIT"
            exit_price = sl
            exit_reason = f"SL hit at ₹{sl} (-8%)"
        elif current_ltp >= t2:
            new_status = "T2_HIT"
            exit_price = t2
            exit_reason = f"T2 hit at ₹{t2} (+25%)"
        elif current_ltp >= t1:
            new_status = "T1_HIT"
            exit_price = t1
            exit_reason = f"T1 hit at ₹{t1} (+12%)"
        elif hold_sec >= SCALPER_MAX_HOLD_MIN * 60:
            # Max hold timeout
            new_status = "TIMEOUT_EXIT"
            exit_price = current_ltp
            exit_reason = f"Max hold {SCALPER_MAX_HOLD_MIN}min reached, exit @ ₹{current_ltp}"

        # Update or close
        pnl_pts = round(current_ltp - entry, 2)
        pnl_rupees = round(pnl_pts * t["qty"], 2)

        conn2 = _conn()
        if new_status != "OPEN":
            final_pnl = round((exit_price - entry) * t["qty"], 2)
            conn2.execute("""
                UPDATE scalper_trades SET
                    status=?, exit_price=?, exit_time=?, exit_reason=?,
                    pnl_pts=?, pnl_rupees=?, peak_ltp=?, hold_seconds=?
                WHERE id=?
            """, (new_status, exit_price, now.isoformat(), exit_reason,
                  round(exit_price - entry, 2), final_pnl, peak, int(hold_sec), t["id"]))
            print(f"[SCALPER] CLOSED #{t['id']} {idx} {action} {strike}: ₹{final_pnl:+,.0f} ({new_status})")
        else:
            conn2.execute("""
                UPDATE scalper_trades SET
                    current_ltp=?, peak_ltp=?, pnl_pts=?, pnl_rupees=?, hold_seconds=?
                WHERE id=?
            """, (current_ltp, peak, pnl_pts, pnl_rupees, int(hold_sec), t["id"]))

        conn2.commit()
        conn2.close()


def get_scalper_open_trades():
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM scalper_trades WHERE status='OPEN' ORDER BY entry_time DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_scalper_closed_trades(days=7):
    cutoff = (ist_now() - timedelta(days=days)).isoformat()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM scalper_trades WHERE status!='OPEN' AND entry_time > ? ORDER BY exit_time DESC",
        (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_scalper_stats():
    """Aggregate scalper performance stats."""
    today_start = ist_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    conn = _conn()

    total = conn.execute("SELECT COUNT(*) FROM scalper_trades").fetchone()[0]
    today_count = conn.execute("SELECT COUNT(*) FROM scalper_trades WHERE entry_time > ?", (today_start,)).fetchone()[0]
    open_count = conn.execute("SELECT COUNT(*) FROM scalper_trades WHERE status='OPEN'").fetchone()[0]

    closed = conn.execute("SELECT pnl_rupees FROM scalper_trades WHERE status!='OPEN'").fetchall()
    closed = [r[0] or 0 for r in closed]
    wins = [p for p in closed if p > 0]
    losses = [p for p in closed if p < 0]

    conn.close()

    win_rate = round(len(wins) / max(len(closed), 1) * 100, 1) if closed else 0
    total_pnl = sum(closed)
    avg_win = round(sum(wins) / max(len(wins), 1), 0) if wins else 0
    avg_loss = round(sum(losses) / max(len(losses), 1), 0) if losses else 0

    return {
        "total": total,
        "todayCount": today_count,
        "open": open_count,
        "wins": len(wins),
        "losses": len(losses),
        "winRate": win_rate,
        "totalPnl": total_pnl,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "dailyCap": SCALPER_DAILY_CAP,
        "remaining": max(0, SCALPER_DAILY_CAP - today_count),
    }


# Module-level toggle (in-memory, can be controlled via API)
_scalper_enabled = False  # OFF by default


def is_scalper_enabled():
    return _scalper_enabled


def enable_scalper():
    global _scalper_enabled
    _scalper_enabled = True
    print("[SCALPER] Mode ENABLED")


def disable_scalper():
    global _scalper_enabled
    _scalper_enabled = False
    print("[SCALPER] Mode DISABLED")
