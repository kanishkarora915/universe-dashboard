"""
Trap Zone Detector — predicts how far the trap will run.

Per spec §5:
  - Bull trap upper bound: min(highest_OI_CE_strike, current_spot + max_synthetic_deviation)
  - Bear trap lower bound: max(highest_OI_PE_strike, current_spot - max_negative_deviation)
  - Trap confidence (already in regime_classifier.calc_trap_confidence)
"""

import time


def find_max_oi_strike(chain, side="CE", min_oi_threshold=500000):
    """Find strike with highest OI on given side (CE = resistance, PE = support).
    Returns (strike, oi) or (None, 0) if nothing meets threshold."""
    best_strike = None
    best_oi = 0
    for strike, data in chain.items():
        oi = data.get(f"{side.lower()}_oi", 0) or 0
        if oi > best_oi and oi >= min_oi_threshold:
            best_oi = oi
            best_strike = strike
    return best_strike, best_oi


def max_synthetic_deviation_recent(state, lookback_secs=1800):
    """Max trinity_deviation observed in last 30 min — used as trap stretch limit."""
    bars = state.bar_buffer.last_n(lookback_secs)
    if not bars:
        return 30.0
    devs = [b.get("deviation") for b in bars if b.get("deviation") is not None]
    if not devs:
        return 30.0
    return max(abs(d) for d in devs) or 30.0


def compute_bull_trap_upper(engine, spot, state):
    """Per spec §5.1: Spot upar max kahan tak ja sakta hai bull trap me?"""
    chain = engine.chains.get("NIFTY", {})
    highest_ce_strike, ce_oi = find_max_oi_strike(chain, side="CE")
    max_dev = max_synthetic_deviation_recent(state)
    spot_plus_dev = spot + max_dev

    if highest_ce_strike is None:
        return {
            "upper_bound": round(spot_plus_dev, 1),
            "highest_oi_strike": None,
            "ce_oi_at_strike": 0,
            "max_synthetic_deviation": round(max_dev, 1),
            "logic": f"No CE wall found — using max recent stretch (+{max_dev:.0f}pts)",
        }

    upper = min(highest_ce_strike, spot_plus_dev)
    return {
        "upper_bound": round(upper, 1),
        "highest_oi_strike": highest_ce_strike,
        "ce_oi_at_strike": int(ce_oi),
        "max_synthetic_deviation": round(max_dev, 1),
        "logic": (
            f"CE wall at {highest_ce_strike} ({ce_oi/100000:.1f}L OI) defends; "
            f"spot+max_dev={spot_plus_dev:.0f}; upper = min = {upper:.0f}"
        ),
    }


def compute_bear_trap_lower(engine, spot, state):
    """Per spec §5.2: Spot neeche max kahan tak ja sakta hai bear trap me?"""
    chain = engine.chains.get("NIFTY", {})
    highest_pe_strike, pe_oi = find_max_oi_strike(chain, side="PE")
    max_dev = max_synthetic_deviation_recent(state)
    spot_minus_dev = spot - max_dev

    if highest_pe_strike is None:
        return {
            "lower_bound": round(spot_minus_dev, 1),
            "highest_oi_strike": None,
            "pe_oi_at_strike": 0,
            "max_negative_deviation": round(-max_dev, 1),
            "logic": f"No PE wall found — using max recent stretch (-{max_dev:.0f}pts)",
        }

    lower = max(highest_pe_strike, spot_minus_dev)
    return {
        "lower_bound": round(lower, 1),
        "highest_oi_strike": highest_pe_strike,
        "pe_oi_at_strike": int(pe_oi),
        "max_negative_deviation": round(-max_dev, 1),
        "logic": (
            f"PE wall at {highest_pe_strike} ({pe_oi/100000:.1f}L OI) supports; "
            f"spot-max_dev={spot_minus_dev:.0f}; lower = max = {lower:.0f}"
        ),
    }


def compute_trap_zones(engine, spot, state, regime):
    """Return appropriate trap zone based on current regime."""
    out = {"timestamp": int(time.time() * 1000), "spot": spot}
    if regime == "BULL_TRAP":
        out["bull_trap"] = compute_bull_trap_upper(engine, spot, state)
        out["display"] = (
            f"Bull trap active. Spot upar max {out['bull_trap']['upper_bound']:.0f} tak ja sakta hai "
            f"(PE writers ka defense), uske baad reversal high probability."
        )
    elif regime == "BEAR_TRAP":
        out["bear_trap"] = compute_bear_trap_lower(engine, spot, state)
        out["display"] = (
            f"Bear trap active. Spot neeche max {out['bear_trap']['lower_bound']:.0f} tak ja sakta hai "
            f"(CE writers covering), reversal expected."
        )
    else:
        # Provide both bounds as reference
        out["bull_trap"] = compute_bull_trap_upper(engine, spot, state)
        out["bear_trap"] = compute_bear_trap_lower(engine, spot, state)
        out["display"] = "No active trap. Reference bounds shown."
    return out
