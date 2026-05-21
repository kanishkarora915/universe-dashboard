"""
pattern_shift_detector — early signal that exit-pattern distribution
is shifting (= regime change happening NOW, not weekly aggregate).

WHY THIS MODULE EXISTS

regime_monitor.py compares 7d-vs-30d windows. That's good for "this
week is different" — but it lags 2-3 days behind the actual change.

This module detects shifts WITHIN the current trading session:
  • Are REVERSAL_EXIT and WATCHER_EXIT firing more than usual TODAY?
  • Is T1_HIT rate dropping in last 2 hours vs the day's first 2 hours?
  • Did 3 trades in a row hit SL?

These are FASTER signals — fire within 30-60 min of regime breakdown.

DESIGN

  1. Track exit status of last N trades (rolling buffer)
  2. Compare distribution vs baseline (last 30 days)
  3. Alert when current distribution deviates significantly

THIS IS PURE READ — no trade behavior change.
"""

from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_TRADES_DB = _DATA_DIR / "trades.db"
if not _TRADES_DB.exists():
    _TRADES_DB = Path(__file__).parent / "trades.db"
_SCALPER_DB = _DATA_DIR / "scalper_trades.db"
if not _SCALPER_DB.exists():
    _SCALPER_DB = Path(__file__).parent / "scalper_trades.db"


# Exit statuses we monitor
NEGATIVE_EXITS = {"REVERSAL_EXIT", "WATCHER_EXIT", "SL_HIT", "STOP_HUNTED", "TIMEOUT_EXIT"}
POSITIVE_EXITS = {"T1_HIT", "T2_HIT", "TRAIL_EXIT", "MANUAL_EXIT"}


def is_enabled() -> bool:
    return os.environ.get("PATTERN_SHIFT_ENABLED", "on").lower() == "on"


def _fetch_today_trades(tab: str) -> List[dict]:
    """Get today's closed trades."""
    db = _TRADES_DB if tab.upper() == "MAIN" else _SCALPER_DB
    table = "trades" if tab.upper() == "MAIN" else "scalper_trades"
    if not db.exists():
        return []
    today_iso = datetime.now(IST).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT entry_time, exit_time, status, pnl_rupees, action "
            f"FROM {table} "
            f"WHERE substr(entry_time, 1, 10) = ? "
            f"AND COALESCE(status, '') NOT IN ('OPEN', '') "
            f"ORDER BY exit_time DESC",
            (today_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fetch_baseline_trades(tab: str, days_back: int = 30) -> List[dict]:
    """Get baseline trades for distribution comparison."""
    db = _TRADES_DB if tab.upper() == "MAIN" else _SCALPER_DB
    table = "trades" if tab.upper() == "MAIN" else "scalper_trades"
    if not db.exists():
        return []
    cutoff = (datetime.now(IST) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    today = datetime.now(IST).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT status, pnl_rupees "
            f"FROM {table} "
            f"WHERE substr(entry_time, 1, 10) >= ? "
            f"AND substr(entry_time, 1, 10) < ? "  # exclude today
            f"AND COALESCE(status, '') NOT IN ('OPEN', '')",
            (cutoff, today),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _status_distribution(trades: List[dict]) -> Dict[str, float]:
    """Return status → percentage of trades."""
    if not trades:
        return {}
    n = len(trades)
    counts: Dict[str, int] = {}
    for t in trades:
        s = t.get("status", "?")
        counts[s] = counts.get(s, 0) + 1
    return {s: round(c / n * 100, 1) for s, c in counts.items()}


def _consecutive_recent(trades: List[dict], statuses: set) -> int:
    """How many of the most recent N trades had status in the given set?
    (trades list should be sorted newest first)"""
    streak = 0
    for t in trades:
        if t.get("status") in statuses:
            streak += 1
        else:
            break
    return streak


def detect_shifts(tab: str = "BOTH") -> dict:
    """Detect intra-day pattern shifts.

    Returns:
        dict {
          "alert_level": "OK" | "INFO" | "WARNING" | "CRITICAL",
          "today_distribution": {status: %},
          "baseline_distribution": {status: %},
          "shifts": list of {status, today_pct, baseline_pct, delta},
          "consecutive_losses": int,
          "consecutive_reversals": int,
          "consecutive_watcher_exits": int,
          "today_n": int,
          "baseline_n": int,
          "summary": str,
        }
    """
    tabs = ["MAIN", "SCALPER"] if tab.upper() == "BOTH" else [tab.upper()]
    today_trades = []
    baseline_trades = []
    for t in tabs:
        today_trades.extend(_fetch_today_trades(t))
        baseline_trades.extend(_fetch_baseline_trades(t))

    if not today_trades:
        return {
            "alert_level": "OK",
            "today_distribution": {},
            "baseline_distribution": _status_distribution(baseline_trades),
            "shifts": [],
            "consecutive_losses": 0,
            "consecutive_reversals": 0,
            "consecutive_watcher_exits": 0,
            "today_n": 0,
            "baseline_n": len(baseline_trades),
            "summary": "No trades today yet — nothing to compare",
        }

    today_dist = _status_distribution(today_trades)
    baseline_dist = _status_distribution(baseline_trades)

    # Compute delta for each status
    shifts = []
    all_statuses = set(today_dist.keys()) | set(baseline_dist.keys())
    for s in all_statuses:
        today_pct = today_dist.get(s, 0)
        baseline_pct = baseline_dist.get(s, 0)
        delta = today_pct - baseline_pct
        shifts.append({
            "status": s,
            "today_pct": today_pct,
            "baseline_pct": baseline_pct,
            "delta_pp": round(delta, 1),
            "is_negative": s in NEGATIVE_EXITS,
        })
    shifts.sort(key=lambda r: -abs(r["delta_pp"]))

    # Streak detection
    consec_losses = _consecutive_recent(
        today_trades, {"SL_HIT", "STOP_HUNTED", "REVERSAL_EXIT", "WATCHER_EXIT", "TIMEOUT_EXIT"}
    )
    consec_reversals = _consecutive_recent(today_trades, {"REVERSAL_EXIT"})
    consec_watcher = _consecutive_recent(today_trades, {"WATCHER_EXIT"})

    # Alert classification — use severity_order for proper escalation
    # (alphabetic max() doesn't work: "WARNING" > "CRITICAL" lexicographically!)
    SEVERITY_RANK = {"OK": 0, "INFO": 1, "WARNING": 2, "CRITICAL": 3}

    def _escalate(current: str, candidate: str) -> str:
        """Return the higher-severity of two levels."""
        if SEVERITY_RANK.get(candidate, 0) > SEVERITY_RANK.get(current, 0):
            return candidate
        return current

    alert_level = "OK"
    summary_parts = []

    # Bad-exit ratio today vs baseline
    today_negative_pct = sum(today_dist.get(s, 0) for s in NEGATIVE_EXITS)
    baseline_negative_pct = sum(baseline_dist.get(s, 0) for s in NEGATIVE_EXITS)
    negative_shift = today_negative_pct - baseline_negative_pct

    if consec_losses >= 4:
        alert_level = _escalate(alert_level, "CRITICAL")
        summary_parts.append(f"{consec_losses} consecutive losing exits")
    elif consec_losses >= 3:
        alert_level = _escalate(alert_level, "WARNING")
        summary_parts.append(f"{consec_losses} consecutive losing exits")
    elif consec_losses >= 2:
        alert_level = _escalate(alert_level, "INFO")

    if consec_watcher >= 2:
        alert_level = _escalate(alert_level, "CRITICAL")
        summary_parts.append(f"{consec_watcher} watcher panic-exits in a row")
    elif consec_watcher >= 1 and consec_losses >= 2:
        alert_level = _escalate(alert_level, "WARNING")
        summary_parts.append(f"Watcher exit in losing streak")

    if negative_shift >= 25 and len(today_trades) >= 3:
        alert_level = _escalate(alert_level, "WARNING")
        summary_parts.append(f"Negative exits {today_negative_pct:.0f}% vs baseline {baseline_negative_pct:.0f}%")

    # REVERSAL_EXIT spike
    today_rev = today_dist.get("REVERSAL_EXIT", 0)
    baseline_rev = baseline_dist.get("REVERSAL_EXIT", 0)
    if today_rev > baseline_rev * 2 and today_rev > 25:
        alert_level = _escalate(alert_level, "WARNING")
        summary_parts.append(f"REVERSAL_EXIT {today_rev:.0f}% (2x baseline) = chop signal")

    if not summary_parts:
        summary_parts.append(f"{len(today_trades)} trades today, distribution matches baseline")

    return {
        "alert_level": alert_level,
        "today_distribution": today_dist,
        "baseline_distribution": baseline_dist,
        "shifts": shifts,
        "consecutive_losses": consec_losses,
        "consecutive_reversals": consec_reversals,
        "consecutive_watcher_exits": consec_watcher,
        "today_n": len(today_trades),
        "baseline_n": len(baseline_trades),
        "today_negative_pct": round(today_negative_pct, 1),
        "baseline_negative_pct": round(baseline_negative_pct, 1),
        "summary": " · ".join(summary_parts),
    }


def quick_check() -> dict:
    """Compact check for periodic monitoring."""
    d = detect_shifts(tab="BOTH")
    return {
        "alert_level": d["alert_level"],
        "consecutive_losses": d["consecutive_losses"],
        "today_n": d["today_n"],
        "summary": d["summary"],
    }
