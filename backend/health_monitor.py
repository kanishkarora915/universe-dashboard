"""
health_monitor — periodic Telegram health + trade activity reports.

Every 30 minutes during market hours (09:15-15:30 IST weekdays),
sends a comprehensive snapshot to your Telegram:
  • Engine + WebSocket status
  • Today's trades (main + scalper) — open, closed, P&L
  • Currently open positions with live P&L

Plus EOD summary at 15:35 IST every weekday.

DESIGN PRINCIPLES
  • Async / fire-and-forget — never blocks anything
  • Never crashes — broad try/except wraps each cycle
  • Silent if Telegram env vars missing
  • Reads from same DBs as the trading engine (no extra writes)
"""

import os
import time
import threading
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List


# Lazy import within functions to avoid hard dependency at module load time


# ── Config ────────────────────────────────────────────────────────────

HEALTH_CHECK_INTERVAL_SEC = 1800  # 30 minutes
MARKET_OPEN_HOUR_MIN = (9, 15)
MARKET_CLOSE_HOUR_MIN = (15, 30)
EOD_SUMMARY_HOUR_MIN = (15, 35)   # 5 min after market close


def _ist_now() -> datetime:
    """IST timestamp (timezone-aware fallback to naive if pytz missing)."""
    try:
        import pytz
        return datetime.now(pytz.timezone("Asia/Kolkata"))
    except Exception:
        return datetime.now()


def _is_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    a = MARKET_OPEN_HOUR_MIN[0] * 60 + MARKET_OPEN_HOUR_MIN[1]
    b = MARKET_CLOSE_HOUR_MIN[0] * 60 + MARKET_CLOSE_HOUR_MIN[1]
    return a <= t <= b


def _is_eod_window(now: datetime) -> bool:
    """The 5-min window where we fire the daily summary (15:35-15:40 IST)."""
    if now.weekday() >= 5:
        return False
    return (
        now.hour == EOD_SUMMARY_HOUR_MIN[0]
        and EOD_SUMMARY_HOUR_MIN[1] <= now.minute <= EOD_SUMMARY_HOUR_MIN[1] + 4
    )


# ── DB paths ──────────────────────────────────────────────────────────

def _data_dir() -> Path:
    return Path("/data") if Path("/data").is_dir() else Path(__file__).parent


def _trades_db_path() -> Path:
    return _data_dir() / "trades.db"


def _scalper_db_path() -> Path:
    return _data_dir() / "scalper_trades.db"


# ── Trade DB queries ──────────────────────────────────────────────────

def _safe_open(path: Path) -> Optional[sqlite3.Connection]:
    """Open SQLite read-only. Returns None if file missing."""
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _today_iso_prefix(now: datetime) -> str:
    """YYYY-MM-DD prefix for matching today's rows."""
    return now.strftime("%Y-%m-%d")


def _get_main_trades_today(now: datetime) -> Dict:
    """Today's main-tab trades. Returns dict with open / closed / pnl stats.

    Schema is the standard trades.db (per trade_logger.py):
      id, idx, action (BUY CE / BUY PE), strike, entry_time, exit_time,
      entry_price, exit_price, status (OPEN / closed-status-string), pnl_rupees.
    """
    today = _today_iso_prefix(now)
    out = {
        "open": [],
        "closed": [],
        "wins": 0,
        "losses": 0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_count": 0,
    }
    conn = _safe_open(_trades_db_path())
    if conn is None:
        return out
    try:
        rows = conn.execute("""
            SELECT id, idx, action, strike, entry_time, exit_time,
                   entry_price, exit_price, current_ltp, status,
                   pnl_rupees, peak_ltp, source
            FROM trades
            WHERE entry_time LIKE ?
            ORDER BY entry_time ASC
        """, (f"{today}%",)).fetchall()

        for r in rows:
            d = dict(r)
            out["total_count"] += 1
            pnl = d.get("pnl_rupees") or 0.0

            if d.get("status") == "OPEN":
                # Unrealized P&L = (current_ltp - entry_price) * qty
                # We don't have qty here, so use the stored pnl_rupees if any
                # OR compute (ltp - entry) directly if engine populated it
                ltp = d.get("current_ltp") or 0
                entry = d.get("entry_price") or 0
                # If pnl_rupees is set on open trade, trust it
                unrealized = pnl if pnl else 0
                out["unrealized_pnl"] += unrealized
                out["open"].append({
                    "id": d["id"],
                    "symbol": f"{d['idx']} {d['strike']} {d['action'][-2:]}",
                    "entry_price": entry,
                    "current_ltp": ltp,
                    "unrealized_pnl": unrealized,
                    "source": d.get("source", "main"),
                })
            else:
                # Closed trade
                out["closed"].append({
                    "id": d["id"],
                    "symbol": f"{d['idx']} {d['strike']} {d['action'][-2:]}",
                    "entry": d.get("entry_price"),
                    "exit": d.get("exit_price"),
                    "pnl": pnl,
                    "status": d.get("status"),
                })
                out["realized_pnl"] += pnl
                if pnl > 0:
                    out["wins"] += 1
                elif pnl < 0:
                    out["losses"] += 1
    except sqlite3.OperationalError as e:
        # Schema mismatch or missing column — degrade gracefully
        out["_error"] = str(e)
    finally:
        conn.close()
    return out


def _get_scalper_trades_today(now: datetime) -> Dict:
    """Today's scalper trades, mirrored shape of main trades."""
    today = _today_iso_prefix(now)
    out = {
        "open": [],
        "closed": [],
        "wins": 0,
        "losses": 0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_count": 0,
    }
    conn = _safe_open(_scalper_db_path())
    if conn is None:
        return out
    try:
        # Try the common scalper schema; tolerant of column drift
        rows = conn.execute("""
            SELECT * FROM scalper_trades
            WHERE entry_time LIKE ?
            ORDER BY entry_time ASC
        """, (f"{today}%",)).fetchall()

        for r in rows:
            d = dict(r)
            out["total_count"] += 1
            pnl = d.get("pnl_rupees") or d.get("pnl") or 0.0
            status = d.get("status", "")
            symbol = (
                d.get("symbol")
                or f"{d.get('idx','?')} {d.get('strike','?')}{d.get('side','')}"
            )

            if status == "OPEN" or status == "":
                ltp = d.get("current_ltp") or d.get("ltp") or 0
                entry = d.get("entry_price") or 0
                out["unrealized_pnl"] += pnl
                out["open"].append({
                    "symbol": symbol,
                    "entry_price": entry,
                    "current_ltp": ltp,
                    "unrealized_pnl": pnl,
                })
            else:
                out["closed"].append({
                    "symbol": symbol,
                    "entry": d.get("entry_price"),
                    "exit": d.get("exit_price"),
                    "pnl": pnl,
                    "status": status,
                })
                out["realized_pnl"] += pnl
                if pnl > 0:
                    out["wins"] += 1
                elif pnl < 0:
                    out["losses"] += 1
    except sqlite3.OperationalError as e:
        out["_error"] = str(e)
    finally:
        conn.close()
    return out


# ── Engine state snapshot ─────────────────────────────────────────────

def _get_engine_state(engine_ref) -> Dict:
    """Read engine state via the passed-in engine instance (or None if dead)."""
    out = {
        "running": False,
        "ticker_alive": False,
        "last_tick_age_sec": None,
        "is_stale": True,
    }
    if engine_ref is None:
        return out
    try:
        out["running"] = bool(getattr(engine_ref, "running", False))
        out["ticker_alive"] = hasattr(engine_ref, "ticker") and engine_ref.ticker is not None
        last_tick = getattr(engine_ref, "_last_tick_time", 0)
        if last_tick > 0:
            age = time.time() - last_tick
            out["last_tick_age_sec"] = round(age, 1)
            out["is_stale"] = age > 60
    except Exception as e:
        out["_error"] = str(e)
    return out


# ── Message formatting ────────────────────────────────────────────────

def _format_inr(amount: float) -> str:
    """Indian-style number formatting with sign."""
    sign = "+" if amount >= 0 else "-"
    abs_amt = abs(amount)
    if abs_amt >= 100000:
        return f"{sign}₹{abs_amt/100000:.2f}L"
    return f"{sign}₹{abs_amt:,.0f}"


def build_health_report(engine_ref, now: Optional[datetime] = None) -> str:
    """Build the periodic Telegram message (Markdown)."""
    if now is None:
        now = _ist_now()

    engine = _get_engine_state(engine_ref)
    main_trades = _get_main_trades_today(now)
    scalper_trades = _get_scalper_trades_today(now)

    # ── Engine status ──
    if engine["running"] and engine["ticker_alive"] and not engine["is_stale"]:
        engine_emoji = "✅"
        engine_status = "Healthy"
    elif engine["running"] and engine["is_stale"]:
        engine_emoji = "⚠️"
        engine_status = "Running but ticks stale"
    elif engine["running"]:
        engine_emoji = "🟡"
        engine_status = "Running, ticker connecting"
    else:
        engine_emoji = "🔴"
        engine_status = "DOWN"

    tick_age_str = (
        f"{engine['last_tick_age_sec']:.0f}s"
        if engine["last_tick_age_sec"] is not None
        else "—"
    )

    # ── Trades summary ──
    main_open = len(main_trades["open"])
    main_closed = len(main_trades["closed"])
    main_realized = main_trades["realized_pnl"]
    main_unrealized = main_trades["unrealized_pnl"]
    main_wl = f"{main_trades['wins']}W / {main_trades['losses']}L"

    scalp_open = len(scalper_trades["open"])
    scalp_closed = len(scalper_trades["closed"])
    scalp_realized = scalper_trades["realized_pnl"]
    scalp_unrealized = scalper_trades["unrealized_pnl"]
    scalp_wl = f"{scalper_trades['wins']}W / {scalper_trades['losses']}L"

    total_realized = main_realized + scalp_realized
    total_unrealized = main_unrealized + scalp_unrealized
    total_pnl = total_realized + total_unrealized

    # ── Build message ──
    lines = []
    lines.append(f"🩺 *System Health* — {now.strftime('%H:%M IST')}")
    lines.append("")
    lines.append(f"{engine_emoji} *Engine*: {engine_status}")
    lines.append(f"  • Tick age: `{tick_age_str}`")
    lines.append("")
    lines.append("📊 *Trades Today*")
    lines.append("")
    lines.append(f"*Main (PnL Tab)*")
    lines.append(f"  Open: `{main_open}` · Closed: `{main_closed}` ({main_wl})")
    lines.append(f"  Realized: `{_format_inr(main_realized)}`")
    if main_open > 0:
        lines.append(f"  Unrealized: `{_format_inr(main_unrealized)}`")
    lines.append("")
    lines.append(f"*Scalper*")
    lines.append(f"  Open: `{scalp_open}` · Closed: `{scalp_closed}` ({scalp_wl})")
    lines.append(f"  Realized: `{_format_inr(scalp_realized)}`")
    if scalp_open > 0:
        lines.append(f"  Unrealized: `{_format_inr(scalp_unrealized)}`")

    # ── Currently open positions (max 5 shown) ──
    all_open = (
        [(*[(p["symbol"], p.get("entry_price"), p.get("current_ltp"),
              p.get("unrealized_pnl"), "main")][0],) for p in main_trades["open"]]
        + [(*[(p["symbol"], p.get("entry_price"), p.get("current_ltp"),
              p.get("unrealized_pnl"), "scalper")][0],) for p in scalper_trades["open"]]
    )
    if all_open:
        lines.append("")
        lines.append("*Currently Open*")
        for sym, entry, ltp, pnl, src in all_open[:5]:
            entry_str = f"₹{entry}" if entry else "?"
            ltp_str = f"₹{ltp}" if ltp else "?"
            pnl_str = _format_inr(pnl) if pnl else "—"
            tag = "📋" if src == "main" else "⚡"
            lines.append(f"  {tag} `{sym}` {entry_str} → {ltp_str} ({pnl_str})")
        if len(all_open) > 5:
            lines.append(f"  _...and {len(all_open) - 5} more_")

    # ── Net P&L ──
    lines.append("")
    lines.append(f"*Net Day P&L: `{_format_inr(total_pnl)}`*")
    if main_open or scalp_open:
        lines.append(
            f"  _Realized: {_format_inr(total_realized)} · "
            f"Unrealized: {_format_inr(total_unrealized)}_"
        )

    return "\n".join(lines)


def build_eod_summary(engine_ref) -> str:
    """End-of-day summary — closed trades only, full breakdown."""
    now = _ist_now()
    main = _get_main_trades_today(now)
    scalper = _get_scalper_trades_today(now)

    total_pnl = main["realized_pnl"] + scalper["realized_pnl"]
    total_trades = len(main["closed"]) + len(scalper["closed"])
    total_wins = main["wins"] + scalper["wins"]
    total_losses = main["losses"] + scalper["losses"]
    win_rate = (total_wins / total_trades * 100) if total_trades else 0

    lines = []
    emoji = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
    lines.append(f"{emoji} *EOD Summary* — {now.strftime('%a, %d %b %Y')}")
    lines.append("")
    lines.append(f"*Net P&L: `{_format_inr(total_pnl)}`*")
    lines.append(f"Trades: `{total_trades}` ({total_wins}W / {total_losses}L · {win_rate:.0f}% wr)")
    lines.append("")

    if main["closed"]:
        lines.append(f"*Main Tab* — `{_format_inr(main['realized_pnl'])}`")
        lines.append(f"  {len(main['closed'])} trades · {main['wins']}W / {main['losses']}L")
    if scalper["closed"]:
        lines.append(f"*Scalper* — `{_format_inr(scalper['realized_pnl'])}`")
        lines.append(f"  {len(scalper['closed'])} trades · {scalper['wins']}W / {scalper['losses']}L")

    if total_trades == 0:
        lines.append("_No trades today._")

    return "\n".join(lines)


# ── Background monitor loop ───────────────────────────────────────────

def run_monitor(engine_getter):
    """Background-thread entry point.

    Args:
        engine_getter: zero-arg callable returning the current MarketEngine
                       (or None). We use a callable so we always see the
                       latest engine reference after restarts.
    """
    try:
        import telegram_alerts
    except Exception as e:
        print(f"[HEALTH-MON] telegram_alerts import failed: {e} — disabled")
        return

    if not telegram_alerts.is_enabled():
        print("[HEALTH-MON] Telegram not configured — monitor will run but no alerts sent")

    print(f"[HEALTH-MON] Started — checks every {HEALTH_CHECK_INTERVAL_SEC // 60} min during market")

    last_full_report_ts = 0
    last_eod_date = None  # track per-day EOD send (don't double-send)

    while True:
        try:
            now = _ist_now()

            # ── EOD summary (once per day at 15:35 IST) ──
            today_str = now.strftime("%Y-%m-%d")
            if _is_eod_window(now) and last_eod_date != today_str:
                last_eod_date = today_str
                try:
                    engine_ref = engine_getter()
                    msg = build_eod_summary(engine_ref)
                    telegram_alerts.send(msg, key="health_eod")
                    print(f"[HEALTH-MON] EOD summary sent for {today_str}")
                except Exception as e:
                    print(f"[HEALTH-MON] EOD send failed: {e}")
                time.sleep(60)
                continue

            # ── 30-min health pings during market hours ──
            if _is_market_hours(now):
                now_ts = time.time()
                if (now_ts - last_full_report_ts) >= HEALTH_CHECK_INTERVAL_SEC:
                    last_full_report_ts = now_ts
                    try:
                        engine_ref = engine_getter()
                        msg = build_health_report(engine_ref, now)
                        telegram_alerts.send(msg, key=f"health_{now.strftime('%H%M')}")
                        print(f"[HEALTH-MON] Health report sent at {now.strftime('%H:%M')}")
                    except Exception as e:
                        print(f"[HEALTH-MON] Health report send failed: {e}")
                time.sleep(60)
            else:
                # Outside market hours — sleep longer
                time.sleep(300)
        except Exception as e:
            print(f"[HEALTH-MON] Outer loop error: {e}")
            time.sleep(60)
