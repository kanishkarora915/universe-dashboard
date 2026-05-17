"""
perf_monitor — periodic system metrics sampling.

PURPOSE
  When engine dies (like 2026-05-14 at 3:55 AM), we currently have
  ZERO data to diagnose root cause. Render logs roll over, no Sentry
  trace, no clue why it died.

  This module samples system state every 5 minutes (always, 24/7)
  and writes to council.db. Next failure → query the table → know
  exactly what was happening when the system crashed.

METRICS CAPTURED PER SAMPLE
  • Memory: RSS, VMS, available
  • CPU: process %, system load avg
  • Threads: count + names (enumerate)
  • Disk: /data usage %, total free
  • Engine: running flag, last_tick_age_sec, ws_alive
  • DB sizes: snapshot of major DB files
  • Open file descriptors

STORAGE
  council.db `perf_samples` table — append only.
  Auto-pruned after 30 days.

EXPOSED VIA
  GET /api/perf-stats              → latest sample
  GET /api/perf-history?hours=24   → last N hours of samples
"""

import os
import time
import threading
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta


# Sample every 5 minutes
SAMPLE_INTERVAL_SEC = 300

# Retention
PERF_RETENTION_DAYS = 30


def _resolve_council_db() -> Path:
    base = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
    return base / "council.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS perf_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,                   -- unix ms
    iso TEXT NOT NULL,                     -- ISO timestamp
    memory_rss_mb REAL,
    memory_vms_mb REAL,
    memory_available_mb REAL,
    cpu_percent REAL,
    load_avg_1min REAL,
    thread_count INTEGER,
    thread_names TEXT,                     -- JSON list
    disk_used_mb REAL,
    disk_free_mb REAL,
    disk_percent REAL,
    engine_running INTEGER,                -- 0/1
    engine_last_tick_age_sec REAL,
    ws_alive INTEGER,                      -- 0/1
    open_fds INTEGER,
    db_sizes TEXT,                         -- JSON dict {db_name: mb}
    extra TEXT                             -- JSON for future fields
);

CREATE INDEX IF NOT EXISTS idx_perf_ts ON perf_samples(ts);
"""


_schema_applied = False
_schema_lock = threading.Lock()


def _ensure_schema():
    global _schema_applied
    if _schema_applied:
        return
    with _schema_lock:
        if _schema_applied:
            return
        try:
            conn = sqlite3.connect(str(_resolve_council_db()), timeout=10)
            conn.executescript(SCHEMA)
            conn.commit()
            conn.close()
            _schema_applied = True
        except Exception as e:
            print(f"[PERF-MON] schema init failed: {e}")


# ── Metric collection helpers ────────────────────────────────────────

def _collect_memory():
    """Return (rss_mb, vms_mb, available_mb)."""
    try:
        import psutil
        p = psutil.Process(os.getpid())
        mem = p.memory_info()
        vmem = psutil.virtual_memory()
        return (
            mem.rss / 1024 / 1024,
            mem.vms / 1024 / 1024,
            vmem.available / 1024 / 1024,
        )
    except Exception:
        # psutil might not be installed — degrade gracefully using /proc
        try:
            with open("/proc/self/status") as f:
                rss = vms = 0
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) / 1024
                    elif line.startswith("VmSize:"):
                        vms = int(line.split()[1]) / 1024
            return (rss, vms, 0.0)
        except Exception:
            return (0.0, 0.0, 0.0)


def _collect_cpu():
    """Return (cpu_percent, load_avg_1min)."""
    try:
        import psutil
        p = psutil.Process(os.getpid())
        cpu = p.cpu_percent(interval=None)  # non-blocking — uses last call's delta
        load1, _, _ = os.getloadavg()
        return (cpu, load1)
    except Exception:
        try:
            load1, _, _ = os.getloadavg()
            return (0.0, load1)
        except Exception:
            return (0.0, 0.0)


def _collect_threads():
    """Return (count, names_json)."""
    try:
        threads = threading.enumerate()
        names = [t.name for t in threads]
        return (len(threads), json.dumps(names))
    except Exception:
        return (0, "[]")


def _collect_disk():
    """Return (used_mb, free_mb, percent_used)."""
    try:
        import shutil
        path = "/data" if os.path.isdir("/data") else "/"
        total, used, free = shutil.disk_usage(path)
        return (
            used / 1024 / 1024,
            free / 1024 / 1024,
            (used / total * 100) if total > 0 else 0,
        )
    except Exception:
        return (0.0, 0.0, 0.0)


def _collect_engine_state(engine_getter):
    """Return (running, last_tick_age_sec, ws_alive)."""
    try:
        eng = engine_getter()
        if eng is None:
            return (0, None, 0)
        running = 1 if getattr(eng, "running", False) else 0
        ws_alive = 1 if (hasattr(eng, "ticker") and eng.ticker is not None) else 0
        last_tick = getattr(eng, "_last_tick_time", 0)
        age = (time.time() - last_tick) if last_tick > 0 else None
        return (running, age, ws_alive)
    except Exception:
        return (0, None, 0)


def _collect_open_fds() -> int:
    """Number of open file descriptors (Linux-only)."""
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except Exception:
        return 0


def _collect_db_sizes() -> str:
    """JSON dict of major DB file sizes in MB."""
    try:
        data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
        sizes = {}
        for p in data_dir.glob("*.db"):
            try:
                sizes[p.name] = round(p.stat().st_size / 1024 / 1024, 2)
            except Exception:
                pass
        return json.dumps(sizes)
    except Exception:
        return "{}"


# ── Sample + persist ─────────────────────────────────────────────────

def take_sample(engine_getter) -> dict:
    """Collect one snapshot. Returns the dict (also written to DB)."""
    _ensure_schema()

    now = time.time()
    iso = datetime.utcfromtimestamp(now).isoformat()

    rss, vms, avail = _collect_memory()
    cpu_pct, load1 = _collect_cpu()
    th_count, th_names = _collect_threads()
    disk_used, disk_free, disk_pct = _collect_disk()
    eng_running, tick_age, ws_alive = _collect_engine_state(engine_getter)
    fds = _collect_open_fds()
    db_sizes = _collect_db_sizes()

    sample = {
        "ts": int(now * 1000),
        "iso": iso,
        "memory_rss_mb": round(rss, 1),
        "memory_vms_mb": round(vms, 1),
        "memory_available_mb": round(avail, 1),
        "cpu_percent": round(cpu_pct, 1),
        "load_avg_1min": round(load1, 2),
        "thread_count": th_count,
        "thread_names": th_names,
        "disk_used_mb": round(disk_used, 1),
        "disk_free_mb": round(disk_free, 1),
        "disk_percent": round(disk_pct, 1),
        "engine_running": eng_running,
        "engine_last_tick_age_sec": round(tick_age, 1) if tick_age is not None else None,
        "ws_alive": ws_alive,
        "open_fds": fds,
        "db_sizes": db_sizes,
        "extra": "{}",
    }

    # Persist
    try:
        conn = sqlite3.connect(str(_resolve_council_db()), timeout=10)
        try:
            cols = ", ".join(sample.keys())
            placeholders = ", ".join("?" * len(sample))
            conn.execute(
                f"INSERT INTO perf_samples ({cols}) VALUES ({placeholders})",
                tuple(sample.values()),
            )
            conn.commit()

            # Prune old samples (keep last 30 days)
            cutoff = int((time.time() - PERF_RETENTION_DAYS * 86400) * 1000)
            conn.execute("DELETE FROM perf_samples WHERE ts < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[PERF-MON] write failed: {e}")

    return sample


def get_latest_sample() -> dict:
    """Most recent perf sample."""
    _ensure_schema()
    try:
        conn = sqlite3.connect(f"file:{_resolve_council_db()}?mode=ro",
                                uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM perf_samples ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {}
            d = dict(row)
            # Decode JSON fields
            for k in ("thread_names", "db_sizes", "extra"):
                if d.get(k):
                    try:
                        d[k] = json.loads(d[k])
                    except Exception:
                        pass
            return d
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


def get_history(hours: int = 24, limit: int = 500) -> list:
    """Recent samples within N hours, capped at `limit` rows."""
    _ensure_schema()
    try:
        cutoff = int((time.time() - hours * 3600) * 1000)
        conn = sqlite3.connect(f"file:{_resolve_council_db()}?mode=ro",
                                uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT ts, iso, memory_rss_mb, cpu_percent, load_avg_1min,
                       thread_count, disk_percent, engine_running, ws_alive,
                       engine_last_tick_age_sec, open_fds
                FROM perf_samples
                WHERE ts > ?
                ORDER BY ts DESC
                LIMIT ?
            """, (cutoff, max(1, min(limit, 2000)))).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        return [{"error": str(e)}]


# ── Background sampling loop ─────────────────────────────────────────

def run_sampler(engine_getter, interval_sec: int = SAMPLE_INTERVAL_SEC):
    """Background thread entrypoint."""
    print(f"[PERF-MON] Started — sampling every {interval_sec}s")
    while True:
        try:
            sample = take_sample(engine_getter)
            # Brief stdout log so it's visible in Render logs too
            print(
                f"[PERF-MON] mem={sample['memory_rss_mb']:.0f}MB "
                f"cpu={sample['cpu_percent']}% "
                f"threads={sample['thread_count']} "
                f"disk={sample['disk_percent']:.1f}% "
                f"engine={'✓' if sample['engine_running'] else '✗'}"
            )
        except Exception as e:
            print(f"[PERF-MON] sample failed: {e}")
        time.sleep(interval_sec)
