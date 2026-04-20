"""
Shadow Autopsy — Auto-track ATM±6 CE+PE paper trades at 9:20 AM.
Learns how strikes move intraday WITHOUT risking real capital.
Used to understand: what patterns win vs lose, how big players move market.

Quantities: NIFTY 1625 qty, BANKNIFTY 600 qty (paper size for realistic PnL math).

Lifecycle:
  9:20 AM → take_snapshot_open()  — creates 52 shadow trades (13 strikes × 2 sides × 2 indices)
  Every 60s → update_all()         — refreshes LTP, OI, peak/trough, snapshot history
  3:15 PM  → close_all()           — marks final PnL, classifies WIN/LOSS
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "shadow_autopsy.db"

# Paper trading quantities (user-specified)
SHADOW_QTY = {"NIFTY": 1625, "BANKNIFTY": 600}
STRIKE_OFFSETS = list(range(-6, 7))  # -6 to +6 = 13 strikes


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            idx TEXT NOT NULL,
            strike INTEGER NOT NULL,
            side TEXT NOT NULL,
            offset INTEGER NOT NULL,
            spot_at_entry REAL,
            atm_at_entry INTEGER,

            entry_time TEXT,
            entry_ltp REAL,
            entry_oi INTEGER,
            entry_volume INTEGER,
            entry_iv REAL,

            peak_ltp REAL,
            peak_time TEXT,
            trough_ltp REAL,
            trough_time TEXT,

            current_ltp REAL,
            current_oi INTEGER,
            oi_change INTEGER,
            oi_change_pct REAL,

            exit_time TEXT,
            exit_ltp REAL,
            pnl_rupees REAL,
            pnl_pct REAL,

            qty INTEGER,
            status TEXT DEFAULT 'OPEN',
            result TEXT,

            UNIQUE(date, idx, strike, side)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_st_date ON shadow_trades(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_st_idx ON shadow_trades(idx)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_trade_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            ltp REAL,
            oi INTEGER,
            volume INTEGER,
            spot REAL,
            FOREIGN KEY(shadow_trade_id) REFERENCES shadow_trades(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ss_tid ON shadow_snapshots(shadow_trade_id)")

    conn.commit()
    conn.close()


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def take_snapshot_open(engine):
    """Called at 9:20 AM — create 52 shadow trades (ATM±6 CE+PE for both indices)."""
    from engine import INDEX_CONFIG

    init_db()
    today = ist_now().strftime("%Y-%m-%d")
    now_iso = ist_now().isoformat()

    # Skip if already taken today
    conn = _conn()
    existing = conn.execute(
        "SELECT COUNT(*) FROM shadow_trades WHERE date=?", (today,)
    ).fetchone()[0]
    if existing > 0:
        conn.close()
        print(f"[SHADOW] Already have {existing} shadow trades for {today} — skipping")
        return existing

    created = 0
    for idx in ["NIFTY", "BANKNIFTY"]:
        cfg = INDEX_CONFIG[idx]
        chain = engine.chains.get(idx, {})
        spot_token = engine.spot_tokens.get(idx)
        spot = engine.prices.get(spot_token, {}).get("ltp", 0)

        if spot <= 0 or not chain:
            print(f"[SHADOW] SKIP {idx} — spot={spot}, chain_size={len(chain)}")
            continue

        atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"]
        qty = SHADOW_QTY[idx]

        for offset in STRIKE_OFFSETS:
            strike = atm + offset * cfg["strike_gap"]
            sd = chain.get(strike, {})
            if not sd:
                continue

            for side in ["CE", "PE"]:
                ltp = sd.get(f"{side.lower()}_ltp", 0)
                oi = sd.get(f"{side.lower()}_oi", 0)
                vol = sd.get(f"{side.lower()}_volume", 0)
                iv = sd.get(f"{side.lower()}_iv", 0)

                if ltp <= 0:
                    continue

                try:
                    conn.execute("""
                        INSERT INTO shadow_trades (
                            date, idx, strike, side, offset, spot_at_entry, atm_at_entry,
                            entry_time, entry_ltp, entry_oi, entry_volume, entry_iv,
                            peak_ltp, peak_time, trough_ltp, trough_time,
                            current_ltp, current_oi, oi_change, oi_change_pct,
                            qty, status
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')
                    """, (
                        today, idx, strike, side, offset, spot, atm,
                        now_iso, ltp, oi, vol, iv,
                        ltp, now_iso, ltp, now_iso,
                        ltp, oi, 0, 0.0,
                        qty
                    ))
                    created += 1
                except sqlite3.IntegrityError:
                    pass  # Already exists for this date/strike/side

    conn.commit()
    conn.close()
    print(f"[SHADOW] Created {created} shadow trades for {today}")
    return created


def update_all(engine):
    """Called periodically (every 60s) — update LTP, OI, peak/trough for all open shadow trades."""
    from engine import INDEX_CONFIG

    init_db()
    today = ist_now().strftime("%Y-%m-%d")
    now_iso = ist_now().isoformat()

    conn = _conn()
    trades = conn.execute(
        "SELECT * FROM shadow_trades WHERE date=? AND status='OPEN'", (today,)
    ).fetchall()

    if not trades:
        conn.close()
        return 0

    updated = 0
    snapshot_rows = []

    for t in trades:
        idx = t["idx"]
        strike = t["strike"]
        side = t["side"]
        chain = engine.chains.get(idx, {})
        sd = chain.get(strike, {})
        if not sd:
            continue

        current_ltp = sd.get(f"{side.lower()}_ltp", 0)
        current_oi = sd.get(f"{side.lower()}_oi", 0)
        current_vol = sd.get(f"{side.lower()}_volume", 0)
        spot_token = engine.spot_tokens.get(idx)
        current_spot = engine.prices.get(spot_token, {}).get("ltp", 0)

        if current_ltp <= 0:
            continue

        entry_ltp = t["entry_ltp"]
        entry_oi = t["entry_oi"]
        peak_ltp = t["peak_ltp"] or entry_ltp
        trough_ltp = t["trough_ltp"] or entry_ltp
        peak_time = t["peak_time"]
        trough_time = t["trough_time"]

        # Update peak/trough
        if current_ltp > peak_ltp:
            peak_ltp = current_ltp
            peak_time = now_iso
        if current_ltp < trough_ltp:
            trough_ltp = current_ltp
            trough_time = now_iso

        oi_change = current_oi - entry_oi
        oi_change_pct = round((oi_change / entry_oi * 100), 2) if entry_oi > 0 else 0

        conn.execute("""
            UPDATE shadow_trades SET
                current_ltp=?, current_oi=?, oi_change=?, oi_change_pct=?,
                peak_ltp=?, peak_time=?, trough_ltp=?, trough_time=?
            WHERE id=?
        """, (current_ltp, current_oi, oi_change, oi_change_pct,
              peak_ltp, peak_time, trough_ltp, trough_time, t["id"]))

        snapshot_rows.append((t["id"], now_iso, current_ltp, current_oi, current_vol, current_spot))
        updated += 1

    # Batch insert snapshots (every 60s → 52 rows/min = 52×330min ≈ 17K rows/day, manageable)
    if snapshot_rows:
        conn.executemany("""
            INSERT INTO shadow_snapshots (shadow_trade_id, timestamp, ltp, oi, volume, spot)
            VALUES (?,?,?,?,?,?)
        """, snapshot_rows)

    conn.commit()
    conn.close()
    return updated


def close_all(engine):
    """Called at 3:15 PM — close all open shadow trades with final PnL."""
    init_db()
    today = ist_now().strftime("%Y-%m-%d")
    now_iso = ist_now().isoformat()

    conn = _conn()
    trades = conn.execute(
        "SELECT * FROM shadow_trades WHERE date=? AND status='OPEN'", (today,)
    ).fetchall()

    closed = 0
    for t in trades:
        idx = t["idx"]
        strike = t["strike"]
        side = t["side"]
        chain = engine.chains.get(idx, {})
        sd = chain.get(strike, {})
        exit_ltp = sd.get(f"{side.lower()}_ltp", t["current_ltp"] or t["entry_ltp"])

        entry_ltp = t["entry_ltp"]
        qty = t["qty"]
        pnl_rupees = round((exit_ltp - entry_ltp) * qty, 2)
        pnl_pct = round((exit_ltp - entry_ltp) / entry_ltp * 100, 2) if entry_ltp > 0 else 0

        # Classify result
        if pnl_pct >= 50:
            result = "BIG_WIN"
        elif pnl_pct >= 10:
            result = "WIN"
        elif pnl_pct <= -50:
            result = "BIG_LOSS"
        elif pnl_pct <= -10:
            result = "LOSS"
        else:
            result = "FLAT"

        conn.execute("""
            UPDATE shadow_trades SET
                exit_time=?, exit_ltp=?, pnl_rupees=?, pnl_pct=?,
                status='CLOSED', result=?
            WHERE id=?
        """, (now_iso, exit_ltp, pnl_rupees, pnl_pct, result, t["id"]))
        closed += 1

    conn.commit()
    conn.close()
    print(f"[SHADOW] Closed {closed} shadow trades for {today}")
    return closed


def get_today_summary():
    """Get today's shadow autopsy summary — which strikes won, which lost, patterns."""
    init_db()
    today = ist_now().strftime("%Y-%m-%d")
    conn = _conn()

    trades = conn.execute(
        "SELECT * FROM shadow_trades WHERE date=? ORDER BY idx, side, offset", (today,)
    ).fetchall()
    conn.close()

    if not trades:
        return {"date": today, "count": 0, "trades": [], "summary": {}}

    rows = [dict(t) for t in trades]

    # Summary by outcome
    wins = [r for r in rows if (r.get("pnl_pct") or 0) > 0]
    losses = [r for r in rows if (r.get("pnl_pct") or 0) < 0]
    flat = [r for r in rows if abs(r.get("pnl_pct") or 0) < 1]

    # Best/worst
    sorted_by_pnl = sorted(rows, key=lambda x: x.get("pnl_pct") or 0, reverse=True)
    best = sorted_by_pnl[:5]
    worst = sorted_by_pnl[-5:]

    return {
        "date": today,
        "count": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(flat),
        "winRate": round(len(wins) / max(len(rows), 1) * 100, 1),
        "best": best,
        "worst": worst,
        "trades": rows,
    }


def get_history(days=7):
    """Historical summary across N days."""
    init_db()
    cutoff = (ist_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _conn()

    daily = conn.execute("""
        SELECT date, idx, side,
               COUNT(*) as total,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               AVG(pnl_pct) as avg_pnl_pct,
               SUM(pnl_rupees) as total_pnl
        FROM shadow_trades
        WHERE date >= ? AND status='CLOSED'
        GROUP BY date, idx, side
        ORDER BY date DESC
    """, (cutoff,)).fetchall()
    conn.close()

    return {"days": days, "rows": [dict(r) for r in daily]}
