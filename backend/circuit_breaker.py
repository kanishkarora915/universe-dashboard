"""
circuit_breaker — daily loss cap + consecutive loss pause.

WHY THIS MODULE EXISTS

60-day audit (2026-05-19):
  May 14: -₹81,078 single session (1W/7L disaster)
  4-session scalper collapse: -₹119,046
  No hard floor existed → system kept firing after 3-4 losses

Two breakers, both env-gated:

  1. DAILY LOSS CAP — when today's cumulative P&L hits limit, refuse
     new entries for rest of day. Resets at 00:01 IST next day.

  2. CONSECUTIVE LOSS PAUSE — after N losses in a row, cool-off period.
     Regime likely changed, signals stale.

WHAT IT DOES NOT DO
  • Does NOT close open positions
  • Does NOT modify exit logic
  • Only blocks NEW entries when triggered

ENV FLAGS

  DAILY_LOSS_CAP_ENABLED=on        master switch (default off)
  DAILY_LOSS_LIMIT_MAIN=15000      ₹ limit for main tab (default 15000)
  DAILY_LOSS_LIMIT_SCALPER=15000   ₹ limit for scalper tab (default 15000)
  CONSECUTIVE_LOSS_LIMIT=3         streak length before pause (default 3)
  COOL_OFF_MINUTES=30              minutes to pause after streak (default 30)

ROLLBACK: flip env vars to off → restart container. ~30s.
"""

from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_TRADES_DB = _DATA_DIR / "trades.db"
if not _TRADES_DB.exists():
    _TRADES_DB = Path(__file__).parent / "trades.db"
_SCALPER_DB = _DATA_DIR / "scalper_trades.db"
if not _SCALPER_DB.exists():
    _SCALPER_DB = Path(__file__).parent / "scalper_trades.db"


# ── Env flags ──────────────────────────────────────────────────────────

def is_enabled() -> bool:
    # DEFAULT CHANGED 2026-06-04: off → on
    # Infrastructure audit found circuit breakers disabled despite the
    # configured -₹15k daily limit. Without enforcement, a runaway loss
    # day (like Apr 28 = -₹146k) goes unprotected. Enabling default-on
    # gives loss-cap protection without requiring env-flag flips.
    # Override: DAILY_LOSS_CAP_ENABLED=off to disable.
    return os.environ.get("DAILY_LOSS_CAP_ENABLED", "on").lower() != "off"


def is_shadow_enabled() -> bool:
    return os.environ.get("CIRCUIT_BREAKER_SHADOW", "on").lower() == "on"


def daily_loss_limit(tab: str) -> float:
    """Per-tab daily loss limit (negative number — represents max loss)."""
    if tab.upper() == "MAIN":
        v = os.environ.get("DAILY_LOSS_LIMIT_MAIN", "15000")
    else:
        v = os.environ.get("DAILY_LOSS_LIMIT_SCALPER", "15000")
    try:
        return float(v)
    except ValueError:
        return 15000


def consecutive_loss_limit() -> int:
    try:
        return int(os.environ.get("CONSECUTIVE_LOSS_LIMIT", "3"))
    except ValueError:
        return 3


def cool_off_minutes() -> int:
    try:
        return int(os.environ.get("COOL_OFF_MINUTES", "30"))
    except ValueError:
        return 30


# ── Today's cumulative P&L ────────────────────────────────────────────

def today_pnl(tab: str) -> float:
    """Sum P&L for closed trades in today's IST date (per tab)."""
    db = _TRADES_DB if tab.upper() == "MAIN" else _SCALPER_DB
    table = "trades" if tab.upper() == "MAIN" else "scalper_trades"

    if not db.exists():
        return 0.0

    today_iso = datetime.now(IST).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(db))
        cur = conn.execute(
            f"SELECT COALESCE(SUM(pnl_rupees), 0) FROM {table} "
            f"WHERE substr(entry_time, 1, 10) = ? "
            f"AND COALESCE(status, '') NOT IN ('OPEN', '')",
            (today_iso,),
        )
        row = cur.fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def recent_consecutive_losses(tab: str) -> int:
    """Count of consecutive losses at the end of today's trades (per tab).
    Returns 0 if last trade was a win or no trades today."""
    db = _TRADES_DB if tab.upper() == "MAIN" else _SCALPER_DB
    table = "trades" if tab.upper() == "MAIN" else "scalper_trades"

    if not db.exists():
        return 0

    today_iso = datetime.now(IST).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(db))
        # Latest closed trades today, newest first
        cur = conn.execute(
            f"SELECT pnl_rupees FROM {table} "
            f"WHERE substr(entry_time, 1, 10) = ? "
            f"AND COALESCE(status, '') NOT IN ('OPEN', '') "
            f"ORDER BY entry_time DESC",
            (today_iso,),
        )
        streak = 0
        for (pnl,) in cur.fetchall():
            if (pnl or 0) >= 0:
                break  # win or breakeven ends streak
            streak += 1
        return streak
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def last_loss_time(tab: str) -> Optional[datetime]:
    """Entry time of the most recent closed loss today (for cool-off calc)."""
    db = _TRADES_DB if tab.upper() == "MAIN" else _SCALPER_DB
    table = "trades" if tab.upper() == "MAIN" else "scalper_trades"

    if not db.exists():
        return None

    now = datetime.now(IST)
    today_iso = now.strftime("%Y-%m-%d")
    # Cutoff: exit_time must be in the past (avoids brittle ordering when
    # clock skew or test data places exit_time in the future).
    now_iso = now.isoformat()
    try:
        conn = sqlite3.connect(str(db))
        cur = conn.execute(
            f"SELECT exit_time FROM {table} "
            f"WHERE substr(entry_time, 1, 10) = ? "
            f"AND COALESCE(status, '') NOT IN ('OPEN', '') "
            f"AND pnl_rupees < 0 "
            f"AND exit_time IS NOT NULL "
            f"AND exit_time <= ? "
            f"ORDER BY exit_time DESC LIMIT 1",
            (today_iso, now_iso),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        dt = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = IST.localize(dt)
        return dt
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Decision ───────────────────────────────────────────────────────────

def assess(tab: str, now: Optional[datetime] = None) -> dict:
    """Evaluate whether new entries should be blocked.

    Returns:
        dict {
          "block": bool,
          "reason": str,
          "today_pnl": float,
          "daily_limit": float,
          "daily_breach": bool,
          "consec_losses": int,
          "consec_limit": int,
          "consec_breach": bool,
          "cool_off_active": bool,
          "cool_off_remaining_min": int,
        }
    """
    if now is None:
        now = datetime.now(IST)
    elif now.tzinfo is None:
        now = IST.localize(now)

    pnl = today_pnl(tab)
    limit = daily_loss_limit(tab)
    daily_breach = pnl <= -abs(limit)

    consec = recent_consecutive_losses(tab)
    consec_limit_v = consecutive_loss_limit()
    consec_breach = consec >= consec_limit_v

    cool_off_active = False
    cool_off_remaining = 0
    if consec_breach:
        last_loss = last_loss_time(tab)
        if last_loss:
            elapsed = (now - last_loss).total_seconds() / 60
            if elapsed < cool_off_minutes():
                cool_off_active = True
                cool_off_remaining = int(cool_off_minutes() - elapsed)

    block = False
    reason = "OK"

    if daily_breach:
        block = True
        reason = (
            f"DAILY_LOSS_CAP: {tab} P&L today ₹{pnl:,.0f} ≤ "
            f"-₹{abs(limit):,.0f} limit → no new entries"
        )
    elif cool_off_active:
        block = True
        reason = (
            f"CONSEC_LOSS_PAUSE: {tab} had {consec} losses in a row → "
            f"cool-off active, {cool_off_remaining}min remaining"
        )

    return {
        "block": block,
        "reason": reason,
        "tab": tab,
        "today_pnl": round(pnl, 2),
        "daily_limit": -abs(limit),
        "daily_breach": daily_breach,
        "consec_losses": consec,
        "consec_limit": consec_limit_v,
        "consec_breach": consec_breach,
        "cool_off_active": cool_off_active,
        "cool_off_remaining_min": cool_off_remaining,
    }


def shadow_log(decision: dict, source: str):
    if not is_shadow_enabled():
        return
    if decision["block"] or decision["daily_breach"] or decision["consec_breach"]:
        # Only log when relevant
        action = "BLOCK" if decision["block"] else "WOULD_BLOCK"
        print(
            f"[CIRCUIT_SHADOW] {source} {decision['tab']} {action} "
            f"pnl=₹{decision['today_pnl']} limit=₹{decision['daily_limit']} "
            f"consec={decision['consec_losses']}/{decision['consec_limit']} "
            f"reason='{decision['reason'][:100]}'"
        )


def should_block(tab: str, source: str = "unknown") -> bool:
    """Public API — returns True if new entries should be blocked.

    Always shadow-logs. Only enforces when DAILY_LOSS_CAP_ENABLED=on.
    """
    decision = assess(tab)
    shadow_log(decision, source)
    if not is_enabled():
        return False
    return decision["block"]


def status(tab: str) -> dict:
    """Snapshot for /api/circuit-breaker/status endpoint."""
    return assess(tab)
