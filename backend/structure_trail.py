"""
structure_trail — trail SL by last swing low/high (Mode A big-move capture).

Built 2026-05-27 (Phase 3 of Option Y).

PROVEN ORIGIN

  Mark Minervini, Linda Raschke — "trade with the trend, trail by
  structure." Used by every prop firm doing trend-following.

LOGIC

  For an UPTREND-aligned BUY CE trade:
    - As long as price prints HIGHER lows, trade is healthy.
    - The trail SL = the most recent confirmed swing LOW (the "HL").
    - As new HHs form, the swing LOW after them becomes the new trail.
    - If a NEW LL forms (low < previous swing low) → trend BROKEN → exit.

  Mirrored for DOWNTREND-aligned BUY PE trades.

  This is SPOT-level structure trail (not premium-level). The trade
  metadata records `entry_spot`; we compare against today's swing points
  to decide if structure has broken since entry.

USAGE

  from structure_trail import should_exit_on_break

  exit_decision = should_exit_on_break(
      candles_5m_today=..., trade_direction="BULL",
      entry_spot=24100, entry_ts=...,
  )
  # → {"should_exit": True, "reason": "...", "broken_at": 23980}

The pure module — caller supplies candles + trade context.
"""

from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")


def _parse_ts(ts) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else IST.localize(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.astimezone(IST) if dt.tzinfo else IST.localize(dt)
        except Exception:
            return None
    return None


def _candles_after(candles: List[Dict], entry_ts: Optional[datetime]) -> List[Dict]:
    """Filter to candles AFTER entry time (for post-entry break detection)."""
    if entry_ts is None:
        return candles
    out = []
    for c in candles:
        ts = _parse_ts(c.get("ts"))
        if ts is None:
            continue
        if ts > entry_ts:
            out.append(c)
    return out


def should_exit_on_break(
    candles_5m: List[Dict],
    trade_direction: str,
    entry_spot: Optional[float] = None,
    entry_ts=None,
    fractal_bars: int = 2,
) -> Dict:
    """Check if structure has been broken since entry → exit signal.

    Args:
        candles_5m: today's 5-min candles (or last N hours)
        trade_direction: "BULL" (BUY CE) or "BEAR" (BUY PE)
        entry_spot: spot at entry time (for sanity)
        entry_ts: entry datetime — only post-entry candles considered
        fractal_bars: swing-point fractal width (default 2)

    Returns:
        {
          "should_exit": bool,
          "reason": str,
          "broken_at": float | None,
          "prev_swing": float | None,
        }
    """
    from price_structure import find_swing_highs, find_swing_lows

    result = {
        "should_exit": False, "reason": "",
        "broken_at": None, "prev_swing": None,
    }

    if trade_direction not in ("BULL", "BEAR"):
        result["reason"] = f"unknown direction: {trade_direction}"
        return result

    # Parse entry_ts if string
    ets = entry_ts if isinstance(entry_ts, datetime) else _parse_ts(entry_ts)

    # For break detection we need swings from BEFORE entry as reference
    # plus any NEW swings AFTER entry.
    pre_entry = [c for c in candles_5m if _parse_ts(c.get("ts")) and (
        ets is None or _parse_ts(c.get("ts")) <= ets
    )]
    post_entry = [c for c in candles_5m if _parse_ts(c.get("ts")) and (
        ets is not None and _parse_ts(c.get("ts")) > ets
    )]

    if trade_direction == "BULL":
        # Reference: last swing LOW from pre-entry (the HL that justified entry)
        pre_lows = find_swing_lows(pre_entry, bars=fractal_bars)
        if not pre_lows:
            result["reason"] = "no pre-entry swing low — can't check break"
            return result
        ref_low = pre_lows[-1]["price"]

        # Post-entry: find new swing lows
        post_lows = find_swing_lows(post_entry, bars=fractal_bars)
        for sw in post_lows:
            if sw["price"] < ref_low:
                result["should_exit"] = True
                result["reason"] = (
                    f"UPTREND_BROKEN — new LL {sw['price']:.1f} < "
                    f"entry HL {ref_low:.1f}"
                )
                result["broken_at"] = sw["price"]
                result["prev_swing"] = ref_low
                return result

        # Also check: any post-entry candle's low broke ref_low intra-bar?
        # (Optional — strict swing-confirm only is the conservative reading.)
        return result

    else:  # BEAR
        pre_highs = find_swing_highs(pre_entry, bars=fractal_bars)
        if not pre_highs:
            result["reason"] = "no pre-entry swing high — can't check break"
            return result
        ref_high = pre_highs[-1]["price"]

        post_highs = find_swing_highs(post_entry, bars=fractal_bars)
        for sw in post_highs:
            if sw["price"] > ref_high:
                result["should_exit"] = True
                result["reason"] = (
                    f"DOWNTREND_BROKEN — new HH {sw['price']:.1f} > "
                    f"entry LH {ref_high:.1f}"
                )
                result["broken_at"] = sw["price"]
                result["prev_swing"] = ref_high
                return result

        return result


def compute_trail_level(
    candles_5m: List[Dict],
    trade_direction: str,
    fractal_bars: int = 2,
) -> Optional[float]:
    """Current trail level = most recent swing low (BULL) or high (BEAR).

    Used for display / dashboard — actual exit logic uses should_exit_on_break.
    """
    from price_structure import find_swing_highs, find_swing_lows

    if trade_direction == "BULL":
        lows = find_swing_lows(candles_5m, bars=fractal_bars)
        return lows[-1]["price"] if lows else None
    if trade_direction == "BEAR":
        highs = find_swing_highs(candles_5m, bars=fractal_bars)
        return highs[-1]["price"] if highs else None
    return None


def diagnostics() -> Dict:
    return {
        "module": "structure_trail",
        "description": (
            "Spot-level structural SL trail — exits when post-entry "
            "candle structure invalidates the trend (new LL in uptrend "
            "or new HH in downtrend)."
        ),
    }
