"""
Peak-Trail SL + Stuck-Profit Detector
─────────────────────────────────────
Two related improvements bundled into one module:

#3 PEAK-TRAIL SL
   Trails SL based on PEAK premium (not entry). Higher peaks earn more
   pullback room — a +30% winner shouldn't be killed by a 5% wiggle.

#4 STUCK-PROFIT AUTO-TIGHTEN
   If peak hasn't advanced in N minutes, tighten SL aggressively.
   Theta starts eating idle profit — lock it before it leaks.

Activates only when peak ≥ +10%. Below that, existing entry-based
ladders (Smart SL, Profit-Trail) keep handling. This module is purely
additive: it returns a candidate SL; callers pick max() vs other layers.

Per-trade in-memory tracker (no DB writes — cheap).
"""

import time
from typing import Dict, Optional, Tuple, List


# ── In-memory per-trade peak state ──
# sid (e.g. "SCALPER:123" or "MAIN:456") -> {"peak_premium", "peak_ts"}
_peak_tracker: Dict[str, Dict] = {}


# ── Peak pullback ladder ──
# (peak_profit_threshold_pct, sl_multiplier_of_peak)
# Higher peaks = more pullback room (avoid whipsaws on big runners).
# +10% peak → SL = peak × 0.93 (7% pullback allowed)
# +50% peak → SL = peak × 0.85 (15% pullback — let it breathe)
PEAK_PULLBACK_LADDER: List[Tuple[float, float]] = [
    (10.0, 0.93),
    (15.0, 0.92),
    (20.0, 0.90),
    (30.0, 0.88),
    (50.0, 0.85),
]


# ── Stuck-profit ladder ──
# (minutes_since_peak, sl_multiplier_of_peak)
# Peak hasn't moved → tighten more as time passes.
# 3 min flat  → SL = peak × 0.95
# 7 min flat  → SL = peak × 0.97
# 12 min flat → SL = peak × 0.98 (very tight — exit incoming)
STUCK_PROFIT_LADDER: List[Tuple[float, float]] = [
    (3.0,  0.95),
    (7.0,  0.97),
    (12.0, 0.98),
]


# Activation threshold — peak must reach this before module engages
PEAK_ACTIVATION_PCT = 10.0


def _peak_pullback_sl(peak_premium: float, peak_profit_pct: float) -> float:
    """SL based purely on peak height (no time component)."""
    pullback = 0.93  # default
    for threshold, mult in PEAK_PULLBACK_LADDER:
        if peak_profit_pct >= threshold:
            pullback = mult
    return round(peak_premium * pullback, 2)


def _stuck_profit_sl(peak_premium: float,
                     minutes_since_peak: float) -> Optional[float]:
    """If peak hasn't advanced for N min, tighten. Returns None if not stuck yet."""
    if minutes_since_peak < STUCK_PROFIT_LADDER[0][0]:
        return None
    mult = None
    for threshold, m in STUCK_PROFIT_LADDER:
        if minutes_since_peak >= threshold:
            mult = m
    if mult is None:
        return None
    return round(peak_premium * mult, 2)


def update_peak(sid: str, current_premium: float) -> Tuple[float, float]:
    """Ratchet up peak. Returns (peak_premium, minutes_since_peak)."""
    now = time.time()
    state = _peak_tracker.setdefault(sid, {
        "peak_premium": float(current_premium),
        "peak_ts": now,
    })
    if current_premium > state["peak_premium"]:
        state["peak_premium"] = float(current_premium)
        state["peak_ts"] = now
    minutes_since = (now - state["peak_ts"]) / 60.0
    return state["peak_premium"], minutes_since


def compute_peak_trail_sl(
    sid: str,
    entry_price: float,
    current_premium: float,
) -> Optional[Dict]:
    """
    Compute peak-aware trailing SL for a trade.

    Returns:
      None — when peak < activation threshold (+10%); existing ladders manage
      Dict — {new_sl, peak_premium, peak_profit_pct, minutes_since_peak,
              source ('PULLBACK' or 'STUCK'), reason}
    """
    if entry_price <= 0 or current_premium <= 0:
        return None

    peak, mins_since = update_peak(sid, current_premium)
    peak_profit_pct = (peak - entry_price) / entry_price * 100

    if peak_profit_pct < PEAK_ACTIVATION_PCT:
        return None

    pullback_sl = _peak_pullback_sl(peak, peak_profit_pct)
    stuck_sl = _stuck_profit_sl(peak, mins_since)

    # Pick TIGHTER of the two (higher SL wins — closer to current price)
    if stuck_sl is not None and stuck_sl > pullback_sl:
        new_sl = stuck_sl
        source = "STUCK"
        reason = (f"Peak-trail STUCK: peak +{peak_profit_pct:.1f}% flat for "
                  f"{mins_since:.1f}m → SL @ ₹{new_sl:.2f}")
    else:
        new_sl = pullback_sl
        source = "PULLBACK"
        pullback_pct = (peak - new_sl) / peak * 100
        reason = (f"Peak-trail: peak +{peak_profit_pct:.1f}% (₹{peak:.2f}) "
                  f"→ SL @ ₹{new_sl:.2f} ({pullback_pct:.1f}% pullback)")

    return {
        "new_sl": new_sl,
        "peak_premium": peak,
        "peak_profit_pct": round(peak_profit_pct, 2),
        "minutes_since_peak": round(mins_since, 2),
        "source": source,
        "reason": reason,
    }


def cleanup(sid: str):
    """Drop tracker entry for a closed trade."""
    _peak_tracker.pop(sid, None)


def get_state(sid: str) -> Optional[Dict]:
    """Snapshot of tracker state for API/UI exposure."""
    s = _peak_tracker.get(sid)
    if not s:
        return None
    return {
        "peak_premium": s["peak_premium"],
        "peak_ts": s["peak_ts"],
        "minutes_since_peak": round((time.time() - s["peak_ts"]) / 60.0, 2),
    }


def get_all_states() -> Dict[str, Dict]:
    """All tracked trades (for diagnostics)."""
    now = time.time()
    return {
        sid: {
            "peak_premium": s["peak_premium"],
            "minutes_since_peak": round((now - s["peak_ts"]) / 60.0, 2),
        }
        for sid, s in _peak_tracker.items()
    }
