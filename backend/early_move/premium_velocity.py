"""
Premium Velocity Divergence Detector.

The Idea:
  When option premium moves FASTER than spot can justify via delta,
  it's institutional positioning leak. Premium "knows" about move
  BEFORE spot reflects it.

Example:
  • NIFTY spot moves +5 pts in 60 sec (normal)
  • Expected ATM CE premium move: +2.5 pts (delta ≈ 0.5)
  • Actual ATM CE premium move: +12 pts (5x expected)
  • → Someone is BUYING CE aggressively
  • → Institutional positioning detected
  • → Move is about to accelerate
  • → ENTER BUY CE now (early signal)

Detector Math:
  ratio = abs(premium_change) / (abs(spot_change) × delta)

  ratio > 1.5  → moderate divergence (institutional building)
  ratio > 2.0  → strong divergence (clear leak)
  ratio > 3.0  → extreme (act fast)

Window:
  Compare 60-90 second windows (not too short = noise, not too long = late)

This is a LEADING indicator — fires before confluence engines align.
"""

from __future__ import annotations
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


# Rolling tick history per (idx, strike, side)
# Each entry: (timestamp, premium_ltp, spot_ltp)
_TICK_HISTORY: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(
    lambda: deque(maxlen=300)  # ~5 min @ 1 tick/sec
)


def is_enabled() -> bool:
    """Default OFF — needs validation before live."""
    return os.environ.get("EARLY_MOVE_VELOCITY_ENABLED", "off").lower() == "on"


def is_shadow_enabled() -> bool:
    """Shadow log even when off — see what WOULD fire."""
    return os.environ.get("EARLY_MOVE_VELOCITY_SHADOW", "on").lower() == "on"


def _key(idx: str, strike: int, side: str) -> str:
    return f"{idx}|{strike}|{side}"


def record_tick(
    *,
    idx: str,
    strike: int,
    side: str,        # "CE" or "PE"
    premium: float,
    spot: float,
    timestamp: Optional[float] = None,
):
    """Record a single tick. Call from tick stream / engine loop.

    Maintains rolling window for divergence calc.
    """
    if premium <= 0 or spot <= 0:
        return
    ts = timestamp or time.time()
    k = _key(idx, strike, side)
    _TICK_HISTORY[k].append((ts, premium, spot))


def _window_change(
    history: Deque[Tuple[float, float, float]],
    window_sec: int = 60,
) -> Optional[Dict]:
    """Compute changes over last N seconds.

    Returns dict {
      "premium_start": float,
      "premium_end": float,
      "premium_change": float,
      "spot_start": float,
      "spot_end": float,
      "spot_change": float,
      "n_ticks": int,
      "window_sec_actual": float,
    } or None if insufficient data.
    """
    if len(history) < 2:
        return None

    now = history[-1][0]
    cutoff = now - window_sec

    # Find oldest tick within window
    start_idx = 0
    for i, (ts, _, _) in enumerate(history):
        if ts >= cutoff:
            start_idx = i
            break

    window = list(history)[start_idx:]
    if len(window) < 2:
        return None

    start_ts, start_prem, start_spot = window[0]
    end_ts, end_prem, end_spot = window[-1]

    return {
        "premium_start": start_prem,
        "premium_end": end_prem,
        "premium_change": end_prem - start_prem,
        "spot_start": start_spot,
        "spot_end": end_spot,
        "spot_change": end_spot - start_spot,
        "n_ticks": len(window),
        "window_sec_actual": end_ts - start_ts,
    }


def detect_divergence(
    *,
    idx: str,
    strike: int,
    side: str,
    delta: float,
    window_sec: int = 60,
    min_ratio: float = 1.5,
    min_premium_change_pct: float = 1.0,
) -> Optional[Dict]:
    """Detect premium velocity divergence in the most recent window.

    Args:
      idx, strike, side: which option
      delta: option's current delta (from BS — should be 0.3-0.7 for sensible signal)
      window_sec: lookback window (default 60s)
      min_ratio: minimum (premium_change / expected) to fire (default 1.5x)
      min_premium_change_pct: minimum premium move to consider (filter noise)

    Returns:
      dict with signal info, or None if no divergence.

    Signal structure:
      {"signal": "EARLY_MOVE",
       "direction": "BULL" | "BEAR",
       "confidence": 0.0-1.0,
       "rationale": str,
       "context": {...all numbers...}}
    """
    k = _key(idx, strike, side)
    history = _TICK_HISTORY.get(k)
    if not history or len(history) < 3:
        return None

    w = _window_change(history, window_sec=window_sec)
    if not w:
        return None

    premium_change = w["premium_change"]
    spot_change = w["spot_change"]
    premium_start = w["premium_start"]

    # Don't fire on tiny moves
    if premium_start <= 0:
        return None
    premium_change_pct = abs(premium_change) / premium_start * 100
    if premium_change_pct < min_premium_change_pct:
        return None

    # Expected premium change given spot move and delta
    abs_delta = abs(delta) if delta else 0.5
    if abs_delta < 0.15:
        # Way OTM — delta too small for meaningful signal
        return None

    expected_premium_change = spot_change * abs_delta
    actual_change = premium_change

    # If spot didn't really move but premium did → that's the signal
    if abs(expected_premium_change) < 0.01:
        # Pure premium move, no spot move = pure positioning
        ratio = abs(actual_change) / 0.01  # use small denominator
        ratio = min(ratio, 10.0)  # cap
    else:
        ratio = abs(actual_change) / abs(expected_premium_change)

    if ratio < min_ratio:
        return None

    # Direction:
    #  CE premium UP   → bullish (spot will go up)
    #  CE premium DOWN → bearish
    #  PE premium UP   → bearish (spot will go down)
    #  PE premium DOWN → bullish
    is_premium_up = premium_change > 0
    if side == "CE":
        direction = "BULL" if is_premium_up else "BEAR"
    else:  # PE
        direction = "BEAR" if is_premium_up else "BULL"

    # Confidence scales with ratio (1.5→0.5, 2.0→0.7, 3.0→0.9, >3.0→0.95)
    if ratio >= 3.0:
        confidence = 0.95
    elif ratio >= 2.0:
        confidence = 0.7 + (ratio - 2.0) * 0.25
    elif ratio >= 1.5:
        confidence = 0.5 + (ratio - 1.5) * 0.4
    else:
        confidence = 0.5
    confidence = min(0.95, confidence)

    rationale = (
        f"Premium velocity divergence: {side} premium moved "
        f"{premium_change:+.2f} ({premium_change_pct:.1f}%) while spot moved "
        f"{spot_change:+.2f} pts in {w['window_sec_actual']:.0f}s. "
        f"Expected premium change ~{expected_premium_change:+.2f} (Δ={abs_delta:.2f}); "
        f"actual is {ratio:.1f}× expected → institutional positioning detected → {direction}."
    )

    return {
        "signal": "EARLY_MOVE",
        "detector": "premium_velocity",
        "direction": direction,
        "confidence": round(confidence, 2),
        "rationale": rationale,
        "context": {
            "idx": idx,
            "strike": strike,
            "side": side,
            "delta": abs_delta,
            "window_sec": w["window_sec_actual"],
            "n_ticks": w["n_ticks"],
            "premium_change": round(premium_change, 2),
            "premium_change_pct": round(premium_change_pct, 2),
            "spot_change": round(spot_change, 2),
            "expected_premium_change": round(expected_premium_change, 2),
            "ratio": round(ratio, 2),
            "min_ratio_threshold": min_ratio,
        },
    }


def shadow_log(signal: Dict, source: str = "engine"):
    """Print signal to stdout (Render logs pick this up)."""
    if not is_shadow_enabled():
        return
    if not signal:
        return
    ctx = signal.get("context", {})
    print(
        f"[EARLY_MOVE_VELOCITY] {source} {ctx.get('idx')} {ctx.get('strike')} "
        f"{ctx.get('side')} → {signal['direction']} "
        f"ratio={ctx.get('ratio')} confidence={signal['confidence']} "
        f"premium_change={ctx.get('premium_change')} spot_change={ctx.get('spot_change')}"
    )


def check_and_log(
    *,
    idx: str,
    strike: int,
    side: str,
    delta: float,
    window_sec: int = 60,
    min_ratio: float = 1.5,
    source: str = "engine",
) -> Optional[Dict]:
    """Public API: detect + always shadow-log.

    Returns the signal dict (or None). Caller decides what to do
    based on is_enabled() — typically: only act on signal when feature on,
    otherwise just log for shadow analysis.
    """
    signal = detect_divergence(
        idx=idx, strike=strike, side=side, delta=delta,
        window_sec=window_sec, min_ratio=min_ratio,
    )
    if signal:
        shadow_log(signal, source=source)
    return signal


def get_history_size() -> Dict[str, int]:
    """Diagnostic: how much tick history we have per key."""
    return {k: len(v) for k, v in _TICK_HISTORY.items()}


def reset_history(key: Optional[str] = None):
    """Reset tick history (for testing or daily reset)."""
    if key:
        _TICK_HISTORY.pop(key, None)
    else:
        _TICK_HISTORY.clear()
