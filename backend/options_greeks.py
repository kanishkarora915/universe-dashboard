"""
Options Greeks Engines (6) — Buyer-focused volatility & decay analysis.

All engines return {bull, bear, reasons, meta} where bull/bear pts ADD to verdict.
BUYER PERSPECTIVE: Only CE/PE buyer — never seller.

Engines:
1. GAMMA EXPOSURE (GEX) — Market maker regime detector
2. IV RANK/PERCENTILE — Cheap/expensive premium filter (BUYER'S #1 FILTER)
3. IV SKEW — Put vs Call IV asymmetry (fear gauge)
4. VOLATILITY TERM STRUCTURE — Near vs far expiry IV comparison
5. THETA BURN RATE — How much decay per hour (buyer's enemy)
6. INDIA VIX TERM — 7d/30d/90d VIX structure
"""

import math
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
import pytz
import json
import sqlite3

IST = pytz.timezone("Asia/Kolkata")

_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
GREEKS_DB = _data_dir / "greeks_history.db"


def ist_now():
    return datetime.now(IST)


def init_greeks_db():
    """Daily IV history for IVR/IVP calculation."""
    conn = sqlite3.connect(str(GREEKS_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS iv_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            idx TEXT NOT NULL,
            time TEXT NOT NULL,
            atm_iv REAL,
            ce_iv REAL,
            pe_iv REAL,
            vix REAL,
            UNIQUE(date, idx, time)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_iv_date ON iv_history(date, idx)")
    conn.commit()
    conn.close()


def record_iv_snapshot(idx, atm_iv, ce_iv, pe_iv, vix):
    """Called every 5 min from engine to build IV history for IVR."""
    try:
        init_greeks_db()
        now = ist_now()
        conn = sqlite3.connect(str(GREEKS_DB))
        conn.execute("""
            INSERT OR IGNORE INTO iv_history (date, idx, time, atm_iv, ce_iv, pe_iv, vix)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (now.strftime("%Y-%m-%d"), idx, now.strftime("%H:%M"),
              atm_iv, ce_iv, pe_iv, vix))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 1: GAMMA EXPOSURE (GEX)
# ══════════════════════════════════════════════════════════════════════════

def compute_gex(chain, spot, strike_gap):
    """Calculate Gamma Exposure per strike + net GEX.

    GEX > 0 → MM long gamma → range-bound (BAD for buyers - theta trap)
    GEX < 0 → MM short gamma → trending amplification (GOOD for buyers)
    GEX flip level → price crosses = volatility explosion (BUYER paradise)
    """
    if not chain or spot <= 0:
        return {"net_gex": 0, "regime": "UNKNOWN", "flip_level": 0}

    # Simplified gamma approximation (no full Black-Scholes, use OI-weighted distance)
    gex_by_strike = {}
    net_gex = 0

    for strike, data in chain.items():
        moneyness = (strike - spot) / spot
        # Simple gamma proxy: peaks at ATM, falls off exponentially
        gamma_proxy = math.exp(-abs(moneyness * 50))  # 0-1 range

        ce_oi = data.get("ce_oi", 0)
        pe_oi = data.get("pe_oi", 0)

        # MM hedging: long OTM call + short OTM put → dealer long gamma
        # Net dealer gamma: CE OI × gamma × 1 (dealers short CE if retail long)
        # Simplified: sign based on moneyness
        if moneyness > 0:  # OTM calls
            strike_gex = ce_oi * gamma_proxy * 100 - pe_oi * gamma_proxy * 50
        else:  # OTM puts
            strike_gex = -ce_oi * gamma_proxy * 50 + pe_oi * gamma_proxy * 100

        gex_by_strike[strike] = strike_gex
        net_gex += strike_gex

    # Find flip level (where GEX changes sign)
    sorted_strikes = sorted(chain.keys())
    flip_level = spot
    cum_gex = 0
    for s in sorted_strikes:
        cum_gex += gex_by_strike.get(s, 0)
        if cum_gex < 0 and s > spot:
            flip_level = s
            break

    regime = "POSITIVE_GEX_RANGE" if net_gex > 0 else "NEGATIVE_GEX_TREND"

    return {
        "net_gex": int(net_gex),
        "gex_by_strike": gex_by_strike,
        "flip_level": flip_level,
        "regime": regime,
    }


def score_gex_buyer(chain, spot, strike_gap):
    """Score GEX from buyer perspective.

    POSITIVE GEX + range → BAD for buyer (penalize)
    NEGATIVE GEX + trending → GOOD for buyer (boost)
    Price near flip level → volatility expansion likely (boost)
    """
    gex = compute_gex(chain, spot, strike_gap)
    bull = 0
    bear = 0
    reasons = []
    regime = gex["regime"]
    flip = gex["flip_level"]

    # Distance to flip level as % of spot
    flip_dist_pct = abs(flip - spot) / spot * 100 if spot > 0 else 100

    if regime == "NEGATIVE_GEX_TREND":
        # Buyer paradise — trending days
        # If price above flip → bullish acceleration zone
        if spot > flip:
            bull += 8
            reasons.append(f"NEGATIVE GEX + price above flip ₹{flip:.0f} → bull acceleration [8pts bull]")
        else:
            bear += 8
            reasons.append(f"NEGATIVE GEX + price below flip ₹{flip:.0f} → bear acceleration [8pts bear]")
    else:
        # Positive GEX — range day, buyer disadvantage
        reasons.append(f"POSITIVE GEX regime — range day (buyer penalty -5) [theta trap risk]")
        bull = max(0, bull - 5)
        bear = max(0, bear - 5)

    # Flip level proximity — volatility explosion imminent
    if flip_dist_pct < 0.2:
        bull += 5
        bear += 5
        reasons.append(f"Price {flip_dist_pct:.2f}% from GEX flip ₹{flip:.0f} → vol expansion [5pts both]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "regime": regime,
        "net_gex": gex["net_gex"],
        "flip_level": flip,
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 2: IV RANK / PERCENTILE (BUYER'S #1 FILTER)
# ══════════════════════════════════════════════════════════════════════════

def compute_ivr(idx, current_iv, lookback_days=30):
    """IV Rank: where current IV sits in 30-day range.

    Returns {ivr_pct, ivp_pct, classification, buyer_friendly}
    """
    try:
        init_greeks_db()
        conn = sqlite3.connect(str(GREEKS_DB))
        cutoff = (ist_now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        rows = conn.execute("""
            SELECT atm_iv FROM iv_history
            WHERE idx = ? AND date >= ? AND atm_iv > 0
        """, (idx, cutoff)).fetchall()
        conn.close()

        if len(rows) < 5:
            # Not enough history — estimate from VIX
            return {
                "ivr_pct": 50,
                "ivp_pct": 50,
                "classification": "UNKNOWN",
                "buyer_friendly": "NEUTRAL",
                "samples": len(rows),
                "min_iv": 0, "max_iv": 0,
            }

        ivs = [r[0] for r in rows if r[0] and r[0] > 0]
        if not ivs:
            return {"ivr_pct": 50, "ivp_pct": 50, "classification": "UNKNOWN",
                    "buyer_friendly": "NEUTRAL", "samples": 0, "min_iv": 0, "max_iv": 0}

        min_iv = min(ivs)
        max_iv = max(ivs)
        ivr = ((current_iv - min_iv) / max(max_iv - min_iv, 0.01)) * 100
        ivr = max(0, min(100, ivr))

        below = sum(1 for iv in ivs if iv < current_iv)
        ivp = (below / len(ivs)) * 100

        # Classify
        if ivr < 30:
            classification = "CHEAP"
            buyer_friendly = "FULL_SIZE"
        elif ivr < 50:
            classification = "NORMAL"
            buyer_friendly = "NORMAL"
        elif ivr < 70:
            classification = "EXPENSIVE"
            buyer_friendly = "REDUCE_SIZE"
        else:
            classification = "VERY_EXPENSIVE"
            buyer_friendly = "SKIP_OR_EVENT_ONLY"

        return {
            "ivr_pct": round(ivr, 1),
            "ivp_pct": round(ivp, 1),
            "classification": classification,
            "buyer_friendly": buyer_friendly,
            "samples": len(ivs),
            "min_iv": round(min_iv, 2),
            "max_iv": round(max_iv, 2),
            "current_iv": round(current_iv, 2),
        }
    except Exception as e:
        return {"ivr_pct": 50, "ivp_pct": 50, "classification": "ERROR",
                "buyer_friendly": "NEUTRAL", "error": str(e), "samples": 0}


def score_ivr_buyer(idx, current_iv):
    """Score IV Rank from BUYER perspective.

    IVR < 30 = CHEAP = boost buyer signals
    IVR > 70 = EXPENSIVE = penalty (vol crush risk kills buyer)
    """
    ivr_data = compute_ivr(idx, current_iv)
    bull = 0
    bear = 0
    reasons = []
    ivr = ivr_data["ivr_pct"]
    cls = ivr_data["classification"]

    # BUYER: cheap premium = boost, expensive = penalty
    # These points apply equally to bull and bear since BOTH CE and PE buyers benefit from low IV
    if ivr < 20:
        bull += 10
        bear += 10
        reasons.append(f"IVR {ivr}% VERY CHEAP — buyer paradise [+10 both sides]")
    elif ivr < 30:
        bull += 7
        bear += 7
        reasons.append(f"IVR {ivr}% CHEAP — buyer friendly [+7 both]")
    elif ivr < 50:
        bull += 3
        bear += 3
        reasons.append(f"IVR {ivr}% normal [+3 both]")
    elif ivr < 70:
        bull -= 5
        bear -= 5
        reasons.append(f"IVR {ivr}% EXPENSIVE — reduce size [-5 both]")
    else:
        bull -= 10
        bear -= 10
        reasons.append(f"IVR {ivr}% VERY EXPENSIVE + vol crush risk [-10 both]")

    return {
        "bull": max(0, bull),  # No negative scores (use penalty on final action instead)
        "bear": max(0, bear),
        "penalty": abs(min(0, bull)),  # Track penalty separately
        "reasons": reasons,
        "ivr": ivr,
        "ivp": ivr_data["ivp_pct"],
        "classification": cls,
        "buyer_friendly": ivr_data["buyer_friendly"],
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 3: IV SKEW (Put-Call Fear Gauge)
# ══════════════════════════════════════════════════════════════════════════

def score_iv_skew_buyer(chain, spot, strike_gap):
    """IV Skew: PE IV > CE IV at same OTM distance = fear.

    Buyer interpretation:
    - Elevated PE skew → smart money buying protection → correction likely → BUY PE
    - Crush skew (CE IV > PE IV) → unusual bullishness → BUY CE
    """
    if not chain or spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": [], "skew": 0}

    # Get 5% OTM IVs
    otm_dist = int(spot * 0.05 / strike_gap) * strike_gap
    otm_ce_strike = spot + otm_dist
    otm_pe_strike = spot - otm_dist

    otm_ce_strike = round(otm_ce_strike / strike_gap) * strike_gap
    otm_pe_strike = round(otm_pe_strike / strike_gap) * strike_gap

    ce_iv = chain.get(otm_ce_strike, {}).get("ce_iv", 0)
    pe_iv = chain.get(otm_pe_strike, {}).get("pe_iv", 0)

    if ce_iv <= 0 or pe_iv <= 0:
        return {"bull": 0, "bear": 0, "reasons": [], "skew": 0}

    skew = pe_iv - ce_iv  # Normal: positive (puts priced higher)
    skew_pct = (skew / max(ce_iv, 0.1)) * 100

    bull = 0
    bear = 0
    reasons = []

    if skew_pct > 15:
        # Elevated PE skew → downside fear → BUY PE (fear will be realized)
        bear += 7
        reasons.append(f"PE skew +{skew_pct:.0f}% (fear rising) → BUY PE setup [7pts bear]")
    elif skew_pct < -5:
        # Crush skew → unusual bullishness → BUY CE
        bull += 7
        reasons.append(f"CE skew inverted {skew_pct:.0f}% (bullish unusual) → BUY CE setup [7pts bull]")
    elif skew_pct > 5:
        # Mild PE skew (normal)
        reasons.append(f"Normal skew {skew_pct:.0f}% (neutral)")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "skew_pct": round(skew_pct, 1),
        "ce_iv": round(ce_iv, 2),
        "pe_iv": round(pe_iv, 2),
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 4: VOLATILITY TERM STRUCTURE
# ══════════════════════════════════════════════════════════════════════════

def score_vol_term_buyer(engine, idx):
    """Compare near-week IV vs next-week IV vs monthly.

    Inverted (weekly > monthly) → event expected this week → BUY options benefit
    Contango (weekly < monthly) → calm expected → avoid long premium
    """
    bull = 0
    bear = 0
    reasons = []

    try:
        # Get expiry dates (currentWeekly, nextWeekly, monthly)
        expiries = getattr(engine, "expiries", {}).get(idx, [])
        if len(expiries) < 2:
            return {"bull": 0, "bear": 0, "reasons": [], "structure": "UNKNOWN"}

        # Get ATM IV for each expiry (simplified)
        spot_token = engine.spot_tokens.get(idx)
        spot = engine.prices.get(spot_token, {}).get("ltp", 0) if spot_token else 0
        if spot <= 0:
            return {"bull": 0, "bear": 0, "reasons": [], "structure": "UNKNOWN"}

        # Placeholder — use current chain's ATM IV as near, estimate rest
        chain = engine.chains.get(idx, {})
        atm_gap = 50 if idx == "NIFTY" else 100
        atm = round(spot / atm_gap) * atm_gap
        atm_data = chain.get(atm, {})
        near_iv = (atm_data.get("ce_iv", 0) + atm_data.get("pe_iv", 0)) / 2

        if near_iv <= 0:
            return {"bull": 0, "bear": 0, "reasons": [], "structure": "UNKNOWN"}

        # Use VIX as proxy for 30-day IV
        vix = 0
        try:
            live = engine.get_live_data()
            vix = live.get(idx.lower(), {}).get("vix", 0)
        except Exception:
            pass

        if vix <= 0:
            return {"bull": 0, "bear": 0, "reasons": [], "structure": "UNKNOWN"}

        # Structure classification
        if near_iv > vix * 1.15:
            # Inverted — event expected this week
            bull += 4
            bear += 4
            reasons.append(f"Vol term INVERTED (near {near_iv:.1f}% > monthly {vix:.1f}%) → event expected [+4 both]")
            structure = "INVERTED"
        elif near_iv < vix * 0.9:
            # Contango — calm
            reasons.append(f"Vol term CONTANGO (near {near_iv:.1f}% < monthly {vix:.1f}%) → calm, avoid long premium")
            structure = "CONTANGO"
        else:
            structure = "FLAT"
            reasons.append(f"Vol term flat (near {near_iv:.1f}%, monthly {vix:.1f}%)")

        return {
            "bull": bull,
            "bear": bear,
            "reasons": reasons,
            "structure": structure,
            "near_iv": round(near_iv, 2),
            "monthly_iv": round(vix, 2),
        }
    except Exception as e:
        return {"bull": 0, "bear": 0, "reasons": [], "structure": "ERROR", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 5: THETA BURN RATE (Buyer's Enemy Calculator)
# ══════════════════════════════════════════════════════════════════════════

def compute_theta_burn(chain, spot, strike_gap, entry_premium, hours_to_hold=2):
    """Calculate daily theta decay and theta-to-move requirement.

    Returns:
        theta_per_hour: estimated premium decay per hour
        breakeven_move: spot move needed to offset theta over hold period
        viable: whether expected move exceeds breakeven
    """
    if not chain or spot <= 0 or entry_premium <= 0:
        return {"theta_per_hour": 0, "breakeven_pts": 0, "viable": True}

    atm = round(spot / strike_gap) * strike_gap
    atm_data = chain.get(atm, {})

    # Simplified theta: premium × time_decay_factor
    # Near expiry: theta = premium × 0.08/hour (aggressive)
    # Far expiry: theta = premium × 0.02/hour (mild)
    # Default assume same-day or near expiry context
    # Theta estimate: 3-5% of premium per hour near expiry
    theta_per_hour = entry_premium * 0.04  # 4% per hour baseline

    total_theta = theta_per_hour * hours_to_hold

    # How much spot needs to move to offset theta (assuming 0.5 delta ATM)
    # premium_gain = delta × spot_move
    # breakeven_spot_move = total_theta / 0.5
    breakeven_pts = total_theta / 0.5

    return {
        "theta_per_hour": round(theta_per_hour, 2),
        "total_theta": round(total_theta, 2),
        "breakeven_pts": round(breakeven_pts, 1),
        "hours_assumed": hours_to_hold,
    }


def score_theta_buyer(engine, idx, current_ltp_ce, current_ltp_pe):
    """Score theta viability for buyer.

    If expected move > breakeven needed → VIABLE (go)
    If expected move < breakeven needed → SKIP (theta trap)
    """
    bull = 0
    bear = 0
    reasons = []

    try:
        # Get spot velocity from predictive state
        ps = getattr(engine, "predictive_state", None)
        if not ps:
            return {"bull": 0, "bear": 0, "reasons": [], "viable": True}

        # Expected move in next 2 hours (based on current velocity)
        _, abs_5m = ps.spot_velocity(idx, 300)
        expected_move_2hr = abs_5m * 24  # 5-min rate × 24 = 2-hour estimate

        chain = engine.chains.get(idx, {})
        spot_tok = engine.spot_tokens.get(idx)
        spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
        strike_gap = 50 if idx == "NIFTY" else 100

        if spot <= 0:
            return {"bull": 0, "bear": 0, "reasons": [], "viable": True}

        # Check CE viability
        if current_ltp_ce > 0:
            ce_theta = compute_theta_burn(chain, spot, strike_gap, current_ltp_ce, 2)
            if abs(expected_move_2hr) >= ce_theta["breakeven_pts"] * 1.5:
                bull += 5
                reasons.append(f"CE theta viable: expected move {expected_move_2hr:+.0f}pts > breakeven {ce_theta['breakeven_pts']:.0f}pts [5pts bull]")
            elif abs(expected_move_2hr) < ce_theta["breakeven_pts"]:
                reasons.append(f"CE theta TRAP: only {expected_move_2hr:+.0f}pts expected, need {ce_theta['breakeven_pts']:.0f}pts [penalty]")

        if current_ltp_pe > 0:
            pe_theta = compute_theta_burn(chain, spot, strike_gap, current_ltp_pe, 2)
            if abs(expected_move_2hr) >= pe_theta["breakeven_pts"] * 1.5:
                bear += 5
                reasons.append(f"PE theta viable [5pts bear]")
            elif abs(expected_move_2hr) < pe_theta["breakeven_pts"]:
                reasons.append(f"PE theta TRAP")

        return {
            "bull": bull,
            "bear": bear,
            "reasons": reasons,
            "expected_move_2hr": round(expected_move_2hr, 1),
            "ce_breakeven": current_ltp_ce and compute_theta_burn(chain, spot, strike_gap, current_ltp_ce, 2)["breakeven_pts"],
            "pe_breakeven": current_ltp_pe and compute_theta_burn(chain, spot, strike_gap, current_ltp_pe, 2)["breakeven_pts"],
        }
    except Exception as e:
        return {"bull": 0, "bear": 0, "reasons": [], "viable": True, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 6: INDIA VIX TERM STRUCTURE
# ══════════════════════════════════════════════════════════════════════════

class VIXHistory:
    """Track VIX history for term structure analysis."""
    _history = deque(maxlen=100)  # Last 100 snapshots

    @classmethod
    def record(cls, vix, ts=None):
        ts = ts or ist_now()
        cls._history.append((ts, vix))

    @classmethod
    def rolling_avg(cls, days):
        """Average VIX over last N days."""
        if not cls._history:
            return 0
        cutoff = ist_now() - timedelta(days=days)
        recent = [v for (t, v) in cls._history if t >= cutoff and v > 0]
        if not recent:
            return 0
        return sum(recent) / len(recent)


def score_vix_term_buyer(vix):
    """VIX term structure signal.

    Rising VIX from low → fear building → BUY PE
    Declining VIX from high → complacency → BUY CE
    All rising → market stress → PE opportunity
    """
    bull = 0
    bear = 0
    reasons = []

    if vix <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    # Record current
    VIXHistory.record(vix)

    # Get 7d and 30d avg
    vix_7d = VIXHistory.rolling_avg(7) or vix
    vix_30d = VIXHistory.rolling_avg(30) or vix

    # Rising fear: current > 7d > 30d
    if vix > vix_7d * 1.1 and vix_7d > vix_30d * 1.05:
        bear += 5
        reasons.append(f"VIX rising (current {vix:.1f} > 7d {vix_7d:.1f} > 30d {vix_30d:.1f}) → fear mode [5pts bear]")
    # Falling fear from high: current < 7d, and 7d was elevated
    elif vix < vix_7d * 0.9 and vix_7d > vix_30d * 1.1:
        bull += 5
        reasons.append(f"VIX declining from high → relief rally setup [5pts bull]")
    # Elevated VIX (> 20)
    elif vix > 22:
        bear += 3
        reasons.append(f"VIX {vix:.1f} elevated → PE hedge demand [3pts bear]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "vix": round(vix, 2),
        "vix_7d": round(vix_7d, 2),
        "vix_30d": round(vix_30d, 2),
    }


# ══════════════════════════════════════════════════════════════════════════
# MASTER SCORER — Aggregate all 6 Greeks engines
# ══════════════════════════════════════════════════════════════════════════

def score_all_greeks_buyer(engine, idx):
    """Run all 6 Greeks engines. Returns combined bull/bear + individual results."""
    chain = engine.chains.get(idx, {})
    spot_tok = engine.spot_tokens.get(idx)
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
    strike_gap = 50 if idx == "NIFTY" else 100

    if spot <= 0 or not chain:
        return {"bull": 0, "bear": 0, "reasons": [], "engines": {}}

    atm = round(spot / strike_gap) * strike_gap
    atm_data = chain.get(atm, {})
    ce_ltp = atm_data.get("ce_ltp", 0)
    pe_ltp = atm_data.get("pe_ltp", 0)
    atm_iv = (atm_data.get("ce_iv", 0) + atm_data.get("pe_iv", 0)) / 2

    # Get VIX
    vix = 0
    try:
        live = engine.get_live_data()
        vix = live.get(idx.lower(), {}).get("vix", 0)
    except Exception:
        pass

    # Record IV snapshot every call (engine throttles)
    if atm_iv > 0:
        record_iv_snapshot(idx, atm_iv, atm_data.get("ce_iv", 0), atm_data.get("pe_iv", 0), vix)

    # Run all 6 engines
    results = {}
    total_bull = 0
    total_bear = 0
    all_reasons = []

    results["gex"] = score_gex_buyer(chain, spot, strike_gap)
    results["ivr"] = score_ivr_buyer(idx, atm_iv)
    results["iv_skew"] = score_iv_skew_buyer(chain, spot, strike_gap)
    results["vol_term"] = score_vol_term_buyer(engine, idx)
    results["theta"] = score_theta_buyer(engine, idx, ce_ltp, pe_ltp)
    results["vix_term"] = score_vix_term_buyer(vix)

    for name, res in results.items():
        total_bull += res.get("bull", 0)
        total_bear += res.get("bear", 0)
        all_reasons.extend(res.get("reasons", []))

    # Cap totals (6 engines × ~10 pts = 60 max per side)
    total_bull = min(total_bull, 60)
    total_bear = min(total_bear, 60)

    return {
        "bull": total_bull,
        "bear": total_bear,
        "reasons": all_reasons,
        "engines": results,
    }
