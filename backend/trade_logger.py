"""
Smart Trade Logger — Auto-logs trades from verdict engine.
Tracks SL/target hits. Detects institutional stop hunts.
SQLite-backed persistence.

Lot sizes: NIFTY = 65 qty, BANKNIFTY = 30 qty, ALWAYS 20 lots
Max SL = 15% of entry premium
"""

import sqlite3
import time
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
DB_PATH = None

LOT_CONFIG = {
    "NIFTY": {"lot_size": 65, "lots": 20, "qty": 1300},
    "BANKNIFTY": {"lot_size": 30, "lots": 20, "qty": 600},
}


def ist_now():
    return datetime.now(IST)


def init_trades_db(db_path):
    global DB_PATH
    DB_PATH = db_path
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            idx TEXT NOT NULL,
            action TEXT NOT NULL,
            strike INTEGER NOT NULL,
            expiry TEXT,
            entry_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            t1_price REAL NOT NULL,
            t2_price REAL NOT NULL,
            current_ltp REAL DEFAULT 0,
            exit_price REAL DEFAULT 0,
            lots INTEGER DEFAULT 20,
            lot_size INTEGER,
            qty INTEGER,
            pnl_pts REAL DEFAULT 0,
            pnl_rupees REAL DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            exit_reason TEXT,
            probability INTEGER DEFAULT 0,
            source TEXT,
            sl_hit_time TEXT,
            reversal_price REAL DEFAULT 0,
            reversal_detected INTEGER DEFAULT 0,
            oi_at_sl_hit INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON trades(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON trades(entry_time)")
    conn.commit()
    conn.close()
    # Purge very old trades (>90 days)
    cutoff = (ist_now() - timedelta(days=90)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM trades WHERE entry_time < ? AND status != 'OPEN'", (cutoff,))
    conn.commit()
    conn.close()
    print(f"[TRADES] Database initialized at {db_path}")


def _conn():
    return sqlite3.connect(DB_PATH)


class TradeManager:
    def __init__(self):
        self._last_verdict_check = 0
        self._last_sl_check = 0

    def log_trade(self, idx, action, strike, entry_price, probability, source="verdict", expiry=""):
        """Log a new trade entry. SL = 15% max. T1 = +20%. T2 = +40%."""
        if entry_price <= 0:
            return None

        cfg = LOT_CONFIG.get(idx, LOT_CONFIG["NIFTY"])
        sl_price = round(entry_price * 0.85)   # 15% SL
        t1_price = round(entry_price * 1.20)   # 20% profit
        t2_price = round(entry_price * 1.40)   # 40% profit

        now = ist_now()
        conn = _conn()
        cursor = conn.execute("""
            INSERT INTO trades (entry_time, idx, action, strike, expiry,
                entry_price, sl_price, t1_price, t2_price, current_ltp,
                lots, lot_size, qty, status, probability, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, (
            now.isoformat(), idx, action, strike, expiry,
            entry_price, sl_price, t1_price, t2_price, entry_price,
            cfg["lots"], cfg["lot_size"], cfg["qty"],
            probability, source,
        ))
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()

        print(f"[TRADE] NEW: {action} {idx} {strike} @ {entry_price} | SL: {sl_price} | T1: {t1_price} | T2: {t2_price} | {cfg['lots']}L x {cfg['lot_size']} = {cfg['qty']} qty | Prob: {probability}%")
        return trade_id

    def check_and_update(self, chains, prices, spot_tokens, token_to_info):
        """Check all OPEN trades against live prices. Auto-close on SL/T1/T2."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        open_trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        conn.close()

        for trade in open_trades:
            t = dict(trade)
            idx = t["idx"]
            strike = t["strike"]
            action = t["action"]

            # Find current LTP for the option
            chain = chains.get(idx, {})
            strike_data = chain.get(strike, {})
            if "CE" in action:
                current_ltp = strike_data.get("ce_ltp", 0)
            else:
                current_ltp = strike_data.get("pe_ltp", 0)

            if current_ltp <= 0:
                continue

            entry = t["entry_price"]
            sl = t["sl_price"]
            t1 = t["t1_price"]
            t2 = t["t2_price"]

            # Calculate PnL
            pnl_pts = round(current_ltp - entry, 2)
            pnl_rupees = round(pnl_pts * t["qty"], 2)

            # Check exit conditions
            new_status = "OPEN"
            exit_reason = None
            exit_price = 0

            if current_ltp <= sl:
                new_status = "SL_HIT"
                exit_price = sl
                exit_reason = f"Stoploss hit at {sl} (entry was {entry}, -{round((1 - sl/entry)*100)}%)"
            elif current_ltp >= t2:
                new_status = "T2_HIT"
                exit_price = t2
                exit_reason = f"Target 2 hit at {t2} (+{round((t2/entry - 1)*100)}% from entry)"
            elif current_ltp >= t1:
                # T1 hit — trail SL to entry (breakeven)
                # Check if already past T1 threshold — auto-exit at T2 or trail
                new_status = "T1_HIT"
                exit_price = current_ltp
                exit_reason = f"Target 1 hit at {current_ltp:.1f} (+{round((current_ltp/entry - 1)*100)}% from entry)"

            # Update trade
            conn = _conn()
            if new_status != "OPEN":
                final_pnl_pts = round(exit_price - entry, 2)
                final_pnl_rupees = round(final_pnl_pts * t["qty"], 2)
                conn.execute("""
                    UPDATE trades SET current_ltp=?, pnl_pts=?, pnl_rupees=?,
                        status=?, exit_price=?, exit_time=?, exit_reason=?
                    WHERE id=?
                """, (current_ltp, final_pnl_pts, final_pnl_rupees,
                      new_status, exit_price, ist_now().isoformat(), exit_reason, t["id"]))
                print(f"[TRADE] CLOSED: {t['action']} {idx} {strike} — {new_status} — PnL: {final_pnl_pts} pts ({final_pnl_rupees:+,.0f})")

                # If SL hit — start stop hunt monitoring
                if new_status == "SL_HIT":
                    # Get OI at SL strike
                    oi_at_sl = 0
                    for tok, info in token_to_info.items():
                        if info["index"] == idx and info["strike"] == strike:
                            opt = "ce" if "CE" in action else "pe"
                            oi_at_sl = strike_data.get(f"{opt}_oi", 0)
                            break
                    conn.execute("""
                        UPDATE trades SET sl_hit_time=?, oi_at_sl_hit=?
                        WHERE id=?
                    """, (ist_now().isoformat(), oi_at_sl, t["id"]))
            else:
                conn.execute("""
                    UPDATE trades SET current_ltp=?, pnl_pts=?, pnl_rupees=?
                    WHERE id=?
                """, (current_ltp, pnl_pts, pnl_rupees, t["id"]))
            conn.commit()
            conn.close()

    def check_stop_hunts(self, chains):
        """Check SL_HIT trades for reversal (stop hunt detection)."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        sl_trades = conn.execute("""
            SELECT * FROM trades WHERE status='SL_HIT' AND reversal_detected=0
            AND sl_hit_time > ?
        """, ((ist_now() - timedelta(minutes=20)).isoformat(),)).fetchall()
        conn.close()

        for trade in sl_trades:
            t = dict(trade)
            idx = t["idx"]
            strike = t["strike"]
            action = t["action"]

            chain = chains.get(idx, {})
            strike_data = chain.get(strike, {})
            opt = "ce" if "CE" in action else "pe"
            current_ltp = strike_data.get(f"{opt}_ltp", 0)
            entry = t["entry_price"]
            sl = t["sl_price"]

            if current_ltp <= 0:
                continue

            # Stop hunt check: did price recover past entry after hitting SL?
            sl_move = entry - sl  # How far SL was from entry
            recovery = current_ltp - sl  # How much recovered from SL

            if recovery > sl_move * 0.5:
                # Price recovered >50% of the SL distance = stop hunt
                conn = _conn()
                conn.execute("""
                    UPDATE trades SET status='STOP_HUNTED', reversal_detected=1,
                        reversal_price=?, exit_reason=?
                    WHERE id=?
                """, (
                    current_ltp,
                    f"STOP HUNT: SL hit at {sl}, then reversed to {current_ltp:.1f} (recovered {recovery:.1f} pts). Institutional flush detected.",
                    t["id"]
                ))
                conn.commit()
                conn.close()
                print(f"[TRADE] STOP HUNT DETECTED: {action} {idx} {strike} — SL at {sl}, now at {current_ltp:.1f}")

    def should_enter_trade(self, idx, verdict_data):
        """Check if we should enter a new trade based on verdict."""
        if not verdict_data or verdict_data.get("action") == "NO TRADE":
            return False

        win_pct = verdict_data.get("winProbability", 0)
        if win_pct < 60:
            return False

        # Check no existing OPEN trade for this index
        conn = _conn()
        existing = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE idx=? AND status='OPEN'", (idx,)
        ).fetchone()[0]

        # Also check cooldown: don't re-enter same index within 30 min of last trade
        last_trade = conn.execute(
            "SELECT entry_time FROM trades WHERE idx=? ORDER BY entry_time DESC LIMIT 1", (idx,)
        ).fetchone()
        conn.close()

        if existing > 0:
            return False

        if last_trade:
            try:
                last_time = datetime.fromisoformat(last_trade[0])
                if (ist_now() - last_time).total_seconds() < 1800:  # 30 min cooldown
                    return False
            except Exception:
                pass

        # Don't trade after 3:20 PM
        now = ist_now()
        if now.hour > 15 or (now.hour == 15 and now.minute > 20):
            return False

        # Don't trade before 9:20 AM
        if now.hour == 9 and now.minute < 20:
            return False

        return True

    # ── PUBLIC API METHODS ──

    def get_open_trades(self):
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY entry_time DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_closed_trades(self, days=7):
        cutoff = (ist_now() - timedelta(days=days)).isoformat()
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE status!='OPEN' AND entry_time > ? ORDER BY exit_time DESC",
            (cutoff,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_trades_by_date(self, date_str):
        """Get all trades for a specific date (YYYY-MM-DD)."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE entry_time LIKE ? ORDER BY entry_time DESC",
            (f"{date_str}%",)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_monthly_report(self, year, month):
        """Get monthly stats + all trades for a given month."""
        prefix = f"{year}-{str(month).zfill(2)}"
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE entry_time LIKE ? ORDER BY entry_time DESC",
            (f"{prefix}%",)
        ).fetchall()
        conn.close()

        trades = [dict(r) for r in rows]
        if not trades:
            return {"month": prefix, "trades": [], "stats": {"total": 0}}

        closed = [t for t in trades if t["status"] != "OPEN"]
        wins = [t for t in trades if t["status"] in ("T1_HIT", "T2_HIT")]
        losses = [t for t in trades if t["status"] == "SL_HIT"]
        hunts = [t for t in trades if t["status"] == "STOP_HUNTED"]
        total_pnl = sum(t["pnl_rupees"] for t in closed)
        win_pnls = [t["pnl_rupees"] for t in wins]
        loss_pnls = [t["pnl_rupees"] for t in losses]

        # Daily breakdown
        daily = {}
        for t in trades:
            day = t["entry_time"][:10]
            if day not in daily:
                daily[day] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0}
            daily[day]["trades"] += 1
            if t["status"] in ("T1_HIT", "T2_HIT"):
                daily[day]["wins"] += 1
            elif t["status"] == "SL_HIT":
                daily[day]["losses"] += 1
            if t["status"] != "OPEN":
                daily[day]["pnl"] += t["pnl_rupees"]

        return {
            "month": prefix,
            "trades": trades,
            "daily": daily,
            "stats": {
                "total": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "stopHunts": len(hunts),
                "winRate": round(len(wins) / len(closed) * 100) if closed else 0,
                "totalPnl": round(total_pnl),
                "avgWin": round(sum(win_pnls) / len(win_pnls)) if win_pnls else 0,
                "avgLoss": round(sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0,
                "bestDay": max(daily.values(), key=lambda x: x["pnl"])["pnl"] if daily else 0,
                "worstDay": min(daily.values(), key=lambda x: x["pnl"])["pnl"] if daily else 0,
            },
        }

    def get_all_dates(self):
        """Get list of all dates that have trades."""
        conn = _conn()
        rows = conn.execute(
            "SELECT DISTINCT substr(entry_time, 1, 10) as d FROM trades ORDER BY d DESC"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]

    def get_stats(self, days=30):
        cutoff = (ist_now() - timedelta(days=days)).isoformat()
        conn = _conn()
        conn.row_factory = sqlite3.Row

        all_trades = conn.execute(
            "SELECT * FROM trades WHERE entry_time > ?", (cutoff,)
        ).fetchall()
        conn.close()

        total = len(all_trades)
        if total == 0:
            return {"total": 0, "open": 0, "wins": 0, "losses": 0, "stopHunts": 0,
                    "winRate": 0, "totalPnl": 0, "avgWin": 0, "avgLoss": 0, "bestTrade": 0, "worstTrade": 0}

        open_trades = [t for t in all_trades if t["status"] == "OPEN"]
        wins = [t for t in all_trades if t["status"] in ("T1_HIT", "T2_HIT")]
        losses = [t for t in all_trades if t["status"] == "SL_HIT"]
        hunts = [t for t in all_trades if t["status"] == "STOP_HUNTED"]
        closed = [t for t in all_trades if t["status"] != "OPEN"]

        total_pnl = sum(t["pnl_rupees"] for t in closed)
        win_pnls = [t["pnl_rupees"] for t in wins]
        loss_pnls = [t["pnl_rupees"] for t in losses]

        closed_count = len(closed)
        win_rate = round(len(wins) / closed_count * 100) if closed_count > 0 else 0

        return {
            "total": total,
            "open": len(open_trades),
            "wins": len(wins),
            "losses": len(losses),
            "stopHunts": len(hunts),
            "winRate": win_rate,
            "totalPnl": round(total_pnl),
            "avgWin": round(sum(win_pnls) / len(win_pnls)) if win_pnls else 0,
            "avgLoss": round(sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0,
            "bestTrade": round(max(win_pnls)) if win_pnls else 0,
            "worstTrade": round(min(loss_pnls)) if loss_pnls else 0,
        }

    def get_stop_hunts(self):
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='STOP_HUNTED' ORDER BY exit_time DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
