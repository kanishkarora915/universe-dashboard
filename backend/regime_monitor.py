"""
regime_monitor — early-warning system for "today is different".

WHY THIS MODULE EXISTS

User insight 2026-05-21: April peak (+₹487k) → May collapse (-₹240k swing).
Same engines, same WR, opposite P&L. System had ZERO awareness that
market regime had changed.

This module tracks 10 KPIs in rolling windows (7-day current vs 30-day
baseline) and fires alerts when multiple metrics deviate >2σ.

WHAT IT TRACKS

  1. Win rate (% wins / total)
  2. Average WIN size (₹)
  3. Average LOSS size (₹)
  4. T1_HIT rate (% of trades that hit T1)
  5. REVERSAL_EXIT rate (% exits via reversal — chop signal)
  6. WATCHER_EXIT count (panic exits — regime broken signal)
  7. Hold time median (winning trades)
  8. Net P&L per day
  9. Trade frequency (trades/day)
  10. SL hit rate (% trades that hit stop loss)

WHEN IT FIRES

  • 1 metric deviates >2σ          → INFO (worth watching)
  • 2-3 metrics deviate >2σ        → WARNING (suggest reduce size)
  • 4+ metrics deviate >2σ         → CRITICAL (regime shift confirmed)

OUTPUT

  • API endpoint /api/regime-monitor → JSON snapshot
  • Auto-runs periodically (every 15 min during market hours)
  • Sends throttled Telegram alerts on threshold breaches

THIS IS PURE READ — does not change trade behavior.
Operator (you) decides what to do with the alerts.
"""

from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median, stdev
from typing import Dict, List, Optional, Tuple

import pytz

IST = pytz.timezone("Asia/Kolkata")

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_TRADES_DB = _DATA_DIR / "trades.db"
if not _TRADES_DB.exists():
    _TRADES_DB = Path(__file__).parent / "trades.db"
_SCALPER_DB = _DATA_DIR / "scalper_trades.db"
if not _SCALPER_DB.exists():
    _SCALPER_DB = Path(__file__).parent / "scalper_trades.db"


# Significant deviation threshold (in standard deviations)
SIGMA_THRESHOLD = 2.0

# Severity escalation
SEVERITY_INFO_COUNT = 1      # 1 metric off → INFO
SEVERITY_WARN_COUNT = 2      # 2-3 → WARNING
SEVERITY_CRITICAL_COUNT = 4  # 4+ → CRITICAL


# ── Env flags ──────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Master switch — default ON since it's read-only monitoring."""
    return os.environ.get("REGIME_MONITOR_ENABLED", "on").lower() == "on"


def is_telegram_enabled() -> bool:
    return os.environ.get("REGIME_MONITOR_TELEGRAM", "on").lower() == "on"


# ── Data fetching ──────────────────────────────────────────────────────

def _fetch_closed_trades(tab: str, days_back: int) -> List[dict]:
    """Get closed trades from N days ago to now."""
    db = _TRADES_DB if tab.upper() == "MAIN" else _SCALPER_DB
    table = "trades" if tab.upper() == "MAIN" else "scalper_trades"

    if not db.exists():
        return []

    now = datetime.now(IST)
    cutoff = now - timedelta(days=days_back)
    cutoff_iso = cutoff.strftime("%Y-%m-%d")

    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT entry_time, exit_time, status, pnl_rupees,
                   entry_price, exit_price, peak_ltp, action, idx
            FROM {table}
            WHERE substr(entry_time, 1, 10) >= ?
              AND COALESCE(status, '') NOT IN ('OPEN', '')
              AND pnl_rupees IS NOT NULL
            """,
            (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── KPI computation ───────────────────────────────────────────────────

def _hold_minutes(t: dict) -> Optional[float]:
    """Compute hold time in minutes from entry_time and exit_time."""
    try:
        e = datetime.fromisoformat(t["entry_time"].replace("Z", "+00:00"))
        x = datetime.fromisoformat(t["exit_time"].replace("Z", "+00:00"))
        if e.tzinfo is None and x.tzinfo is not None:
            x = x.replace(tzinfo=None)
        elif x.tzinfo is None and e.tzinfo is not None:
            e = e.replace(tzinfo=None)
        secs = (x - e).total_seconds()
        if secs < 0:
            return None
        return secs / 60
    except Exception:
        return None


def compute_kpis(trades: List[dict]) -> dict:
    """Compute the 10 KPI values for a set of trades."""
    if not trades:
        return {}

    wins = [t for t in trades if (t.get("pnl_rupees") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl_rupees") or 0) <= 0]
    n = len(trades)

    # Trade days
    unique_days = set()
    for t in trades:
        try:
            unique_days.add(t["entry_time"][:10])
        except Exception:
            pass
    n_days = max(1, len(unique_days))

    win_amounts = [(t.get("pnl_rupees") or 0) for t in wins]
    loss_amounts = [(t.get("pnl_rupees") or 0) for t in losses]

    win_holds = [_hold_minutes(t) for t in wins]
    win_holds = [h for h in win_holds if h is not None and h > 0]

    status_counts = {}
    for t in trades:
        s = t.get("status", "?")
        status_counts[s] = status_counts.get(s, 0) + 1

    total_pnl = sum((t.get("pnl_rupees") or 0) for t in trades)

    return {
        "wr_pct": round((len(wins) / n) * 100, 2) if n > 0 else 0,
        "avg_win": round(mean(win_amounts), 2) if win_amounts else 0,
        "avg_loss": round(mean(loss_amounts), 2) if loss_amounts else 0,
        "t1_hit_rate": round(status_counts.get("T1_HIT", 0) / n * 100, 2),
        "reversal_exit_rate": round(status_counts.get("REVERSAL_EXIT", 0) / n * 100, 2),
        "watcher_exit_count": status_counts.get("WATCHER_EXIT", 0),
        "median_hold_min_wins": round(median(win_holds), 2) if win_holds else 0,
        "pnl_per_day": round(total_pnl / n_days, 2),
        "trades_per_day": round(n / n_days, 2),
        "sl_hit_rate": round(status_counts.get("SL_HIT", 0) / n * 100, 2),
        "_n_trades": n,
        "_n_days": n_days,
        "_total_pnl": round(total_pnl, 2),
    }


# ── Baseline distribution computation ─────────────────────────────────

def _compute_baseline_distribution(
    trades_baseline: List[dict],
    window_days: int = 7,
) -> Dict[str, Tuple[float, float]]:
    """For each KPI, compute (mean, stddev) of rolling 7-day windows
    over the baseline period. This gives us the historical variation
    of each metric so we can compute z-scores.

    Returns:
        {kpi_name: (mean, stddev), ...}
    """
    if not trades_baseline:
        return {}

    # Group trades by entry date
    by_date = {}
    for t in trades_baseline:
        try:
            d = t["entry_time"][:10]
            by_date.setdefault(d, []).append(t)
        except Exception:
            continue

    dates = sorted(by_date.keys())
    if len(dates) < window_days * 2:
        # Not enough data for meaningful baseline
        return {}

    # Compute KPIs for each rolling window of `window_days`
    window_kpis: Dict[str, List[float]] = {}
    for i in range(len(dates) - window_days + 1):
        window_dates = dates[i:i + window_days]
        window_trades = []
        for d in window_dates:
            window_trades.extend(by_date.get(d, []))
        if not window_trades:
            continue
        kpis = compute_kpis(window_trades)
        for k, v in kpis.items():
            if k.startswith("_"):  # skip private fields
                continue
            window_kpis.setdefault(k, []).append(v)

    # Now compute mean and stddev for each KPI
    result = {}
    for k, values in window_kpis.items():
        if len(values) < 3:
            continue
        try:
            result[k] = (mean(values), stdev(values) or 0.01)  # avoid /0
        except Exception:
            pass

    return result


def _compute_z_scores(current: dict, baseline: Dict[str, Tuple[float, float]]) -> dict:
    """Compute z-score for each KPI: how many σ off baseline."""
    z = {}
    for k, val in current.items():
        if k.startswith("_") or k not in baseline:
            continue
        mu, sigma = baseline[k]
        z[k] = round((val - mu) / sigma, 2)
    return z


# ── Severity assessment ───────────────────────────────────────────────

# Direction interpretation: which way is BAD for each KPI?
# +1 means higher value is GOOD, -1 means higher value is BAD
KPI_DIRECTION = {
    "wr_pct": +1,                  # higher WR = good
    "avg_win": +1,                  # bigger wins = good
    "avg_loss": +1,                 # less negative = good (so positive delta)
    "t1_hit_rate": +1,              # more T1 hits = good
    "reversal_exit_rate": -1,       # MORE reversals = BAD (chop)
    "watcher_exit_count": -1,       # more watcher exits = BAD
    "median_hold_min_wins": +1,     # longer holds = good (trade had room)
    "pnl_per_day": +1,              # higher P&L = good
    "trades_per_day": 0,            # neutral (more isn't necessarily good)
    "sl_hit_rate": -1,              # more SL hits = bad
}


def _interpret_deviations(z_scores: dict) -> List[dict]:
    """For each significantly deviated metric, classify as good or bad."""
    deviations = []
    for kpi, z in z_scores.items():
        if abs(z) < SIGMA_THRESHOLD:
            continue
        direction = KPI_DIRECTION.get(kpi, 0)
        bad = (z < 0 and direction > 0) or (z > 0 and direction < 0)
        deviations.append({
            "kpi": kpi,
            "z_score": z,
            "direction": direction,
            "is_bad": bad,
            "magnitude": abs(z),
        })
    deviations.sort(key=lambda r: -r["magnitude"])
    return deviations


def _classify_severity(deviations: List[dict]) -> str:
    """How many bad deviations? → severity level."""
    bad_count = sum(1 for d in deviations if d["is_bad"])
    if bad_count >= SEVERITY_CRITICAL_COUNT:
        return "CRITICAL"
    if bad_count >= SEVERITY_WARN_COUNT:
        return "WARNING"
    if bad_count >= SEVERITY_INFO_COUNT:
        return "INFO"
    return "OK"


# ── Main public API ───────────────────────────────────────────────────

def assess(tab: str = "BOTH", current_days: int = 7, baseline_days: int = 30) -> dict:
    """Full regime assessment for the given tab.

    Args:
        tab: "MAIN" / "SCALPER" / "BOTH"
        current_days: window for "today" metrics (default 7)
        baseline_days: window for historical baseline (default 30)

    Returns:
        dict {
          "tab": str,
          "severity": "OK" | "INFO" | "WARNING" | "CRITICAL",
          "summary": str,
          "current_window": dict (KPIs for last 7 days),
          "baseline": dict (mean+std for last 30 days),
          "z_scores": dict (deviations in sigma),
          "deviations": list (sorted, bad ones first),
          "n_trades_current": int,
          "n_trades_baseline": int,
          "recommendation": str,
        }
    """
    tabs = ["MAIN", "SCALPER"] if tab.upper() == "BOTH" else [tab.upper()]
    all_current = []
    all_baseline = []
    for t in tabs:
        all_current.extend(_fetch_closed_trades(t, current_days))
        all_baseline.extend(_fetch_closed_trades(t, baseline_days))

    if len(all_baseline) < 20:
        return {
            "tab": tab,
            "severity": "OK",
            "summary": "Insufficient baseline data (< 20 trades)",
            "current_window": compute_kpis(all_current),
            "baseline": {},
            "z_scores": {},
            "deviations": [],
            "n_trades_current": len(all_current),
            "n_trades_baseline": len(all_baseline),
            "recommendation": "Need more historical trades for meaningful baseline",
        }

    current_kpis = compute_kpis(all_current)
    baseline_dist = _compute_baseline_distribution(all_baseline, window_days=current_days)
    z_scores = _compute_z_scores(current_kpis, baseline_dist)
    deviations = _interpret_deviations(z_scores)
    severity = _classify_severity(deviations)

    bad_devs = [d for d in deviations if d["is_bad"]]
    bad_list = ", ".join(f"{d['kpi']}({d['z_score']:+.1f}σ)" for d in bad_devs[:3])

    if severity == "CRITICAL":
        summary = f"CRITICAL regime shift detected — {len(bad_devs)} metrics off: {bad_list}"
        recommendation = "REGIME SHIFT CONFIRMED. Reduce position size 70% or pause until regime clears."
    elif severity == "WARNING":
        summary = f"WARNING signs detected — {len(bad_devs)} metrics off: {bad_list}"
        recommendation = "Consider reducing position size 40-50%. Watch next 1-2 days."
    elif severity == "INFO":
        summary = f"One metric drifting: {bad_list}"
        recommendation = "Worth watching, no action needed yet."
    else:
        summary = f"All metrics within normal range ({len(all_current)} trades in last {current_days}d)"
        recommendation = "System operating normally."

    return {
        "tab": tab,
        "severity": severity,
        "summary": summary,
        "current_window": current_kpis,
        "baseline": {k: {"mean": round(v[0], 2), "std": round(v[1], 2)} for k, v in baseline_dist.items()},
        "z_scores": z_scores,
        "deviations": deviations,
        "n_trades_current": len(all_current),
        "n_trades_baseline": len(all_baseline),
        "current_days": current_days,
        "baseline_days": baseline_days,
        "recommendation": recommendation,
    }


def quick_status() -> dict:
    """Compact status for dashboard widget."""
    a = assess(tab="BOTH")
    return {
        "severity": a["severity"],
        "summary": a["summary"],
        "n_bad_metrics": sum(1 for d in a["deviations"] if d["is_bad"]),
        "recommendation": a["recommendation"],
    }
