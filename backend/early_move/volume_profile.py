"""
Volume Profile Detector — breakout confirmation leading indicator.

WHY THIS EXISTS

Price moving alone means nothing. Price moving WITH VOLUME = real move.
Price moving WITHOUT volume = fakeout that reverses.

60-day audit: many REVERSAL_EXIT losses were FAKEOUT breakouts —
price ticked to a new high, system chased it, no volume behind it,
price snapped back.

THE 4 SIGNALS

  1. VOLUME_BREAKOUT  — new session high/low + volume spike (>2x typical)
     → Real breakout, momentum starting → enter in breakout direction

  2. FAKEOUT_WARNING  — new high/low but volume LOW (<0.6x typical)
     → No conviction behind move → SKIP / fade

  3. VOLUME_EXHAUSTION — huge volume spike then volume collapse
     → Last buyers/sellers exhausted → move ending → exit signal

  4. VOLUME_NODE      — price returning to a high-volume level
     → That level is real support/resistance

HOW WE MEASURE VOLUME

  Index spot has no direct volume. We use a PROXY: the sum of
  ATM±2 option volume (CE + PE traded contracts). Option volume
  tracks underlying activity tightly for index options.

ARCHITECTURE

  Engine feeds (spot, volume_proxy) samples over time.
  Detector builds a price→volume histogram for the session +
  tracks volume velocity for breakout/fakeout/exhaustion.

ENV FLAGS

  EARLY_MOVE_VOLUME_ENABLED=on   activate (default off)
  EARLY_MOVE_VOLUME_SHADOW=on    always log (default on)
"""

from __future__ import annotations
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple


# Per-index tick samples: deque of (timestamp, spot, volume_proxy)
_VOL_HISTORY: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(
    lambda: deque(maxlen=600)  # ~1.5 hour @ 10s
)

# Per-index session price→volume histogram: {idx: {price_bucket: total_volume}}
_PRICE_VOLUME: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))

# Session high/low tracker
_SESSION_EXTREMES: Dict[str, Dict[str, float]] = defaultdict(
    lambda: {"high": 0.0, "low": float("inf")}
)


# Thresholds
DEFAULT_BREAKOUT_VOL_RATIO = 2.0    # volume > 2x typical = real
DEFAULT_FAKEOUT_VOL_RATIO = 0.6    # volume < 0.6x typical = fakeout
DEFAULT_WINDOW_SEC = 120           # 2-min window for volume velocity
DEFAULT_PRICE_BUCKET = 25          # group prices into 25-pt buckets


def is_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_VOLUME_ENABLED", "off").lower() == "on"


def is_shadow_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_VOLUME_SHADOW", "on").lower() == "on"


# ── RECORDING ──────────────────────────────────────────────────────────

def record_tick(
    *,
    idx: str,
    spot: float,
    volume_proxy: float,
    bucket_size: int = DEFAULT_PRICE_BUCKET,
    timestamp: Optional[float] = None,
):
    """Record a spot + volume sample.

    volume_proxy: sum of ATM±2 option volume (CE+PE traded contracts).
                  This is the per-interval activity measure.
    """
    if spot <= 0 or volume_proxy < 0:
        return
    ts = timestamp or time.time()
    _VOL_HISTORY[idx].append((ts, spot, volume_proxy))

    # Update price→volume histogram
    bucket = int(round(spot / bucket_size) * bucket_size)
    _PRICE_VOLUME[idx][bucket] += volume_proxy

    # Update session extremes
    ext = _SESSION_EXTREMES[idx]
    if spot > ext["high"]:
        ext["high"] = spot
    if spot < ext["low"]:
        ext["low"] = spot


# ── HELPERS ────────────────────────────────────────────────────────────

def _typical_volume(history: Deque[Tuple[float, float, float]], window_sec: float) -> float:
    """Average per-tick volume over the lookback window (the 'typical')."""
    if len(history) < 3:
        return 0.0
    now = history[-1][0]
    cutoff = now - window_sec
    vols = [v for ts, _, v in history if ts >= cutoff]
    if len(vols) < 2:
        return 0.0
    return sum(vols) / len(vols)


def _session_typical_volume(
    history: Deque[Tuple[float, float, float]],
    exclude_recent_sec: float = 0,
) -> float:
    """Average per-tick volume across the session.

    If exclude_recent_sec > 0, ticks within that recent window are
    EXCLUDED — this gives the 'normal baseline' before a breakout,
    so the breakout spike isn't diluting its own comparison.
    """
    if len(history) < 3:
        return 0.0
    if exclude_recent_sec > 0:
        now = history[-1][0]
        cutoff = now - exclude_recent_sec
        vols = [v for ts, _, v in history if ts < cutoff]
        if len(vols) >= 2:
            return sum(vols) / len(vols)
        # Not enough pre-window data — fall back to full session
    vols = [v for _, _, v in history]
    return sum(vols) / len(vols)


def _recent_window(
    history: Deque[Tuple[float, float, float]],
    window_sec: float,
) -> List[Tuple[float, float, float]]:
    if not history:
        return []
    now = history[-1][0]
    cutoff = now - window_sec
    return [t for t in history if t[0] >= cutoff]


# ── DETECTION ──────────────────────────────────────────────────────────

def detect_volume_signal(
    *,
    idx: str,
    window_sec: float = DEFAULT_WINDOW_SEC,
    breakout_ratio: float = DEFAULT_BREAKOUT_VOL_RATIO,
    fakeout_ratio: float = DEFAULT_FAKEOUT_VOL_RATIO,
    new_extreme_buffer: float = 5.0,
) -> Optional[Dict]:
    """Detect volume breakout / fakeout / exhaustion.

    Returns signal dict or None.
    """
    history = _VOL_HISTORY.get(idx)
    if not history or len(history) < 5:
        return None

    # Current state
    cur_ts, cur_spot, cur_vol = history[-1]
    recent = _recent_window(history, window_sec)
    if len(recent) < 3:
        return None

    recent_vol_sum = sum(v for _, _, v in recent)
    recent_vol_avg = recent_vol_sum / len(recent)

    # Baseline EXCLUDES the recent window so a breakout spike doesn't
    # dilute its own comparison ratio.
    session_typical = _session_typical_volume(history, exclude_recent_sec=window_sec)
    if session_typical <= 0:
        return None

    vol_ratio = recent_vol_avg / session_typical

    ext = _SESSION_EXTREMES[idx]
    near_high = cur_spot >= (ext["high"] - new_extreme_buffer)
    near_low = cur_spot <= (ext["low"] + new_extreme_buffer)

    # ── SIGNAL 1: VOLUME BREAKOUT ──
    if near_high and vol_ratio >= breakout_ratio:
        confidence = min(0.90, 0.5 + (vol_ratio - breakout_ratio) / 6)
        return {
            "signal": "EARLY_MOVE",
            "detector": "volume_profile",
            "type": "VOLUME_BREAKOUT",
            "idx": idx,
            "direction": "BULL",
            "confidence": round(confidence, 2),
            "rationale": (
                f"VOLUME BREAKOUT on {idx}: new session high ~{cur_spot:.0f} "
                f"with volume {vol_ratio:.1f}x typical. Real momentum — "
                f"breakout confirmed by volume. → BULL"
            ),
            "context": {
                "spot": round(cur_spot, 1),
                "session_high": round(ext["high"], 1),
                "volume_ratio": round(vol_ratio, 2),
            },
        }

    if near_low and vol_ratio >= breakout_ratio:
        confidence = min(0.90, 0.5 + (vol_ratio - breakout_ratio) / 6)
        return {
            "signal": "EARLY_MOVE",
            "detector": "volume_profile",
            "type": "VOLUME_BREAKOUT",
            "idx": idx,
            "direction": "BEAR",
            "confidence": round(confidence, 2),
            "rationale": (
                f"VOLUME BREAKDOWN on {idx}: new session low ~{cur_spot:.0f} "
                f"with volume {vol_ratio:.1f}x typical. Real momentum — "
                f"breakdown confirmed by volume. → BEAR"
            ),
            "context": {
                "spot": round(cur_spot, 1),
                "session_low": round(ext["low"], 1),
                "volume_ratio": round(vol_ratio, 2),
            },
        }

    # ── SIGNAL 2: FAKEOUT WARNING ──
    if (near_high or near_low) and vol_ratio <= fakeout_ratio:
        which = "high" if near_high else "low"
        return {
            "signal": "EARLY_MOVE",
            "detector": "volume_profile",
            "type": "FAKEOUT_WARNING",
            "idx": idx,
            "direction": "AVOID",
            "confidence": round(min(0.85, 0.5 + (fakeout_ratio - vol_ratio)), 2),
            "rationale": (
                f"FAKEOUT WARNING on {idx}: price at session {which} "
                f"~{cur_spot:.0f} but volume only {vol_ratio:.1f}x typical. "
                f"No conviction — likely fakeout, will reverse. → SKIP/FADE"
            ),
            "context": {
                "spot": round(cur_spot, 1),
                "at_extreme": which,
                "volume_ratio": round(vol_ratio, 2),
            },
        }

    return None


def detect_exhaustion(
    *,
    idx: str,
    spike_window_sec: float = 60,
    collapse_window_sec: float = 60,
) -> Optional[Dict]:
    """Detect volume exhaustion — huge spike then collapse = move ending."""
    history = _VOL_HISTORY.get(idx)
    if not history or len(history) < 8:
        return None

    now = history[-1][0]
    # Recent collapse window
    collapse = [v for ts, _, v in history if ts >= now - collapse_window_sec]
    # Spike window just before
    spike = [
        v for ts, _, v in history
        if now - collapse_window_sec - spike_window_sec <= ts < now - collapse_window_sec
    ]
    if len(collapse) < 2 or len(spike) < 2:
        return None

    spike_avg = sum(spike) / len(spike)
    collapse_avg = sum(collapse) / len(collapse)
    session_typical = _session_typical_volume(history)
    if session_typical <= 0 or spike_avg <= 0:
        return None

    # Exhaustion = spike was >2.5x typical AND collapsed to <0.7x of spike
    spike_ratio = spike_avg / session_typical
    collapse_ratio = collapse_avg / spike_avg

    if spike_ratio >= 2.5 and collapse_ratio <= 0.5:
        return {
            "signal": "EARLY_MOVE",
            "detector": "volume_profile",
            "type": "VOLUME_EXHAUSTION",
            "idx": idx,
            "direction": "EXIT",  # signals move ending, not a new entry
            "confidence": round(min(0.85, 0.5 + spike_ratio / 10), 2),
            "rationale": (
                f"VOLUME EXHAUSTION on {idx}: volume spiked {spike_ratio:.1f}x "
                f"typical then collapsed to {collapse_ratio*100:.0f}% of spike. "
                f"Last buyers/sellers exhausted — move ending. → EXIT signal"
            ),
            "context": {
                "spike_ratio": round(spike_ratio, 2),
                "collapse_ratio": round(collapse_ratio, 2),
            },
        }
    return None


def get_volume_nodes(idx: str, top_n: int = 3) -> List[Dict]:
    """Return the highest-volume price levels (real support/resistance)."""
    pv = _PRICE_VOLUME.get(idx, {})
    if not pv:
        return []
    ranked = sorted(pv.items(), key=lambda kv: -kv[1])[:top_n]
    total = sum(pv.values()) or 1
    return [
        {"price": price, "volume": round(vol, 0), "pct_of_session": round(vol / total * 100, 1)}
        for price, vol in ranked
    ]


def detect_all(*, idx: str) -> Dict:
    """Run all volume sub-detectors, return combined result."""
    signals = []
    vs = detect_volume_signal(idx=idx)
    if vs:
        signals.append(vs)
    ex = detect_exhaustion(idx=idx)
    if ex:
        signals.append(ex)

    return {
        "idx": idx,
        "signals": signals,
        "signal_count": len(signals),
        "volume_nodes": get_volume_nodes(idx),
        "top_confidence": max((s["confidence"] for s in signals), default=0.0),
    }


def shadow_log(result: Dict, source: str = "engine"):
    if not is_shadow_enabled() or not result.get("signals"):
        return
    for s in result["signals"]:
        print(
            f"[EARLY_MOVE_VOLUME] {source} {s['idx']} {s['type']} "
            f"dir={s['direction']} conf={s['confidence']} — {s['rationale'][:110]}"
        )


def check_and_log(*, idx: str, source: str = "engine") -> Dict:
    """Public API — detect + shadow-log."""
    result = detect_all(idx=idx)
    if result.get("signals"):
        shadow_log(result, source=source)
    return result


def get_history_size() -> Dict[str, int]:
    return {k: len(v) for k, v in _VOL_HISTORY.items()}


def reset_history():
    _VOL_HISTORY.clear()
    _PRICE_VOLUME.clear()
    _SESSION_EXTREMES.clear()
