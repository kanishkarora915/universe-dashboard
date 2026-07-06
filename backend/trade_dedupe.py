"""
trade_dedupe — cross-tab dedupe for same-strike-and-side entries.

FORENSIC MOTIVATION (2026-07-06 agent analysis, 60d window):

  Same-strike revenge trades = -₹8,73,000 across 86 fires. Both tabs
  (main + scalper) run independent verdict engines that consume the
  same market data. When both engines cross a threshold on the same
  signal, they fire on the SAME (index, strike, side) minutes apart —
  once from scalper, once from main — often both losing because the
  move already happened by the second fire.

  Real example (2026-07-06):
    12:18:03  scalper → NIFTY 24450 BUY CE @ ₹62.40  → -₹448
    12:20:16  main    → NIFTY 24450 BUY CE @ ₹62.50  → -₹4,275
    Both tabs bought the same top. Same side, same strike, 2 min apart.

  Blocking these = the single biggest ₹ leak fix identified. Historical
  counterfactual: 86 losing re-entries blocked, ~7 winners collateral
  damage → net +₹8.4L over 60d.

MODULE CONTRACT:

  1. FULLY ISOLATED — no imports from trade_logger / scalper_mode /
     position_watcher / structure_gate / any trading logic.
     Tests enforce this.
  2. READ-ONLY on the two SQLite files (trades.db + scalper_trades.db).
     Never mutates.
  3. Fail-safe ALLOW — any error → allow the trade. Never block on
     an infra failure.
  4. Env-controlled. Default ON, disable with CROSS_ENGINE_DEDUPE=off.

RULE:

  Given a proposed entry (idx, strike, action_str):
    Normalize action → "CE" or "PE"
    Compute cutoff = now_ist - DEDUPE_WINDOW_MIN minutes
    Look at main.trades AND scalper.scalper_trades:
      Any row where:
        - idx == proposed idx
        - strike == proposed strike
        - action LIKE "%CE%" or "%PE%" (matching side)
        - entry_time >= cutoff  (IST prefix compare on YYYY-MM-DD... strings works)
    If ANY row matches → BLOCK. Return (False, reason describing which tab + when).
    Else → ALLOW.

  A trade already CLOSED still counts as a hit — the point is to avoid
  the "second click at the same top" pattern.

ENV OVERRIDES:

  CROSS_ENGINE_DEDUPE=off              — disable entirely (default on)
  DEDUPE_WINDOW_MIN=30                 — window minutes (default 30)
  DEDUPE_INCLUDE_CLOSED=on             — count closed trades too (default on)
  DEDUPE_MAIN_DB_PATH                  — override main trades DB path
  DEDUPE_SCALPER_DB_PATH               — override scalper DB path

DIAGNOSTICS: GET /api/admin/trade-dedupe
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytz


_IST = pytz.timezone("Asia/Kolkata")


# ── Data-dir + DB paths ─────────────────────────────────────────────

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent


def _main_db_path() -> str:
    return os.environ.get("DEDUPE_MAIN_DB_PATH", "").strip() or str(_DATA_DIR / "trades.db")


def _scalper_db_path() -> str:
    return (
        os.environ.get("DEDUPE_SCALPER_DB_PATH", "").strip()
        or str(_DATA_DIR / "scalper_trades.db")
    )


# ── Env helpers ──────────────────────────────────────────────────────

def _enabled(env_key: str, default: bool = True) -> bool:
    v = os.environ.get(env_key, "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    return default


def _f(env_key: str, default: float) -> float:
    try:
        return float(os.environ.get(env_key, "").strip() or default)
    except Exception:
        return default


# ── Public helper: parse "BUY CE" / "BUY PE" → "CE" / "PE" ──────────

def _side_from_action(action: str) -> Optional[str]:
    if not action:
        return None
    a = action.upper()
    if "CE" in a:
        return "CE"
    if "PE" in a:
        return "PE"
    return None


# ── SQLite lookup (defensive) ────────────────────────────────────────

def _lookup_recent(
    db_path: str,
    table: str,
    idx: str,
    strike: int,
    side: str,
    cutoff_iso_prefix: str,
    include_closed: bool,
) -> Optional[Dict[str, Any]]:
    """Return the most recent matching row or None. Never raises.

    entry_time is stored as ISO 8601 IST prefix (YYYY-MM-DDTHH:MM:SS...)
    so a lexical `>=` comparison is equivalent to timestamp `>=` as long as
    both sides are same-timezone / same-format. We hold cutoff formatted
    as "YYYY-MM-DDTHH:MM:SS" (no timezone) — SQLite's `>=` still works
    because rows have prefix-longer strings that start with the same
    T-separated timestamp.
    """
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        # Match action with either "BUY CE" / "BUY PE" or plain "CE" / "PE".
        side_pattern = f"%{side}%"
        if include_closed:
            sql = (
                f"SELECT id, entry_time, exit_time, action, status, pnl_rupees "
                f"FROM {table} "
                f"WHERE idx = ? AND strike = ? AND action LIKE ? "
                f"      AND entry_time >= ? "
                f"ORDER BY entry_time DESC LIMIT 1"
            )
            params = (idx, int(strike), side_pattern, cutoff_iso_prefix)
        else:
            sql = (
                f"SELECT id, entry_time, exit_time, action, status, pnl_rupees "
                f"FROM {table} "
                f"WHERE idx = ? AND strike = ? AND action LIKE ? "
                f"      AND entry_time >= ? AND status = 'OPEN' "
                f"ORDER BY entry_time DESC LIMIT 1"
            )
            params = (idx, int(strike), side_pattern, cutoff_iso_prefix)
        row = conn.execute(sql, params).fetchone()
        conn.close()
        if row is None:
            return None
        return dict(row)
    except Exception as e:
        # Fail-safe — never block on infra error
        print(f"[TRADE_DEDUPE] lookup {db_path}/{table} error (allow): {e}")
        return None


# ── Public API ───────────────────────────────────────────────────────

def check_dedupe(idx: str, strike: int, action_str: str,
                 requesting_tab: str = "unknown") -> Tuple[bool, str]:
    """Should this proposed entry be blocked due to a recent same-strike fire?

    Args:
      idx           "NIFTY" / "BANKNIFTY"
      strike        integer strike (e.g. 24450)
      action_str    "BUY CE" / "BUY PE" / "CE" / "PE" — any string containing side
      requesting_tab optional caller tag for logs ("main" / "scalper")

    Returns:
      (block, reason)  — block=True means DO NOT ENTER.
                        block=False means allow.

    Behaviour:
      * Env kill switch CROSS_ENGINE_DEDUPE=off returns (False, "").
      * Any exception → (False, ""). Fail-safe allow.
    """
    try:
        if not _enabled("CROSS_ENGINE_DEDUPE", default=True):
            return False, ""

        side = _side_from_action(action_str)
        if side is None:
            return False, ""  # unknown action → don't block

        window_min = _f("DEDUPE_WINDOW_MIN", 30.0)
        include_closed = _enabled("DEDUPE_INCLUDE_CLOSED", default=True)

        now_ist = datetime.now(_IST).replace(tzinfo=None)
        cutoff = now_ist - timedelta(minutes=window_min)
        # entry_time in DB looks like 2026-07-06T12:20:16.032557+05:30
        # Prefix comparison up to T-second is sufficient
        cutoff_prefix = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

        main_hit = _lookup_recent(_main_db_path(), "trades", idx, strike, side,
                                  cutoff_prefix, include_closed)
        if main_hit:
            return True, _format_reason(
                "main", main_hit, idx, strike, side, window_min, requesting_tab,
            )

        scalper_hit = _lookup_recent(_scalper_db_path(), "scalper_trades",
                                     idx, strike, side, cutoff_prefix,
                                     include_closed)
        if scalper_hit:
            return True, _format_reason(
                "scalper", scalper_hit, idx, strike, side, window_min,
                requesting_tab,
            )

        return False, ""

    except Exception as e:
        # Fail-safe: never block on error
        print(f"[TRADE_DEDUPE] error (allow): {e}")
        return False, ""


def _format_reason(
    prior_tab: str,
    row: Dict[str, Any],
    idx: str,
    strike: int,
    side: str,
    window_min: float,
    requesting_tab: str,
) -> str:
    entry = str(row.get("entry_time", "?"))[:19]
    status = row.get("status", "?")
    pnl = row.get("pnl_rupees") or 0
    return (
        f"CROSS_ENGINE_DEDUPE: {requesting_tab} tab blocked from firing "
        f"{idx} {strike} {side} — {prior_tab} tab already entered "
        f"at {entry} (status={status}, pnl=₹{pnl:+.0f}) within "
        f"{window_min:.0f}min window"
    )


# ── Diagnostics for /api/admin ──────────────────────────────────────

def diagnostics() -> Dict[str, Any]:
    return {
        "enabled": _enabled("CROSS_ENGINE_DEDUPE", True),
        "window_min": _f("DEDUPE_WINDOW_MIN", 30.0),
        "include_closed": _enabled("DEDUPE_INCLUDE_CLOSED", True),
        "main_db_path": _main_db_path(),
        "scalper_db_path": _scalper_db_path(),
        "main_db_exists": os.path.exists(_main_db_path()),
        "scalper_db_exists": os.path.exists(_scalper_db_path()),
        "env_overrides": {
            "CROSS_ENGINE_DEDUPE": os.environ.get("CROSS_ENGINE_DEDUPE", ""),
            "DEDUPE_WINDOW_MIN": os.environ.get("DEDUPE_WINDOW_MIN", ""),
            "DEDUPE_INCLUDE_CLOSED": os.environ.get("DEDUPE_INCLUDE_CLOSED", ""),
        },
    }
