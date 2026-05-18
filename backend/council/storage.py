"""
council.db — persistent storage for engine votes + council verdicts.

WHY ITS OWN DB
  Keeps council data isolated from the live trading databases (trades.db,
  scalper_trades.db, etc). Council writes are append-only and high-volume
  (one row per engine per pulse) — separate DB prevents lock contention
  on the trading-critical write paths.

SCHEMA (see ARCHITECTURE.md section 7 for full spec)
  engine_votes        — every vote emitted by every engine
  council_verdicts    — aggregated decision per pulse
  daily_briefings     — pre-market briefing snapshots (Phase 3)
  engine_accuracy     — rolling per-engine accuracy stats (Phase 5)
  scenarios_live      — scenario tree state (Phase 3)

LOCATION
  /data/council.db on Render (persistent disk).
  Local: backend/council.db (dev fallback).
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

from .vote import EngineVote, CouncilVerdict, Direction, Action


# ── Path resolution ──────────────────────────────────────────────────

def _resolve_db_path() -> Path:
    """/data/council.db on Render, else backend/council.db."""
    data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent.parent
    return data_dir / "council.db"


# ── Schema management ────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    pulse_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    direction TEXT NOT NULL,
    conviction REAL NOT NULL,
    reasoning TEXT,
    horizon TEXT,
    raw_score TEXT
);

CREATE INDEX IF NOT EXISTS idx_engine_votes_pulse ON engine_votes(pulse_id);
CREATE INDEX IF NOT EXISTS idx_engine_votes_timestamp ON engine_votes(timestamp);
CREATE INDEX IF NOT EXISTS idx_engine_votes_engine ON engine_votes(engine);

CREATE TABLE IF NOT EXISTS council_verdicts (
    pulse_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    action TEXT NOT NULL,
    bull_strength REAL,
    bear_strength REAL,
    neutral_count INTEGER,
    dissent_pct REAL,
    reasoning TEXT,
    actual_trade_fired INTEGER DEFAULT 0,
    actual_outcome_pnl REAL
);

CREATE INDEX IF NOT EXISTS idx_verdicts_timestamp ON council_verdicts(timestamp);
CREATE INDEX IF NOT EXISTS idx_verdicts_direction ON council_verdicts(direction);

CREATE TABLE IF NOT EXISTS daily_briefings (
    date TEXT PRIMARY KEY,
    today_close REAL,
    tomorrow_bias TEXT,
    conviction INTEGER,
    expected_range_low REAL,
    expected_range_high REAL,
    primary_scenario TEXT,
    narrative TEXT,
    actual_close_next_day REAL,
    bias_accuracy TEXT,
    raw_payload TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS engine_accuracy (
    engine TEXT PRIMARY KEY,
    total_predictions INTEGER DEFAULT 0,
    correct_predictions INTEGER DEFAULT 0,
    current_weight REAL DEFAULT 1.0,
    rolling_20d_accuracy REAL,
    last_updated TEXT
);

-- Auto-login attempts — every login attempt (daemon, external cron,
-- manual) writes one row here. Lets us tell, without reading Render
-- logs, whether the daemon fired on a given morning and what happened.
CREATE TABLE IF NOT EXISTS auto_login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trigger_source TEXT NOT NULL,        -- daemon / external_cron / manual / self_heal
    status TEXT NOT NULL,                -- success / failed / skipped
    error TEXT,                          -- exception message if failed
    access_token_preview TEXT,           -- first 8 chars for audit
    duration_ms INTEGER,                 -- how long the attempt took
    extra TEXT                           -- JSON extras (retry count, etc.)
);

CREATE INDEX IF NOT EXISTS idx_autologin_timestamp ON auto_login_attempts(timestamp);
CREATE INDEX IF NOT EXISTS idx_autologin_source ON auto_login_attempts(trigger_source);
CREATE INDEX IF NOT EXISTS idx_autologin_status ON auto_login_attempts(status);
"""


_schema_applied = False
_schema_lock = None  # lazy-import threading to avoid top-level dependency


def _conn() -> sqlite3.Connection:
    """Open a connection. Caller responsible for closing.
    Ensures schema is applied at least once per process."""
    _ensure_schema_once()
    path = _resolve_db_path()
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrent reads
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema_once() -> None:
    """Idempotent + thread-safe schema apply. Cheap on hot path."""
    global _schema_applied, _schema_lock
    if _schema_applied:
        return
    import threading
    if _schema_lock is None:
        _schema_lock = threading.Lock()
    with _schema_lock:
        if _schema_applied:
            return
        path = _resolve_db_path()
        conn = sqlite3.connect(str(path), timeout=10.0)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _schema_applied = True


def init_db() -> Path:
    """Create schema if not exists. Idempotent. Safe to call eagerly
    (e.g. at FastAPI startup) — subsequent calls are no-ops."""
    _ensure_schema_once()
    return _resolve_db_path()


# ── Vote / verdict persistence ───────────────────────────────────────

def save_verdict(verdict: CouncilVerdict) -> None:
    """Persist a verdict + all its underlying engine votes.

    Idempotent — uses pulse_id as primary key; re-inserts replace.
    """
    conn = _conn()
    try:
        # Save the verdict
        conn.execute("""
            INSERT OR REPLACE INTO council_verdicts (
                pulse_id, timestamp, direction, confidence, action,
                bull_strength, bear_strength, neutral_count, dissent_pct,
                reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            verdict.pulse_id,
            verdict.timestamp.isoformat(),
            verdict.direction.value,
            verdict.confidence,
            verdict.action.value,
            verdict.bull_strength,
            verdict.bear_strength,
            verdict.neutral_count,
            verdict.dissent_pct,
            verdict.reasoning,
        ))

        # Save each underlying vote
        for vote in verdict.votes:
            if not isinstance(vote, EngineVote):
                continue  # safety
            conn.execute("""
                INSERT INTO engine_votes (
                    timestamp, pulse_id, engine, direction, conviction,
                    reasoning, horizon, raw_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                vote.timestamp.isoformat(),
                verdict.pulse_id,
                vote.engine,
                vote.direction.value,
                vote.conviction,
                vote.reasoning,
                vote.horizon.value,
                json.dumps(vote.raw_score) if vote.raw_score else None,
            ))
        conn.commit()
    finally:
        conn.close()


# ── Read APIs (powering /api/council/*) ──────────────────────────────

def get_latest_verdict() -> Optional[dict]:
    """Return the most recent council verdict with its underlying votes."""
    conn = _conn()
    try:
        row = conn.execute("""
            SELECT * FROM council_verdicts
            ORDER BY timestamp DESC LIMIT 1
        """).fetchone()
        if not row:
            return None
        verdict = dict(row)

        # Attach votes for this pulse
        votes = conn.execute("""
            SELECT engine, direction, conviction, reasoning, horizon, raw_score
            FROM engine_votes
            WHERE pulse_id = ?
            ORDER BY engine
        """, (verdict["pulse_id"],)).fetchall()
        verdict["votes"] = [
            {
                **dict(v),
                "raw_score": json.loads(v["raw_score"]) if v["raw_score"] else None,
            }
            for v in votes
        ]
        return verdict
    finally:
        conn.close()


def get_recent_verdicts(limit: int = 100) -> List[dict]:
    """Return the most recent N verdicts (without votes — use latest for that)."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT pulse_id, timestamp, direction, confidence, action,
                   bull_strength, bear_strength, neutral_count, dissent_pct,
                   reasoning, actual_trade_fired, actual_outcome_pnl
            FROM council_verdicts
            ORDER BY timestamp DESC
            LIMIT ?
        """, (max(1, min(limit, 500)),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_verdicts_in_range(start_iso: str, end_iso: str) -> List[dict]:
    """All verdicts between two ISO timestamps (inclusive)."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT * FROM council_verdicts
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
        """, (start_iso, end_iso)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_engine_stats(engine: Optional[str] = None) -> List[dict]:
    """Aggregated stats per engine — vote count, direction breakdown.

    If `engine` provided, returns just that one. Else all.
    """
    conn = _conn()
    try:
        if engine:
            rows = conn.execute("""
                SELECT engine, direction, COUNT(*) as n, AVG(conviction) as avg_conviction
                FROM engine_votes
                WHERE engine = ?
                GROUP BY engine, direction
            """, (engine,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT engine, direction, COUNT(*) as n, AVG(conviction) as avg_conviction
                FROM engine_votes
                GROUP BY engine, direction
                ORDER BY engine, direction
            """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_trade_outcome(pulse_id: str, trade_fired: bool, pnl: Optional[float] = None) -> None:
    """Update a verdict with what actually happened.

    Called after trade entry decision is made (regardless of council's
    advice in Phase 1 — we want to compare council's call vs reality).
    """
    conn = _conn()
    try:
        conn.execute("""
            UPDATE council_verdicts
            SET actual_trade_fired = ?, actual_outcome_pnl = ?
            WHERE pulse_id = ?
        """, (1 if trade_fired else 0, pnl, pulse_id))
        conn.commit()
    finally:
        conn.close()


# ── Auto-login attempt persistence ──────────────────────────────────

def log_autologin_attempt(
    trigger_source: str,
    status: str,
    error: Optional[str] = None,
    access_token_preview: Optional[str] = None,
    duration_ms: Optional[int] = None,
    extra: Optional[dict] = None,
) -> None:
    """Record one auto-login attempt. Safe to call from any thread.
    NEVER raises — DB failures are swallowed + logged.

    Args:
        trigger_source: "daemon" / "external_cron" / "manual" / "self_heal"
        status: "success" / "failed" / "skipped"
        error: exception message if status==failed
        access_token_preview: first 8 chars of token if success
        duration_ms: how long the attempt took
        extra: JSON-serializable extras (retry count, window info, etc.)
    """
    import json as _json
    conn = None
    try:
        conn = _conn()
        conn.execute("""
            INSERT INTO auto_login_attempts (
                timestamp, trigger_source, status, error,
                access_token_preview, duration_ms, extra
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            trigger_source,
            status,
            error,
            access_token_preview,
            duration_ms,
            _json.dumps(extra) if extra else None,
        ))
        conn.commit()
    except Exception as e:
        # Never let logging failures propagate
        print(f"[STORAGE] log_autologin_attempt failed: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def get_recent_autologin_attempts(limit: int = 50) -> List[dict]:
    """Most recent auto-login attempts across all triggers."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT id, timestamp, trigger_source, status, error,
                   access_token_preview, duration_ms, extra
            FROM auto_login_attempts
            ORDER BY timestamp DESC
            LIMIT ?
        """, (max(1, min(limit, 200)),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_autologin_summary(days: int = 7) -> dict:
    """Aggregated stats per day per source.

    Returns:
        {
            "by_day": {
                "2026-05-12": {
                    "daemon": {"success": 1, "failed": 3, "total": 4},
                    "external_cron": {"success": 0, "failed": 0, "total": 0},
                    ...
                },
                ...
            },
            "totals": { "success": N, "failed": M, "skipped": K }
        }
    """
    from collections import defaultdict
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT timestamp, trigger_source, status
            FROM auto_login_attempts
            WHERE timestamp > ?
            ORDER BY timestamp DESC
        """, (cutoff,)).fetchall()

        by_day: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        totals = defaultdict(int)
        for r in rows:
            day = r["timestamp"][:10]
            src = r["trigger_source"]
            status = r["status"]
            by_day[day][src][status] += 1
            by_day[day][src]["total"] += 1
            totals[status] += 1

        # Convert defaultdicts → plain dicts for JSON
        return {
            "days": days,
            "by_day": {
                day: {src: dict(stats) for src, stats in sources.items()}
                for day, sources in by_day.items()
            },
            "totals": dict(totals),
        }
    finally:
        conn.close()


def summary_stats(days: int = 1) -> dict:
    """Quick summary — verdict counts by direction over last N days."""
    conn = _conn()
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute("""
            SELECT direction, COUNT(*) as n
            FROM council_verdicts
            WHERE timestamp > ?
            GROUP BY direction
        """, (cutoff,)).fetchall()
        total = sum(r["n"] for r in rows)
        return {
            "days": days,
            "total_verdicts": total,
            "by_direction": {r["direction"]: r["n"] for r in rows},
        }
    finally:
        conn.close()
