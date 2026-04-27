"""
BUYER MODE — philosophy toggle for option buyers.

One switch flips 8 thresholds across trade_logger + scalper_mode:
  - Breakeven trigger (2% → 20%)
  - Peak trail give-back (50% → 25%)
  - Reversal exit (-3% → -8%)
  - T1 partial booking (on → off)
  - Conviction-based exit (on → off)
  - Engine flip cycles needed (1 → 3)
  - Scalper max hold (30min → 180min)
  - Scalper re-entry cooldown (10min → 2min)

HEDGER mode (default): conservative, capital-protection focused
BUYER mode: aggressive, trend-riding focused

Storage: SQLite singleton row in /data/buyer_mode.db
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "buyer_mode.db"


# ─────────────────────────────────────────────────────────
# THRESHOLDS — single source of truth
# ─────────────────────────────────────────────────────────

HEDGER_DEFAULTS = {
    "mode": "HEDGER",
    "breakeven_pct": 2.0,            # +2% triggers BE
    "trail_giveback_pct": 50.0,      # 50% peak trail
    "tight_trail_giveback_pct": 25.0,  # @ +35% profit, 75% lock
    "tight_trail_trigger_pct": 35.0,
    "reversal_exit_pct": -3.0,       # -3% after 10min
    "reversal_exit_min_hold_sec": 600,  # 10 min minimum hold
    "t1_partial_booking": True,      # T1 books 50% qty
    "t1_partial_pct": 50,            # 50% qty exits at T1
    "conviction_exit_enabled": True,  # conviction drop → BE
    "conviction_exit_threshold": 50,  # below 50% conviction
    "conviction_exit_min_profit": 5,  # only if profit > +5%
    "engine_flip_cycles": 1,         # 1 verdict cycle = exit
    "scalper_max_hold_min": 30,
    "scalper_cooldown_same_strike_min": 10,
    "scalper_cooldown_flip_min": 15,
    "scalper_sl_pct": 0.12,
    "scalper_t1_pct": 0.20,
    "scalper_t2_pct": 0.40,
}

BUYER_DEFAULTS = {
    "mode": "BUYER",
    "breakeven_pct": 20.0,           # +20% triggers BE — meaningful move
    "trail_giveback_pct": 25.0,      # 25% trail — let it breathe
    "tight_trail_giveback_pct": 15.0,  # @ +60% profit, lock 85%
    "tight_trail_trigger_pct": 60.0,
    "reversal_exit_pct": -8.0,       # -8% only after 10min (hard SL still 15%)
    "reversal_exit_min_hold_sec": 600,
    "t1_partial_booking": False,     # NO partial book — ride full to T2
    "t1_partial_pct": 0,
    "conviction_exit_enabled": False,  # ignore conviction noise
    "conviction_exit_threshold": 30,   # only if conv tanks below 30%
    "conviction_exit_min_profit": 15,  # and only if big profit (>+15%)
    "engine_flip_cycles": 3,         # need 3 consecutive flips
    "scalper_max_hold_min": 180,     # 3 hours
    "scalper_cooldown_same_strike_min": 2,  # quick re-entry
    "scalper_cooldown_flip_min": 5,
    "scalper_sl_pct": 0.18,          # wider SL (18%)
    "scalper_t1_pct": 0.50,          # T1 +50% (not partial-booked anyway)
    "scalper_t2_pct": 1.00,          # T2 +100% (home run target)
}


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS buyer_mode_state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            mode TEXT DEFAULT 'HEDGER',
            updated_at TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO buyer_mode_state (id, mode, updated_at) VALUES (1, 'HEDGER', ?)",
        (datetime.now(IST).isoformat(),)
    )
    # Custom overrides table (advanced users can tune individual thresholds)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS buyer_mode_overrides (
            id INTEGER PRIMARY KEY CHECK (id=1),
            overrides_json TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_mode():
    """Returns current mode: 'HEDGER' or 'BUYER'."""
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute("SELECT mode FROM buyer_mode_state WHERE id=1").fetchone()
        return row[0] if row else "HEDGER"
    finally:
        conn.close()


def set_mode(mode):
    """Set mode to 'HEDGER' or 'BUYER'."""
    if mode not in ("HEDGER", "BUYER"):
        raise ValueError(f"Invalid mode: {mode}")
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            "UPDATE buyer_mode_state SET mode=?, updated_at=? WHERE id=1",
            (mode, datetime.now(IST).isoformat())
        )
        conn.commit()
        print(f"[BUYER-MODE] Switched to {mode}")
        return mode
    finally:
        conn.close()


def get_thresholds():
    """Returns full threshold dict for current mode (with any custom overrides applied)."""
    init_db()
    mode = get_mode()
    base = BUYER_DEFAULTS.copy() if mode == "BUYER" else HEDGER_DEFAULTS.copy()

    # Apply custom overrides
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute("SELECT overrides_json FROM buyer_mode_overrides WHERE id=1").fetchone()
        if row and row[0]:
            import json
            try:
                overrides = json.loads(row[0])
                base.update(overrides)
            except Exception:
                pass
    finally:
        conn.close()

    return base


def set_overrides(overrides):
    """Save custom threshold overrides (overrides take precedence over mode defaults)."""
    init_db()
    import json
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO buyer_mode_overrides (id, overrides_json, updated_at) VALUES (1, ?, ?)",
            (json.dumps(overrides), datetime.now(IST).isoformat())
        )
        conn.commit()
    finally:
        conn.close()


def reset_overrides():
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("DELETE FROM buyer_mode_overrides WHERE id=1")
        conn.commit()
    finally:
        conn.close()


def is_buyer_mode():
    return get_mode() == "BUYER"


def get_summary():
    """For UI: current mode + key threshold previews."""
    mode = get_mode()
    th = get_thresholds()
    return {
        "mode": mode,
        "is_buyer": mode == "BUYER",
        "thresholds": th,
        "hedger_defaults": HEDGER_DEFAULTS,
        "buyer_defaults": BUYER_DEFAULTS,
    }
