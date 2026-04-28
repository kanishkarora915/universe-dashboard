"""
Truth/Lie Detector (A3) — Pattern matching for failed setups.

Logic:
  - After every trade closes → classify as TRUTH (won) or LIE (lost)
  - Build pattern fingerprint: {day, time_window, action, top_engines, vix_band, prob_band}
  - Before new trade → query similar past patterns
  - If win rate < 40% on similar patterns → BLOCK trade

Result: Same mistakes don't repeat. System learns from "this exact setup failed yesterday".
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "truth_lie.db"


DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            day_of_week TEXT,         -- MON, TUE, WED...
            time_window TEXT,         -- MORNING_TREND, LUNCH_CHOP, etc.
            action TEXT,              -- BUY CE / BUY PE
            top_engine TEXT,          -- top contributing engine
            second_engine TEXT,
            prob_band TEXT,           -- 50-60, 60-70, 70-80, 80+
            vix_band TEXT,            -- LOW, MED, HIGH, EXTREME
            is_expiry INTEGER,
            outcome TEXT,             -- TRUTH (win) / LIE (loss) / NEUTRAL (BE)
            pnl_rupees REAL,
            trade_id INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tp_pattern ON trade_patterns(day_of_week, time_window, action, prob_band)")
    conn.commit()
    conn.close()


def _get_time_window():
    now = ist_now()
    hm = now.hour * 100 + now.minute
    if hm < 920: return "OPENING_FIRST_5MIN"
    if hm < 1030: return "MORNING_TREND"
    if hm < 1130: return "MID_MORNING"
    if hm < 1230: return "LUNCH_CHOP"
    if hm < 1400: return "AFTERNOON"
    if hm < 1515: return "POWER_HOUR"
    return "CLOSING"


def _prob_band(prob):
    if prob >= 80: return "80+"
    if prob >= 70: return "70-80"
    if prob >= 60: return "60-70"
    return "50-60"


def _vix_band(vix):
    if vix >= 25: return "EXTREME"
    if vix >= 18: return "HIGH"
    if vix >= 12: return "MED"
    return "LOW"


def record_trade_outcome(trade_data):
    """Called after trade closes. Records pattern fingerprint + outcome.

    trade_data needs:
      action, status, pnl_rupees, probability, vix, engine_scores, trade_id
    """
    init_db()
    now = ist_now()

    status = trade_data.get("status", "")
    if status in ("T1_HIT", "T2_HIT", "TRAIL_EXIT"):
        outcome = "TRUTH"
    elif status in ("SL_HIT", "REVERSAL_EXIT"):
        outcome = "LIE"
    else:
        outcome = "NEUTRAL"

    # Get top 2 contributing engines
    engine_scores = trade_data.get("engine_scores", {}) or {}
    sorted_eng = sorted(engine_scores.items(), key=lambda x: abs(x[1] or 0), reverse=True)
    top_engine = sorted_eng[0][0] if sorted_eng else "unknown"
    second_engine = sorted_eng[1][0] if len(sorted_eng) > 1 else "none"

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT INTO trade_patterns
        (ts, day_of_week, time_window, action, top_engine, second_engine,
         prob_band, vix_band, is_expiry, outcome, pnl_rupees, trade_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        now.isoformat(),
        DAY_NAMES[now.weekday()],
        _get_time_window(),
        trade_data.get("action", ""),
        top_engine,
        second_engine,
        _prob_band(trade_data.get("probability", 0)),
        _vix_band(trade_data.get("vix", 18)),
        1 if now.weekday() == 1 else 0,
        outcome,
        trade_data.get("pnl_rupees", 0),
        trade_data.get("trade_id"),
    ))
    conn.commit()
    conn.close()


def check_pattern(action, probability, top_engine, vix=18, lookback_days=14):
    """Check if proposed trade matches a 'lie pattern' from history.

    Returns (is_lie, confidence, win_rate, total_samples, message).
    """
    init_db()
    now = ist_now()
    day_name = DAY_NAMES[now.weekday()]
    time_win = _get_time_window()
    prob_band = _prob_band(probability)
    vix_band = _vix_band(vix)
    is_expiry = 1 if now.weekday() == 1 else 0
    cutoff = (now - timedelta(days=lookback_days)).isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Try strictest match first: same day + time + action + engine + prob + vix
    rows = conn.execute("""
        SELECT outcome, pnl_rupees FROM trade_patterns
        WHERE day_of_week=? AND time_window=? AND action=? AND top_engine=?
          AND prob_band=? AND vix_band=? AND is_expiry=? AND ts > ?
        ORDER BY ts DESC LIMIT 30
    """, (day_name, time_win, action, top_engine, prob_band, vix_band, is_expiry, cutoff)).fetchall()

    # If too few samples, relax: same day + time + action + engine
    if len(rows) < 5:
        rows = conn.execute("""
            SELECT outcome, pnl_rupees FROM trade_patterns
            WHERE day_of_week=? AND time_window=? AND action=? AND top_engine=?
              AND ts > ?
            ORDER BY ts DESC LIMIT 30
        """, (day_name, time_win, action, top_engine, cutoff)).fetchall()

    # If still too few, even more relaxed: same day + action + engine
    if len(rows) < 5:
        rows = conn.execute("""
            SELECT outcome, pnl_rupees FROM trade_patterns
            WHERE day_of_week=? AND action=? AND top_engine=? AND ts > ?
            ORDER BY ts DESC LIMIT 30
        """, (day_name, action, top_engine, cutoff)).fetchall()

    conn.close()

    total = len(rows)
    if total < 3:
        return False, 0, 0, total, "Not enough history for pattern match"

    truths = sum(1 for r in rows if r["outcome"] == "TRUTH")
    lies = sum(1 for r in rows if r["outcome"] == "LIE")
    win_rate = truths / max(total, 1) * 100

    # Decision
    if win_rate < 40 and total >= 5:
        return (True, 100 - win_rate, win_rate, total,
                f"LIE pattern: {win_rate:.0f}% win rate over {total} similar trades ({day_name} {time_win} {action} top engine={top_engine})")
    if win_rate < 50 and total >= 8:
        return (True, 100 - win_rate, win_rate, total,
                f"WEAK pattern: {win_rate:.0f}% win rate over {total} samples — borderline, recommend skip")
    return False, win_rate, win_rate, total, f"Pattern OK: {win_rate:.0f}% win rate ({total} samples)"


def get_pattern_summary(days=30):
    """For UI: aggregate truth/lie patterns by day/time/action."""
    init_db()
    cutoff = (ist_now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT day_of_week, time_window, action, top_engine,
               COUNT(*) as total,
               SUM(CASE WHEN outcome='TRUTH' THEN 1 ELSE 0 END) as truths,
               SUM(CASE WHEN outcome='LIE' THEN 1 ELSE 0 END) as lies,
               AVG(pnl_rupees) as avg_pnl
        FROM trade_patterns
        WHERE ts > ?
        GROUP BY day_of_week, time_window, action, top_engine
        HAVING total >= 3
        ORDER BY total DESC
        LIMIT 50
    """, (cutoff,)).fetchall()
    conn.close()

    patterns = []
    for r in rows:
        d = dict(r)
        d["win_rate"] = round(d["truths"] / max(d["total"], 1) * 100, 1)
        d["classification"] = (
            "STRONG_TRUTH" if d["win_rate"] >= 65
            else "TRUTH" if d["win_rate"] >= 55
            else "NEUTRAL" if d["win_rate"] >= 45
            else "LIE" if d["win_rate"] >= 30
            else "STRONG_LIE"
        )
        patterns.append(d)
    return patterns


def get_recent_blocks(hours=24):
    """How many trades blocked by Truth/Lie filter in last N hours."""
    # This would need a separate log table — for now just count from history
    init_db()
    return []
