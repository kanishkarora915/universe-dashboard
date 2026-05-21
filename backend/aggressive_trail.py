"""
aggressive_trail — PEAK-anchored tight trail (offensive profit capture).

WHY THIS MODULE EXISTS

User insight 2026-05-21:
  "Why focus only on losses? I want BIG PROFITS too. The cons of
   defensive Phase 2 are blocking my profits."

60-day audit data showed top wins gave back too much:
  Win #1 (NIFTY PE): peak +51%, exit +38% → gave back 13pp = ₹35k lost
  Win #2 (BNF PE):   peak +21%, exit +13% → gave back 8pp  = ₹40k lost
  Win #4 (NIFTY CE): peak +23%, exit +17% → gave back 6pp  = ₹14k lost

  Top 5 wins alone: ₹100k+ left on the table.

CURRENT BEHAVIOR (entry-anchored ladder):

  Profit +5%   → SL at entry (breakeven)
  Profit +10%  → SL at entry × 1.05 (+5% locked)
  Profit +20%  → SL at entry × 1.10 (+10% locked)
  Profit +40%  → SL at entry × 1.25 (+25% locked)

At peak +51%, SL locked at +25%. Big giveback.

NEW BEHAVIOR (peak-anchored tight trail):

  Peak ≤ +5%    → SL at entry × 0.95   (standard)
  Peak +5-10%   → SL at entry (breakeven)
  Peak +10-20%  → SL at peak × 0.92    (8% from PEAK, not entry)
  Peak +20-40%  → SL at peak × 0.94    (6% from peak)
  Peak +40-70%  → SL at peak × 0.95    (5% from peak — runner)
  Peak >+70%    → SL at peak × 0.96    (4% from peak — moonshot mode)

At peak +51%, SL at peak × 0.95 = ₹296 (vs current ₹259).
Captures ₹35k more on a single trade.

ENV FLAG

  AGGRESSIVE_TRAIL_ENABLED=on    activate (default off until proven)

ROLLBACK: flip to off → restart. ~30s.

WHAT IT DOES NOT DO
  • Does NOT change SL placement for losing trades (only winners)
  • Does NOT block trades or change entry logic
  • Does NOT affect T1/T2 targets
  • Only TIGHTENS the trail behind the peak
"""

from __future__ import annotations
import os
from typing import Dict, Optional


def is_enabled() -> bool:
    """Default OFF until shadow-validated."""
    return os.environ.get("AGGRESSIVE_TRAIL_ENABLED", "off").lower() == "on"


def is_shadow_enabled() -> bool:
    """Shadow-log even when off, so we can see what WOULD happen."""
    return os.environ.get("AGGRESSIVE_TRAIL_SHADOW", "on").lower() == "on"


# Peak-anchored trail bands (peak_pct, giveback_pct from peak)
# Tighter as peak grows — runner mode at high peaks.
PEAK_TRAIL_BANDS = [
    # (peak_threshold_pct, giveback_pct_from_peak)
    # Below +5%: no trail change (use standard SL)
    (5.0, None),     # 5-10% — special: lock to entry (breakeven)
    (10.0, 8.0),    # 10-20% peak: trail 8% from peak
    (20.0, 6.0),    # 20-40%: trail 6% from peak
    (40.0, 5.0),    # 40-70%: trail 5% from peak (RUNNER)
    (70.0, 4.0),    # 70%+: trail 4% from peak (MOONSHOT)
]


def calculate_aggressive_trail(
    entry_price: float,
    peak_price: float,
    current_price: float,
    current_sl: float,
    min_gap_from_current_pct: float = 0.5,
) -> Optional[Dict]:
    """
    Compute peak-anchored trail SL.

    Args:
        entry_price:   original entry premium
        peak_price:    highest price seen since entry (peak_ltp)
        current_price: current premium
        current_sl:    existing SL (won't lower it)
        min_gap_from_current_pct: don't set SL too close to current price

    Returns:
        None  if no change recommended (or feature off)
        dict  {"new_sl": float, "peak_pct": float, "giveback_pct": float,
               "stage": str, "locked_pct": float, "method": "aggressive_peak"}
    """
    if entry_price <= 0 or peak_price <= 0 or current_price <= 0:
        return None

    peak_pct = (peak_price - entry_price) / entry_price * 100

    # Below +5%: no aggressive trail yet (let standard SL handle)
    if peak_pct < 5:
        return None

    # +5% to +10%: lock breakeven (give back the +5% gain but no loss)
    if peak_pct < 10:
        new_sl = round(entry_price, 2)
        stage = "+5%_breakeven_lock"
    else:
        # Walk bands top-down for highest applicable
        giveback = None
        stage = "?"
        for threshold, gb in sorted(PEAK_TRAIL_BANDS, reverse=True):
            if peak_pct >= threshold and gb is not None:
                giveback = gb
                stage = f"+{int(threshold)}%_band_{gb}%_giveback"
                break

        if giveback is None:
            return None

        # SL = peak × (1 - giveback/100)
        new_sl = round(peak_price * (1 - giveback / 100), 2)

    # Safety: never set SL within Y% of current price
    safe_max = round(current_price * (1 - min_gap_from_current_pct / 100), 2)
    if new_sl > safe_max:
        new_sl = safe_max
        stage += "_clamped_to_safe_max"

    # Only return if it RAISES the SL
    if new_sl <= current_sl:
        return None

    locked_pct = (new_sl - entry_price) / entry_price * 100
    giveback_realized = (peak_price - new_sl) / peak_price * 100 if peak_price > 0 else 0

    return {
        "new_sl": new_sl,
        "peak_price": peak_price,
        "peak_pct": round(peak_pct, 2),
        "giveback_pct_from_peak": round(giveback_realized, 2),
        "locked_pct": round(locked_pct, 2),
        "stage": stage,
        "method": "aggressive_peak_anchored",
    }


def compare_with_legacy(
    entry_price: float,
    peak_price: float,
    current_price: float,
    current_sl: float,
    legacy_sl: Optional[float] = None,
) -> Dict:
    """For shadow logging — show what aggressive trail would do vs legacy."""
    aggressive = calculate_aggressive_trail(entry_price, peak_price, current_price, current_sl)
    return {
        "entry": entry_price,
        "peak": peak_price,
        "current": current_price,
        "current_sl": current_sl,
        "legacy_sl": legacy_sl,
        "aggressive_new_sl": aggressive["new_sl"] if aggressive else None,
        "aggressive_locked_pct": aggressive["locked_pct"] if aggressive else None,
        "aggressive_stage": aggressive["stage"] if aggressive else None,
        "delta_vs_legacy": (
            round(aggressive["new_sl"] - legacy_sl, 2)
            if aggressive and legacy_sl else None
        ),
    }


def shadow_log(
    *,
    entry_price: float,
    peak_price: float,
    current_price: float,
    current_sl: float,
    legacy_sl: Optional[float],
    trade_id: Optional[int],
    source: str = "scalper",
):
    """Log comparison even when feature is off — see real-time what we'd do."""
    if not is_shadow_enabled():
        return
    aggressive = calculate_aggressive_trail(entry_price, peak_price, current_price, current_sl)
    if not aggressive:
        return
    delta = (aggressive["new_sl"] - legacy_sl) if legacy_sl else None
    print(
        f"[AGGRESSIVE_TRAIL_SHADOW] {source} #{trade_id} "
        f"entry=₹{entry_price} peak=₹{peak_price} (+{aggressive['peak_pct']}%) "
        f"current=₹{current_price} legacy_sl=₹{legacy_sl} "
        f"aggressive_sl=₹{aggressive['new_sl']} (locked +{aggressive['locked_pct']}%) "
        f"delta_vs_legacy=₹{delta} stage={aggressive['stage']}"
    )


def get_or_legacy(
    *,
    entry_price: float,
    peak_price: float,
    current_price: float,
    current_sl: float,
    legacy_sl: Optional[float] = None,
    trade_id: Optional[int] = None,
    source: str = "scalper",
) -> Optional[float]:
    """Public API — returns aggressive SL if enabled, else legacy.

    Always shadow-logs. Only returns aggressive when AGGRESSIVE_TRAIL_ENABLED=on.
    """
    # Always shadow-log
    shadow_log(
        entry_price=entry_price,
        peak_price=peak_price,
        current_price=current_price,
        current_sl=current_sl,
        legacy_sl=legacy_sl,
        trade_id=trade_id,
        source=source,
    )

    if not is_enabled():
        return legacy_sl

    aggressive = calculate_aggressive_trail(entry_price, peak_price, current_price, current_sl)
    if not aggressive:
        return legacy_sl

    # Use the higher of aggressive vs legacy (always prefer higher SL on winners)
    if legacy_sl is not None and legacy_sl > aggressive["new_sl"]:
        return legacy_sl
    return aggressive["new_sl"]
