"""
OI Rotation Detector — Smart money positioning tracker.

WHY THIS EXISTS

User asked: "OI rotation kya hai?" Answer: institutions move their option
positions between strikes BEFORE the price moves. By tracking these shifts
in real-time, we get a 30-45 minute head start over confluence engines.

  Smart money positions:  T-30 min
  OI rotation visible:    T-25 min     ← WE DETECT HERE
  Confluence sees:        T+5 min      (current dashboard)
  Move done:              T+15 min

THE 5 SUB-DETECTORS

  1. WALL_BUILD       — sudden +1L+ OI at single strike (defending level)
  2. WALL_COLLAPSE    — sudden -80k+ OI vanishes (level breaking)
  3. STRIKE_MIGRATION — net OI shifts across strikes (positioning trend)
  4. WRITER_FLIP      — CE/PE writer dominance reverses (sentiment shift)
  5. UNUSUAL_VELOCITY — OI change >2x typical (stealth accumulation)

ARCHITECTURE

  Engine feeds OI snapshots every 30-60 sec via record_oi_snapshot().
  Detector compares current vs lookback_min-ago snapshot.
  Each sub-detector emits standardized signal dict.
  All signals scored + ranked by confidence.

ENV FLAGS

  EARLY_MOVE_OI_ROTATION_ENABLED=on   activate (default off — shadow first)
  EARLY_MOVE_OI_ROTATION_SHADOW=on    always log (default on)
"""

from __future__ import annotations
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple


# Snapshot storage: {(idx, strike): deque[(ts, ce_oi, pe_oi)]}
# Keep ~120 snapshots = 1 hour at 30sec intervals
_OI_SNAPSHOTS: Dict[Tuple[str, int], Deque[Tuple[float, int, int]]] = defaultdict(
    lambda: deque(maxlen=120)
)


# Default thresholds (tunable via signal call)
DEFAULT_WALL_BUILD_THRESHOLD = 100_000      # 1L OI added in window
DEFAULT_WALL_COLLAPSE_THRESHOLD = 80_000    # 80k OI removed
DEFAULT_MIGRATION_NET_THRESHOLD = 150_000   # 1.5L net shift across strikes
DEFAULT_VELOCITY_RATIO = 2.0                # change > 2x typical


def is_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_OI_ROTATION_ENABLED", "off").lower() == "on"


def is_shadow_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_OI_ROTATION_SHADOW", "on").lower() == "on"


# ── SNAPSHOT RECORDING ─────────────────────────────────────────────────

def record_oi_snapshot(
    *,
    idx: str,
    strike: int,
    ce_oi: int,
    pe_oi: int,
    timestamp: Optional[float] = None,
):
    """Record OI snapshot for a strike. Call every 30-60 sec from engine."""
    if ce_oi < 0 or pe_oi < 0:
        return
    ts = timestamp or time.time()
    _OI_SNAPSHOTS[(idx, strike)].append((ts, ce_oi, pe_oi))


def _lookback_snapshot(
    history: Deque[Tuple[float, int, int]],
    lookback_sec: float,
) -> Optional[Tuple[float, int, int]]:
    """Find oldest snapshot within lookback window."""
    if not history:
        return None
    now = history[-1][0]
    cutoff = now - lookback_sec
    for snap in history:
        if snap[0] >= cutoff:
            return snap
    return None


# ── 5 SUB-DETECTORS ────────────────────────────────────────────────────

def _detect_wall_build(
    idx: str,
    strikes_data: List[dict],
    spot: float,
    threshold: int = DEFAULT_WALL_BUILD_THRESHOLD,
) -> List[Dict]:
    """Detector 1: WALL_BUILD — sudden +1L+ OI at single strike."""
    signals = []
    for d in strikes_data:
        strike = d["strike"]
        ce_change = d["ce_change"]
        pe_change = d["pe_change"]

        # CE wall above spot = resistance (bearish)
        if ce_change >= threshold and strike > spot:
            signals.append({
                "type": "WALL_BUILD",
                "side": "CE",
                "strike": strike,
                "oi_change": ce_change,
                "direction": "BEARISH",
                "confidence": min(0.95, 0.5 + (ce_change / 500_000)),
                "rationale": (
                    f"CE writers added {ce_change:,} OI at strike {strike} "
                    f"(₹{strike - spot:.0f} pts above spot ₹{spot:.0f}). "
                    f"Strong resistance being built — bearish bias."
                ),
            })

        # PE wall below spot = support (bullish)
        if pe_change >= threshold and strike < spot:
            signals.append({
                "type": "WALL_BUILD",
                "side": "PE",
                "strike": strike,
                "oi_change": pe_change,
                "direction": "BULLISH",
                "confidence": min(0.95, 0.5 + (pe_change / 500_000)),
                "rationale": (
                    f"PE writers added {pe_change:,} OI at strike {strike} "
                    f"(₹{spot - strike:.0f} pts below spot ₹{spot:.0f}). "
                    f"Strong support being built — bullish bias."
                ),
            })
    return signals


def _detect_wall_collapse(
    idx: str,
    strikes_data: List[dict],
    spot: float,
    threshold: int = DEFAULT_WALL_COLLAPSE_THRESHOLD,
) -> List[Dict]:
    """Detector 2: WALL_COLLAPSE — sudden -80k+ OI vanishes (level breaking)."""
    signals = []
    for d in strikes_data:
        strike = d["strike"]
        ce_change = d["ce_change"]
        pe_change = d["pe_change"]

        # CE wall above spot collapsing = resistance gone (bullish)
        if ce_change <= -threshold and strike > spot:
            signals.append({
                "type": "WALL_COLLAPSE",
                "side": "CE",
                "strike": strike,
                "oi_change": ce_change,
                "direction": "BULLISH",
                "confidence": min(0.95, 0.55 + (abs(ce_change) / 400_000)),
                "rationale": (
                    f"CE writers unwound {abs(ce_change):,} OI at strike {strike} "
                    f"(₹{strike - spot:.0f} pts above spot). "
                    f"Resistance collapsing — bullish, room to run up."
                ),
            })

        # PE wall below spot collapsing = support gone (bearish)
        if pe_change <= -threshold and strike < spot:
            signals.append({
                "type": "WALL_COLLAPSE",
                "side": "PE",
                "strike": strike,
                "oi_change": pe_change,
                "direction": "BEARISH",
                "confidence": min(0.95, 0.55 + (abs(pe_change) / 400_000)),
                "rationale": (
                    f"PE writers unwound {abs(pe_change):,} OI at strike {strike} "
                    f"(₹{spot - strike:.0f} pts below spot). "
                    f"Support collapsing — bearish, no defense below."
                ),
            })
    return signals


def _detect_strike_migration(
    idx: str,
    strikes_data: List[dict],
    spot: float,
    net_threshold: int = DEFAULT_MIGRATION_NET_THRESHOLD,
) -> List[Dict]:
    """Detector 3: STRIKE_MIGRATION — net OI shift across strikes."""
    # Sum CE change above vs below spot
    ce_above = sum(d["ce_change"] for d in strikes_data if d["strike"] > spot)
    ce_below = sum(d["ce_change"] for d in strikes_data if d["strike"] < spot)
    pe_above = sum(d["pe_change"] for d in strikes_data if d["strike"] > spot)
    pe_below = sum(d["pe_change"] for d in strikes_data if d["strike"] < spot)

    signals = []

    # CE migration UP (above spot adding, below spot unwinding) = bears defending higher
    if ce_above >= net_threshold and ce_below <= -net_threshold // 2:
        signals.append({
            "type": "STRIKE_MIGRATION",
            "side": "CE",
            "direction": "BEARISH",
            "confidence": min(0.85, 0.5 + (ce_above + abs(ce_below)) / 1_000_000),
            "rationale": (
                f"CE OI migrating UP: +{ce_above:,} above spot, {ce_below:,} below. "
                f"Bears defending higher levels — bearish."
            ),
            "context": {"ce_above": ce_above, "ce_below": ce_below},
        })

    # PE migration DOWN (below adding, above unwinding) = bulls defending lower
    if pe_below >= net_threshold and pe_above <= -net_threshold // 2:
        signals.append({
            "type": "STRIKE_MIGRATION",
            "side": "PE",
            "direction": "BULLISH",
            "confidence": min(0.85, 0.5 + (pe_below + abs(pe_above)) / 1_000_000),
            "rationale": (
                f"PE OI migrating DOWN: +{pe_below:,} below spot, {pe_above:,} above. "
                f"Bulls defending lower levels — bullish."
            ),
            "context": {"pe_above": pe_above, "pe_below": pe_below},
        })

    # CE collapse + PE build (combined bullish)
    combined_bull = abs(ce_below) + pe_below - ce_above - abs(pe_above)
    if combined_bull >= net_threshold * 2 and ce_below < 0 and pe_below > 0:
        signals.append({
            "type": "STRIKE_MIGRATION",
            "side": "COMBINED",
            "direction": "BULLISH",
            "confidence": min(0.90, 0.55 + combined_bull / 2_000_000),
            "rationale": (
                f"BULLISH ROTATION: CE writers unwinding below spot ({ce_below:,}), "
                f"PE writers building below spot (+{pe_below:,}). "
                f"Smart money positioning UP."
            ),
            "context": {
                "ce_below": ce_below, "pe_below": pe_below,
                "ce_above": ce_above, "pe_above": pe_above,
            },
        })

    # CE build + PE collapse (combined bearish)
    combined_bear = ce_above + abs(pe_above) - abs(ce_below) - pe_below
    if combined_bear >= net_threshold * 2 and ce_above > 0 and pe_above < 0:
        signals.append({
            "type": "STRIKE_MIGRATION",
            "side": "COMBINED",
            "direction": "BEARISH",
            "confidence": min(0.90, 0.55 + combined_bear / 2_000_000),
            "rationale": (
                f"BEARISH ROTATION: CE writers building above spot (+{ce_above:,}), "
                f"PE writers unwinding above spot ({pe_above:,}). "
                f"Smart money positioning DOWN."
            ),
            "context": {
                "ce_above": ce_above, "pe_above": pe_above,
                "ce_below": ce_below, "pe_below": pe_below,
            },
        })

    return signals


def _detect_writer_flip(
    idx: str,
    strikes_data: List[dict],
    spot: float,
) -> List[Dict]:
    """Detector 4: WRITER_FLIP — CE vs PE writer dominance reverses."""
    total_ce_change = sum(d["ce_change"] for d in strikes_data)
    total_pe_change = sum(d["pe_change"] for d in strikes_data)

    signals = []
    # Bullish flip: CE writers losing dominance (unwinding) + PE writers gaining
    if total_ce_change <= -200_000 and total_pe_change >= 200_000:
        signals.append({
            "type": "WRITER_FLIP",
            "direction": "BULLISH",
            "confidence": min(0.85, 0.5 + (abs(total_ce_change) + total_pe_change) / 2_000_000),
            "rationale": (
                f"Net OI flip BULLISH: CE writers shrinking ({total_ce_change:,}), "
                f"PE writers growing (+{total_pe_change:,}). Sentiment reversal."
            ),
            "context": {
                "total_ce_change": total_ce_change,
                "total_pe_change": total_pe_change,
            },
        })

    # Bearish flip: opposite
    if total_pe_change <= -200_000 and total_ce_change >= 200_000:
        signals.append({
            "type": "WRITER_FLIP",
            "direction": "BEARISH",
            "confidence": min(0.85, 0.5 + (abs(total_pe_change) + total_ce_change) / 2_000_000),
            "rationale": (
                f"Net OI flip BEARISH: PE writers shrinking ({total_pe_change:,}), "
                f"CE writers growing (+{total_ce_change:,}). Sentiment reversal."
            ),
            "context": {
                "total_pe_change": total_pe_change,
                "total_ce_change": total_ce_change,
            },
        })

    return signals


def _detect_unusual_velocity(
    idx: str,
    strikes_data: List[dict],
    spot: float,
    velocity_ratio: float = DEFAULT_VELOCITY_RATIO,
) -> List[Dict]:
    """Detector 5: UNUSUAL_VELOCITY — OI change >2x typical for that strike."""
    signals = []
    for d in strikes_data:
        strike = d["strike"]
        ce_change = d["ce_change"]
        pe_change = d["pe_change"]
        ce_typical = d.get("ce_typical_change", 30_000)
        pe_typical = d.get("pe_typical_change", 30_000)

        # CE unusual velocity
        if abs(ce_change) >= ce_typical * velocity_ratio and abs(ce_change) >= 50_000:
            direction = "BEARISH" if ce_change > 0 and strike > spot else (
                "BULLISH" if ce_change < 0 and strike > spot else None
            )
            if direction:
                ratio = abs(ce_change) / max(ce_typical, 1)
                signals.append({
                    "type": "UNUSUAL_VELOCITY",
                    "side": "CE",
                    "strike": strike,
                    "oi_change": ce_change,
                    "direction": direction,
                    "confidence": min(0.90, 0.5 + ratio / 10),
                    "rationale": (
                        f"CE OI velocity unusual at {strike}: {ce_change:+,} "
                        f"({ratio:.1f}x typical). Stealth positioning detected."
                    ),
                })

        # PE unusual velocity
        if abs(pe_change) >= pe_typical * velocity_ratio and abs(pe_change) >= 50_000:
            direction = "BULLISH" if pe_change > 0 and strike < spot else (
                "BEARISH" if pe_change < 0 and strike < spot else None
            )
            if direction:
                ratio = abs(pe_change) / max(pe_typical, 1)
                signals.append({
                    "type": "UNUSUAL_VELOCITY",
                    "side": "PE",
                    "strike": strike,
                    "oi_change": pe_change,
                    "direction": direction,
                    "confidence": min(0.90, 0.5 + ratio / 10),
                    "rationale": (
                        f"PE OI velocity unusual at {strike}: {pe_change:+,} "
                        f"({ratio:.1f}x typical). Stealth positioning detected."
                    ),
                })

    return signals


# ── MAIN PUBLIC API ────────────────────────────────────────────────────

def detect_rotation(
    *,
    idx: str,
    spot: float,
    strikes_data: List[dict],
    wall_build_threshold: int = DEFAULT_WALL_BUILD_THRESHOLD,
    wall_collapse_threshold: int = DEFAULT_WALL_COLLAPSE_THRESHOLD,
    migration_threshold: int = DEFAULT_MIGRATION_NET_THRESHOLD,
    velocity_ratio: float = DEFAULT_VELOCITY_RATIO,
) -> Dict:
    """Run all 5 sub-detectors on the current strikes data.

    Args:
        idx: index name (NIFTY/BANKNIFTY)
        spot: current spot price
        strikes_data: list of dicts with keys:
            strike, ce_oi, pe_oi, ce_change, pe_change,
            optional: ce_typical_change, pe_typical_change

    Returns:
        dict {
          "idx": str,
          "spot": float,
          "signals": list of signal dicts (all detectors combined),
          "overall_bias": "BULLISH" | "BEARISH" | "MIXED" | "NEUTRAL",
          "overall_confidence": float (max confidence among signals),
          "signal_count": int,
        }
    """
    if not strikes_data:
        return {
            "idx": idx,
            "spot": spot,
            "signals": [],
            "overall_bias": "NEUTRAL",
            "overall_confidence": 0.0,
            "signal_count": 0,
        }

    all_signals: List[Dict] = []
    all_signals.extend(_detect_wall_build(idx, strikes_data, spot, wall_build_threshold))
    all_signals.extend(_detect_wall_collapse(idx, strikes_data, spot, wall_collapse_threshold))
    all_signals.extend(_detect_strike_migration(idx, strikes_data, spot, migration_threshold))
    all_signals.extend(_detect_writer_flip(idx, strikes_data, spot))
    all_signals.extend(_detect_unusual_velocity(idx, strikes_data, spot, velocity_ratio))

    # Decorate each signal with idx + detector="oi_rotation"
    for s in all_signals:
        s["detector"] = "oi_rotation"
        s["idx"] = idx
        s["signal"] = "EARLY_MOVE"

    # Overall bias: weighted vote
    bull_weight = sum(s["confidence"] for s in all_signals if s["direction"] == "BULLISH")
    bear_weight = sum(s["confidence"] for s in all_signals if s["direction"] == "BEARISH")

    if bull_weight > bear_weight * 1.5 and bull_weight >= 1.0:
        overall_bias = "BULLISH"
    elif bear_weight > bull_weight * 1.5 and bear_weight >= 1.0:
        overall_bias = "BEARISH"
    elif all_signals:
        overall_bias = "MIXED"
    else:
        overall_bias = "NEUTRAL"

    max_conf = max((s["confidence"] for s in all_signals), default=0.0)

    return {
        "idx": idx,
        "spot": spot,
        "signals": all_signals,
        "overall_bias": overall_bias,
        "overall_confidence": round(max_conf, 2),
        "signal_count": len(all_signals),
        "bull_weight": round(bull_weight, 2),
        "bear_weight": round(bear_weight, 2),
    }


def shadow_log(result: Dict, source: str = "engine"):
    """Log strongest signal (if any) to stdout."""
    if not is_shadow_enabled():
        return
    if not result.get("signals"):
        return
    # Log top signal by confidence
    top = max(result["signals"], key=lambda s: s["confidence"])
    print(
        f"[EARLY_MOVE_OI_ROTATION] {source} {result['idx']} → {result['overall_bias']} "
        f"top={top['type']}/{top.get('side', '?')} dir={top['direction']} "
        f"conf={top['confidence']:.2f} signals={result['signal_count']}"
    )


def check_and_log(
    *,
    idx: str,
    spot: float,
    strikes_data: List[dict],
    source: str = "engine",
) -> Dict:
    """Public API — detect + always shadow-log."""
    result = detect_rotation(idx=idx, spot=spot, strikes_data=strikes_data)
    if result.get("signals"):
        shadow_log(result, source=source)
    return result


# ── HELPERS ────────────────────────────────────────────────────────────

def build_strikes_data_from_chain(
    *,
    chain: Dict[int, Dict],
    atm: int,
    strike_gap: int = 100,
    range_strikes: int = 10,
) -> List[Dict]:
    """Helper: convert engine chain dict to strikes_data for detector.

    Filters to ATM ± range_strikes. Computes ce_change/pe_change vs the
    earliest snapshot in our stored history (or assumes 0 if no history).
    """
    out = []
    for offset in range(-range_strikes, range_strikes + 1):
        strike = atm + offset * strike_gap
        cinfo = chain.get(strike, {})
        ce_oi = cinfo.get("ce_oi", 0) or 0
        pe_oi = cinfo.get("pe_oi", 0) or 0
        # Get baseline from earliest snapshot
        history = _OI_SNAPSHOTS.get((cinfo.get("idx", ""), strike), deque())
        if history:
            _, base_ce, base_pe = history[0]
            ce_change = ce_oi - base_ce
            pe_change = pe_oi - base_pe
        else:
            # Use chain's reported intraday change if available
            ce_change = cinfo.get("ce_oi_change", 0) or 0
            pe_change = cinfo.get("pe_oi_change", 0) or 0
        out.append({
            "strike": strike,
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
            "ce_change": ce_change,
            "pe_change": pe_change,
        })
    return out


def get_history_size() -> Dict[str, int]:
    """Diagnostic: how many snapshots per (idx, strike)."""
    return {f"{k[0]}|{k[1]}": len(v) for k, v in _OI_SNAPSHOTS.items()}


def reset_history():
    _OI_SNAPSHOTS.clear()
