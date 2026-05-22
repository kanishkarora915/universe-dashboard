"""
IV Term Structure Detector — volatility-timing leading indicator.

WHY THIS EXISTS

IV (implied volatility) is FORWARD-LOOKING by nature — it's the market's
expectation of future movement. When IV expands, a big move is being
priced in BEFORE it happens. That's a leading signal.

THE 3 SIGNALS

  1. IV EXPANSION — near-month ATM IV rising fast (>X% in N min)
     → Big move being priced in → position BEFORE the move

  2. IV CRUSH — IV falling fast
     → Move expected to be over / event passed
     → DON'T buy options (theta + vega both against you)

  3. IV INVERSION — near-month IV > next-month IV (term structure flip)
     → Market expects imminent volatility (this expiry)
     → Strong "move coming soon" signal

WHY IT MATTERS FOR OPTION BUYERS

  Buying when IV is EXPANDING  → vega works FOR you (premium grows)
  Buying when IV is CRUSHING   → vega works AGAINST you (premium shrinks
                                  even if direction is right)

  60-day audit: 19 VELOCITY_EXIT trades lost ₹212k — many were buys
  into IV crush. This detector flags that BEFORE entry.

ARCHITECTURE

  Engine feeds ATM IV samples over time via record_iv().
  Detector computes IV velocity + (optionally) term-structure spread.

ENV FLAGS

  EARLY_MOVE_IV_ENABLED=on    activate (default off)
  EARLY_MOVE_IV_SHADOW=on     always log (default on)

NOTE: IV detector is DIRECTION-AGNOSTIC. It tells you a move is COMING
and whether vega is friendly. Direction comes from other detectors
(oi_rotation, premium_velocity). The aggregator combines them.
"""

from __future__ import annotations
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple


# IV samples per index: deque of (timestamp, atm_iv_pct)
_IV_HISTORY: Dict[str, Deque[Tuple[float, float]]] = defaultdict(
    lambda: deque(maxlen=360)  # ~1 hour @ 10s intervals
)

# Term-structure samples: deque of (timestamp, near_iv, next_iv)
_TERM_HISTORY: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(
    lambda: deque(maxlen=360)
)


# Thresholds
DEFAULT_EXPANSION_PCT = 15.0   # IV up >15% relative in window = expansion
DEFAULT_CRUSH_PCT = 12.0       # IV down >12% relative = crush
DEFAULT_WINDOW_SEC = 600       # 10-min window


def is_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_IV_ENABLED", "off").lower() == "on"


def is_shadow_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_IV_SHADOW", "on").lower() == "on"


# ── RECORDING ──────────────────────────────────────────────────────────

def record_iv(*, idx: str, atm_iv: float, timestamp: Optional[float] = None):
    """Record near-month ATM IV sample. Call periodically from engine."""
    if atm_iv <= 0:
        return
    ts = timestamp or time.time()
    _IV_HISTORY[idx].append((ts, atm_iv))


def record_term_structure(
    *,
    idx: str,
    near_iv: float,
    next_iv: float,
    timestamp: Optional[float] = None,
):
    """Record near-month + next-month ATM IV for term-structure analysis."""
    if near_iv <= 0 or next_iv <= 0:
        return
    ts = timestamp or time.time()
    _TERM_HISTORY[idx].append((ts, near_iv, next_iv))


# ── WINDOW HELPERS ─────────────────────────────────────────────────────

def _iv_window_change(
    history: Deque[Tuple[float, float]],
    window_sec: float,
) -> Optional[Dict]:
    """Compute IV change over last N seconds."""
    if len(history) < 2:
        return None
    now = history[-1][0]
    cutoff = now - window_sec
    window = [(ts, iv) for ts, iv in history if ts >= cutoff]
    if len(window) < 2:
        return None
    start_ts, start_iv = window[0]
    end_ts, end_iv = window[-1]
    if start_iv <= 0:
        return None
    return {
        "start_iv": start_iv,
        "end_iv": end_iv,
        "abs_change": end_iv - start_iv,
        "rel_change_pct": (end_iv - start_iv) / start_iv * 100,
        "window_sec_actual": end_ts - start_ts,
        "n_samples": len(window),
    }


# ── DETECTION ──────────────────────────────────────────────────────────

def detect_iv_signal(
    *,
    idx: str,
    window_sec: float = DEFAULT_WINDOW_SEC,
    expansion_pct: float = DEFAULT_EXPANSION_PCT,
    crush_pct: float = DEFAULT_CRUSH_PCT,
) -> Optional[Dict]:
    """Detect IV expansion / crush / inversion for the given index.

    Returns signal dict or None. The signal is DIRECTION-AGNOSTIC for
    expansion/crush (tells you a move is coming + vega friendliness).
    Inversion gets "BULLISH"-leaning-neutral because IV inversion alone
    doesn't pick direction — but vega-favorable.

    Signal structure:
      {"signal": "EARLY_MOVE",
       "detector": "iv_term_structure",
       "type": "IV_EXPANSION" | "IV_CRUSH" | "IV_INVERSION",
       "direction": "NEUTRAL" | "AVOID",   # AVOID = don't buy (crush)
       "confidence": 0.0-1.0,
       "vega_friendly": bool,
       "rationale": str,
       "context": {...}}
    """
    iv_history = _IV_HISTORY.get(idx)
    if not iv_history or len(iv_history) < 3:
        return None

    w = _iv_window_change(iv_history, window_sec)
    if not w:
        return None

    rel = w["rel_change_pct"]

    # ── IV EXPANSION ──
    if rel >= expansion_pct:
        confidence = min(0.90, 0.5 + (rel - expansion_pct) / 40)
        return {
            "signal": "EARLY_MOVE",
            "detector": "iv_term_structure",
            "type": "IV_EXPANSION",
            "idx": idx,
            "direction": "NEUTRAL",   # move coming, direction TBD by other detectors
            "confidence": round(confidence, 2),
            "vega_friendly": True,
            "rationale": (
                f"IV EXPANSION on {idx}: ATM IV rose {w['start_iv']:.1f}% → "
                f"{w['end_iv']:.1f}% (+{rel:.0f}% relative) in "
                f"{w['window_sec_actual']:.0f}s. Big move being priced in — "
                f"position BEFORE it. Vega is FRIENDLY for option buyers."
            ),
            "context": {
                "start_iv": round(w["start_iv"], 2),
                "end_iv": round(w["end_iv"], 2),
                "rel_change_pct": round(rel, 1),
                "window_sec": round(w["window_sec_actual"], 0),
            },
        }

    # ── IV CRUSH ──
    if rel <= -crush_pct:
        confidence = min(0.90, 0.5 + (abs(rel) - crush_pct) / 40)
        return {
            "signal": "EARLY_MOVE",
            "detector": "iv_term_structure",
            "type": "IV_CRUSH",
            "idx": idx,
            "direction": "AVOID",   # don't buy options into crush
            "confidence": round(confidence, 2),
            "vega_friendly": False,
            "rationale": (
                f"IV CRUSH on {idx}: ATM IV fell {w['start_iv']:.1f}% → "
                f"{w['end_iv']:.1f}% ({rel:.0f}% relative) in "
                f"{w['window_sec_actual']:.0f}s. Move expected over / event "
                f"passed. DON'T buy options — vega works AGAINST you "
                f"(premium shrinks even if direction is right)."
            ),
            "context": {
                "start_iv": round(w["start_iv"], 2),
                "end_iv": round(w["end_iv"], 2),
                "rel_change_pct": round(rel, 1),
                "window_sec": round(w["window_sec_actual"], 0),
            },
        }

    return None


def detect_inversion(*, idx: str) -> Optional[Dict]:
    """Detect IV term-structure inversion (near-month IV > next-month IV).

    Normal: next-month IV >= near-month IV (more time = more uncertainty).
    Inverted: near-month IV > next-month → market expects imminent vol.

    Returns signal dict or None.
    """
    term = _TERM_HISTORY.get(idx)
    if not term:
        return None
    ts, near_iv, next_iv = term[-1]
    if next_iv <= 0:
        return None

    # Inversion: near > next by meaningful margin
    spread = near_iv - next_iv
    spread_pct = spread / next_iv * 100

    if spread_pct >= 5.0:  # near-month IV at least 5% above next-month
        confidence = min(0.85, 0.5 + spread_pct / 30)
        return {
            "signal": "EARLY_MOVE",
            "detector": "iv_term_structure",
            "type": "IV_INVERSION",
            "idx": idx,
            "direction": "NEUTRAL",  # imminent move, direction from other detectors
            "confidence": round(confidence, 2),
            "vega_friendly": True,
            "rationale": (
                f"IV INVERSION on {idx}: near-month IV {near_iv:.1f}% > "
                f"next-month IV {next_iv:.1f}% (spread +{spread_pct:.0f}%). "
                f"Market pricing imminent volatility THIS expiry. "
                f"Big move expected soon — position now."
            ),
            "context": {
                "near_iv": round(near_iv, 2),
                "next_iv": round(next_iv, 2),
                "spread_pct": round(spread_pct, 1),
            },
        }
    return None


def detect_all(*, idx: str) -> Dict:
    """Run both IV velocity + inversion detectors, return combined result."""
    signals = []
    vel = detect_iv_signal(idx=idx)
    if vel:
        signals.append(vel)
    inv = detect_inversion(idx=idx)
    if inv:
        signals.append(inv)

    # Determine vega-friendliness (key output for option buyers)
    vega_friendly = None
    if any(s["type"] == "IV_CRUSH" for s in signals):
        vega_friendly = False
    elif any(s["type"] in ("IV_EXPANSION", "IV_INVERSION") for s in signals):
        vega_friendly = True

    return {
        "idx": idx,
        "signals": signals,
        "signal_count": len(signals),
        "vega_friendly": vega_friendly,
        "top_confidence": max((s["confidence"] for s in signals), default=0.0),
    }


def shadow_log(result: Dict, source: str = "engine"):
    if not is_shadow_enabled() or not result.get("signals"):
        return
    for s in result["signals"]:
        print(
            f"[EARLY_MOVE_IV] {source} {s['idx']} {s['type']} "
            f"conf={s['confidence']} vega_friendly={s.get('vega_friendly')} "
            f"— {s['rationale'][:120]}"
        )


def check_and_log(*, idx: str, source: str = "engine") -> Dict:
    """Public API — detect + shadow log."""
    result = detect_all(idx=idx)
    if result.get("signals"):
        shadow_log(result, source=source)
    return result


def get_history_size() -> Dict[str, int]:
    return {
        **{f"iv|{k}": len(v) for k, v in _IV_HISTORY.items()},
        **{f"term|{k}": len(v) for k, v in _TERM_HISTORY.items()},
    }


def reset_history():
    _IV_HISTORY.clear()
    _TERM_HISTORY.clear()
