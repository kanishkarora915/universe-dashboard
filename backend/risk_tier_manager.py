"""
Adaptive Risk Tier System (A5) — Smart sizing instead of pauses.

User's insight: "Pause after losses is dumb — opportunity miss bigger than loss"

Philosophy:
  Loss aana = market regime tough hai.
  Pause karne se REAL setup miss hota hai.
  Solution: ADAPT (smaller qty + tighter SL + higher threshold) NOT PAUSE.

Tier System (auto-progresses through day):
  Tier 1 (default):  Threshold 50%, Qty 100%, SL ATR×1.0, Targets ATR×1.5/3.0
  Tier 2 (3-4 SLs):  Threshold 65%, Qty 75%,  SL ATR×0.7, Targets ATR×1.2/2.4
  Tier 3 (5+ SLs):   Threshold 75%, Qty 50%,  SL ATR×0.5, Targets ATR×1.0/2.0
  Tier 4 (>5% loss): Threshold 80%, Qty 25%,  SL ATR×0.3, Targets ATR×0.8/1.5

Auto-Reset:
  2 wins in a row → drop one tier
  3 wins in a row → back to Tier 1
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "risk_tier.db"


TIERS = {
    1: {
        "name": "NORMAL",
        "min_probability": 50,
        "qty_multiplier": 1.0,
        "sl_atr_mult": 1.0,
        "t1_atr_mult": 1.5,
        "t2_atr_mult": 3.0,
    },
    2: {
        "name": "CAUTIOUS",
        "min_probability": 65,
        "qty_multiplier": 0.75,
        "sl_atr_mult": 0.7,
        "t1_atr_mult": 1.2,
        "t2_atr_mult": 2.4,
    },
    3: {
        "name": "DEFENSIVE",
        "min_probability": 75,
        "qty_multiplier": 0.5,
        "sl_atr_mult": 0.5,
        "t1_atr_mult": 1.0,
        "t2_atr_mult": 2.0,
    },
    4: {
        "name": "SURVIVAL",
        "min_probability": 80,
        "qty_multiplier": 0.25,
        "sl_atr_mult": 0.3,
        "t1_atr_mult": 0.8,
        "t2_atr_mult": 1.5,
    },
}


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tier_state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            current_tier INTEGER DEFAULT 1,
            win_streak INTEGER DEFAULT 0,
            loss_streak INTEGER DEFAULT 0,
            today_sl_count INTEGER DEFAULT 0,
            today_loss_pct REAL DEFAULT 0.0,
            today_date TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tier_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            event TEXT,
            from_tier INTEGER,
            to_tier INTEGER,
            reason TEXT
        )
    """)
    today = ist_now().strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR IGNORE INTO tier_state (id, current_tier, today_date, updated_at) VALUES (1, 1, ?, ?)",
        (today, ist_now().isoformat())
    )
    conn.commit()
    conn.close()


def get_state():
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tier_state WHERE id=1").fetchone()
    conn.close()
    if not row:
        return {"current_tier": 1, "win_streak": 0, "loss_streak": 0,
                "today_sl_count": 0, "today_loss_pct": 0.0}
    state = dict(row)

    # Reset on new day
    today = ist_now().strftime("%Y-%m-%d")
    if state.get("today_date") != today:
        reset_for_new_day()
        return get_state()

    state["tier_config"] = TIERS.get(state["current_tier"], TIERS[1])
    return state


def reset_for_new_day():
    """Called at start of new trading day."""
    init_db()
    today = ist_now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        UPDATE tier_state SET
            current_tier=1, win_streak=0, loss_streak=0,
            today_sl_count=0, today_loss_pct=0.0,
            today_date=?, updated_at=?
        WHERE id=1
    """, (today, ist_now().isoformat()))
    conn.commit()
    conn.close()
    print(f"[RISK-TIER] Daily reset to Tier 1 for {today}")


def record_trade_outcome(status, pnl_rupees=0, capital=1000000):
    """Called after every closed trade. Updates tier state.

    status: T1_HIT, T2_HIT, TRAIL_EXIT (wins) / SL_HIT, REVERSAL_EXIT (losses) / BREAKEVEN_EXIT (neutral)
    """
    init_db()
    state = get_state()
    cur_tier = state["current_tier"]
    win_streak = state["win_streak"]
    loss_streak = state["loss_streak"]
    sl_count = state["today_sl_count"]
    loss_pct = state["today_loss_pct"] or 0

    is_win = status in ("T1_HIT", "T2_HIT", "TRAIL_EXIT")
    is_loss = status in ("SL_HIT", "REVERSAL_EXIT")

    new_tier = cur_tier
    transition_event = None
    transition_reason = None

    if is_win:
        win_streak += 1
        loss_streak = 0
        # Auto-promote: 2 wins → drop one tier
        if win_streak >= 2 and cur_tier > 1:
            old = new_tier
            new_tier = max(1, cur_tier - 1)
            transition_event = "PROMOTE"
            transition_reason = f"{win_streak} wins streak — promoted Tier {old} → {new_tier}"
        # 3 wins → straight to Tier 1
        if win_streak >= 3 and cur_tier > 1:
            new_tier = 1
            transition_event = "PROMOTE"
            transition_reason = f"{win_streak} wins streak — back to NORMAL (Tier 1)"

    elif is_loss:
        loss_streak += 1
        win_streak = 0
        if status == "SL_HIT":
            sl_count += 1
        if pnl_rupees < 0 and capital > 0:
            loss_pct += abs(pnl_rupees / capital * 100)

        # Demote logic
        if loss_pct > 5.0 and cur_tier < 4:
            new_tier = 4
            transition_event = "DEMOTE"
            transition_reason = f"Daily loss {loss_pct:.1f}% > 5% — SURVIVAL mode"
        elif sl_count >= 5 and cur_tier < 3:
            new_tier = 3
            transition_event = "DEMOTE"
            transition_reason = f"{sl_count} SLs today — DEFENSIVE mode"
        elif sl_count >= 3 and cur_tier < 2:
            new_tier = 2
            transition_event = "DEMOTE"
            transition_reason = f"{sl_count} SLs today — CAUTIOUS mode"

    # Save state
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        UPDATE tier_state SET
            current_tier=?, win_streak=?, loss_streak=?,
            today_sl_count=?, today_loss_pct=?, updated_at=?
        WHERE id=1
    """, (new_tier, win_streak, loss_streak, sl_count, loss_pct, ist_now().isoformat()))

    if transition_event and new_tier != cur_tier:
        conn.execute("""
            INSERT INTO tier_history (ts, event, from_tier, to_tier, reason)
            VALUES (?, ?, ?, ?, ?)
        """, (ist_now().isoformat(), transition_event, cur_tier, new_tier, transition_reason))
        print(f"[RISK-TIER] {transition_event}: Tier {cur_tier} → {new_tier} ({transition_reason})")

    conn.commit()
    conn.close()

    return {
        "tier": new_tier,
        "tier_config": TIERS[new_tier],
        "win_streak": win_streak,
        "loss_streak": loss_streak,
        "sl_count": sl_count,
        "loss_pct": round(loss_pct, 2),
        "transition": transition_event,
        "reason": transition_reason,
    }


def get_tier_qty_multiplier():
    """Quick lookup for trade_logger.py."""
    return get_state().get("tier_config", TIERS[1])["qty_multiplier"]


def get_tier_min_probability():
    """Min probability required for current tier."""
    return get_state().get("tier_config", TIERS[1])["min_probability"]


def get_tier_atr_multipliers():
    """Returns (sl_mult, t1_mult, t2_mult) for ATR target calc."""
    cfg = get_state().get("tier_config", TIERS[1])
    return cfg["sl_atr_mult"], cfg["t1_atr_mult"], cfg["t2_atr_mult"]


def get_summary():
    state = get_state()
    cfg = state.get("tier_config", TIERS[1])
    return {
        "tier": state["current_tier"],
        "tier_name": cfg["name"],
        "tier_config": cfg,
        "win_streak": state["win_streak"],
        "loss_streak": state["loss_streak"],
        "today_sl_count": state["today_sl_count"],
        "today_loss_pct": round(state["today_loss_pct"] or 0, 2),
        "all_tiers": TIERS,
    }


def get_history(limit=50):
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM tier_history ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
