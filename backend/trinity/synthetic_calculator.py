"""
Synthetic Nifty calculator — Put-Call Parity per strike + weighted composite.

Per spec §3:
  synthetic_at_strike(K) = K + CE_ltp(K) - PE_ltp(K)
  synthetic_nifty = weighted_avg of 9 strikes (ATM ±200 range)
  trinity_deviation = synthetic_nifty - nifty_spot
  strike_deviation(K) = synthetic_at_strike(K) - nifty_spot

Weights (sum to 100%):
  ATM        = 30%
  ATM ± 50   = 20% each
  ATM ± 100  = 12% each
  ATM ± 150  = 8% each
  ATM ± 200  = 5% each   (Total 30+40+24+16+10 = 120 → renormalize)
"""

# Per-strike weights (per spec §3.3) — keys are offset in points from ATM
# Note: spec adds to 120%; we renormalize to 100% to keep weighted-avg correct.
_RAW_WEIGHTS = {
    -200: 5, -150: 8, -100: 12, -50: 20,
    0: 30,
    50: 20, 100: 12, 150: 8, 200: 5,
}
_TOTAL = sum(_RAW_WEIGHTS.values())
WEIGHTS = {k: v / _TOTAL for k, v in _RAW_WEIGHTS.items()}


def synthetic_at_strike(K, ce_ltp, pe_ltp):
    """Put-Call Parity: synthetic forward = K + Call - Put."""
    if ce_ltp is None or pe_ltp is None:
        return None
    if ce_ltp <= 0 or pe_ltp <= 0:
        return None
    return K + ce_ltp - pe_ltp


def compute_per_strike_synthetics(engine, atm, strike_gap=50, min_volume=100):
    """For each of 9 strikes, compute synthetic.
    Validity rule (any of):
      - Volume >= min_volume*2 (intraday active liquidity), OR
      - OI >= 1000 contracts on both sides (positions exist, valid synthetic).
    Per spec §10.3 we skip truly illiquid strikes; OI fallback handles
    pre-market / post-close / spread-only periods where volume momentarily 0.
    Returns: {strike: {"synthetic": float, "ce_ltp", "pe_ltp", "ce_oi", "pe_oi", "valid": bool}}"""
    chain = engine.chains.get("NIFTY", {})
    out = {}
    for offset_pts, _w in _RAW_WEIGHTS.items():
        strike = atm + offset_pts
        d = chain.get(strike, {})
        ce_ltp = d.get("ce_ltp", 0) or 0
        pe_ltp = d.get("pe_ltp", 0) or 0
        ce_vol = d.get("ce_volume", 0) or 0
        pe_vol = d.get("pe_volume", 0) or 0
        ce_oi = d.get("ce_oi", 0) or 0
        pe_oi = d.get("pe_oi", 0) or 0

        syn = synthetic_at_strike(strike, ce_ltp, pe_ltp)
        # Validity: synthetic computable AND (volume liquid OR OI present both sides)
        liquid_vol = (ce_vol + pe_vol) >= min_volume * 2
        liquid_oi = ce_oi >= 1000 and pe_oi >= 1000
        valid = syn is not None and (liquid_vol or liquid_oi)

        out[strike] = {
            "strike": strike,
            "offset_pts": offset_pts,
            "synthetic": syn,
            "ce_ltp": ce_ltp, "pe_ltp": pe_ltp,
            "ce_oi": ce_oi, "pe_oi": pe_oi,
            "ce_volume": ce_vol, "pe_volume": pe_vol,
            "valid": valid,
            "weight": WEIGHTS[offset_pts],
        }
    return out


def compute_composite_synthetic(per_strike):
    """Weighted average of valid strikes. Renormalizes weights when some are skipped."""
    total_weight = 0.0
    weighted_sum = 0.0
    used = 0
    for s, info in per_strike.items():
        if info["valid"] and info["synthetic"] is not None:
            weighted_sum += info["synthetic"] * info["weight"]
            total_weight += info["weight"]
            used += 1
    if total_weight <= 0:
        return None, 0
    return weighted_sum / total_weight, used


def compute_trinity_deviation(synthetic, spot):
    if synthetic is None or spot is None or spot <= 0:
        return None
    return synthetic - spot


def compute_strike_deviations(per_strike, spot):
    """strike_deviation(K) = synthetic_at_strike(K) - spot for each strike.
    Returns sorted list (descending stress for heatmap coloring)."""
    out = []
    for s, info in per_strike.items():
        if info["synthetic"] is not None and spot > 0:
            dev = info["synthetic"] - spot
            out.append({
                "strike": s,
                "offset_pts": info["offset_pts"],
                "ce_ltp": info["ce_ltp"],
                "pe_ltp": info["pe_ltp"],
                "ce_oi": info["ce_oi"],
                "pe_oi": info["pe_oi"],
                "synthetic": info["synthetic"],
                "deviation": dev,
                "valid": info["valid"],
                "weight": info["weight"],
            })
    out.sort(key=lambda x: x["offset_pts"])
    return out


def compute_future_premium(future_ltp, spot_ltp):
    """future_premium = future - spot (per spec §3.1)."""
    if future_ltp is None or spot_ltp is None or future_ltp <= 0 or spot_ltp <= 0:
        return None
    return future_ltp - spot_ltp
