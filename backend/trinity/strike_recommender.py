"""
Strike Recommender — generates BUY_CE/BUY_PE signals with strike, SL, target.

Per spec §6:
  - Strong (>85%): ATM
  - Medium (70-85%): ATM±50
  - Trap reversal: ATM±100 (lottery)

Position sizing: floor(risk_capital * (confidence/100) / (premium * lot_size))
"""

import time

NIFTY_LOT_SIZE = 75   # As of 2025+ (changed from 25 → 75 in Jan 2025; can be made dynamic)


def select_strike(regime, confidence, atm, action, strike_gap=50):
    """Pick strike based on regime + confidence (per spec §6.1, §6.2)."""
    if action not in ("BUY_CE", "BUY_PE"):
        return atm

    sign = 1 if action == "BUY_CE" else -1

    # Trap reversal plays — go OTM for cheap lottery
    if regime in ("BULL_TRAP", "BEAR_TRAP"):
        # On trap, action is opposite to regime: bull trap → BUY_PE; bear trap → BUY_CE
        # Use ATM±100 OTM (lottery)
        return atm + sign * 2 * strike_gap

    # Strong signal — ATM
    if confidence >= 85:
        return atm
    # Medium — ATM±50 OTM
    if confidence >= 70:
        return atm + sign * 1 * strike_gap
    # Low — also ATM (don't go OTM with low conf)
    return atm


def get_premium_at_strike(engine, strike, side):
    chain = engine.chains.get("NIFTY", {})
    d = chain.get(strike, {})
    if side == "CE":
        return d.get("ce_ltp", 0) or 0
    return d.get("pe_ltp", 0) or 0


def calc_stop_target(premium, regime):
    """Per spec §4 + §6.4: stop loss + target premiums."""
    if premium <= 0:
        return 0, 0
    # Default: SL -19%, T +37% (R:R ~1:2). Trap reversals: tighter.
    if regime in ("BULL_TRAP", "BEAR_TRAP"):
        sl_mult = 0.75   # 25% SL on lottery (cheaper option)
        tgt_mult = 2.00  # 100% target
    elif regime in ("REAL_RALLY", "REAL_CRASH"):
        sl_mult = 0.81   # ~19% SL
        tgt_mult = 1.37  # ~37% target
    else:
        sl_mult = 0.85
        tgt_mult = 1.30
    return round(premium * sl_mult, 1), round(premium * tgt_mult, 1)


def calc_position_size(risk_capital, confidence, premium, lot_size=NIFTY_LOT_SIZE):
    """Per spec §6.3:
       suggested_lots = floor(risk_capital * (confidence/100) / (premium * lot_size))
    """
    if premium <= 0 or lot_size <= 0:
        return 0
    raw = risk_capital * (confidence / 100.0) / (premium * lot_size)
    return max(0, int(raw))


def estimate_duration_mins(regime):
    """Heuristic from spec — how long the move typically lasts."""
    return {
        "REAL_RALLY": 12,
        "REAL_CRASH": 12,
        "BULL_TRAP": 8,
        "BEAR_TRAP": 8,
        "DISTRIBUTION": 20,
        "ACCUMULATION": 20,
    }.get(regime, 5)


def regime_to_action(regime):
    """Map regime → trade action."""
    return {
        "REAL_RALLY": "BUY_CE",
        "REAL_CRASH": "BUY_PE",
        "BULL_TRAP": "BUY_PE",      # opposite of trap
        "BEAR_TRAP": "BUY_CE",
        "DISTRIBUTION": "EXIT_LONGS_PREP_PE",
        "ACCUMULATION": "EXIT_SHORTS_PREP_CE",
    }.get(regime)


def build_signal(engine, regime, confidence, atm, spot, reasoning,
                 trap_zones=None, risk_capital=1000000):
    """Construct full signal object per spec §6.4 output format."""
    action = regime_to_action(regime)
    if action is None or action.startswith("EXIT_"):
        # Distribution/Accumulation → no fresh signal, just heads-up
        return {
            "signal_type": action or "WAIT",
            "regime": regime,
            "confidence": confidence,
            "reasoning": reasoning,
            "ts": int(time.time() * 1000),
        }

    side = "CE" if action == "BUY_CE" else "PE"
    strike = select_strike(regime, confidence, atm, action)
    premium = get_premium_at_strike(engine, strike, side)
    sl_premium, tgt_premium = calc_stop_target(premium, regime)
    suggested_lots = calc_position_size(risk_capital, confidence, premium)
    duration = estimate_duration_mins(regime)

    out = {
        "signal_type": action,
        "regime": regime,
        "strike": int(strike),
        "side": side,
        "premium": round(premium, 2),
        "lot_size": NIFTY_LOT_SIZE,
        "suggested_lots": suggested_lots,
        "confidence": round(confidence, 1),
        "reasoning": reasoning,
        "stop_loss_premium": sl_premium,
        "target_premium": tgt_premium,
        "expected_duration_mins": duration,
        "ts": int(time.time() * 1000),
    }
    if trap_zones:
        if "bull_trap" in trap_zones and trap_zones.get("bull_trap"):
            out["trap_zone_upper"] = trap_zones["bull_trap"].get("upper_bound")
        if "bear_trap" in trap_zones and trap_zones.get("bear_trap"):
            out["trap_zone_lower"] = trap_zones["bear_trap"].get("lower_bound")
    return out
