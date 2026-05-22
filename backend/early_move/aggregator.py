"""
aggregator — combines all 5 early-move detectors into ONE verdict.

WHY THIS EXISTS

The 5 detectors (premium_velocity, cross_asset, oi_rotation,
iv_term_structure, volume_profile) each emit signals independently.
On their own they're just alerts. The aggregator is the "jury" that
reads all of them and produces a single FIRE / NO_TRADE / BLOCKED
decision.

THE CORE RULE

  ANY 2+ detectors agree on direction  →  FIRE EARLY
  Only 1 detector                      →  NO_TRADE (could be noise)
  3+ detectors agree                   →  FIRE with HIGH conviction

  Confluence engines wait for 5+ to agree (lagging, late).
  Aggregator fires on 2 LEADING detectors (early).

VETO LAYER

  Even if 2+ detectors agree on direction, certain conditions BLOCK:

    • IV_CRUSH        → vega works against option buyers → BLOCK
    • FAKEOUT_WARNING → volume says the move is fake → BLOCK
    • VOLUME_EXHAUSTION → move is ending → BLOCK new entry

DIRECTION NORMALIZATION

  Detectors emit varied direction strings. Normalized to:
    BULL    — bullish (BULLISH / BULL)
    BEAR    — bearish (BEARISH / BEAR)
    NEUTRAL — move coming but direction unknown (IV expansion, etc)
    AVOID   — explicit "don't buy" (IV crush)
    EXIT    — move ending (volume exhaustion)

  NEUTRAL signals add CONTEXT (a move is coming) but don't pick side.
  AVOID/EXIT act as vetoes.

OUTPUT

  {"verdict": "FIRE" | "NO_TRADE" | "BLOCKED",
   "direction": "BULL" | "BEAR" | None,
   "confidence": 0.0-1.0,
   "detectors_agreed": int,
   "vega_friendly": bool | None,
   "contributing": [str, ...],
   "blocked_by": str | None,
   "action": str,
   "all_signals": [...]}

ENV FLAGS

  EARLY_MOVE_AGGREGATOR_ENABLED=on   activate (default off)
  EARLY_MOVE_MIN_DETECTORS=2         min agreeing detectors to fire
"""

from __future__ import annotations
import os
from typing import Dict, List, Optional


# Direction normalization map
_DIRECTION_MAP = {
    "BULL": "BULL", "BULLISH": "BULL", "STRONG_BULLISH": "BULL",
    "BEAR": "BEAR", "BEARISH": "BEAR", "STRONG_BEARISH": "BEAR",
    "NEUTRAL": "NEUTRAL",
    "AVOID": "AVOID",
    "EXIT": "EXIT",
}

# Signal types that act as vetoes (block entry even if direction agrees)
_VETO_TYPES = {"IV_CRUSH", "FAKEOUT_WARNING", "VOLUME_EXHAUSTION"}


def is_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_AGGREGATOR_ENABLED", "off").lower() == "on"


def min_detectors() -> int:
    try:
        return max(2, int(os.environ.get("EARLY_MOVE_MIN_DETECTORS", "2")))
    except ValueError:
        return 2


def _normalize_direction(d: Optional[str]) -> str:
    if not d:
        return "NEUTRAL"
    return _DIRECTION_MAP.get(str(d).upper(), "NEUTRAL")


def aggregate(signals: List[Dict], min_agree: Optional[int] = None) -> Dict:
    """Combine a list of detector signal dicts into one verdict.

    Args:
        signals: list of signal dicts from the 5 detectors. Each must
                 have at least: detector, direction, confidence.
                 Optional: type, rationale.
        min_agree: minimum distinct detectors that must agree on a
                   direction to FIRE. Defaults to env EARLY_MOVE_MIN_DETECTORS.

    Returns:
        verdict dict (see module docstring).
    """
    if min_agree is None:
        min_agree = min_detectors()

    if not signals:
        return {
            "verdict": "NO_TRADE",
            "direction": None,
            "confidence": 0.0,
            "detectors_agreed": 0,
            "vega_friendly": None,
            "contributing": [],
            "blocked_by": None,
            "action": "NO TRADE — no early-move signals",
            "all_signals": [],
        }

    # ── Tally votes per direction, tracking DISTINCT detectors ──
    bull_detectors: Dict[str, float] = {}   # detector_name → max confidence
    bear_detectors: Dict[str, float] = {}
    neutral_detectors: Dict[str, float] = {}
    veto_signals: List[Dict] = []
    vega_friendly: Optional[bool] = None

    for sig in signals:
        detector = sig.get("detector", "unknown")
        direction = _normalize_direction(sig.get("direction"))
        confidence = float(sig.get("confidence", 0.5) or 0.5)
        sig_type = sig.get("type", "")

        # Track vega friendliness from IV detector
        if "vega_friendly" in sig:
            vega_friendly = sig["vega_friendly"]

        # Veto signals
        if sig_type in _VETO_TYPES or direction in ("AVOID", "EXIT"):
            veto_signals.append(sig)
            continue

        if direction == "BULL":
            bull_detectors[detector] = max(bull_detectors.get(detector, 0), confidence)
        elif direction == "BEAR":
            bear_detectors[detector] = max(bear_detectors.get(detector, 0), confidence)
        else:  # NEUTRAL
            neutral_detectors[detector] = max(neutral_detectors.get(detector, 0), confidence)

    n_bull = len(bull_detectors)
    n_bear = len(bear_detectors)
    bull_weight = sum(bull_detectors.values())
    bear_weight = sum(bear_detectors.values())

    # ── Determine winning direction ──
    if n_bull > n_bear:
        winning_dir = "BULL"
        n_agreed = n_bull
        win_detectors = bull_detectors
        win_weight = bull_weight
    elif n_bear > n_bull:
        winning_dir = "BEAR"
        n_agreed = n_bear
        win_detectors = bear_detectors
        win_weight = bear_weight
    else:
        # Tie (incl. 0-0) → no clear direction
        winning_dir = None
        n_agreed = 0
        win_detectors = {}
        win_weight = 0.0

    # ── VETO CHECK ──
    # IV crush / fakeout / exhaustion blocks entry regardless of votes
    blocking_veto = None
    for v in veto_signals:
        vt = v.get("type", "")
        # IV crush + fakeout block fresh entries
        if vt in ("IV_CRUSH", "FAKEOUT_WARNING", "VOLUME_EXHAUSTION"):
            blocking_veto = v
            break

    contributing = []
    for det, conf in sorted(win_detectors.items(), key=lambda kv: -kv[1]):
        # find the rationale for this detector
        rat = next(
            (s.get("rationale", "")[:120] for s in signals
             if s.get("detector") == det
             and _normalize_direction(s.get("direction")) == winning_dir),
            det,
        )
        contributing.append(f"{det}: {rat}")

    all_signals_compact = [
        {
            "detector": s.get("detector"),
            "type": s.get("type"),
            "direction": _normalize_direction(s.get("direction")),
            "confidence": s.get("confidence"),
        }
        for s in signals
    ]

    # ── VERDICT ──
    if blocking_veto is not None:
        return {
            "verdict": "BLOCKED",
            "direction": winning_dir,
            "confidence": 0.0,
            "detectors_agreed": n_agreed,
            "vega_friendly": vega_friendly,
            "contributing": contributing,
            "blocked_by": blocking_veto.get("type"),
            "action": (
                f"BLOCKED — {blocking_veto.get('type')}: "
                f"{blocking_veto.get('rationale', '')[:120]}"
            ),
            "all_signals": all_signals_compact,
        }

    if winning_dir and n_agreed >= min_agree:
        # Confidence: average of winning detector confidences,
        # boosted slightly when more than min_agree detectors concur.
        avg_conf = win_weight / n_agreed
        boost = min(0.15, (n_agreed - min_agree) * 0.08)
        final_conf = min(0.95, avg_conf + boost)
        conviction = "HIGH" if n_agreed >= 3 else "MEDIUM"
        return {
            "verdict": "FIRE",
            "direction": winning_dir,
            "confidence": round(final_conf, 2),
            "conviction": conviction,
            "detectors_agreed": n_agreed,
            "vega_friendly": vega_friendly,
            "contributing": contributing,
            "blocked_by": None,
            "action": (
                f"FIRE {'BUY CE' if winning_dir == 'BULL' else 'BUY PE'} "
                f"— {n_agreed} leading detectors agree ({conviction} conviction)"
            ),
            "all_signals": all_signals_compact,
        }

    # Not enough agreement
    return {
        "verdict": "NO_TRADE",
        "direction": winning_dir,
        "confidence": 0.0,
        "detectors_agreed": n_agreed,
        "vega_friendly": vega_friendly,
        "contributing": contributing,
        "blocked_by": None,
        "action": (
            f"NO TRADE — only {n_agreed} detector(s) agree, need {min_agree}+"
        ),
        "all_signals": all_signals_compact,
    }


def collect_all_signals(
    *,
    engine,
    idx: str,
) -> List[Dict]:
    """Gather signals from all 5 detectors for a given index.

    Pulls live data from the engine and runs each detector. Returns a
    flat list of signal dicts ready for aggregate().

    Safe — each detector wrapped in try/except. A broken detector
    contributes nothing rather than crashing the aggregation.
    """
    signals: List[Dict] = []

    # 1. cross_asset (no idx needed — global NIFTY/BANKNIFTY)
    try:
        from . import cross_asset
        sig = cross_asset.check_and_log(source="aggregator")
        if sig:
            signals.append(sig)
    except Exception:
        pass

    # 2. oi_rotation
    try:
        from . import oi_rotation
        spot_token = engine.spot_tokens.get(idx)
        spot = engine.prices.get(spot_token, {}).get("ltp", 0) if spot_token else 0
        if spot > 0:
            try:
                from engine import INDEX_CONFIG as _IDX
                gap = _IDX.get(idx, {}).get("strike_gap", 100)
            except Exception:
                gap = 100 if idx == "BANKNIFTY" else 50
            atm = round(spot / gap) * gap
            chain = engine.chains.get(idx, {})
            strikes_data = []
            for off in range(-10, 11):
                s = atm + off * gap
                cinfo = chain.get(s, {})
                strikes_data.append({
                    "strike": s,
                    "ce_oi": cinfo.get("ce_oi", 0) or 0,
                    "pe_oi": cinfo.get("pe_oi", 0) or 0,
                    "ce_change": cinfo.get("ce_oi_change", 0) or 0,
                    "pe_change": cinfo.get("pe_oi_change", 0) or 0,
                })
            result = oi_rotation.detect_rotation(
                idx=idx, spot=spot, strikes_data=strikes_data,
            )
            signals.extend(result.get("signals", []))
    except Exception:
        pass

    # 3. iv_term_structure
    try:
        from . import iv_term_structure
        result = iv_term_structure.detect_all(idx=idx)
        signals.extend(result.get("signals", []))
    except Exception:
        pass

    # 4. volume_profile
    try:
        from . import volume_profile
        result = volume_profile.detect_all(idx=idx)
        signals.extend(result.get("signals", []))
    except Exception:
        pass

    # 5. premium_velocity (scan ATM±1 strikes)
    try:
        from . import premium_velocity
        for k in list(premium_velocity.get_history_size().keys()):
            parts = k.split("|")
            if len(parts) != 3 or parts[0] != idx:
                continue
            _, strike_s, side = parts
            sig = premium_velocity.detect_divergence(
                idx=idx, strike=int(strike_s), side=side, delta=0.5,
            )
            if sig:
                signals.append(sig)
    except Exception:
        pass

    return signals


def get_verdict(*, engine, idx: str, min_agree: Optional[int] = None) -> Dict:
    """Full pipeline — collect signals + aggregate into verdict for an index."""
    signals = collect_all_signals(engine=engine, idx=idx)
    verdict = aggregate(signals, min_agree=min_agree)
    verdict["idx"] = idx
    verdict["enabled"] = is_enabled()
    return verdict
