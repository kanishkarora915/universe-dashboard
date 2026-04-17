"""
Alerts DB — persistent alert store for Universe Pro.
Severity: CRITICAL | WARNING | INFO | AMBIENT
Types: TRADE_ENTRY, TRADE_EXIT_SL, TRADE_EXIT_T1, TRADE_EXIT_T2, MANUAL_EXIT_REQ,
       STALE_TICKER, KITE_DISCONNECT, SL_APPROACHING, PROFIT_PROTECT,
       NEW_SIGNAL_HIGH, GAP_PREDICTION_HIGH, UNUSUAL_OI_SPIKE, TRAP_FINGERPRINT,
       SIGNAL_CHANGE, PCR_EXTREME, VIX_SPIKE, EXPIRY_WARNING, AI_INSIGHT,
       AUTOPSY_INSIGHT, WEEKLY_TRAINING_DONE, REPORT_READY
"""

import sqlite3
import json
import threading
from datetime import datetime
from pathlib import Path
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "alerts.db"

_lock = threading.Lock()

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"
SEVERITY_AMBIENT = "AMBIENT"

# Map alert_type -> severity + target tab (sidebar icon to flash)
ALERT_META = {
    "TRADE_ENTRY":          {"severity": SEVERITY_CRITICAL, "tab": "pnl"},
    "TRADE_EXIT_SL":        {"severity": SEVERITY_CRITICAL, "tab": "pnl"},
    "TRADE_EXIT_T1":        {"severity": SEVERITY_CRITICAL, "tab": "pnl"},
    "TRADE_EXIT_T2":        {"severity": SEVERITY_CRITICAL, "tab": "pnl"},
    "TRADE_EXIT_EOD":       {"severity": SEVERITY_INFO,     "tab": "pnl"},
    "MANUAL_EXIT_REQ":      {"severity": SEVERITY_CRITICAL, "tab": "pnl"},
    "STALE_TICKER":         {"severity": SEVERITY_CRITICAL, "tab": "dashboard"},
    "KITE_DISCONNECT":      {"severity": SEVERITY_CRITICAL, "tab": "dashboard"},
    "SL_APPROACHING":       {"severity": SEVERITY_WARNING,  "tab": "pnl"},
    "PROFIT_PROTECT":       {"severity": SEVERITY_WARNING,  "tab": "pnl"},
    "NEW_SIGNAL_HIGH":      {"severity": SEVERITY_WARNING,  "tab": "dashboard"},
    "GAP_PREDICTION_HIGH":  {"severity": SEVERITY_WARNING,  "tab": "autopsy"},
    "UNUSUAL_OI_SPIKE":     {"severity": SEVERITY_WARNING,  "tab": "oi"},
    "TRAP_FINGERPRINT":     {"severity": SEVERITY_WARNING,  "tab": "dashboard"},
    "SIGNAL_CHANGE":        {"severity": SEVERITY_INFO,     "tab": "dashboard"},
    "PCR_EXTREME":          {"severity": SEVERITY_INFO,     "tab": "dashboard"},
    "VIX_SPIKE":            {"severity": SEVERITY_INFO,     "tab": "dashboard"},
    "EXPIRY_WARNING":       {"severity": SEVERITY_INFO,     "tab": "dashboard"},
    "AI_INSIGHT":           {"severity": SEVERITY_INFO,     "tab": "dashboard"},
    "AUTOPSY_INSIGHT":      {"severity": SEVERITY_AMBIENT,  "tab": "autopsy"},
    "WEEKLY_TRAINING_DONE": {"severity": SEVERITY_AMBIENT,  "tab": "settings"},
    "REPORT_READY":         {"severity": SEVERITY_AMBIENT,  "tab": "reports"},
}


def ist_now():
    return datetime.now(IST)


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                tab TEXT,
                title TEXT NOT NULL,
                message TEXT,
                meta_json TEXT,
                read INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0,
                dismissed INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_read ON alerts(read)")
        conn.commit()
        conn.close()


def push_alert(alert_type: str, title: str, message: str = "", meta: dict = None) -> dict:
    """Insert a new alert. Returns the created alert row as dict."""
    init_db()
    meta_info = ALERT_META.get(alert_type, {"severity": SEVERITY_INFO, "tab": "dashboard"})
    severity = meta_info["severity"]
    tab = meta_info["tab"]

    now = ist_now().isoformat()
    meta_json = json.dumps(meta or {}, default=str)

    with _lock:
        conn = _conn()
        cur = conn.execute("""
            INSERT INTO alerts (created_at, alert_type, severity, tab, title, message, meta_json)
            VALUES (?,?,?,?,?,?,?)
        """, (now, alert_type, severity, tab, title, message, meta_json))
        alert_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        conn.close()

    print(f"[ALERT] {severity} · {alert_type} · {title}")

    # Notify WS subscribers (best effort)
    try:
        from ws_manager import broadcast  # optional
        broadcast({"type": "alert_new", "alert": _row_to_dict(row)})
    except Exception:
        pass

    return _row_to_dict(row)


def _row_to_dict(r):
    d = dict(r)
    try:
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
    except Exception:
        d["meta"] = {}
    return d


def list_alerts(limit: int = 100, offset: int = 0, severity: str = None, alert_type: str = None,
                unread_only: bool = False, include_dismissed: bool = False):
    init_db()
    where = []
    args = []
    if severity:
        where.append("severity=?")
        args.append(severity)
    if alert_type:
        where.append("alert_type=?")
        args.append(alert_type)
    if unread_only:
        where.append("read=0")
    if not include_dismissed:
        where.append("dismissed=0")
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    args += [limit, offset]

    conn = _conn()
    rows = conn.execute(
        f"SELECT * FROM alerts {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", args
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_unread_counts() -> dict:
    """Return unread counts per tab + total."""
    init_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT tab, COUNT(*) as c FROM alerts WHERE read=0 AND dismissed=0 GROUP BY tab"
    ).fetchall()
    total_row = conn.execute(
        "SELECT COUNT(*) as c FROM alerts WHERE read=0 AND dismissed=0"
    ).fetchone()
    conn.close()

    out = {"total": total_row["c"] if total_row else 0, "byTab": {}}
    for r in rows:
        out["byTab"][r["tab"]] = r["c"]
    return out


def mark_read(alert_ids: list = None, tab: str = None, all_: bool = False):
    init_db()
    with _lock:
        conn = _conn()
        if all_:
            conn.execute("UPDATE alerts SET read=1")
        elif tab:
            conn.execute("UPDATE alerts SET read=1 WHERE tab=? AND read=0", (tab,))
        elif alert_ids:
            placeholders = ",".join(["?"] * len(alert_ids))
            conn.execute(f"UPDATE alerts SET read=1 WHERE id IN ({placeholders})", alert_ids)
        conn.commit()
        conn.close()


def dismiss(alert_id: int):
    init_db()
    with _lock:
        conn = _conn()
        conn.execute("UPDATE alerts SET dismissed=1 WHERE id=?", (alert_id,))
        conn.commit()
        conn.close()


def pin(alert_id: int, pinned: bool = True):
    init_db()
    with _lock:
        conn = _conn()
        conn.execute("UPDATE alerts SET pinned=? WHERE id=?", (1 if pinned else 0, alert_id))
        conn.commit()
        conn.close()


def clear_old(days: int = 30):
    """Delete dismissed alerts older than N days to keep DB small."""
    init_db()
    cutoff = (ist_now() - __import__("datetime").timedelta(days=days)).isoformat()
    with _lock:
        conn = _conn()
        conn.execute("DELETE FROM alerts WHERE dismissed=1 AND created_at<?", (cutoff,))
        conn.commit()
        conn.close()
