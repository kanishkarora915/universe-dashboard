"""
profit_floor — HARD GUARANTEE: profitable trades never close in loss.

WHY THIS MODULE EXISTS

User question 2026-05-21:
  "Trade went to +₹10k profit, then closed in LOSS. Why?
   Should system act like this?"

ANSWER: NO. That's a fundamental design flaw.

Real data shows 159 trades (60d) went profitable then closed in loss.
Total damage: -₹2.22M of "should-have-been-protected" P&L.

  18 trades peaked +5% or more and still closed in LOSS

WHY IT HAPPENS

  1. Trail SL ladder triggers at +5% (main) / +4% (scalper) — TOO LATE
  2. If price spikes briefly and reverses, ladder uses CURRENT price
     (which is now low) — never triggers
  3. peak_ltp IS tracked in DB but legacy trail doesn't use it
  4. PEAK_FLOOR safety net is at -5% (still a loss!)
  5. Multiple trail systems with conflicting logic

THIS MODULE — THE HARD FLOOR

For ANY open trade, compute the minimum acceptable SL based on PEAK
ever achieved. The rules:

  Peak ever ≥ +3%   →  Floor = entry          (BREAKEVEN — no loss possible)
  Peak ever ≥ +5%   →  Floor = entry × 1.01   (lock +1%)
  Peak ever ≥ +8%   →  Floor = entry × 1.02   (lock +2%)
  Peak ever ≥ +12%  →  Floor = entry × 1.05   (lock +5%)
  Peak ever ≥ +18%  →  Floor = entry × 1.10   (lock +10%)
  Peak ever ≥ +25%  →  Floor = entry × 1.15   (lock +15%)
  Peak ever ≥ +40%  →  Floor = entry × 1.25   (lock +25%)
  Peak ever ≥ +60%  →  Floor = entry × 1.40   (lock +40%)
  Peak ever ≥ +80%  →  Floor = entry × 1.55   (lock +55%)

This is IDEMPOTENT — looks at PEAK (which is sticky in DB), not current.

  ✅ If peak was briefly +8% but price crashed back to -5%,
     floor still says "minimum SL = entry × 1.02"
  ✅ This means any exit (SL_HIT, REVERSAL_EXIT, WATCHER_EXIT)
     MUST be at the floor or higher
  ✅ Worst case for a "profitable" trade = locked profit (never loss)

THIS IS THE GUARANTEE: profitable trade = profitable exit.

ENV FLAG

  PROFIT_FLOOR_ENABLED=on    activate (default ON — this is a safety guard)

WHY DEFAULT ON

  Because the alternative (allowing profitable trades to close in loss)
  is mathematically and emotionally unacceptable. This is a SAFETY layer.

  Compatible with aggressive_trail (which trails behind peak with %).
  profit_floor is the LOWER bound. aggressive_trail can set HIGHER.
  Final SL = max(legacy_sl, profit_floor, aggressive_trail).

INTEGRATION

  Called from profit_trailing_sl.update_main_trail / update_scalper_trail
  Returns minimum_acceptable_sl. Caller never sets SL below this.
"""

from __future__ import annotations
import os
from typing import Dict, Optional


# Peak threshold → floor multiplier of entry
# (peak_pct_reached, floor_multiplier_of_entry)
# Floor = entry × multiplier
PROFIT_FLOOR_BANDS = [
    # (peak_threshold_pct, sl_multiplier)
    (3.0,   1.00),    # +3% peak → BREAKEVEN floor (no loss)
    (5.0,   1.01),    # +5% peak → +1% locked
    (8.0,   1.02),    # +8% peak → +2% locked
    (12.0,  1.05),    # +12% peak → +5% locked
    (18.0,  1.10),    # +18% peak → +10% locked
    (25.0,  1.15),    # +25% peak → +15% locked
    (40.0,  1.25),    # +40% peak → +25% locked
    (60.0,  1.40),    # +60% peak → +40% locked (RUNNER)
    (80.0,  1.55),    # +80% peak → +55% locked (MOONSHOT)
    (100.0, 1.75),    # +100% peak → +75% locked
]


def is_enabled() -> bool:
    """Default ON — this is a safety guarantee."""
    return os.environ.get("PROFIT_FLOOR_ENABLED", "on").lower() == "on"


def is_shadow_enabled() -> bool:
    return os.environ.get("PROFIT_FLOOR_SHADOW", "on").lower() == "on"


def compute_floor(entry_price: float, peak_price: float) -> Optional[Dict]:
    """Compute minimum acceptable SL based on peak ever achieved.

    Args:
        entry_price: option premium at entry
        peak_price: highest LTP ever seen (from DB peak_ltp field)

    Returns:
        None  if peak hasn't crossed +3% threshold
        dict  {"floor_sl": float, "peak_pct": float, "locked_pct": float,
               "band": str, "guarantee": str}
    """
    if entry_price <= 0 or peak_price <= 0:
        return None
    if peak_price <= entry_price:
        return None

    peak_pct = (peak_price - entry_price) / entry_price * 100

    # Find highest applicable band
    floor_sl = None
    band_name = None
    for threshold, multiplier in sorted(PROFIT_FLOOR_BANDS, reverse=True):
        if peak_pct >= threshold:
            floor_sl = round(entry_price * multiplier, 2)
            band_name = f"+{int(threshold)}%_peak"
            break

    if floor_sl is None:
        return None  # peak didn't cross any threshold

    locked_pct = (floor_sl - entry_price) / entry_price * 100
    guarantee = (
        f"Peak reached +{peak_pct:.1f}% → SL floor at ₹{floor_sl} "
        f"(min {locked_pct:+.1f}% locked, NO LOSS possible)"
    )

    return {
        "floor_sl": floor_sl,
        "peak_pct": round(peak_pct, 2),
        "locked_pct": round(locked_pct, 2),
        "band": band_name,
        "guarantee": guarantee,
    }


def get_minimum_sl(
    *,
    entry_price: float,
    peak_price: float,
    current_sl: float,
) -> float:
    """Public API — return the MINIMUM acceptable SL for this trade.

    Caller should: final_sl = max(legacy_sl, get_minimum_sl(...))

    Always returns SL ≥ current_sl (never lowers).
    """
    if not is_enabled():
        return current_sl

    floor_info = compute_floor(entry_price, peak_price)
    if not floor_info:
        return current_sl

    floor_sl = floor_info["floor_sl"]
    # Never lower SL
    return max(current_sl, floor_sl)


def shadow_log(
    *,
    entry_price: float,
    peak_price: float,
    current_sl: float,
    trade_id: Optional[int] = None,
    tab: str = "?",
):
    """Log what floor WOULD be (even when feature off)."""
    if not is_shadow_enabled():
        return
    floor_info = compute_floor(entry_price, peak_price)
    if not floor_info:
        return
    # Only log if floor would RAISE the SL
    if floor_info["floor_sl"] > current_sl:
        print(
            f"[PROFIT_FLOOR_SHADOW] {tab} #{trade_id} "
            f"entry=₹{entry_price} peak=₹{peak_price} ({floor_info['peak_pct']}%) "
            f"current_sl=₹{current_sl} floor=₹{floor_info['floor_sl']} "
            f"(band {floor_info['band']}, locked {floor_info['locked_pct']:+.1f}%)"
        )


def diagnose(entry_price: float, peak_price: float, current_sl: float) -> dict:
    """Diagnostic info — useful for API responses + debugging."""
    enabled = is_enabled()
    floor_info = compute_floor(entry_price, peak_price)
    return {
        "enabled": enabled,
        "entry_price": entry_price,
        "peak_price": peak_price,
        "current_sl": current_sl,
        "floor_info": floor_info,
        "would_raise_sl": floor_info is not None and floor_info["floor_sl"] > current_sl,
        "final_sl": get_minimum_sl(
            entry_price=entry_price,
            peak_price=peak_price,
            current_sl=current_sl,
        ),
    }
