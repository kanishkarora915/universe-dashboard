"""
daily_diagnostic — automated EOD report explaining today's trading.

WHY THIS MODULE EXISTS

Currently EOD review is manual. Each evening you'd have to:
  • Check today's P&L
  • Count wins/losses
  • Look at status distribution
  • Compare to recent days
  • Identify what worked/didn't

This module does all of that automatically and produces a plain-English
report. Sent via Telegram at 15:35 IST (5 min after market close).

OUTPUT

  Telegram message + API endpoint `/api/daily-diagnostic`

  Sample output:
    ━━ EOD REPORT — 23 May 2026 (Fri) ━━
    Total trades: 8 | Wins 5 | Losses 3 | WR 63%
    Net P&L: +₹12,340 (above 7-day avg ₹+5,200)

    BEST: 11:30 BANKNIFTY CE +₹8,200 (T1 hit)
    WORST: 13:15 NIFTY CE -₹6,100 (STOP_HUNTED)

    What worked:
      • TRAIL_EXIT caught 2 winners
      • Smart SL prevented 1 sweep

    What didn't:
      • 1 STOP_HUNTED (₹6.1k) — still happens
      • 1 WATCHER_EXIT (₹3.2k) — chop signal

    Verdict: Normal trading day. Continue tomorrow.
"""

from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import List, Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_TRADES_DB = _DATA_DIR / "trades.db"
if not _TRADES_DB.exists():
    _TRADES_DB = Path(__file__).parent / "trades.db"
_SCALPER_DB = _DATA_DIR / "scalper_trades.db"
if not _SCALPER_DB.exists():
    _SCALPER_DB = Path(__file__).parent / "scalper_trades.db"


def is_enabled() -> bool:
    return os.environ.get("DAILY_DIAGNOSTIC_ENABLED", "on").lower() == "on"


def _fmt_inr(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}₹{x:,.0f}"


def _fetch_day(tab: str, date_iso: str) -> List[dict]:
    db = _TRADES_DB if tab.upper() == "MAIN" else _SCALPER_DB
    table = "trades" if tab.upper() == "MAIN" else "scalper_trades"
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT entry_time, exit_time, status, pnl_rupees, "
            f"  entry_price, exit_price, action, idx, strike "
            f"FROM {table} "
            f"WHERE substr(entry_time, 1, 10) = ? "
            f"AND COALESCE(status, '') NOT IN ('OPEN', '')",
            (date_iso,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["_tab"] = tab.upper()
            results.append(d)
        return results
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fetch_recent_days(tab: str, days: int = 7) -> List[List[dict]]:
    """Fetch each day's trades for last N days (excluding today)."""
    db = _TRADES_DB if tab.upper() == "MAIN" else _SCALPER_DB
    table = "trades" if tab.upper() == "MAIN" else "scalper_trades"
    if not db.exists():
        return []
    today = datetime.now(IST).date()
    days_list = []
    for i in range(1, days + 1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        days_list.append(_fetch_day(tab, d))
    return days_list


def generate_report(date_iso: Optional[str] = None) -> dict:
    """Generate diagnostic report for the given date (default: today)."""
    if date_iso is None:
        date_iso = datetime.now(IST).strftime("%Y-%m-%d")

    main_trades = _fetch_day("MAIN", date_iso)
    scalp_trades = _fetch_day("SCALPER", date_iso)
    all_trades = main_trades + scalp_trades

    if not all_trades:
        return {
            "date": date_iso,
            "summary": "No trades today",
            "total": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl": 0,
            "wr_pct": 0,
        }

    wins = [t for t in all_trades if (t.get("pnl_rupees") or 0) > 0]
    losses = [t for t in all_trades if (t.get("pnl_rupees") or 0) <= 0]
    net = sum((t.get("pnl_rupees") or 0) for t in all_trades)

    # Best / worst single trade
    best = max(all_trades, key=lambda t: (t.get("pnl_rupees") or 0))
    worst = min(all_trades, key=lambda t: (t.get("pnl_rupees") or 0))

    # Exit status breakdown
    status_breakdown = {}
    for t in all_trades:
        s = t.get("status", "?")
        status_breakdown.setdefault(s, {"count": 0, "pnl": 0})
        status_breakdown[s]["count"] += 1
        status_breakdown[s]["pnl"] += (t.get("pnl_rupees") or 0)

    # Compare with 7-day baseline (excluding today)
    recent_main = _fetch_recent_days("MAIN", 7)
    recent_scalp = _fetch_recent_days("SCALPER", 7)

    daily_pnls = []
    for day_idx in range(7):
        day_total = sum(
            (t.get("pnl_rupees") or 0)
            for t in (recent_main[day_idx] if day_idx < len(recent_main) else [])
        ) + sum(
            (t.get("pnl_rupees") or 0)
            for t in (recent_scalp[day_idx] if day_idx < len(recent_scalp) else [])
        )
        # Only count days with actual trades
        n_trades_that_day = (
            (len(recent_main[day_idx]) if day_idx < len(recent_main) else 0) +
            (len(recent_scalp[day_idx]) if day_idx < len(recent_scalp) else 0)
        )
        if n_trades_that_day > 0:
            daily_pnls.append(day_total)

    avg_7d = mean(daily_pnls) if daily_pnls else 0
    today_vs_baseline = net - avg_7d

    # Day of week
    day_name = datetime.strptime(date_iso, "%Y-%m-%d").strftime("%A")

    # What worked / didn't (qualitative)
    what_worked = []
    what_didnt = []

    for status, info in status_breakdown.items():
        if status in ("TRAIL_EXIT", "T1_HIT", "T2_HIT"):
            what_worked.append(f"{status}: {info['count']} trades, {_fmt_inr(info['pnl'])}")
        elif status in ("STOP_HUNTED", "WATCHER_EXIT", "REVERSAL_EXIT", "REV_ZONE_SL_HIT"):
            if info["pnl"] < 0:
                what_didnt.append(f"{status}: {info['count']} trades, {_fmt_inr(info['pnl'])}")

    # Overall verdict
    if net >= 15000:
        verdict = "EXCELLENT day. Target hit."
    elif net >= 5000:
        verdict = "Profitable day. Continue tomorrow."
    elif net >= 0:
        verdict = "Near-flat day. Recheck setup quality."
    elif net >= -15000:
        verdict = "Loss day but within limits. Review tomorrow."
    else:
        verdict = "BAD day. Investigate before next session."

    # Best/worst details
    best_str = (
        f"{best.get('entry_time','')[:16]} {best.get('idx','')} "
        f"{best.get('action','')} {_fmt_inr(best.get('pnl_rupees', 0))} "
        f"({best.get('status','?')})"
    )
    worst_str = (
        f"{worst.get('entry_time','')[:16]} {worst.get('idx','')} "
        f"{worst.get('action','')} {_fmt_inr(worst.get('pnl_rupees', 0))} "
        f"({worst.get('status','?')})"
    )

    return {
        "date": date_iso,
        "day_of_week": day_name,
        "total": len(all_trades),
        "wins": len(wins),
        "losses": len(losses),
        "wr_pct": round(len(wins) / len(all_trades) * 100, 1) if all_trades else 0,
        "net_pnl": round(net, 2),
        "avg_win": round(mean([t.get("pnl_rupees", 0) for t in wins]), 2) if wins else 0,
        "avg_loss": round(mean([t.get("pnl_rupees", 0) for t in losses]), 2) if losses else 0,
        "main_count": len(main_trades),
        "scalper_count": len(scalp_trades),
        "main_pnl": round(sum((t.get("pnl_rupees") or 0) for t in main_trades), 2),
        "scalper_pnl": round(sum((t.get("pnl_rupees") or 0) for t in scalp_trades), 2),
        "best_trade": best_str,
        "worst_trade": worst_str,
        "status_breakdown": {
            s: {"count": info["count"], "pnl": round(info["pnl"], 2)}
            for s, info in status_breakdown.items()
        },
        "what_worked": what_worked,
        "what_didnt": what_didnt,
        "vs_7day_baseline": round(today_vs_baseline, 2),
        "baseline_avg": round(avg_7d, 2),
        "verdict": verdict,
    }


def format_telegram(report: dict) -> str:
    """Format report for Telegram (terse, fits in message)."""
    lines = []
    date = report["date"]
    day = report.get("day_of_week", "")
    lines.append(f"━━ EOD REPORT — {date} ({day[:3]}) ━━")
    lines.append("")
    lines.append(f"Trades: {report['total']} | W {report['wins']} L {report['losses']} | WR {report['wr_pct']:.0f}%")
    lines.append(f"Net P&L: {_fmt_inr(report['net_pnl'])} (7d avg {_fmt_inr(report.get('baseline_avg', 0))})")
    lines.append(f"MAIN {_fmt_inr(report.get('main_pnl', 0))} | SCALPER {_fmt_inr(report.get('scalper_pnl', 0))}")
    lines.append("")

    if report.get("best_trade"):
        lines.append(f"🟢 BEST:  {report['best_trade']}")
    if report.get("worst_trade"):
        lines.append(f"🔴 WORST: {report['worst_trade']}")
    lines.append("")

    if report["what_worked"]:
        lines.append("What worked:")
        for w in report["what_worked"][:3]:
            lines.append(f"  ✓ {w}")
    if report["what_didnt"]:
        lines.append("What didn't:")
        for w in report["what_didnt"][:3]:
            lines.append(f"  ✗ {w}")

    lines.append("")
    lines.append(f"Verdict: {report['verdict']}")

    return "\n".join(lines)


def send_eod_telegram(date_iso: Optional[str] = None) -> dict:
    """Generate report and send via Telegram. Returns the report dict."""
    report = generate_report(date_iso)
    try:
        import telegram_alerts as _tg
        if _tg.is_enabled():
            msg = format_telegram(report)
            _tg.send(msg, key=f"eod_report_{report['date']}")
            report["telegram_sent"] = True
        else:
            report["telegram_sent"] = False
    except Exception as e:
        report["telegram_error"] = str(e)
        report["telegram_sent"] = False
    return report
