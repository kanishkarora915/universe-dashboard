"""
trinity/prune.py — DB maintenance for trinity.db.

PURPOSE
  Trinity captures tick-level data every 1s (was 500ms) → ~900K rows
  per day across 40 strikes. trinity.db grew to 111 MB before this
  module was added. Without pruning, would fill the 5 GB persistent
  disk in ~25 days.

STRATEGY
  Keep last N days of raw tick data, delete older.
  (Simple delete + VACUUM. Aggregation into minute bars is a future
  enhancement — for now, simple time-window retention is enough.)

SCHEDULE
  Nightly at 01:00 AM IST (off-hours, zero traffic, engine usually
  in idle state). Wired in main.py lifespan via APScheduler.

RETENTION
  RAW_TICKS_KEEP_DAYS days of trinity_ticks
  STRIKE_DATA_KEEP_DAYS days of trinity_strike_data
  Signals: never pruned (small table, valuable history)
"""

import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


# Retention windows — tunable
RAW_TICKS_KEEP_DAYS = 7
STRIKE_DATA_KEEP_DAYS = 14


def _trinity_db_path() -> Path:
    base = Path("/data") if Path("/data").is_dir() else Path(__file__).parent.parent
    return base / "trinity.db"


def _db_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / 1024 / 1024
    except Exception:
        return 0.0


def prune_trinity_db(
    raw_ticks_keep_days: int = RAW_TICKS_KEEP_DAYS,
    strike_data_keep_days: int = STRIKE_DATA_KEEP_DAYS,
    do_vacuum: bool = True,
) -> dict:
    """Run the prune. Safe to call any time. Returns stats dict.

    Behavior:
      1. DELETE FROM trinity_ticks WHERE ts < <cutoff>
      2. DELETE FROM trinity_strike_data WHERE ts < <cutoff>
      3. PRAGMA wal_checkpoint(TRUNCATE) — recover WAL space
      4. VACUUM — reclaim file space (slow, ~30s for 100MB)

    Returns:
        {
            "started_at": ISO timestamp,
            "size_before_mb": float,
            "size_after_mb": float,
            "freed_mb": float,
            "ticks_deleted": int,
            "strike_rows_deleted": int,
            "duration_sec": float,
            "vacuum_ran": bool,
            "error": Optional[str],
        }
    """
    path = _trinity_db_path()
    started = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()
    result = {
        "started_at": started_iso,
        "db_path": str(path),
        "size_before_mb": _db_size_mb(path),
        "size_after_mb": 0.0,
        "freed_mb": 0.0,
        "ticks_deleted": 0,
        "strike_rows_deleted": 0,
        "duration_sec": 0.0,
        "vacuum_ran": False,
        "error": None,
    }

    if not path.exists():
        result["error"] = "trinity.db not found"
        result["duration_sec"] = time.time() - started
        return result

    try:
        # ts column in trinity tables is stored as milliseconds-since-epoch
        # (per storage.py — int(time.time() * 1000))
        raw_cutoff_ms = int((time.time() - raw_ticks_keep_days * 86400) * 1000)
        strike_cutoff_ms = int((time.time() - strike_data_keep_days * 86400) * 1000)

        conn = sqlite3.connect(str(path), timeout=30.0)
        try:
            # 1. Delete old raw ticks
            cur = conn.execute(
                "DELETE FROM trinity_ticks WHERE ts < ?",
                (raw_cutoff_ms,),
            )
            result["ticks_deleted"] = cur.rowcount

            # 2. Delete old strike data
            cur = conn.execute(
                "DELETE FROM trinity_strike_data WHERE ts < ?",
                (strike_cutoff_ms,),
            )
            result["strike_rows_deleted"] = cur.rowcount

            conn.commit()

            # 3. WAL checkpoint — recover WAL file space
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as e:
                print(f"[TRINITY-PRUNE] wal_checkpoint failed: {e}")

            # 4. VACUUM — physically reclaim file space
            if do_vacuum:
                try:
                    conn.execute("VACUUM")
                    result["vacuum_ran"] = True
                except Exception as e:
                    print(f"[TRINITY-PRUNE] VACUUM failed: {e}")
                    result["error"] = f"vacuum_failed: {e}"
        finally:
            conn.close()

        result["size_after_mb"] = _db_size_mb(path)
        result["freed_mb"] = round(result["size_before_mb"] - result["size_after_mb"], 2)

    except Exception as e:
        result["error"] = str(e)

    result["duration_sec"] = round(time.time() - started, 2)
    return result


def get_trinity_db_stats() -> dict:
    """Quick stats without modifying anything. Used by /api/trinity/db-stats."""
    path = _trinity_db_path()
    out = {
        "db_path": str(path),
        "size_mb": _db_size_mb(path),
        "exists": path.exists(),
        "tables": {},
    }
    if not path.exists():
        return out

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        try:
            for table in ["trinity_ticks", "trinity_signals", "trinity_strike_data"]:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
                    out["tables"][table] = {"row_count": cur.fetchone()[0]}
                    # Min/max ts (oldest + newest)
                    cur = conn.execute(f"SELECT MIN(ts), MAX(ts) FROM {table}")
                    mn, mx = cur.fetchone()
                    out["tables"][table]["min_ts"] = mn
                    out["tables"][table]["max_ts"] = mx
                    if mn and mx:
                        out["tables"][table]["span_days"] = round((mx - mn) / 86400000, 1)
                except Exception as e:
                    out["tables"][table] = {"error": str(e)}
        finally:
            conn.close()
    except Exception as e:
        out["error"] = str(e)

    return out
