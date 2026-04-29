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
            mode TEXT DEFAULT 'SCALPER',
            entry_reasoning TEXT,
            entry_bull_pct REAL,
            entry_bear_pct REAL,
            entry_spot REAL,
            capital_used REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scalper_status ON scalper_trades(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scalper_date ON scalper_trades(entry_time)")

    # Migrate older DB: add new columns if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(scalper_trades)").fetchall()]
    for col, sql in [
        ("entry_reasoning", "ALTER TABLE scalper_trades ADD COLUMN entry_reasoning TEXT"),
        ("entry_bull_pct", "ALTER TABLE scalper_trades ADD COLUMN entry_bull_pct REAL"),
        ("entry_bear_pct", "ALTER TABLE scalper_trades ADD COLUMN entry_bear_pct REAL"),
        ("entry_spot", "ALTER TABLE scalper_trades ADD COLUMN entry_spot REAL"),
        ("capital_used", "ALTER TABLE scalper_trades ADD COLUMN capital_used REAL"),
        # Smart SL state per trade
        ("smart_sl_stage", "ALTER TABLE scalper_trades ADD COLUMN smart_sl_stage INTEGER DEFAULT 0"),
        ("smart_sl_value", "ALTER TABLE scalper_trades ADD COLUMN smart_sl_value REAL"),
        ("sl_hit_time", "ALTER TABLE scalper_trades ADD COLUMN sl_hit_time TEXT"),
        ("sl_reason", "ALTER TABLE scalper_trades ADD COLUMN sl_reason TEXT"),
    ]:
        if col not in cols:
            try: conn.execute(sql)
            except Exception: pass

    # Smart SL config row (separate from main scalper_config to avoid conflicts)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS smart_sl_config (
            id INTEGER PRIMARY KEY CHECK (id=1),
            enabled INTEGER DEFAULT 0,
            spot_anchor_pct REAL DEFAULT 0.4,
            updated_at TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO smart_sl_config (id, enabled, spot_anchor_pct, updated_at) VALUES (1, 0, 0.4, ?)",
        (ist_now().isoformat(),)
    )

    # Tick history (live LTP samples per trade)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scalper_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            ts INTEGER NOT NULL,
            ltp REAL,
            spot REAL,
            pnl_rupees REAL,
            pnl_pct REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scalp_ticks ON scalper_ticks(trade_id, ts DESC)")

    # User-configurable scalper settings
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scalper_config (
            id INTEGER PRIMARY KEY CHECK (id=1),
            capital REAL DEFAULT 1000000,
            nifty_qty INTEGER DEFAULT 0,
            banknifty_qty INTEGER DEFAULT 0,
            sl_pct REAL DEFAULT 0.12,
            t1_pct REAL DEFAULT 0.20,
            t2_pct REAL DEFAULT 0.40,
            threshold INTEGER DEFAULT 55,
            daily_cap INTEGER DEFAULT 15,
            updated_at TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO scalper_config (id, capital, nifty_qty, banknifty_qty, updated_at) "
        "VALUES (1, 1000000, 0, 0, ?)",
        (ist_now().isoformat(),)
    )
    conn.commit()
    conn.close()


def get_scalper_config():
    """Return user-configurable scalper settings."""
    init_scalper_db()
    conn = _conn()
    row = conn.execute("SELECT * FROM scalper_config WHERE id=1").fetchone()
    conn.close()
    if not row:
        return {
            "capital": 1000000, "nifty_qty": 0, "banknifty_qty": 0,
            "sl_pct": SCALPER_SL_PCT, "t1_pct": SCALPER_T1_PCT, "t2_pct": SCALPER_T2_PCT,
            "threshold": SCALPER_THRESHOLD, "daily_cap": SCALPER_DAILY_CAP,
        }
    return dict(row)


def set_scalper_config(capital=None, nifty_qty=None, banknifty_qty=None,
                      sl_pct=None, t1_pct=None, t2_pct=None,
                      threshold=None, daily_cap=None):
    """Update user-configurable scalper settings (only provided fields)."""
    init_scalper_db()
    cur = get_scalper_config()
    updated = {
        "capital": capital if capital is not None else cur.get("capital", 1000000),
        "nifty_qty": nifty_qty if nifty_qty is not None else cur.get("nifty_qty", 0),
        "banknifty_qty": banknifty_qty if banknifty_qty is not None else cur.get("banknifty_qty", 0),
        "sl_pct": sl_pct if sl_pct is not None else cur.get("sl_pct", SCALPER_SL_PCT),
        "t1_pct": t1_pct if t1_pct is not None else cur.get("t1_pct", SCALPER_T1_PCT),
        "t2_pct": t2_pct if t2_pct is not None else cur.get("t2_pct", SCALPER_T2_PCT),
        "threshold": threshold if threshold is not None else cur.get("threshold", SCALPER_THRESHOLD),
        "daily_cap": daily_cap if daily_cap is not None else cur.get("daily_cap", SCALPER_DAILY_CAP),
    }
    conn = _conn()
    conn.execute("""
        UPDATE scalper_config
        SET capital=?, nifty_qty=?, banknifty_qty=?, sl_pct=?, t1_pct=?, t2_pct=?,
            threshold=?, daily_cap=?, updated_at=?
        WHERE id=1
    """, (
        updated["capital"], updated["nifty_qty"], updated["banknifty_qty"],
        updated["sl_pct"], updated["t1_pct"], updated["t2_pct"],
        updated["threshold"], updated["daily_cap"], ist_now().isoformat(),
    ))
    conn.commit()
    conn.close()
    return updated


_scalper_pragma_done = False
def _conn():
    global _scalper_pragma_done
    init_scalper_db()
    conn = sqlite3.connect(str(SCALPER_DB), timeout=10.0)
    conn.row_factory = sqlite3.Row
    if not _scalper_pragma_done:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-128000")  # 128MB (2GB RAM)
            conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
            _scalper_pragma_done = True
        except Exception:
            pass
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

    # Threshold (user-configurable)
    cfg = get_scalper_config()
    threshold = cfg.get("threshold") or SCALPER_THRESHOLD
    daily_cap = cfg.get("daily_cap") or SCALPER_DAILY_CAP
    # Use RUNNING capital from tracker (compounds with P&L)
    try:
        from capital_tracker import get_running_capital
        capital = get_running_capital("SCALPER") or cfg.get("capital") or 1000000
    except Exception:
        capital = cfg.get("capital") or 1000000

    win_pct = verdict_data.get("winProbability", 0)
    if win_pct < threshold:
        return False

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    conn = _conn()

    # Daily cap
    today_count = conn.execute(
        "SELECT COUNT(*) FROM scalper_trades WHERE entry_time > ?",
        (today_start,)
    ).fetchone()[0]
    if today_count >= daily_cap:
        conn.close()
        return False

    # ── CAPITAL CHECK — total committed across all OPEN trades must not exceed capital ──
    committed_row = conn.execute("""
        SELECT COALESCE(SUM(COALESCE(capital_used, entry_price * qty)), 0) as committed
        FROM scalper_trades WHERE status='OPEN'
    """).fetchone()
    committed = committed_row["committed"] or 0
    available = capital - committed

    if available <= 0:
        conn.close()
        print(f"[SCALPER] REJECT entry: capital ₹{capital:,.0f} fully committed across {today_count} open trades (₹{committed:,.0f}). Available: ₹{available:,.0f}")
        return False

    # Estimate new trade cost from user qty config or 10% of capital fallback
    user_qty_field = "nifty_qty" if idx == "NIFTY" else "banknifty_qty"
    user_qty = cfg.get(user_qty_field, 0) or 0
    # Use a conservative premium estimate — most scalper entries are ₹50-300
    est_premium = 200
    if user_qty > 0:
        est_cost = user_qty * est_premium
    else:
        # Auto-sizing: 1% risk × capital, with 12% SL → max qty cost ≈ ~10% of capital
        est_cost = capital * 0.10

    if est_cost > available:
        conn.close()
        print(f"[SCALPER] REJECT entry: estimated cost ₹{est_cost:,.0f} > available ₹{available:,.0f} (capital ₹{capital:,.0f}, committed ₹{committed:,.0f})")
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
    # Scalper INDEPENDENT — uses its own config, not buyer_mode (which is for main trades)
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


# ═══════════════════════════════════════════════════════════════
# SMART SL SYSTEM — 7-stage Profit Ladder + Spot Anchor
# ═══════════════════════════════════════════════════════════════

# 7 stages: (profit_trigger_pct, sl_offset_pct, label)
SMART_SL_LADDER = [
    (0,   -15, "Initial"),
    (5,   -3,  "Tight"),
    (10,  0,   "Breakeven"),
    (15,  +5,  "Lock +5%"),
    (25,  +12, "Lock +12%"),
    (40,  +25, "Lock +25%"),
    (60,  +40, "Lock +40%"),
]


def get_smart_sl_config():
    """Returns {enabled: bool, spot_anchor_pct: float}."""
    init_scalper_db()
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM smart_sl_config WHERE id=1").fetchone()
        if not row:
            return {"enabled": False, "spot_anchor_pct": 0.4}
        return {
            "enabled": bool(row["enabled"]),
            "spot_anchor_pct": row["spot_anchor_pct"] or 0.4,
        }
    finally:
        conn.close()


def set_smart_sl_config(enabled=None, spot_anchor_pct=None):
    """Update smart SL config (only provided fields)."""
    init_scalper_db()
    cur = get_smart_sl_config()
    new_enabled = 1 if (enabled if enabled is not None else cur["enabled"]) else 0
    new_pct = float(spot_anchor_pct) if spot_anchor_pct is not None else cur["spot_anchor_pct"]
    conn = _conn()
    try:
        conn.execute("""
            UPDATE smart_sl_config SET enabled=?, spot_anchor_pct=?, updated_at=? WHERE id=1
        """, (new_enabled, new_pct, ist_now().isoformat()))
        conn.commit()
    finally:
        conn.close()
    return get_smart_sl_config()


def compute_smart_sl(entry_price, current_premium, current_stage_saved=0, saved_sl=None):
    """Compute smart SL based on profit ladder.
    Returns (active_sl, stage, stage_label).
    Ratchet rule: SL never goes DOWN — stays at highest achieved stage."""
    if entry_price <= 0:
        return entry_price * 0.85, 0, "Initial"

    profit_pct = (current_premium - entry_price) / entry_price * 100

    # Find highest stage achieved by current profit
    new_stage = 0
    new_offset = -15
    new_label = "Initial"
    for trigger_pct, offset_pct, label in SMART_SL_LADDER:
        if profit_pct >= trigger_pct:
            new_stage = SMART_SL_LADDER.index((trigger_pct, offset_pct, label))
            new_offset = offset_pct
            new_label = label

    new_sl = round(entry_price * (1 + new_offset / 100), 2)

    # Ratchet rule: if saved_sl is higher, use that (SL never decreases)
    if saved_sl is not None and saved_sl > new_sl:
        return saved_sl, current_stage_saved, SMART_SL_LADDER[current_stage_saved][2]

    # Stage upgraded
    return new_sl, new_stage, new_label


def check_spot_anchor(action, entry_spot, current_spot, threshold_pct=0.4):
    """Returns (should_exit, reason).
    BUY CE: exit if spot drops > threshold_pct
    BUY PE: exit if spot rises > threshold_pct"""
    if not entry_spot or entry_spot <= 0 or not current_spot or current_spot <= 0:
        return False, None
    spot_change_pct = (current_spot - entry_spot) / entry_spot * 100
    is_ce = "CE" in (action or "")
    if is_ce and spot_change_pct < -threshold_pct:
        return True, f"Spot anchor: NIFTY dropped {spot_change_pct:.2f}% from entry {entry_spot} (now {current_spot:.1f})"
    if not is_ce and spot_change_pct > threshold_pct:
        return True, f"Spot anchor: NIFTY rose {spot_change_pct:.2f}% from entry {entry_spot} (now {current_spot:.1f})"
    return False, None


def get_ladder_progress(entry_price, current_premium, current_stage_saved=0):
    """For UI: full ladder state with current/done/pending stages."""
    if entry_price <= 0:
        return []
    profit_pct = (current_premium - entry_price) / entry_price * 100
    out = []
    for i, (trigger, offset, label) in enumerate(SMART_SL_LADDER):
        sl_at = round(entry_price * (1 + offset / 100), 2)
        if profit_pct >= trigger:
            status = "DONE" if i < len([s for s in SMART_SL_LADDER if profit_pct >= s[0]]) - 1 else "ACTIVE"
        else:
            status = "PENDING"
        out.append({
            "stage": i,
            "trigger_pct": trigger,
            "sl_offset_pct": offset,
            "sl_at": sl_at,
            "label": label,
            "status": status,
        })
    return out


def calc_scalper_size(entry_price, sl_price, running_capital=1000000):
    """Smaller position for scalper — 1.5% risk per trade."""
    if entry_price <= 0:
        return 1, 25, 25

    max_risk = running_capital * SCALPER_RISK_PCT / 100
    risk_per_unit = max(entry_price - sl_price, 1) if sl_price > 0 else entry_price * 0.08
    risk_per_unit = max(risk_per_unit, 1)

    max_qty = int(max_risk / risk_per_unit)
    return max_qty


def log_scalp_trade(idx, action, strike, entry_price, probability, expiry="",
                    entry_reasoning=None, entry_bull_pct=None, entry_bear_pct=None,
                    entry_spot=None):
    """Create new scalper trade with RUNNING capital (auto-adjusts after profit/loss)."""
    if entry_price <= 0:
        return None

    cfg = get_scalper_config()
    sl_pct = cfg.get("sl_pct") or SCALPER_SL_PCT
    t1_pct = cfg.get("t1_pct") or SCALPER_T1_PCT
    t2_pct = cfg.get("t2_pct") or SCALPER_T2_PCT
    # Use RUNNING capital (capital tracker) — base falls back to user config
    try:
        from capital_tracker import get_running_capital
        capital = get_running_capital("SCALPER") or cfg.get("capital") or 1000000
    except Exception:
        capital = cfg.get("capital") or 1000000

    sl_price = round(entry_price * (1 - sl_pct))
    t1_price = round(entry_price * (1 + t1_pct))
    t2_price = round(entry_price * (1 + t2_pct))

    # Lot size lookup (exchange-fixed) — current as of 2025
    lot_sizes = {"NIFTY": 75, "BANKNIFTY": 35}
    lot_size = lot_sizes.get(idx, 75)

    user_qty = cfg.get("nifty_qty") if idx == "NIFTY" else cfg.get("banknifty_qty")
    if user_qty and user_qty > 0:
        qty = int(user_qty)
        lots = max(1, qty // lot_size)
    else:
        max_qty = calc_scalper_size(entry_price, sl_price, running_capital=capital)
        lots = max(1, max_qty // lot_size)
        qty = lots * lot_size

    # ── HARD CAPITAL ENFORCEMENT ──
    # Total committed (open trades) + this trade MUST NOT exceed user's capital.
    # If it would, shrink THIS trade's qty to fit. If even 1 lot won't fit, REJECT.
    init_scalper_db()
    _conn_check = _conn()
    committed_row = _conn_check.execute("""
        SELECT COALESCE(SUM(COALESCE(capital_used, entry_price * qty)), 0) as committed
        FROM scalper_trades WHERE status='OPEN'
    """).fetchone()
    _conn_check.close()
    committed = committed_row["committed"] or 0
    available = capital - committed
    needed = entry_price * qty

    if needed > available:
        # Try to shrink qty to fit available capital (round down to lot multiple)
        max_affordable_qty = int(available // entry_price)
        max_affordable_qty = (max_affordable_qty // lot_size) * lot_size  # round to lot
        if max_affordable_qty < lot_size:
            # Can't even fit 1 lot — reject
            print(f"[SCALPER] REJECT log: needed ₹{needed:,.0f} > available ₹{available:,.0f} (capital ₹{capital:,.0f}, committed ₹{committed:,.0f}). Cannot fit even 1 lot.")
            return None
        # Shrink to fit
        old_qty = qty
        qty = max_affordable_qty
        lots = qty // lot_size
        print(f"[SCALPER] SHRUNK qty {old_qty}→{qty} ({lots} lots) to fit available ₹{available:,.0f} (was needing ₹{needed:,.0f})")

    capital_used = entry_price * qty

    now = ist_now()
    conn = _conn()
    cursor = conn.execute("""
        INSERT INTO scalper_trades (entry_time, idx, action, strike, expiry,
            entry_price, sl_price, t1_price, t2_price,
            current_ltp, peak_ltp, lots, lot_size, qty,
            status, probability,
            entry_reasoning, entry_bull_pct, entry_bear_pct, entry_spot, capital_used)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?)
    """, (now.isoformat(), idx, action, strike, expiry,
          entry_price, sl_price, t1_price, t2_price,
          entry_price, entry_price, lots, lot_size, qty,
          probability, entry_reasoning, entry_bull_pct, entry_bear_pct,
          entry_spot, capital_used))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()

    print(f"[SCALPER] OPENED #{trade_id}: {action} {idx} {strike} @ ₹{entry_price} | qty {qty} | capital used ₹{capital_used:,.0f} (of ₹{capital:,.0f}) | SL ₹{sl_price} T1 ₹{t1_price}")
    return trade_id


def record_tick(trade_id, ltp, spot=None):
    """Sample tick for an open scalper trade. Call from check_scalper_exits loop."""
    init_scalper_db()
    import time
    conn = _conn()
    try:
        # Get entry context for pnl calc
        row = conn.execute("SELECT entry_price, qty FROM scalper_trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            return
        entry = row["entry_price"]
        qty = row["qty"]
        pnl_rupees = round((ltp - entry) * qty, 2)
        pnl_pct = round((ltp - entry) / entry * 100, 2) if entry > 0 else 0
        conn.execute("""
            INSERT INTO scalper_ticks (trade_id, ts, ltp, spot, pnl_rupees, pnl_pct)
            VALUES (?,?,?,?,?,?)
        """, (trade_id, int(time.time() * 1000), ltp, spot, pnl_rupees, pnl_pct))
        conn.commit()
    except Exception as e:
        print(f"[SCALPER] tick record error: {e}")
    finally:
        conn.close()


def get_trade_ticks(trade_id, limit=500):
    """Return tick history for one trade (chart data)."""
    init_scalper_db()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT ts, ltp, spot, pnl_rupees, pnl_pct
            FROM scalper_ticks WHERE trade_id=?
            ORDER BY ts ASC LIMIT ?
        """, (trade_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def manual_exit(trade_id, current_ltp, reason="MANUAL_EXIT"):
    """User-triggered manual exit of an open scalper trade."""
    init_scalper_db()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM scalper_trades WHERE id=? AND status='OPEN'", (trade_id,)
        ).fetchone()
        if not row:
            return {"error": "Trade not found or already closed"}

        entry_price = row["entry_price"]
        qty = row["qty"]
        exit_price = current_ltp if current_ltp > 0 else (row["current_ltp"] or entry_price)
        pnl_rupees = round((exit_price - entry_price) * qty, 2)
        pnl_pts = round(exit_price - entry_price, 2)

        try:
            entry_dt = datetime.fromisoformat(row["entry_time"])
            hold_sec = int((ist_now() - entry_dt).total_seconds())
        except Exception:
            hold_sec = 0

        result_status = "MANUAL_EXIT"
        exit_reason_str = f"Manual exit by user @ ₹{exit_price:.2f} (PnL ₹{pnl_rupees:+,.0f}, +{round((exit_price/entry_price-1)*100,1)}%, held {hold_sec//60}m{hold_sec%60}s)"

        conn.execute("""
            UPDATE scalper_trades SET
                status=?, exit_time=?, exit_price=?, pnl_rupees=?, pnl_pts=?,
                hold_seconds=?, exit_reason=?
            WHERE id=?
        """, (result_status, ist_now().isoformat(), exit_price, pnl_rupees, pnl_pts,
              hold_sec, exit_reason_str, trade_id))
        conn.commit()

        print(f"[SCALPER] MANUAL EXIT #{trade_id}: ₹{exit_price} | PnL ₹{pnl_rupees:+,.0f}")
        # Record P&L in capital tracker (auto-adjust)
        try:
            from capital_tracker import record_trade_pnl
            record_trade_pnl("SCALPER", pnl_rupees, trade_id=trade_id,
                             description=f"Manual exit @ ₹{exit_price:.2f}")
        except Exception as e:
            print(f"[CAPITAL] manual exit record error: {e}")
        return {
            "ok": True, "trade_id": trade_id, "exit_price": exit_price,
            "pnl_rupees": pnl_rupees, "pnl_pts": pnl_pts, "hold_seconds": hold_sec,
            "reason": exit_reason_str,
        }
    finally:
        conn.close()


def get_capital_usage():
    """Return current capital usage breakdown for scalper."""
    init_scalper_db()
    cfg = get_scalper_config()
    capital = cfg.get("capital", 1000000)
    conn = _conn()
    try:
        # Currently committed (open trades)
        open_rows = conn.execute("""
            SELECT id, entry_price, current_ltp, qty, capital_used, idx, strike, action
            FROM scalper_trades WHERE status='OPEN'
        """).fetchall()
        committed = 0.0
        live_value = 0.0
        unrealized = 0.0
        open_list = []
        for r in open_rows:
            row = dict(r)
            cap_used = row.get("capital_used") or (row["entry_price"] * row["qty"])
            committed += cap_used
            cur_ltp = row.get("current_ltp") or row["entry_price"]
            live_val = cur_ltp * row["qty"]
            live_value += live_val
            unrealized += (cur_ltp - row["entry_price"]) * row["qty"]
            open_list.append({
                "id": row["id"], "idx": row["idx"], "strike": row["strike"],
                "action": row["action"],
                "entry": row["entry_price"], "current": cur_ltp, "qty": row["qty"],
                "capital_used": round(cap_used, 2),
                "live_value": round(live_val, 2),
                "unrealized": round((cur_ltp - row["entry_price"]) * row["qty"], 2),
            })

        # Today's realized P&L
        today = ist_now().strftime("%Y-%m-%d")
        realized = conn.execute("""
            SELECT COALESCE(SUM(pnl_rupees), 0) FROM scalper_trades
            WHERE status!='OPEN' AND date(entry_time)=?
        """, (today,)).fetchone()[0]

        available = capital - committed
        return {
            "capital": capital,
            "committed": round(committed, 2),
            "available": round(available, 2),
            "committed_pct": round((committed / capital * 100), 2) if capital > 0 else 0,
            "live_value": round(live_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_today": round(realized, 2),
            "total_today_pnl": round(realized + unrealized, 2),
            "open_count": len(open_list),
            "open_trades": open_list,
        }
    finally:
        conn.close()


def _eod_close_all_scalper(open_trades, chains, now):
    """Close every open scalper trade at last-known LTP. Status=EOD_CLOSE."""
    if not open_trades:
        return
    conn = _conn()
    closed = 0
    for t in open_trades:
        t = dict(t)
        idx = t["idx"]
        strike = t["strike"]
        action = t["action"]
        chain = chains.get(idx, {}) if chains else {}
        sd = chain.get(strike, {}) if isinstance(chain, dict) else {}
        opt_key = "ce_ltp" if "CE" in action else "pe_ltp"
        # last-known live LTP, fallback to current_ltp / entry
        exit_price = sd.get(opt_key, 0) or t.get("current_ltp") or t.get("entry_price") or 0
        if exit_price <= 0:
            continue
        entry = t.get("entry_price", 0) or 0
        qty = t.get("qty", 0) or 0
        pnl_pts = round(exit_price - entry, 2)
        pnl_rupees = round(pnl_pts * qty, 2)
        try:
            entry_dt = datetime.fromisoformat(t["entry_time"])
            hold_sec = int((now - entry_dt).total_seconds())
        except Exception:
            hold_sec = 0
        try:
            conn.execute("""
                UPDATE scalper_trades SET
                    status='EOD_CLOSE', exit_price=?, exit_time=?, exit_reason=?,
                    pnl_pts=?, pnl_rupees=?, hold_seconds=?, peak_ltp=?
                WHERE id=? AND status='OPEN'
            """, (exit_price, now.isoformat(),
                  "Market closing 3:25 PM — scalper EOD auto-close (options intraday only).",
                  pnl_pts, pnl_rupees, hold_sec, max(t.get("peak_ltp") or entry, exit_price),
                  t["id"]))
            closed += 1
            print(f"[SCALPER-EOD] Closed #{t['id']} {idx} {action} {strike} @ ₹{exit_price} → ₹{pnl_rupees:+,.0f}")
            try:
                from capital_tracker import record_trade_pnl
                record_trade_pnl("SCALPER", pnl_rupees, trade_id=t["id"],
                                 description=f"EOD auto-close: {idx} {action} {strike}")
            except Exception:
                pass
        except Exception as e:
            print(f"[SCALPER-EOD] failed to close #{t.get('id')}: {e}")
    conn.commit()
    conn.close()
    if closed:
        print(f"[SCALPER-EOD] Closed {closed} open scalper trade(s) at 3:25 PM IST.")


def get_market_close_status():
    """Return market-close countdown info for the UI banner.
    States: NORMAL → CLOSING_SOON (3:20-3:25) → CLOSING_NOW (3:25-3:30) → CLOSED
    """
    now = ist_now()
    h, m = now.hour, now.minute
    # Pre-3:20 = normal, 3:20-3:24 = warning, 3:25-3:30 = auto-closing, post = closed
    state = "NORMAL"
    seconds_to_close = None
    if h == 15 and 20 <= m < 25:
        state = "CLOSING_SOON"
        # seconds until 3:25
        target = now.replace(hour=15, minute=25, second=0, microsecond=0)
        seconds_to_close = int((target - now).total_seconds())
    elif h == 15 and 25 <= m < 30:
        state = "AUTO_CLOSING"
        seconds_to_close = 0
    elif h == 15 and m >= 30:
        state = "CLOSED"
    elif h >= 16:
        state = "CLOSED"
    elif h < 9 or (h == 9 and m < 15):
        state = "PRE_OPEN"

    return {
        "state": state,
        "seconds_to_close": seconds_to_close,
        "now_ist": now.strftime("%H:%M:%S"),
        "warning_active": state in ("CLOSING_SOON", "AUTO_CLOSING"),
        "auto_close_active": state == "AUTO_CLOSING",
    }


def check_scalper_exits(chains):
    """Monitor open scalper trades — quick exit logic.
    Smart SL applied if enabled in smart_sl_config (else static SL)."""
    conn = _conn()
    open_trades = conn.execute(
        "SELECT * FROM scalper_trades WHERE status='OPEN'"
    ).fetchall()
    conn.close()

    smart_cfg = get_smart_sl_config()
    smart_enabled = smart_cfg.get("enabled", False)
    spot_anchor_pct = smart_cfg.get("spot_anchor_pct", 0.4)

    now = ist_now()

    # ── EOD AUTO-CLOSE: close all scalper trades at 3:25 PM IST ──
    # Options are intraday only. Holding past 3:25 risks settlement at
    # closing-auction prices and loss of any meaningful exit liquidity.
    if now.hour == 15 and now.minute >= 25:
        _eod_close_all_scalper(open_trades, chains, now)
        return

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
        sl_reason_text = None

        # ─── SMART SL LADDER (if enabled) ───
        smart_active_sl = sl  # fallback to static
        smart_stage = 0
        smart_label = "Static"
        if smart_enabled:
            saved_sl = t.get("smart_sl_value") or sl
            saved_stage = t.get("smart_sl_stage") or 0
            smart_active_sl, smart_stage, smart_label = compute_smart_sl(
                entry, current_ltp,
                current_stage_saved=saved_stage,
                saved_sl=saved_sl,
            )

        # ─── SPOT ANCHOR check (if enabled) ───
        spot_exit = False
        spot_reason = None
        if smart_enabled:
            spot_token_idx = idx
            entry_spot = t.get("entry_spot")
            # Get current spot from chains (use any strike's underlying — chain doesn't store spot directly)
            # Better: spot from engine is accessible via global; fallback to skip
            try:
                # We don't have engine ref here; use approximate: spot ≈ strike + (CE_ltp - PE_ltp) at ATM.
                # But simpler: rely on entry_spot stored, current_spot from engine.spot_tokens
                # For now, check_scalper_exits is called with chains only — skip spot anchor if entry_spot missing
                if entry_spot:
                    # Pull from chain's "underlying_value" if available
                    # Fallback: use peak_ltp_strike as proxy (not ideal). Skip if can't determine.
                    pass
            except Exception:
                pass

        # ─── Exit logic (priority order) ───
        active_sl_used = smart_active_sl if smart_enabled else sl

        if current_ltp <= active_sl_used:
            new_status = "SL_HIT"
            exit_price = active_sl_used
            if smart_enabled:
                exit_reason = f"Smart SL hit (Stage {smart_stage} - {smart_label}) at ₹{active_sl_used:.2f}"
                sl_reason_text = f"Profit ladder triggered: Stage {smart_stage} ({smart_label}). Premium ₹{current_ltp:.2f} hit SL ₹{active_sl_used:.2f}"
            else:
                exit_reason = f"SL hit at ₹{sl:.2f}"
                sl_reason_text = f"Static SL hit. Premium ₹{current_ltp:.2f} ≤ SL ₹{sl:.2f}"
        elif spot_exit:
            new_status = "SPOT_ANCHOR_EXIT"
            exit_price = current_ltp
            exit_reason = spot_reason
            sl_reason_text = spot_reason
        elif current_ltp >= t2:
            new_status = "T2_HIT"
            exit_price = t2
            exit_reason = f"T2 hit at ₹{t2}"
        elif current_ltp >= t1:
            new_status = "T1_HIT"
            exit_price = t1
            exit_reason = f"T1 hit at ₹{t1}"
        elif hold_sec >= SCALPER_MAX_HOLD_MIN * 60:
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
                    pnl_pts=?, pnl_rupees=?, peak_ltp=?, hold_seconds=?,
                    sl_hit_time=?, sl_reason=?, smart_sl_stage=?, smart_sl_value=?
                WHERE id=?
            """, (new_status, exit_price, now.isoformat(), exit_reason,
                  round(exit_price - entry, 2), final_pnl, peak, int(hold_sec),
                  now.isoformat() if "SL" in new_status else None,
                  sl_reason_text,
                  smart_stage if smart_enabled else None,
                  smart_active_sl if smart_enabled else None,
                  t["id"]))
            print(f"[SCALPER] CLOSED #{t['id']} {idx} {action} {strike}: ₹{final_pnl:+,.0f} ({new_status})")
            # ── Record P&L in capital tracker (auto-adjust running capital + profit bank) ──
            try:
                from capital_tracker import record_trade_pnl
                desc = f"{idx} {action} {strike} @ ₹{exit_price} ({new_status})"
                record_trade_pnl("SCALPER", final_pnl, trade_id=t["id"], description=desc)
            except Exception as e:
                print(f"[CAPITAL] scalper record error: {e}")
        else:
            conn2.execute("""
                UPDATE scalper_trades SET
                    current_ltp=?, peak_ltp=?, pnl_pts=?, pnl_rupees=?, hold_seconds=?,
                    smart_sl_stage=?, smart_sl_value=?
                WHERE id=?
            """, (current_ltp, peak, pnl_pts, pnl_rupees, int(hold_sec),
                  smart_stage if smart_enabled else None,
                  smart_active_sl if smart_enabled else None,
                  t["id"]))
            # Record tick sample (live LTP history per trade)
            try:
                import time as _time
                conn2.execute("""
                    INSERT INTO scalper_ticks (trade_id, ts, ltp, spot, pnl_rupees, pnl_pct)
                    VALUES (?,?,?,?,?,?)
                """, (t["id"], int(_time.time() * 1000), current_ltp, None, pnl_rupees,
                      round((current_ltp - entry) / entry * 100, 2) if entry > 0 else 0))
            except Exception:
                pass

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
_scalper_enabled = True  # ALWAYS ON — user wants live tick accuracy preserved


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
