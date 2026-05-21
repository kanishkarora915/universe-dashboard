"""
Cross-Asset Lead-Lag Detector.

The Idea:
  NIFTY and BANKNIFTY are highly correlated (~0.85 daily). When one
  moves AHEAD of the other within a short window (30-90 sec), the
  laggard usually catches up.

Example:
  • NIFTY moves +0.3% in last 60 sec
  • BANKNIFTY moved only +0.05% in same window
  • Historical correlation: 0.85 → BANKNIFTY should have moved ~0.25%
  • → BANKNIFTY will likely catch up
  • → ENTER BUY CE on BANKNIFTY (early signal)

  Or reverse:
  • BANKNIFTY moves -0.4%
  • NIFTY hasn't moved much yet
  • → NIFTY will likely catch the down move
  • → ENTER BUY PE on NIFTY

Math:
  expected_correlated_move = leader_move × correlation_coefficient
  divergence = expected_correlated_move - laggard_move
  if abs(divergence) > threshold → fire signal on LAGGARD

Window:
  60-90 seconds (enough time for divergence to be real, not noise)

Limitations:
  Doesn't work in regime breaks (e.g., bank-specific news affecting
  BANKNIFTY but not NIFTY). Add to ensemble, not as standalone.
"""

from __future__ import annotations
import os
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple


# Rolling spot tick history per index
_SPOT_HISTORY: Dict[str, Deque[Tuple[float, float]]] = {
    "NIFTY": deque(maxlen=600),      # ~10 min @ 1 tick/sec
    "BANKNIFTY": deque(maxlen=600),
}

# Historical correlation (typical for Indian indices)
DEFAULT_CORRELATION = 0.85


def is_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_CROSS_ASSET_ENABLED", "off").lower() == "on"


def is_shadow_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_CROSS_ASSET_SHADOW", "on").lower() == "on"


def record_tick(*, idx: str, spot: float, timestamp: Optional[float] = None):
    """Record spot tick for cross-asset comparison."""
    if spot <= 0 or idx not in _SPOT_HISTORY:
        return
    ts = timestamp or time.time()
    _SPOT_HISTORY[idx].append((ts, spot))


def _window_change(
    history: Deque[Tuple[float, float]],
    window_sec: int = 60,
) -> Optional[Dict]:
    """Compute spot change over last N seconds."""
    if len(history) < 2:
        return None
    now = history[-1][0]
    cutoff = now - window_sec
    window_data = [(ts, val) for ts, val in history if ts >= cutoff]
    if len(window_data) < 2:
        return None
    start_ts, start_val = window_data[0]
    end_ts, end_val = window_data[-1]
    if start_val <= 0:
        return None
    return {
        "start": start_val,
        "end": end_val,
        "change": end_val - start_val,
        "change_pct": (end_val - start_val) / start_val * 100,
        "n_ticks": len(window_data),
        "window_sec_actual": end_ts - start_ts,
    }


def detect_divergence(
    *,
    window_sec: int = 60,
    correlation: float = DEFAULT_CORRELATION,
    min_leader_move_pct: float = 0.15,
    min_divergence_pct: float = 0.10,
) -> Optional[Dict]:
    """Detect lead-lag divergence between NIFTY and BANKNIFTY.

    Args:
      window_sec: lookback (default 60s)
      correlation: expected NIFTY↔BANKNIFTY correlation (default 0.85)
      min_leader_move_pct: minimum % move on leader to consider (default 0.15%)
      min_divergence_pct: minimum laggard divergence to fire (default 0.10%)

    Returns signal dict or None.
    """
    n_w = _window_change(_SPOT_HISTORY["NIFTY"], window_sec)
    b_w = _window_change(_SPOT_HISTORY["BANKNIFTY"], window_sec)
    if not n_w or not b_w:
        return None

    n_pct = n_w["change_pct"]
    b_pct = b_w["change_pct"]

    # Identify leader (one with bigger absolute move)
    if abs(n_pct) > abs(b_pct):
        leader_idx, leader_move = "NIFTY", n_pct
        laggard_idx, laggard_move = "BANKNIFTY", b_pct
        leader_w, laggard_w = n_w, b_w
    else:
        leader_idx, leader_move = "BANKNIFTY", b_pct
        laggard_idx, laggard_move = "NIFTY", n_pct
        leader_w, laggard_w = b_w, n_w

    # Only fire if leader actually moved
    if abs(leader_move) < min_leader_move_pct:
        return None

    # Expected laggard move based on correlation
    expected_laggard_move = leader_move * correlation

    # Actual divergence
    divergence = expected_laggard_move - laggard_move

    # Fire only if divergence is significant
    if abs(divergence) < min_divergence_pct:
        return None

    # Direction for LAGGARD trade:
    #  If leader went UP and laggard underperformed → laggard should catch up → BULL
    #  If leader went DOWN and laggard underperformed → laggard should catch down → BEAR
    # The sign of `divergence` matters:
    #   divergence > 0 means laggard is BEHIND leader's direction
    #   → trade laggard in leader's direction
    direction_to_trade = "BULL" if expected_laggard_move > 0 else "BEAR"

    # Confidence: bigger divergence = higher conviction (capped)
    confidence = min(0.85, 0.4 + abs(divergence) * 2)

    rationale = (
        f"Cross-asset lead-lag: {leader_idx} moved {leader_move:+.2f}% in "
        f"{window_sec}s, but {laggard_idx} only moved {laggard_move:+.2f}%. "
        f"Expected (corr {correlation}): {expected_laggard_move:+.2f}%. "
        f"Divergence: {divergence:+.2f}% — {laggard_idx} likely catches up → {direction_to_trade}"
    )

    return {
        "signal": "EARLY_MOVE",
        "detector": "cross_asset",
        "direction": direction_to_trade,
        "confidence": round(confidence, 2),
        "target_index": laggard_idx,  # which index to trade
        "leader_index": leader_idx,
        "rationale": rationale,
        "context": {
            "leader_idx": leader_idx,
            "leader_change_pct": round(leader_move, 3),
            "laggard_idx": laggard_idx,
            "laggard_change_pct": round(laggard_move, 3),
            "expected_laggard_move_pct": round(expected_laggard_move, 3),
            "divergence_pct": round(divergence, 3),
            "correlation_used": correlation,
            "window_sec": window_sec,
            "n_ticks_leader": leader_w["n_ticks"],
            "n_ticks_laggard": laggard_w["n_ticks"],
        },
    }


def shadow_log(signal: Dict, source: str = "engine"):
    if not is_shadow_enabled() or not signal:
        return
    ctx = signal.get("context", {})
    print(
        f"[EARLY_MOVE_CROSS_ASSET] {source} "
        f"target={signal.get('target_index')} dir={signal['direction']} "
        f"leader={ctx.get('leader_idx')} ({ctx.get('leader_change_pct')}%) "
        f"laggard={ctx.get('laggard_change_pct')}% "
        f"divergence={ctx.get('divergence_pct')}% conf={signal['confidence']}"
    )


def check_and_log(
    *,
    window_sec: int = 60,
    min_leader_move_pct: float = 0.15,
    source: str = "engine",
) -> Optional[Dict]:
    """Public API: detect + shadow-log."""
    signal = detect_divergence(
        window_sec=window_sec,
        min_leader_move_pct=min_leader_move_pct,
    )
    if signal:
        shadow_log(signal, source=source)
    return signal


def reset_history():
    for k in _SPOT_HISTORY:
        _SPOT_HISTORY[k].clear()
