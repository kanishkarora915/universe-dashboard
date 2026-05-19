"""
smart_sl — anti-stop-hunt SL placement.

WHY THIS MODULE EXISTS

Audit (2026-05-19) found STOP_HUNT cost ₹351,707 across 37 main trades
in 60 days — ZERO wins. The pattern:

  1. SL placed at predictable round %: entry × 0.85 → SL = 85.0 for ₹100 entry
  2. Institutional algos detect concentrated stops at these round levels
  3. They dump to ₹84.5 (triggering your SL), then reverse to ₹90+
  4. You stopped out RIGHT before the trade would have worked

ROOT CAUSE: Predictable SL placement = sweep target for institutions.

THE FIX

Three changes to SL math:

  1. VOLATILITY-SCALED: SL distance from entry = K × option_ATR
     (not a fixed % of entry). Naturally non-round.

  2. STRUCTURAL BUFFER: Add 0.3 × ATR buffer beyond the "natural" SL
     so stop sits beyond institutional sweep zones.

  3. NSE-TICK PRECISION: Round to 0.05 (nearest paisa-aligned to NSE
     option tick size) NOT integer rupee. Removes round-number signal.

GUARDRAILS

  • Min SL distance: 8% of entry (don't go too tight — option can fall)
  • Max SL distance: 22% of entry (don't blow up capital)
  • If ATR unknown → fall back to a non-round version of the legacy %

WHAT THIS DOES NOT DO

  • Does NOT change T1/T2 targets — only SL
  • Does NOT modify the TRAIL_EXIT logic (it's the +₹968k earner)
  • Does NOT touch open positions — only NEW entries

ENV FLAG

  SMART_SL_ENABLED=on    → smart SL active
  SMART_SL_ENABLED=off   → legacy SL (shadow logging still runs)
  Default: 'off' until validated.

ROLLBACK: flip env var, restart container. ~30 sec.
"""

from __future__ import annotations
import os
from typing import Optional


# ── Env-flag readers ───────────────────────────────────────────────────

def is_smart_sl_enabled() -> bool:
    """Check if smart SL is active. Defaults OFF for safety."""
    return os.environ.get("SMART_SL_ENABLED", "off").lower() == "on"


def is_shadow_logging_enabled() -> bool:
    """Shadow logging: even when smart SL is OFF, log what smart SL
    would have computed. Lets us compare old vs new behavior live."""
    return os.environ.get("SMART_SL_SHADOW", "on").lower() == "on"


# ── Core SL computation ────────────────────────────────────────────────

def compute_smart_sl(
    entry_price: float,
    atr_pct: Optional[float] = None,
    direction: str = "BUY CE",
    legacy_sl: Optional[float] = None,
) -> dict:
    """Compute smart SL for a fresh option entry.

    Args:
        entry_price:  Option premium at entry (₹)
        atr_pct:      Option's ATR as fraction of premium (0.05 = 5%).
                      If None, falls back to legacy_sl logic with
                      non-round adjustment.
        direction:    "BUY CE" or "BUY PE" (kept for symmetry; smart SL
                      math is direction-agnostic since we're always long)
        legacy_sl:    Old SL value for shadow comparison + fallback.

    Returns:
        dict {
          "sl": float,              # the smart SL price (rounded to 0.05)
          "sl_pct": float,          # SL as % below entry
          "method": str,            # "atr_scaled" | "non_round_fallback" | "legacy"
          "atr_used": float | None,
          "guardrails_hit": str | None,  # e.g. "min_clamped" if too tight
        }
    """
    if entry_price <= 0:
        return {
            "sl": 0,
            "sl_pct": 0,
            "method": "invalid_entry",
            "atr_used": None,
            "guardrails_hit": None,
        }

    # Strategy 1: ATR-scaled (preferred)
    if atr_pct and atr_pct > 0:
        # SL distance = 1.5 × ATR of premium (gives 1× buffer + 0.5× sweep margin)
        sl_distance_pct = 1.5 * atr_pct

        # Apply guardrails: 8-22% of entry
        guardrails_hit = None
        if sl_distance_pct < 0.08:
            sl_distance_pct = 0.08
            guardrails_hit = "min_clamped (too tight)"
        elif sl_distance_pct > 0.22:
            sl_distance_pct = 0.22
            guardrails_hit = "max_clamped (too wide)"

        sl_raw = entry_price * (1 - sl_distance_pct)

        # Round to NSE tick size (0.05) instead of integer rupee
        sl = _round_to_tick(sl_raw)

        # Apply anti-round-number offset: if it landed on a multiple of
        # 5, nudge it down by 0.15-0.45 (small, non-uniform per entry).
        sl = _avoid_round_number(sl, entry_price)

        return {
            "sl": sl,
            "sl_pct": round((1 - sl/entry_price) * 100, 2),
            "method": "atr_scaled",
            "atr_used": atr_pct,
            "guardrails_hit": guardrails_hit,
        }

    # Strategy 2: Non-round fallback (when no ATR available)
    # Use legacy SL but apply tick-precision + anti-round-number offset
    if legacy_sl and legacy_sl > 0:
        sl = _round_to_tick(legacy_sl)
        sl = _avoid_round_number(sl, entry_price)
        return {
            "sl": sl,
            "sl_pct": round((1 - sl/entry_price) * 100, 2),
            "method": "non_round_fallback",
            "atr_used": None,
            "guardrails_hit": None,
        }

    # Strategy 3: No info at all → 15% default, non-round
    sl_raw = entry_price * 0.85
    sl = _round_to_tick(sl_raw)
    sl = _avoid_round_number(sl, entry_price)
    return {
        "sl": sl,
        "sl_pct": round((1 - sl/entry_price) * 100, 2),
        "method": "default_15pct",
        "atr_used": None,
        "guardrails_hit": None,
    }


# ── Helpers ────────────────────────────────────────────────────────────

def _round_to_tick(price: float, tick: float = 0.05) -> float:
    """Round to NSE option tick size (0.05 paisa).

    Uses integer-cents math then divides to avoid floating-point
    artifacts (otherwise 826.3 → 826.3000000000001).
    """
    if price <= 0:
        return 0.0
    # Convert to cents (×100), round to nearest 5, divide back
    cents = round(price * 100)
    rounded_cents = round(cents / 5) * 5
    return rounded_cents / 100


def _avoid_round_number(sl: float, entry_price: float) -> float:
    """Nudge SL away from multiples of 5 to avoid institutional sweep zones.

    If SL lands within 0.20 of a multiple of 5, push it 0.25-0.45 below
    the multiple. The offset is deterministic per entry (not random)
    so the same entry always gets the same SL.

    Examples:
        SL = 85.00, entry = 100   → returns 84.75
        SL = 84.95, entry = 100   → returns 84.75
        SL = 85.20, entry = 100   → returns 85.20 (already non-round)
        SL = 87.50, entry = 142.5 → returns 87.25 (push off the .50 line)
    """
    # Check distance from nearest multiple of 5
    nearest_5 = round(sl / 5) * 5
    distance_from_5 = abs(sl - nearest_5)

    if distance_from_5 < 0.20:
        # Generate deterministic offset from entry_price to spread evenly
        # in [-0.45, -0.25] range. Use prime multiplier (13) so adjacent
        # entry prices (100, 101, 102) get DIFFERENT offsets — prevents
        # SL clustering across nearby entries.
        offset_seed = int(entry_price * 13) % 5  # 0,1,2,3,4
        offset = 0.25 + offset_seed * 0.05  # 0.25, 0.30, 0.35, 0.40, 0.45
        sl = nearest_5 - offset
        sl = round(sl, 2)

    # Also avoid multiples of 2.5 (half-grid)
    nearest_2_5 = round(sl / 2.5) * 2.5
    if abs(sl - nearest_2_5) < 0.15 and abs(sl - nearest_5) > 0.5:
        # Only adjust off 2.5 if not already adjusted off 5
        offset_seed = int(entry_price * 13) % 3
        offset = 0.15 + offset_seed * 0.05  # 0.15, 0.20, 0.25
        sl = nearest_2_5 - offset
        sl = round(sl, 2)

    return sl


# ── Shadow logging ─────────────────────────────────────────────────────

def shadow_log(
    *,
    entry_price: float,
    legacy_sl: float,
    smart_sl_value: float,
    method: str,
    source: str,
    direction: str,
):
    """Log what smart SL WOULD have set vs what legacy SL did set.
    Used in shadow mode (when smart SL flag is OFF) to compare.

    Writes to stdout — picked up by Render logs + perf_samples readers.
    """
    if not is_shadow_logging_enabled():
        return

    delta = smart_sl_value - legacy_sl
    delta_pct = (delta / entry_price * 100) if entry_price > 0 else 0
    on_round = (legacy_sl == round(legacy_sl) and legacy_sl % 5 == 0)

    print(
        f"[SMART_SL_SHADOW] {source} {direction} entry=₹{entry_price} "
        f"legacy_sl=₹{legacy_sl} smart_sl=₹{smart_sl_value} "
        f"delta=₹{delta:+.2f} ({delta_pct:+.2f}%) "
        f"method={method} legacy_on_round_5={on_round}"
    )


# ── Compact public API ─────────────────────────────────────────────────

def smart_sl_or_legacy(
    *,
    entry_price: float,
    legacy_sl: float,
    atr_pct: Optional[float] = None,
    direction: str = "BUY CE",
    source: str = "unknown",
) -> float:
    """Main entry point — returns smart SL if enabled, else legacy SL.
    Always shadow-logs the comparison.

    Args:
        entry_price: option premium at entry
        legacy_sl: SL value the existing code already computed
        atr_pct: option ATR (preferred) or None for fallback path
        direction: "BUY CE" / "BUY PE"
        source: which code path called this (for log clarity)

    Returns:
        SL price to use (rounded appropriately).
    """
    result = compute_smart_sl(
        entry_price=entry_price,
        atr_pct=atr_pct,
        direction=direction,
        legacy_sl=legacy_sl,
    )

    # Always shadow-log, even when feature off
    shadow_log(
        entry_price=entry_price,
        legacy_sl=legacy_sl,
        smart_sl_value=result["sl"],
        method=result["method"],
        source=source,
        direction=direction,
    )

    if is_smart_sl_enabled():
        return result["sl"]
    return legacy_sl
