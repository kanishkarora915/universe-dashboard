"""SQLite persistence — exact schema from spec §7.3 + auto-pruning."""

import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta

_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent.parent
DB_PATH = _data_dir / "trinity.db"


def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    # WAL for concurrent reads/writes + 2GB RAM optimizations
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-128000")  # 128MB cache
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trinity_ticks (
            ts INTEGER PRIMARY KEY,
            spot REAL,
            future REAL,
            synthetic REAL,
            deviation REAL,
            premium REAL,
            regime TEXT,
            confidence REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tt_ts ON trinity_ticks(ts DESC)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trinity_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            signal_type TEXT,
            strike INTEGER,
            premium REAL,
            confidence REAL,
            reasoning TEXT,
            trap_zone_upper REAL,
            trap_zone_lower REAL,
            stop_loss_premium REAL,
            target_premium REAL,
            regime TEXT,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_ts ON trinity_signals(ts DESC)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trinity_strike_data (
            ts INTEGER,
            strike INTEGER,
            type TEXT,
            ltp REAL,
            oi INTEGER,
            volume INTEGER,
            iv REAL,
            PRIMARY KEY (ts, strike, type)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tsd_ts ON trinity_strike_data(ts DESC)")

    conn.commit()
    conn.close()


def save_tick(snapshot):
    """Persist a 1-sec aggregated trinity tick."""
    init_db()
    conn = _conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO trinity_ticks
            (ts, spot, future, synthetic, deviation, premium, regime, confidence)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            int(snapshot.get("ts", time.time() * 1000)),
            snapshot.get("spot"), snapshot.get("future"),
            snapshot.get("synthetic"), snapshot.get("deviation"),
            snapshot.get("premium"), snapshot.get("regime"),
            snapshot.get("confidence"),
        ))
        conn.commit()
    finally:
        conn.close()


def save_strike_batch(ts_ms, strikes):
    """Batch insert per-strike snapshot. strikes = list of dicts."""
    if not strikes:
        return
    init_db()
    conn = _conn()
    try:
        rows = [
            (ts_ms, s.get("strike"), s.get("type"), s.get("ltp"),
             s.get("oi"), s.get("volume"), s.get("iv"))
            for s in strikes
        ]
        conn.executemany("""
            INSERT OR REPLACE INTO trinity_strike_data
            (ts, strike, type, ltp, oi, volume, iv) VALUES (?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
    finally:
        conn.close()


def save_signal(signal):
    init_db()
    conn = _conn()
    try:
        cur = conn.execute("""
            INSERT INTO trinity_signals
            (ts, signal_type, strike, premium, confidence, reasoning,
             trap_zone_upper, trap_zone_lower, stop_loss_premium, target_premium,
             regime, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'ACTIVE')
        """, (
            int(signal.get("ts", time.time() * 1000)),
            signal.get("signal_type"),
            signal.get("strike"),
            signal.get("premium"),
            signal.get("confidence"),
            signal.get("reasoning"),
            signal.get("trap_zone_upper"),
            signal.get("trap_zone_lower"),
            signal.get("stop_loss_premium"),
            signal.get("target_premium"),
            signal.get("regime"),
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_timeseries(mins=30, limit=2000):
    """Return last N minutes of trinity ticks for chart."""
    init_db()
    cutoff_ms = int((time.time() - mins * 60) * 1000)
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT ts, spot, future, synthetic, deviation, premium, regime, confidence
            FROM trinity_ticks WHERE ts>=? ORDER BY ts ASC LIMIT ?
        """, (cutoff_ms, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_signals(limit=20):
    init_db()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT * FROM trinity_signals ORDER BY ts DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active_signals():
    init_db()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT * FROM trinity_signals WHERE status='ACTIVE'
            ORDER BY ts DESC LIMIT 5
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_signal_status(sid, status):
    init_db()
    conn = _conn()
    try:
        conn.execute("UPDATE trinity_signals SET status=? WHERE id=?", (status, sid))
        conn.commit()
    finally:
        conn.close()


def prune_old_data(days=7):
    """Auto-prune ticks older than N days (keeps DB size bounded)."""
    init_db()
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    conn = _conn()
    try:
        conn.execute("DELETE FROM trinity_ticks WHERE ts<?", (cutoff_ms,))
        conn.execute("DELETE FROM trinity_strike_data WHERE ts<?", (cutoff_ms,))
        conn.execute("DELETE FROM trinity_signals WHERE ts<? AND status!='ACTIVE'", (cutoff_ms,))
        conn.commit()
    finally:
        conn.close()
