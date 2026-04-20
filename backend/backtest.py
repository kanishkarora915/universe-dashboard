"""
Backtest Engine — Tracks every verdict signal and verifies actual outcome.
Calculates REAL win rate from historical data.
"""

import sqlite3
import time
import threading
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
DB_PATH = None


def ist_now():
    return datetime.now(IST)


# Engine score column names — must match engine keys in verdict
ENGINE_SCORE_COLS = [
    "seller_pts", "trap_pts", "price_action_pts", "oi_flow_pts",
    "market_context_pts", "vwap_pts", "mtf_pts", "fii_dii_pts", "global_cues_pts",
]


def init_backtest_db(db_path):
    global DB_PATH
    DB_PATH = db_path
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            idx TEXT NOT NULL,
            verdict_action TEXT,
            verdict_probability INTEGER DEFAULT 0,
            spot_at_verdict REAL DEFAULT 0,
            spot_15min REAL DEFAULT 0,
            spot_30min REAL DEFAULT 0,
            spot_1hr REAL DEFAULT 0,
            outcome_15min TEXT DEFAULT 'PENDING',
            outcome_30min TEXT DEFAULT 'PENDING',
            outcome_1hr TEXT DEFAULT 'PENDING',
            checked INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bt_ts ON backtest_log(timestamp)")

    # ── Safe migration: add per-engine score columns if missing ──
    existing = {row[1] for row in conn.execute("PRAGMA table_info(backtest_log)").fetchall()}
    for col in ENGINE_SCORE_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE backtest_log ADD COLUMN {col} INTEGER DEFAULT 0")
            print(f"[BACKTEST] Added column: {col}")

    # ── Training log table ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS training_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            accuracy_before REAL DEFAULT 0,
            accuracy_after REAL DEFAULT 0,
            old_weights TEXT,
            new_weights TEXT,
            data_points INTEGER DEFAULT 0,
            notes TEXT
        )
    """)

    conn.commit()
    conn.close()
    # Purge >60 days
    cutoff = (ist_now() - timedelta(days=60)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM backtest_log WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()
    print(f"[BACKTEST] Database initialized at {db_path}")


def _conn():
    return sqlite3.connect(DB_PATH)


class BacktestTracker:
    def __init__(self):
        self._last_log = {}  # Per-index: {"NIFTY": timestamp, "BANKNIFTY": timestamp}

    def log_verdict(self, idx, action, probability, spot_price, engine_scores=None):
        """Log a verdict signal with per-engine score breakdown for later verification."""
        if action == "NO TRADE" or probability < 60 or spot_price <= 0:
            return
        # Don't log more than once per 2 minutes PER INDEX
        now = time.time()
        if now - self._last_log.get(idx, 0) < 120:
            return
        self._last_log[idx] = now

        es = engine_scores or {}
        cols = "timestamp, idx, verdict_action, verdict_probability, spot_at_verdict"
        vals = [ist_now().isoformat(), idx, action, probability, spot_price]

        # Add per-engine scores
        for col in ENGINE_SCORE_COLS:
            cols += f", {col}"
            # Map column name to engine key: seller_pts → seller_positioning
            key = col.replace("_pts", "")
            # Handle naming mismatches
            key_map = {
                "seller": "seller_positioning",
                "trap": "trap_fingerprints",
                "price_action": "price_action",
                "oi_flow": "oi_flow",
                "market_context": "market_context",
                "vwap": "vwap",
                "mtf": "multi_timeframe",
                "fii_dii": "fii_dii",
                "global_cues": "global_cues",
            }
            vals.append(es.get(key_map.get(key, key), 0))

        placeholders = ", ".join(["?"] * len(vals))
        conn = _conn()
        conn.execute(f"INSERT INTO backtest_log ({cols}) VALUES ({placeholders})", vals)
        conn.commit()
        conn.close()

    def check_outcomes(self, prices, spot_tokens):
        """Check pending verdicts — did spot move in predicted direction?"""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        pending = conn.execute(
            "SELECT * FROM backtest_log WHERE checked=0 AND timestamp < ?",
            ((ist_now() - timedelta(minutes=15)).isoformat(),)
        ).fetchall()
        conn.close()

        for row in pending:
            r = dict(row)
            idx = r["idx"]
            action = r["verdict_action"]
            entry_spot = r["spot_at_verdict"]
            ts = datetime.fromisoformat(r["timestamp"])
            age_min = (ist_now() - ts).total_seconds() / 60

            current_spot = prices.get(spot_tokens.get(idx), {}).get("ltp", 0)
            if current_spot <= 0:
                continue

            updates = {}

            # SKIP stale rows (engine was offline — prices aren't from the right window anymore)
            # Only backfill outcomes within a small tolerance after their target time.
            # 15min → fill between 15-25 min, 30min → 30-45 min, 1hr → 60-90 min.
            if age_min > 90:
                updates["checked"] = 1  # Mark as checked to skip future retries
                continue_row = False
            else:
                continue_row = True

            if continue_row:
                # Check 15 min outcome (within 15-25 min window)
                if 15 <= age_min <= 25 and r["outcome_15min"] == "PENDING":
                    updates["spot_15min"] = current_spot
                    if "CE" in action:
                        updates["outcome_15min"] = "WIN" if current_spot > entry_spot else "LOSS"
                    else:
                        updates["outcome_15min"] = "WIN" if current_spot < entry_spot else "LOSS"

                # Check 30 min (30-45 min window)
                if 30 <= age_min <= 45 and r["outcome_30min"] == "PENDING":
                    updates["spot_30min"] = current_spot
                    if "CE" in action:
                        updates["outcome_30min"] = "WIN" if current_spot > entry_spot else "LOSS"
                    else:
                        updates["outcome_30min"] = "WIN" if current_spot < entry_spot else "LOSS"

                # Check 1 hr (60-90 min window)
                if 60 <= age_min <= 90 and r["outcome_1hr"] == "PENDING":
                    updates["spot_1hr"] = current_spot
                    if "CE" in action:
                        updates["outcome_1hr"] = "WIN" if current_spot > entry_spot else "LOSS"
                    else:
                        updates["outcome_1hr"] = "WIN" if current_spot < entry_spot else "LOSS"
                    updates["checked"] = 1

            if updates:
                set_clause = ", ".join(f"{k}=?" for k in updates.keys())
                conn = _conn()
                conn.execute(f"UPDATE backtest_log SET {set_clause} WHERE id=?",
                             (*updates.values(), r["id"]))
                conn.commit()
                conn.close()

    def get_stats(self, days=30):
        """Get backtest accuracy stats."""
        cutoff = (ist_now() - timedelta(days=days)).isoformat()
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM backtest_log WHERE timestamp > ?", (cutoff,)).fetchall()
        conn.close()

        total = len(rows)
        if total == 0:
            return {"total": 0, "message": "No backtest data yet. Signals will be tracked automatically."}

        checked = [r for r in rows if r["checked"]]

        def calc_rate(field):
            valid = [r for r in checked if r[field] in ("WIN", "LOSS")]
            if not valid:
                return 0
            wins = sum(1 for r in valid if r[field] == "WIN")
            return round(wins / len(valid) * 100)

        # Per-hour accuracy
        hourly = {}
        for r in checked:
            try:
                h = datetime.fromisoformat(r["timestamp"]).hour
                if h not in hourly:
                    hourly[h] = {"total": 0, "wins": 0}
                hourly[h]["total"] += 1
                if r["outcome_30min"] == "WIN":
                    hourly[h]["wins"] += 1
            except Exception:
                pass

        best_hour = max(hourly.items(), key=lambda x: x[1]["wins"] / max(x[1]["total"], 1) * 100, default=(0, {}))
        worst_hour = min(hourly.items(), key=lambda x: x[1]["wins"] / max(x[1]["total"], 1) * 100, default=(0, {}))

        return {
            "total": total,
            "checked": len(checked),
            "winRate15min": calc_rate("outcome_15min"),
            "winRate30min": calc_rate("outcome_30min"),
            "winRate1hr": calc_rate("outcome_1hr"),
            "bestHour": f"{best_hour[0]}:00" if best_hour[1] else "N/A",
            "bestHourRate": round(best_hour[1].get("wins", 0) / max(best_hour[1].get("total", 1), 1) * 100) if best_hour[1] else 0,
            "worstHour": f"{worst_hour[0]}:00" if worst_hour[1] else "N/A",
            "worstHourRate": round(worst_hour[1].get("wins", 0) / max(worst_hour[1].get("total", 1), 1) * 100) if worst_hour[1] else 0,
            "recentSignals": [dict(r) for r in rows[:20]],
        }
