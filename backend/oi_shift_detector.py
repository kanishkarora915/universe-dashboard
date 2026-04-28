"""
OI Shift Detector (A2) — Wall movement tracking.

Smart money trick:
  9:30 AM: Wall at 24500 CE (50L OI) — retail thinks "resistance"
  10:00 AM: Smart money covers 50L → 30L at 24500
  10:00 AM: Smart money writes 60L at 24550 NEW WALL
  Retail still trades against 24500 → STOP HUNTED
  Real wall is 24550, retail PE trades fail

This module:
  1. Snapshots top 3 CE/PE walls every 5 min
  2. Detects when wall SHIFTS to different strike (>20% OI move)
  3. Alerts: "Wall shifted 24500 → 24550 in last 30 min"
  4. Blocks trades AGAINST the new (real) wall direction
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "oi_shifts.db"


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wall_snapshots (
            ts TEXT,
            idx TEXT,
            side TEXT,         -- CE or PE
            strike INTEGER,
            oi INTEGER,
            rank INTEGER,      -- 1, 2, 3 (top 3 walls)
            PRIMARY KEY (ts, idx, side, strike)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shift_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            idx TEXT,
            side TEXT,
            from_strike INTEGER,
            to_strike INTEGER,
            from_oi INTEGER,
            to_oi INTEGER,
            shift_magnitude_pct REAL,  -- (new_oi - old_oi) / old_oi
            description TEXT
        )
    """)
    conn.commit()
    conn.close()


def capture_wall_snapshot(engine):
    """Capture top 3 CE/PE walls per index. Call every 5 min."""
    init_db()
    now_iso = ist_now().isoformat()
    conn = sqlite3.connect(str(DB_PATH))

    for idx in ["NIFTY", "BANKNIFTY"]:
        chain = engine.chains.get(idx, {})
        if not chain:
            continue

        # Get all strikes with OI > threshold
        strikes_oi = []
        for strike, data in chain.items():
            ce_oi = data.get("ce_oi", 0) or 0
            pe_oi = data.get("pe_oi", 0) or 0
            strikes_oi.append((strike, "CE", ce_oi))
            strikes_oi.append((strike, "PE", pe_oi))

        # Top 3 CE walls
        ce_walls = sorted([s for s in strikes_oi if s[1] == "CE"],
                          key=lambda x: x[2], reverse=True)[:3]
        # Top 3 PE walls
        pe_walls = sorted([s for s in strikes_oi if s[1] == "PE"],
                          key=lambda x: x[2], reverse=True)[:3]

        for rank, (strike, side, oi) in enumerate(ce_walls + pe_walls, start=1):
            if oi < 100000:  # ignore weak walls
                continue
            actual_rank = ((rank - 1) % 3) + 1
            conn.execute("""
                INSERT OR REPLACE INTO wall_snapshots (ts, idx, side, strike, oi, rank)
                VALUES (?,?,?,?,?,?)
            """, (now_iso, idx, side, strike, oi, actual_rank))

    conn.commit()
    conn.close()

    # Detect shifts (compare to 30 min ago)
    detect_shifts()


def detect_shifts():
    """Compare current top wall to 30 min ago. Log shifts."""
    init_db()
    now = ist_now()
    cutoff_old = (now - timedelta(minutes=35)).isoformat()
    cutoff_new = (now - timedelta(minutes=2)).isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    for idx in ["NIFTY", "BANKNIFTY"]:
        for side in ["CE", "PE"]:
            # Old top wall (30 min ago)
            old = conn.execute("""
                SELECT strike, oi FROM wall_snapshots
                WHERE idx=? AND side=? AND rank=1 AND ts <= ?
                ORDER BY ts DESC LIMIT 1
            """, (idx, side, cutoff_old)).fetchone()

            # New top wall (now)
            new = conn.execute("""
                SELECT strike, oi FROM wall_snapshots
                WHERE idx=? AND side=? AND rank=1 AND ts >= ?
                ORDER BY ts DESC LIMIT 1
            """, (idx, side, cutoff_new)).fetchone()

            if not old or not new:
                continue

            if old["strike"] != new["strike"]:
                shift_pct = round(((new["oi"] - old["oi"]) / max(old["oi"], 1)) * 100, 1)
                desc = f"{idx} {side} wall shifted {old['strike']} ({old['oi']/100000:.1f}L) → {new['strike']} ({new['oi']/100000:.1f}L)"

                # Avoid duplicate alerts (same shift within 10 min)
                existing = conn.execute("""
                    SELECT id FROM shift_events
                    WHERE idx=? AND side=? AND to_strike=? AND ts > ?
                """, (idx, side, new["strike"],
                      (now - timedelta(minutes=10)).isoformat())).fetchone()

                if not existing:
                    conn.execute("""
                        INSERT INTO shift_events (ts, idx, side, from_strike, to_strike,
                            from_oi, to_oi, shift_magnitude_pct, description)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (now.isoformat(), idx, side, old["strike"], new["strike"],
                          old["oi"], new["oi"], shift_pct, desc))
                    print(f"[OI-SHIFT] {desc}")

    conn.commit()
    conn.close()


def get_recent_shifts(idx=None, hours=2):
    """Recent wall shifts for UI/decision logic."""
    init_db()
    cutoff = (ist_now() - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if idx:
        rows = conn.execute("""
            SELECT * FROM shift_events WHERE idx=? AND ts > ?
            ORDER BY ts DESC LIMIT 30
        """, (idx, cutoff)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM shift_events WHERE ts > ?
            ORDER BY ts DESC LIMIT 30
        """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_trade_against_shift(idx, action, current_spot, recent_minutes=30):
    """Check if proposed trade is AGAINST a recent wall shift.

    Returns (block_trade, reason).
    """
    init_db()
    cutoff = (ist_now() - timedelta(minutes=recent_minutes)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    shifts = conn.execute("""
        SELECT * FROM shift_events WHERE idx=? AND ts > ?
        ORDER BY ts DESC
    """, (idx, cutoff)).fetchall()
    conn.close()

    if not shifts:
        return False, None

    is_ce = "CE" in (action or "")

    # CE wall shifted UP = sellers expect higher levels = BUY CE alignment ✓
    # CE wall shifted DOWN = sellers cap at lower = BUY PE alignment ✓
    # PE wall shifted UP = sellers expect higher floor = BUY CE alignment ✓
    # PE wall shifted DOWN = floor breaking = BUY PE alignment ✓
    for s in shifts:
        s = dict(s)
        if s["side"] == "CE":
            ce_shifted_up = s["to_strike"] > s["from_strike"]
            if is_ce and not ce_shifted_up:
                return True, f"CE wall shifted DOWN ({s['from_strike']} → {s['to_strike']}) — sellers capping resistance, AVOID BUY CE"
            if not is_ce and ce_shifted_up:
                return True, f"CE wall shifted UP ({s['from_strike']} → {s['to_strike']}) — bullish bias, AVOID BUY PE"
        elif s["side"] == "PE":
            pe_shifted_up = s["to_strike"] > s["from_strike"]
            if not is_ce and pe_shifted_up:
                return True, f"PE wall shifted UP ({s['from_strike']} → {s['to_strike']}) — support raised, AVOID BUY PE"
            if is_ce and not pe_shifted_up:
                return True, f"PE wall shifted DOWN ({s['from_strike']} → {s['to_strike']}) — support breaking, AVOID BUY CE"

    return False, None
