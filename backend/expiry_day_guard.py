"""
expiry_day_guard — block BUYER entries on theta-toxic days.

WHY THIS MODULE EXISTS

60-day audit (2026-05-19) found Tuesday (NIFTY weekly expiry day) is
catastrophic for option BUYERS:

  Day        n    WR%    Total P&L
  ──────────────────────────────────────
  Tue (NIFTY weekly expiry):
    MAIN     31   52%    -₹116,411  🔴
    SCALPER  74   35%    -₹77,394  🔴
    COMBINED 105  42%    -₹193,805 (24% of all losses, ONE day)

  Mon (day before NIFTY expiry):
    MAIN     42   38%    -₹47,676  🔴

WHY BUYERS DIE ON EXPIRY DAY

  1. Theta vertical drop — options lose 30-50% just from time decay
  2. Pin action — institutions push spot to max-pain strike
  3. Range-bound chop with violent fakeouts
  4. IV crush — even on right direction, premiums shrink
  5. Late-day premium = pure gamble (OTM worth zero in hours)

THIS GUARD

  • Default: skips ALL entries on Tuesday (NIFTY weekly expiry day)
  • Optional: also skip Monday (theta-acceleration day before expiry)
  • Optional: allow late-day entries on expiry (post-pin)

ENV FLAGS

  EXPIRY_DAY_SKIP_ENABLED=on     master switch (default 'off')
  EXPIRY_DAY_SKIP_TUESDAY=on     skip NIFTY weekly expiry (default 'on')
  EXPIRY_DAY_SKIP_MONDAY=off     skip day-before-expiry (default 'off')
  EXPIRY_DAY_ALLOW_LATE_HOUR=15  allow entries after this hour even on expiry (default 14)

ROLLBACK: flip EXPIRY_DAY_SKIP_ENABLED=off → restart. ~30s.
"""

from __future__ import annotations
import os
from datetime import datetime
from typing import Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")


def is_enabled() -> bool:
    """Master switch — default OFF for shadow validation."""
    return os.environ.get("EXPIRY_DAY_SKIP_ENABLED", "off").lower() == "on"


def is_shadow_enabled() -> bool:
    """Shadow log decisions even when gate is off."""
    return os.environ.get("EXPIRY_DAY_SHADOW", "on").lower() == "on"


def skip_tuesday() -> bool:
    """Tuesday is NIFTY weekly expiry — defaults to skip."""
    return os.environ.get("EXPIRY_DAY_SKIP_TUESDAY", "on").lower() == "on"


def skip_monday() -> bool:
    """Monday is day before expiry — defaults to NOT skip (less toxic)."""
    return os.environ.get("EXPIRY_DAY_SKIP_MONDAY", "off").lower() == "on"


def allow_late_hour() -> int:
    """Allow entries after this hour even on expiry day (post-pin)."""
    try:
        return int(os.environ.get("EXPIRY_DAY_ALLOW_LATE_HOUR", "14"))
    except ValueError:
        return 14


def assess(now: Optional[datetime] = None) -> dict:
    """Return decision dict for current moment.

    Args:
        now: IST datetime (defaults to actual current time)

    Returns dict {
        "skip": bool,
        "reason": str,
        "day_of_week": str,
        "is_tuesday": bool,
        "is_monday": bool,
        "is_late_hour": bool,
    }
    """
    if now is None:
        now = datetime.now(IST)
    elif now.tzinfo is None:
        now = IST.localize(now)

    dow = now.weekday()  # Mon=0, Tue=1, ..., Sun=6
    day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dow]
    hour = now.hour

    is_tuesday = (dow == 1)
    is_monday = (dow == 0)
    is_late = hour >= allow_late_hour()

    info = {
        "day_of_week": day_name,
        "is_tuesday": is_tuesday,
        "is_monday": is_monday,
        "is_late_hour": is_late,
        "skip": False,
        "reason": "",
    }

    # Tuesday (NIFTY weekly expiry) — main target
    if is_tuesday and skip_tuesday() and not is_late:
        info["skip"] = True
        info["reason"] = (
            f"EXPIRY_DAY_SKIP: Tuesday = NIFTY weekly expiry "
            f"(audit: 105 trades, 42% WR, -₹193,805 / 60 days). "
            f"Late-hour entries allowed after {allow_late_hour()}:00."
        )
        return info

    # Monday (day before expiry — optional)
    if is_monday and skip_monday():
        info["skip"] = True
        info["reason"] = (
            f"EXPIRY_DAY_SKIP: Monday = day before NIFTY expiry "
            f"(theta acceleration zone)"
        )
        return info

    info["reason"] = f"OK to trade ({day_name})"
    return info


def shadow_log(decision: dict, source: str):
    if not is_shadow_enabled():
        return
    if decision.get("is_tuesday") or decision.get("is_monday"):
        # Only log on potentially-relevant days
        action = "WOULD_SKIP" if decision["skip"] else "ALLOW"
        print(
            f"[EXPIRY_GUARD_SHADOW] {source} day={decision['day_of_week']} "
            f"{action} reason='{decision['reason'][:100]}'"
        )


def should_skip(source: str = "unknown", now: Optional[datetime] = None) -> bool:
    """Main entry — returns True if entry should be blocked.

    Always shadow-logs. Only blocks when EXPIRY_DAY_SKIP_ENABLED=on.
    """
    decision = assess(now=now)
    shadow_log(decision, source)
    if not is_enabled():
        return False
    return decision["skip"]
