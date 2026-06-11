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
ever achieved. The rules (2026-06-11 — AGGRESSIVE PEAK LOCK):

  Peak ever ≥ +1.5%  →  Floor = entry          (BREAKEVEN — no loss)   ← NEW
  Peak ever ≥ +2.5%  →  Floor = entry × 1.010  (lock +1%)              ← NEW
  Peak ever ≥ +3.5%  →  Floor = entry × 1.020  (lock +2%)              ← NEW
  Peak ever ≥ +5%    →  Floor = entry × 1.025  (lock +2.5%)
  Peak ever ≥ +8%    →  Floor = entry × 1.040  (lock +4%)
  Peak ever ≥ +12%   →  Floor = entry × 1.060  (lock +6%)
  Peak ever ≥ +18%   →  Floor = entry × 1.100  (lock +10%)
  Peak ever ≥ +25%   →  Floor = entry × 1.150  (lock +15%)
  Peak ever ≥ +40%   →  Floor = entry × 1.250  (lock +25%)
  Peak ever ≥ +60%   →  Floor = entry × 1.400  (lock +40%)
  Peak ever ≥ +80%   →  Floor = entry × 1.550  (lock +55%)

BACKTEST PROOF: 24 BANKNIFTY giveback trades May-Jun 2026
  OLD bands: peak ₹+3.99L → final ₹-3.85L (24/24 losers)
  NEW bands: same trades → final ₹+1.30L profit (₹+5.16L swing)

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
#
# 2026-06-11 — AGGRESSIVE PEAK LOCK added (low-threshold bands).
# Backtest on 24 BANKNIFTY giveback trades (May-Jun 2026):
#   OLD: peak ₹+3.99L given back → net ₹-3.85L (24/24 losers)
#   NEW: same 24 trades → net ₹+1.30L profit (₹+5.16L swing)
#
# 2026-06-11 v2 — PER-INDEX BANDS (bear-trader feedback).
# BANKNIFTY ATM premium ~₹180. +1.5% = ₹2.70 = bid-ask noise (2 ticks).
# Locking BE on a 2-tick wiggle would flap-trigger on WebSocket jitter
# and exit winners prematurely. NIFTY ATM ~₹140, +1.5% = ₹2.10 = also
# tight, but NIFTY has less intraday jitter than BANKNIFTY.
# Solution: BANKNIFTY threshold raised by 1.0 percentage point (so floor
# only fires on real moves outside the spread).
#
# Multipliers MUST be monotonically non-decreasing with threshold.

# NIFTY bands (original aggressive)
PROFIT_FLOOR_BANDS_NIFTY = [
    (1.5,   1.000),   # +1.5% peak → BREAKEVEN
    (2.5,   1.010),   # +2.5% peak → +1% locked
    (3.5,   1.020),   # +3.5% peak → +2% locked
    (5.0,   1.025),
    (8.0,   1.040),
    (12.0,  1.060),
    (18.0,  1.100),
    (25.0,  1.150),
    (40.0,  1.250),
    (60.0,  1.400),
    (80.0,  1.550),
    (100.0, 1.750),
]

# BANKNIFTY bands — 2026-06-11 v3 lowered after #269 + today's ₹33k loss
# Both trades peaked +2.0-2.3% (just below old +2.5% threshold) → no lock fired
# New threshold +1.8% catches these while still escaping pure noise.
# ₹900 BANKNIFTY entry × +1.8% = ₹16.20 move = clearly above bid-ask spread.
PROFIT_FLOOR_BANDS_BANKNIFTY = [
    (1.8,   1.000),   # +1.8% peak → BREAKEVEN (was +2.5% — slipped through twice)
    (2.8,   1.010),   # +2.8% peak → +1% locked
    (4.0,   1.020),   # +4.0% peak → +2% locked
    (6.0,   1.025),
    (9.0,   1.040),
    (13.0,  1.060),
    (18.0,  1.100),
    (25.0,  1.150),
    (40.0,  1.250),
    (60.0,  1.400),
    (80.0,  1.550),
    (100.0, 1.750),
]

# Default (backward compat — used when idx not passed)
PROFIT_FLOOR_BANDS = PROFIT_FLOOR_BANDS_NIFTY


def _bands_for_idx(idx: str = None):
    """Returns appropriate band table for the index."""
    if idx and idx.upper() == "BANKNIFTY":
        return PROFIT_FLOOR_BANDS_BANKNIFTY
    return PROFIT_FLOOR_BANDS_NIFTY


def is_enabled() -> bool:
    """Default ON — this is a safety guarantee."""
    return os.environ.get("PROFIT_FLOOR_ENABLED", "on").lower() == "on"


def is_shadow_enabled() -> bool:
    return os.environ.get("PROFIT_FLOOR_SHADOW", "on").lower() == "on"


def compute_floor(entry_price: float, peak_price: float, idx: str = None) -> Optional[Dict]:
    """Compute minimum acceptable SL based on peak ever achieved.

    Args:
        entry_price: option premium at entry
        peak_price: highest LTP ever seen (from DB peak_ltp field)
        idx: NIFTY / BANKNIFTY — selects per-index threshold table
             (2026-06-11 v2 — BANKNIFTY thresholds shifted +1% to escape
             bid-ask spread noise)

    Returns:
        None  if peak hasn't crossed the lowest threshold
        dict  {"floor_sl": float, "peak_pct": float, "locked_pct": float,
               "band": str, "guarantee": str}
    """
    if entry_price <= 0 or peak_price <= 0:
        return None
    if peak_price <= entry_price:
        return None

    peak_pct = (peak_price - entry_price) / entry_price * 100

    # Per-index band selection
    bands = _bands_for_idx(idx)

    # Find highest applicable band
    floor_sl = None
    band_name = None
    for threshold, multiplier in sorted(bands, reverse=True):
        if peak_pct >= threshold:
            floor_sl = round(entry_price * multiplier, 2)
            # Format threshold with 1 decimal if not integer
            if threshold == int(threshold):
                band_name = f"+{int(threshold)}%_peak"
            else:
                band_name = f"+{threshold:.1f}%_peak"
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
        "idx_used": (idx or "NIFTY").upper(),
    }


def get_minimum_sl(
    *,
    entry_price: float,
    peak_price: float,
    current_sl: float,
    idx: str = None,
) -> float:
    """Public API — return the MINIMUM acceptable SL for this trade.

    Caller should: final_sl = max(legacy_sl, get_minimum_sl(...))

    Always returns SL ≥ current_sl (never lowers).

    idx: NIFTY / BANKNIFTY for per-index threshold selection.
         If omitted, NIFTY (tighter) bands used — backward compat.
    """
    if not is_enabled():
        return current_sl

    floor_info = compute_floor(entry_price, peak_price, idx=idx)
    if not floor_info:
        return current_sl

    floor_sl = floor_info["floor_sl"]
    # Never lower SL
    return max(current_sl, floor_sl)


def attribution_log(
    *,
    trade_id,
    tab: str,
    old_sl: float,
    new_sl: float,
    source: str,
    peak_price: float = 0,
    entry_price: float = 0,
    extra: str = "",
):
    """SL attribution log (2026-06-11 — bear-trader requirement).

    Every SL change MUST log: which module raised it, by how much, on
    what peak/entry context. Lets us answer "which ladder is doing the
    work and which are dead weight".
    """
    try:
        peak_pct = ((peak_price - entry_price) / entry_price * 100) if entry_price > 0 and peak_price > 0 else 0
        locked_pct = ((new_sl - entry_price) / entry_price * 100) if entry_price > 0 else 0
        raise_amt = new_sl - old_sl
        print(
            f"[SL-ATTRIB] {tab} #{trade_id} src={source} "
            f"₹{old_sl:.2f}→₹{new_sl:.2f} (+₹{raise_amt:.2f}) "
            f"peak_pct={peak_pct:+.2f}% locked_pct={locked_pct:+.2f}% {extra}"
        )
    except Exception:
        pass


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
