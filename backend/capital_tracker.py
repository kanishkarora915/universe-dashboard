"""
Capital Tracker — Independent capital management per system.

Two systems:
  - SCALPER  → scalper trades drive capital
  - MAIN     → main P&L trades drive capital

Logic:
  - Base capital is the TARGET LEVEL (e.g. ₹10L)
  - Current capital fluctuates with realized P&L
  - On profit: repair capital first (if below base), excess → profit_bank
  - On loss: capital reduces (profit_bank UNTOUCHED — never consumed)
  - Trade qty sizing uses CURRENT capital (smaller after losses)
  - Profit bank manually withdrawable

DB: /data/capital_tracker.db (persistent, never auto-deleted)
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "capital_tracker.db"

# Default base capital per system
DEFAULT_BASE = {
    "SCALPER": 1000000,
    "MAIN": 1000000,
}


def ist_now():
    return datetime.now(IST)


_pragma_done = False
def _conn():
    global _pragma_done
    init_db()
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    if not _pragma_done:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            _pragma_done = True
        except Exception:
            pass
    return conn


def init_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    # Per-system state (singleton row each)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capital_state (
            system TEXT PRIMARY KEY,
            base_capital REAL NOT NULL,
            current_capital REAL NOT NULL,
            profit_bank REAL DEFAULT 0,
            loss_recovered REAL DEFAULT 0,
            total_withdrawn REAL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    # Detailed history of every capital adjustment
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capital_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT NOT NULL,
            ts TEXT NOT NULL,
            event_type TEXT NOT NULL,
            amount REAL NOT NULL,
            capital_before REAL,
            capital_after REAL,
            profit_bank_before REAL,
            profit_bank_after REAL,
            trade_id INTEGER,
            description TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ch_system_ts ON capital_history(system, ts DESC)")

    # Initialize default rows if missing
    for system, default_base in DEFAULT_BASE.items():
        existing = conn.execute("SELECT 1 FROM capital_state WHERE system=?", (system,)).fetchone()
        if not existing:
            now_iso = ist_now().isoformat()
            conn.execute("""
                INSERT INTO capital_state
                (system, base_capital, current_capital, profit_bank, loss_recovered, total_withdrawn, created_at, updated_at)
                VALUES (?, ?, ?, 0, 0, 0, ?, ?)
            """, (system, default_base, default_base, now_iso, now_iso))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# READS
# ═══════════════════════════════════════════════════════════════

def get_state(system):
    """Return full capital state dict for a system."""
    if system not in DEFAULT_BASE:
        return {"error": f"Invalid system: {system}"}
    init_db()
    conn = _conn()
    row = conn.execute("SELECT * FROM capital_state WHERE system=?", (system,)).fetchone()
    conn.close()
    if not row:
        return {"error": f"No state for {system}"}
    d = dict(row)
    d["below_base"] = d["current_capital"] < d["base_capital"]
    d["repair_needed"] = max(0, d["base_capital"] - d["current_capital"])
    return d


def get_running_capital(system):
    """Returns current capital for sizing trades. Used by qty calculators."""
    s = get_state(system)
    if "error" in s:
        return DEFAULT_BASE.get(system, 1000000)
    return s["current_capital"]


def get_history(system, limit=50):
    """Returns recent capital adjustments."""
    init_db()
    conn = _conn()
    rows = conn.execute("""
        SELECT * FROM capital_history WHERE system=?
        ORDER BY ts DESC LIMIT ?
    """, (system, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
# AUTO-ADJUST LOGIC (called from trade close hooks)
# ═══════════════════════════════════════════════════════════════

def record_trade_pnl(system, amount, trade_id=None, description=None):
    """Apply auto-adjust logic for a closed trade.

    PROFIT: repair capital first (if below base), excess → profit_bank
    LOSS:   capital reduces, profit_bank UNTOUCHED
    """
    if system not in DEFAULT_BASE:
        return {"error": f"Invalid system: {system}"}
    if amount == 0:
        return {"ok": True, "no_change": True}

    init_db()
    conn = _conn()
    state = conn.execute("SELECT * FROM capital_state WHERE system=?", (system,)).fetchone()
    if not state:
        conn.close()
        return {"error": "State row missing"}
    state = dict(state)

    base = state["base_capital"]
    current = state["current_capital"]
    bank = state["profit_bank"]
    loss_rec = state["loss_recovered"]

    capital_before = current
    bank_before = bank
    new_current = current
    new_bank = bank
    new_loss_rec = loss_rec
    events = []

    if amount > 0:  # ── PROFIT ──
        if current < base:
            # Repair capital first
            repair_needed = base - current
            repair_amount = min(amount, repair_needed)
            new_current = current + repair_amount
            new_loss_rec = loss_rec + repair_amount
            events.append({
                "type": "PROFIT_REPAIR",
                "amount": repair_amount,
                "desc": f"₹{repair_amount:,.0f} of profit used to restore capital toward base ₹{base:,.0f}",
            })
            # Any leftover goes to profit_bank
            leftover = amount - repair_amount
            if leftover > 0:
                new_bank = bank + leftover
                events.append({
                    "type": "PROFIT_BANK",
                    "amount": leftover,
                    "desc": f"₹{leftover:,.0f} excess profit → Profit Bank",
                })
        else:
            # Capital already at/above base — all to profit_bank
            new_bank = bank + amount
            events.append({
                "type": "PROFIT_BANK",
                "amount": amount,
                "desc": f"₹{amount:,.0f} profit → Profit Bank (capital already at base)",
            })

    else:  # ── LOSS ──
        loss = abs(amount)
        new_current = current - loss
        events.append({
            "type": "LOSS",
            "amount": -loss,
            "desc": f"Capital reduced by ₹{loss:,.0f} (profit_bank untouched)",
        })

    # Apply state update
    now_iso = ist_now().isoformat()
    conn.execute("""
        UPDATE capital_state
        SET current_capital=?, profit_bank=?, loss_recovered=?, updated_at=?
        WHERE system=?
    """, (new_current, new_bank, new_loss_rec, now_iso, system))

    # Log every event in history
    for ev in events:
        conn.execute("""
            INSERT INTO capital_history
            (system, ts, event_type, amount, capital_before, capital_after,
             profit_bank_before, profit_bank_after, trade_id, description)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            system, now_iso, ev["type"], ev["amount"],
            capital_before, new_current, bank_before, new_bank,
            trade_id, ev["desc"],
        ))

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "system": system,
        "amount": amount,
        "capital_before": capital_before,
        "capital_after": new_current,
        "profit_bank_before": bank_before,
        "profit_bank_after": new_bank,
        "events": events,
    }


# ═══════════════════════════════════════════════════════════════
# MANUAL ACTIONS
# ═══════════════════════════════════════════════════════════════

def withdraw_profit_bank(system, amount=None):
    """User-triggered withdrawal from Profit Bank.
    If amount=None, withdraws ALL bank."""
    state = get_state(system)
    if "error" in state:
        return state
    bank = state["profit_bank"]
    if bank <= 0:
        return {"error": "Profit Bank is empty"}

    withdraw_amount = bank if amount is None else min(amount, bank)
    new_bank = bank - withdraw_amount
    new_total_withdrawn = state["total_withdrawn"] + withdraw_amount

    conn = _conn()
    now_iso = ist_now().isoformat()
    conn.execute("""
        UPDATE capital_state SET profit_bank=?, total_withdrawn=?, updated_at=?
        WHERE system=?
    """, (new_bank, new_total_withdrawn, now_iso, system))
    conn.execute("""
        INSERT INTO capital_history
        (system, ts, event_type, amount, capital_before, capital_after,
         profit_bank_before, profit_bank_after, description)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        system, now_iso, "WITHDRAW", -withdraw_amount,
        state["current_capital"], state["current_capital"],
        bank, new_bank,
        f"Manual withdrawal of ₹{withdraw_amount:,.0f} from Profit Bank",
    ))
    conn.commit()
    conn.close()

    return {
        "ok": True, "withdrawn": withdraw_amount,
        "profit_bank_remaining": new_bank,
        "total_withdrawn_lifetime": new_total_withdrawn,
    }


def set_base_capital(system, new_base):
    """Update base capital (target level). Doesn't change current."""
    if new_base <= 0:
        return {"error": "Base capital must be > 0"}
    state = get_state(system)
    if "error" in state:
        return state
    conn = _conn()
    now_iso = ist_now().isoformat()
    conn.execute("""
        UPDATE capital_state SET base_capital=?, updated_at=? WHERE system=?
    """, (new_base, now_iso, system))
    conn.execute("""
        INSERT INTO capital_history
        (system, ts, event_type, amount, capital_before, capital_after,
         profit_bank_before, profit_bank_after, description)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        system, now_iso, "BASE_CHANGE", new_base - state["base_capital"],
        state["current_capital"], state["current_capital"],
        state["profit_bank"], state["profit_bank"],
        f"Base capital changed: ₹{state['base_capital']:,.0f} → ₹{new_base:,.0f}",
    ))
    conn.commit()
    conn.close()
    return {"ok": True, "new_base": new_base}


def reset_capital(system, to_base=True):
    """Reset current capital to base level. Use with caution.
    If to_base=False, sets current = 0 (full reset)."""
    state = get_state(system)
    if "error" in state:
        return state
    new_current = state["base_capital"] if to_base else 0
    conn = _conn()
    now_iso = ist_now().isoformat()
    conn.execute("""
        UPDATE capital_state SET current_capital=?, updated_at=? WHERE system=?
    """, (new_current, now_iso, system))
    conn.execute("""
        INSERT INTO capital_history
        (system, ts, event_type, amount, capital_before, capital_after,
         profit_bank_before, profit_bank_after, description)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        system, now_iso, "RESET", new_current - state["current_capital"],
        state["current_capital"], new_current,
        state["profit_bank"], state["profit_bank"],
        f"Capital reset to {'base' if to_base else 'zero'}: ₹{new_current:,.0f}",
    ))
    conn.commit()
    conn.close()
    return {"ok": True, "new_current": new_current}


def backfill_from_trades(system):
    """Scan existing closed trades from the DB and replay through tracker.
    Builds full historical capital state from past trades.

    SCALPER → reads scalper_trades.db
    MAIN    → reads trades.db (via trade_logger DB_PATH)
    """
    if system not in DEFAULT_BASE:
        return {"error": f"Invalid system: {system}"}

    # Reset to clean state first (avoid double counting)
    init_db()
    conn = _conn()
    base = conn.execute(
        "SELECT base_capital FROM capital_state WHERE system=?", (system,)
    ).fetchone()
    base_amt = base["base_capital"] if base else DEFAULT_BASE[system]

    now_iso = ist_now().isoformat()
    # Reset state to clean
    conn.execute("""
        UPDATE capital_state
        SET current_capital=?, profit_bank=0, loss_recovered=0, total_withdrawn=0, updated_at=?
        WHERE system=?
    """, (base_amt, now_iso, system))
    conn.execute("DELETE FROM capital_history WHERE system=?", (system,))
    conn.commit()
    conn.close()

    # Now scan trades and replay
    closed_trades = []

    if system == "SCALPER":
        from pathlib import Path
        scalper_db = Path("/data/scalper_trades.db") if Path("/data").is_dir() \
                     else Path(__file__).parent / "scalper_trades.db"
        if not scalper_db.exists():
            return {"ok": True, "replayed": 0, "message": "No scalper DB found"}
        sc = sqlite3.connect(str(scalper_db))
        sc.row_factory = sqlite3.Row
        try:
            rows = sc.execute("""
                SELECT id, entry_time, exit_time, idx, action, strike,
                       entry_price, exit_price, pnl_rupees, status
                FROM scalper_trades
                WHERE status != 'OPEN' AND pnl_rupees IS NOT NULL
                ORDER BY exit_time ASC, entry_time ASC
            """).fetchall()
            closed_trades = [dict(r) for r in rows]
        finally:
            sc.close()

    elif system == "MAIN":
        from pathlib import Path
        # Locate trades.db (usually on persistent disk)
        main_db = Path("/data/trades.db") if Path("/data/trades.db").exists() \
                  else Path(__file__).parent / "trades.db"
        if not main_db.exists():
            return {"ok": True, "replayed": 0, "message": "No main trades DB found"}
        mc = sqlite3.connect(str(main_db))
        mc.row_factory = sqlite3.Row
        try:
            rows = mc.execute("""
                SELECT id, entry_time, exit_time, idx, action, strike,
                       entry_price, exit_price, pnl_rupees, status
                FROM trades
                WHERE status != 'OPEN' AND pnl_rupees IS NOT NULL
                ORDER BY exit_time ASC, entry_time ASC
            """).fetchall()
            closed_trades = [dict(r) for r in rows]
        finally:
            mc.close()

    # Replay each trade through record_trade_pnl
    replayed = 0
    total_pnl = 0
    for t in closed_trades:
        pnl = t.get("pnl_rupees") or 0
        if pnl == 0:
            continue
        desc = f"{t.get('idx')} {t.get('action')} {t.get('strike')} @ ₹{t.get('exit_price', 0):.2f} ({t.get('status')})"
        try:
            record_trade_pnl(
                system,
                pnl,
                trade_id=t.get("id"),
                description=f"[BACKFILL] {desc}",
            )
            replayed += 1
            total_pnl += pnl
        except Exception as e:
            print(f"[CAPITAL] backfill error trade #{t.get('id')}: {e}")

    final_state = get_state(system)
    return {
        "ok": True,
        "system": system,
        "replayed": replayed,
        "total_pnl_replayed": round(total_pnl, 2),
        "final_capital": final_state.get("current_capital"),
        "final_profit_bank": final_state.get("profit_bank"),
        "final_loss_recovered": final_state.get("loss_recovered"),
    }


def get_summary(system):
    """One-shot summary for UI."""
    state = get_state(system)
    if "error" in state:
        return state
    history = get_history(system, limit=20)
    return {
        "system": system,
        "base_capital": state["base_capital"],
        "current_capital": state["current_capital"],
        "profit_bank": state["profit_bank"],
        "loss_recovered": state["loss_recovered"],
        "total_withdrawn": state["total_withdrawn"],
        "below_base": state["below_base"],
        "repair_needed": state["repair_needed"],
        "deficit_pct": round((state["repair_needed"] / state["base_capital"]) * 100, 2) if state["base_capital"] > 0 else 0,
        "growth_pct": round(((state["current_capital"] - state["base_capital"]) / state["base_capital"]) * 100, 2) if state["base_capital"] > 0 else 0,
        "history": history,
    }
