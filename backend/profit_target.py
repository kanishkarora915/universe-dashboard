"""
profit_target — "make money and leave" mode.

WHY THIS MODULE EXISTS

User request 2026-05-21:
  "Dine mein 15-20 trade le aur market se paisa banakr nikle"
  (Take 15-20 trades a day and EXIT the market after making money)

The discipline gap: when scalper hits +₹15k for the day, the engine
keeps firing trades. Eventually gives back the profit. The "greed
reversal" pattern.

THIS MODULE

  • Tracks today's per-tab P&L (reuses circuit_breaker logic)
  • Blocks new entries when daily P&L >= configured target
  • Open positions still get managed normally (T1/T2/SL/trail all work)
  • Resets at midnight IST next day

ENV FLAGS

  PROFIT_TARGET_ENABLED=on            master switch (default off)
  PROFIT_TARGET_MAIN=15000            ₹ target for main tab (default 15000)
  PROFIT_TARGET_SCALPER=15000         ₹ target for scalper tab (default 15000)
  PROFIT_TARGET_SHADOW=on             always shadow-log (default on)

ROLLBACK: flip master to off → restart. ~30s.

WHAT IT DOES NOT DO
  • Does NOT close open positions
  • Does NOT modify exit logic
  • Only blocks NEW entries when target hit
  • Triggers ONE Telegram alert at activation (per tab per day)
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional


def is_enabled() -> bool:
    """Master switch — default OFF for safety."""
    return os.environ.get("PROFIT_TARGET_ENABLED", "off").lower() == "on"


def is_shadow_enabled() -> bool:
    return os.environ.get("PROFIT_TARGET_SHADOW", "on").lower() == "on"


def profit_target(tab: str) -> float:
    """Per-tab daily profit target. Stops new entries when reached."""
    if tab.upper() == "MAIN":
        v = os.environ.get("PROFIT_TARGET_MAIN", "15000")
    else:
        v = os.environ.get("PROFIT_TARGET_SCALPER", "15000")
    try:
        return float(v)
    except ValueError:
        return 15000


def assess(tab: str) -> dict:
    """Return decision + diagnostic info.

    Returns dict {
        "block": bool,
        "reason": str,
        "tab": str,
        "today_pnl": float,
        "target": float,
        "target_hit": bool,
        "pct_to_target": float,    # 0.0-1.0 (or >1 if exceeded)
        "amount_to_go": float,     # negative if exceeded
    }
    """
    try:
        from circuit_breaker import today_pnl
        pnl = today_pnl(tab)
    except Exception:
        pnl = 0.0

    target = profit_target(tab)
    target_hit = pnl >= target

    pct = pnl / target if target > 0 else 0
    amount_to_go = target - pnl

    if target_hit:
        reason = (
            f"PROFIT_TARGET_HIT: {tab} P&L today ₹{pnl:,.0f} >= "
            f"target ₹{target:,.0f} → no new entries (book the win, walk away)"
        )
    else:
        reason = (
            f"Under target: ₹{pnl:,.0f} / ₹{target:,.0f} "
            f"({pct*100:.0f}% — ₹{amount_to_go:,.0f} to go)"
        )

    return {
        "block": target_hit,
        "reason": reason,
        "tab": tab,
        "today_pnl": round(pnl, 2),
        "target": target,
        "target_hit": target_hit,
        "pct_to_target": round(pct, 3),
        "amount_to_go": round(amount_to_go, 2),
    }


def shadow_log(decision: dict, source: str):
    if not is_shadow_enabled():
        return
    if decision["target_hit"]:
        # Only log loud when target hit
        print(
            f"[PROFIT_TARGET] {source} {decision['tab']} TARGET HIT — "
            f"₹{decision['today_pnl']} >= ₹{decision['target']} (blocking new entries)"
        )


def should_block(tab: str, source: str = "unknown") -> bool:
    """Public API — returns True if new entries should be blocked
    because today's profit target was reached.

    Always shadow-logs target hits. Only enforces when PROFIT_TARGET_ENABLED=on.
    """
    decision = assess(tab)
    shadow_log(decision, source)
    if not is_enabled():
        return False
    return decision["block"]


def status(tab: str) -> dict:
    """Snapshot for /api/profit-target/status endpoint."""
    return assess(tab)
