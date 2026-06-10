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
    # Tuned 2026-05-07 per user feedback: actual closed trades exited at
    # +0.6% to +1.76% (REVERSAL_EXIT) while T1/T2 were set at +50%/+100%
    # — fantasy targets, real cuts at micro-reversals.
    # Death-by-thousand-cuts: 4 wins of +1.21% wiped out by 1 loss at -7.9%.
    # New target: realistic T1 +5% (achievable, locks profit), T2 +12% (stretch),
    # max loss capped at -5% (asymmetry fixed: 1:1 → 1:2.4 R:R).
    "breakeven_pct": 3.0,            # +3% triggers BE (was +20%, never hit)
    "trail_giveback_pct": 30.0,      # 30% peak trail (between old 25% and 50%)
    "tight_trail_giveback_pct": 20.0,  # @ +8% profit, lock 80%
    "tight_trail_trigger_pct": 8.0,    # tighten earlier (was +60%, fantasy)
    "reversal_exit_pct": -5.0,       # MAX LOSS CAP — never beyond -5% (was -8%)
    "reversal_exit_min_hold_sec": 120,  # 2 min hold (was 10 min, allow faster cuts)
    "early_neg_exit_pct": -3.0,      # NEW: if 30-min strike trend down, exit at -3%
    "t1_partial_booking": False,     # No partial — ride full position
    "t1_partial_pct": 0,
    "conviction_exit_enabled": False,
    "conviction_exit_threshold": 30,
    "conviction_exit_min_profit": 5,  # was +15%, lower bar for protective exit
    "engine_flip_cycles": 2,         # 2 flips (was 3, react faster)
    "scalper_max_hold_min": 180,
    "scalper_cooldown_same_strike_min": 2,
    "scalper_cooldown_flip_min": 5,
    "scalper_sl_pct": 0.05,          # MAX 5% LOSS (was 18%) — hard cap
    "scalper_t1_pct": 0.05,          # T1 +5% (was +50%, never realistic)
    "scalper_t2_pct": 0.12,          # T2 +12% (was +100%, mid of 10-15%)
    "post_t2_trail_giveback_pct": 30.0,  # NEW: after T2, trail 30% of peak-from-T2
    "post_t2_lock_t2": True,         # NEW: SL never below T2 once T2 crossed
}


def init_db():
    # 2026-06-10: default changed HEDGER → BUYER
    # System is designed for option buying. HEDGER default caused Main mode
    # to use restrictive seller/hedger thresholds (10m cooldown, 30m max
    # hold) and effectively block all entries. Fresh installs / DB resets
    # now correctly start in BUYER mode.
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS buyer_mode_state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            mode TEXT DEFAULT 'BUYER',
            updated_at TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO buyer_mode_state (id, mode, updated_at) VALUES (1, 'BUYER', ?)",
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
        return row[0] if row else "BUYER"
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
