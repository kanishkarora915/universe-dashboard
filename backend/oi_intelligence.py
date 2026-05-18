"""
OI Intelligence Engines (5) — Buyer-focused OI flow analysis.

Engines:
1. MAX PAIN DRIFT — Track max pain movement over day (price magnet)
2. STRIKE ROTATION — OI moving between strikes (pre-breakout signal)
3. DELTA-ADJUSTED OI — True exposure (not contract count)
4. FRESH vs ROLLED OI — Distinguish new positions from rolls
5. OTM vs ITM VOLUME — Retail (OTM) vs Institutional (ITM) flow
"""

import math
from collections import deque, defaultdict
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 1: MAX PAIN DRIFT TRACKER
# ══════════════════════════════════════════════════════════════════════════

class MaxPainHistory:
    """Track max pain over time per index."""
    _history = defaultdict(lambda: deque(maxlen=60))  # {idx: deque of (ts, max_pain)}

    @classmethod
    def record(cls, idx, max_pain, ts=None):
        ts = ts or ist_now()
        cls._history[idx].append((ts, max_pain))

    @classmethod
    def get_history(cls, idx):
        return list(cls._history.get(idx, []))


def compute_max_pain(chain):
    """Standard max pain calculation — strike where total premium paid is minimum."""
    if not chain:
        return 0

    strikes = sorted(chain.keys())
    min_pain = float("inf")
    max_pain_strike = strikes[len(strikes) // 2] if strikes else 0

    for s in strikes:
        total_pain = 0
        for s2 in strikes:
            data = chain.get(s2, {})
            ce_oi = data.get("ce_oi", 0)
            pe_oi = data.get("pe_oi", 0)
            if s > s2:
                total_pain += (s - s2) * ce_oi
            else:
                total_pain += (s2 - s) * pe_oi
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = s

    return max_pain_strike


def score_max_pain_drift_buyer(chain, spot, idx, is_expiry_day=False):
    """Track max pain drift — where are big players pulling price?

    Drifting UP → BUY CE (price magnet pulling up)
    Drifting DOWN → BUY PE
    Stable → Neutral (no directional pull)

    Expiry day: 2x weight (70% accuracy)
    """
    bull = 0
    bear = 0
    reasons = []

    current_mp = compute_max_pain(chain)
    if current_mp <= 0:
        return {"bull": 0, "bear": 0, "reasons": [], "current": 0, "drift": "NONE"}

    MaxPainHistory.record(idx, current_mp)
    history = MaxPainHistory.get_history(idx)

    if len(history) < 5:
        return {"bull": 0, "bear": 0, "reasons": [f"Max pain {current_mp} (insufficient history)"], "current": current_mp, "drift": "UNKNOWN"}

    # Compare current vs 30-min ago vs 2-hour ago
    now = ist_now()
    mp_30m_ago = 0
    mp_2h_ago = 0
    for (ts, mp) in reversed(history):
        delta = (now - ts).total_seconds() / 60
        if delta >= 25 and mp_30m_ago == 0:
            mp_30m_ago = mp
        if delta >= 110:
            mp_2h_ago = mp
            break

    if mp_30m_ago == 0:
        mp_30m_ago = history[0][1]

    drift_30m = current_mp - mp_30m_ago
    drift_2h = current_mp - mp_2h_ago if mp_2h_ago > 0 else 0

    multiplier = 2.0 if is_expiry_day else 1.0

    # Significant drift (>50 pts for NIFTY, >100 for BN — same as strike gap)
    strike_gap = 50 if idx == "NIFTY" else 100

    if drift_30m >= strike_gap:
        pts = int(6 * multiplier)
        bull += pts
        reasons.append(f"Max pain drifting UP: {mp_30m_ago}→{current_mp} (+{drift_30m}) → BUY CE {'[EXPIRY 2x]' if is_expiry_day else ''} [{pts}pts bull]")
    elif drift_30m <= -strike_gap:
        pts = int(6 * multiplier)
        bear += pts
        reasons.append(f"Max pain drifting DOWN: {mp_30m_ago}→{current_mp} ({drift_30m}) → BUY PE {'[EXPIRY 2x]' if is_expiry_day else ''} [{pts}pts bear]")

    # 2-hour drift adds conviction
    if drift_2h >= strike_gap * 2 and drift_30m > 0:
        bull += 3
        reasons.append(f"2hr drift confirms bull: +{drift_2h}pts [3pts bull]")
    elif drift_2h <= -strike_gap * 2 and drift_30m < 0:
        bear += 3
        reasons.append(f"2hr drift confirms bear: {drift_2h}pts [3pts bear]")

    direction = "UP" if drift_30m > 0 else "DOWN" if drift_30m < 0 else "STABLE"

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "current": current_mp,
        "drift_30m": drift_30m,
        "drift_2h": drift_2h,
        "drift": direction,
        "is_expiry": is_expiry_day,
        "distance_from_spot": current_mp - spot,
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 2: STRIKE ROTATION DETECTOR
# ══════════════════════════════════════════════════════════════════════════

class StrikeOIHistory:
    """Track OI per strike to detect rotation."""
    _snapshots = defaultdict(lambda: deque(maxlen=30))  # {(idx, strike, side): deque of (ts, oi)}

    @classmethod
    def record(cls, idx, strike, side, oi, ts=None):
        ts = ts or ist_now()
        cls._snapshots[(idx, strike, side)].append((ts, oi))

    @classmethod
    def get_change(cls, idx, strike, side, window_min=30):
        """OI change over last N minutes."""
        history = cls._snapshots.get((idx, strike, side), deque())
        if len(history) < 2:
            return 0
        now = ist_now()
        for (ts, oi) in history:
            if (now - ts).total_seconds() / 60 >= window_min:
                return history[-1][1] - oi
        return history[-1][1] - history[0][1]


def score_strike_rotation_buyer(chain, spot, idx, strike_gap):
    """Detect OI rotation — money moving to higher/lower strikes.

    CE rotation UP (OI leaving lower, entering higher) → price moving UP → BUY CE (higher strike)
    PE rotation DOWN (OI entering lower PE) → price moving DOWN → BUY PE (lower strike)
    """
    bull = 0
    bear = 0
    reasons = []

    if not chain or spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    atm = round(spot / strike_gap) * strike_gap

    # Record current OI
    for offset in range(-5, 6):
        strike = atm + offset * strike_gap
        data = chain.get(strike, {})
        StrikeOIHistory.record(idx, strike, "CE", data.get("ce_oi", 0))
        StrikeOIHistory.record(idx, strike, "PE", data.get("pe_oi", 0))

    # Detect rotation: which strikes gained, which lost OI in last 30 min
    ce_changes = {}
    pe_changes = {}
    for offset in range(-5, 6):
        strike = atm + offset * strike_gap
        ce_changes[strike] = StrikeOIHistory.get_change(idx, strike, "CE", 30)
        pe_changes[strike] = StrikeOIHistory.get_change(idx, strike, "PE", 30)

    # Find biggest gainer and biggest loser
    ce_gain_strike = max(ce_changes, key=ce_changes.get) if ce_changes else None
    ce_loss_strike = min(ce_changes, key=ce_changes.get) if ce_changes else None
    pe_gain_strike = max(pe_changes, key=pe_changes.get) if pe_changes else None
    pe_loss_strike = min(pe_changes, key=pe_changes.get) if pe_changes else None

    rotation_threshold = 20000  # Minimum OI for meaningful rotation

    # CE writers shifting UP (OI leaving lower, entering higher) → price heading UP
    if (ce_gain_strike and ce_loss_strike and
            ce_changes[ce_gain_strike] >= rotation_threshold and
            abs(ce_changes[ce_loss_strike]) >= rotation_threshold and
            ce_gain_strike > ce_loss_strike):
        bull += 7
        reasons.append(f"CE rotation UP: writers shifting {ce_loss_strike}→{ce_gain_strike} ({abs(ce_changes[ce_loss_strike]):,}→{ce_changes[ce_gain_strike]:,}) → price target {ce_gain_strike} [7pts bull]")
    # CE writers retreating DOWN (resistance breaking)
    elif (ce_loss_strike and ce_changes[ce_loss_strike] < -rotation_threshold and
          ce_loss_strike >= atm):
        bull += 4
        reasons.append(f"CE OI leaving {ce_loss_strike} ({ce_changes[ce_loss_strike]:,}) → resistance breaking [4pts bull]")

    # PE writers shifting DOWN (support retreating = price heading DOWN)
    if (pe_gain_strike and pe_loss_strike and
            pe_changes[pe_gain_strike] >= rotation_threshold and
            abs(pe_changes[pe_loss_strike]) >= rotation_threshold and
            pe_gain_strike < pe_loss_strike):
        bear += 7
        reasons.append(f"PE rotation DOWN: writers shifting {pe_loss_strike}→{pe_gain_strike} → price target {pe_gain_strike} [7pts bear]")
    elif (pe_loss_strike and pe_changes[pe_loss_strike] < -rotation_threshold and
          pe_loss_strike <= atm):
        bear += 4
        reasons.append(f"PE OI leaving {pe_loss_strike} → support breaking [4pts bear]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "ce_changes": {str(k): v for k, v in ce_changes.items()},
        "pe_changes": {str(k): v for k, v in pe_changes.items()},
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 3: DELTA-ADJUSTED OI
# ══════════════════════════════════════════════════════════════════════════

def approximate_delta(strike, spot, is_call):
    """Simplified delta approximation without Black-Scholes.
    Works for quick signal generation, not for actual pricing."""
    if spot <= 0:
        return 0.5 if is_call else -0.5
    moneyness = (spot - strike) / strike  # positive if ITM for CE
    if is_call:
        # CE: ITM → delta 0.6-1.0, ATM → 0.5, OTM → 0.0-0.4
        if moneyness > 0.03:
            return min(0.95, 0.5 + moneyness * 10)
        elif moneyness < -0.03:
            return max(0.05, 0.5 + moneyness * 10)
        else:
            return 0.5 + moneyness * 10  # Linear near ATM
    else:
        # PE: OTM → delta -0.0 to -0.4, ATM → -0.5, ITM → -0.6 to -1.0
        if moneyness > 0.03:  # CE ITM = PE OTM
            return max(-0.05, -0.5 + moneyness * 10)
        elif moneyness < -0.03:
            return min(-0.95, -0.5 + moneyness * 10)
        else:
            return -0.5 + moneyness * 10


def score_delta_adjusted_oi_buyer(chain, spot):
    """True exposure based on delta-weighted OI.

    Heavy positive delta from CE OI + negative from PE OI = net positioning.
    Shows REAL bias, not misleading raw PCR.
    """
    bull = 0
    bear = 0
    reasons = []

    if not chain or spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    ce_exposure = 0  # Positive = buyers of CE (bullish)
    pe_exposure = 0  # Negative = buyers of PE (bearish)

    for strike, data in chain.items():
        ce_oi = data.get("ce_oi", 0)
        pe_oi = data.get("pe_oi", 0)
        ce_delta = approximate_delta(strike, spot, is_call=True)
        pe_delta = approximate_delta(strike, spot, is_call=False)
        # OI × delta × 100 (lot size abstraction)
        ce_exposure += ce_oi * ce_delta
        pe_exposure += pe_oi * pe_delta  # Already negative due to PE delta

    net_exposure = ce_exposure + pe_exposure  # Sum (PE is negative)

    # Normalized by total OI
    total_oi = sum(data.get("ce_oi", 0) + data.get("pe_oi", 0) for data in chain.values())
    if total_oi == 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    exposure_ratio = net_exposure / total_oi

    if exposure_ratio > 0.15:
        bull += 5
        reasons.append(f"Delta-adj exposure BULLISH: net {net_exposure/1e5:.0f}L (ratio {exposure_ratio:.2f}) [5pts bull]")
    elif exposure_ratio < -0.15:
        bear += 5
        reasons.append(f"Delta-adj exposure BEARISH: net {net_exposure/1e5:.0f}L (ratio {exposure_ratio:.2f}) [5pts bear]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "ce_exposure": int(ce_exposure),
        "pe_exposure": int(pe_exposure),
        "net_exposure": int(net_exposure),
        "ratio": round(exposure_ratio, 3),
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 4: FRESH vs ROLLED OI
# ══════════════════════════════════════════════════════════════════════════

def score_fresh_vs_rolled_buyer(chain, spot, idx, strike_gap):
    """Detect if OI change is FRESH writing or ROLL (shift between strikes).

    Fresh CE writing = real resistance → avoid BUY CE near that strike
    CE roll UP = writers retreating = BULLISH (buy CE)
    Fresh PE writing = real support → avoid BUY PE near that strike
    PE roll DOWN = writers retreating = BEARISH (buy PE)
    """
    bull = 0
    bear = 0
    reasons = []

    if not chain or spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    atm = round(spot / strike_gap) * strike_gap

    # Compare gainers vs losers within ±3 strikes
    ce_gainer_oi = 0
    ce_loser_oi = 0
    pe_gainer_oi = 0
    pe_loser_oi = 0

    for offset in range(-3, 4):
        strike = atm + offset * strike_gap
        ce_chg = StrikeOIHistory.get_change(idx, strike, "CE", 30)
        pe_chg = StrikeOIHistory.get_change(idx, strike, "PE", 30)
        if ce_chg > 0:
            ce_gainer_oi += ce_chg
        else:
            ce_loser_oi += abs(ce_chg)
        if pe_chg > 0:
            pe_gainer_oi += pe_chg
        else:
            pe_loser_oi += abs(pe_chg)

    # Roll detection: gainer ≈ loser → it's a roll, not fresh
    def is_roll(gainer, loser, tolerance=0.3):
        if gainer == 0 or loser == 0:
            return False
        ratio = abs(gainer - loser) / max(gainer, loser)
        return ratio < tolerance and gainer > 20000  # Within 30% = roll

    # CE roll
    if is_roll(ce_gainer_oi, ce_loser_oi):
        # Roll detected — check direction
        # Find where most gain is vs most loss
        gain_wt_strike = 0
        loss_wt_strike = 0
        for offset in range(-3, 4):
            strike = atm + offset * strike_gap
            chg = StrikeOIHistory.get_change(idx, strike, "CE", 30)
            if chg > 0:
                gain_wt_strike += strike * chg
            else:
                loss_wt_strike += strike * abs(chg)
        avg_gain = gain_wt_strike / max(ce_gainer_oi, 1)
        avg_loss = loss_wt_strike / max(ce_loser_oi, 1)

        if avg_gain > avg_loss:
            # Roll UP = writers retreating = BULLISH
            bull += 4
            reasons.append(f"CE ROLL UP: writers shifting {avg_loss:.0f}→{avg_gain:.0f} [4pts bull]")
        else:
            # Roll DOWN = writers tightening = BEARISH
            bear += 3
            reasons.append(f"CE ROLL DOWN: writers tightening {avg_loss:.0f}→{avg_gain:.0f} [3pts bear]")
    elif ce_gainer_oi > 50000 and ce_gainer_oi > ce_loser_oi * 2:
        # Fresh CE writing
        bear += 4
        reasons.append(f"FRESH CE writing: +{ce_gainer_oi:,} (real resistance) [4pts bear]")

    # PE roll
    if is_roll(pe_gainer_oi, pe_loser_oi):
        gain_wt_strike = 0
        loss_wt_strike = 0
        for offset in range(-3, 4):
            strike = atm + offset * strike_gap
            chg = StrikeOIHistory.get_change(idx, strike, "PE", 30)
            if chg > 0:
                gain_wt_strike += strike * chg
            else:
                loss_wt_strike += strike * abs(chg)
        avg_gain = gain_wt_strike / max(pe_gainer_oi, 1)
        avg_loss = loss_wt_strike / max(pe_loser_oi, 1)

        if avg_gain < avg_loss:
            # PE roll DOWN = writers retreating = BEARISH
            bear += 4
            reasons.append(f"PE ROLL DOWN: writers shifting {avg_loss:.0f}→{avg_gain:.0f} [4pts bear]")
        else:
            bull += 3
            reasons.append(f"PE ROLL UP: writers tightening [3pts bull]")
    elif pe_gainer_oi > 50000 and pe_gainer_oi > pe_loser_oi * 2:
        # Fresh PE writing
        bull += 4
        reasons.append(f"FRESH PE writing: +{pe_gainer_oi:,} (real support) [4pts bull]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "ce_gainer_oi": ce_gainer_oi,
        "ce_loser_oi": ce_loser_oi,
        "pe_gainer_oi": pe_gainer_oi,
        "pe_loser_oi": pe_loser_oi,
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 5: OTM vs ITM VOLUME (Retail vs Institutional)
# ══════════════════════════════════════════════════════════════════════════

def score_otm_itm_volume_buyer(chain, spot, strike_gap):
    """Differentiate retail (OTM lottery tickets) vs institutional (ITM serious).

    OTM volume surge → retail crowded → FADE (contrarian)
    ITM volume surge → institutional conviction → FOLLOW
    """
    bull = 0
    bear = 0
    reasons = []

    if not chain or spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    atm = round(spot / strike_gap) * strike_gap

    # Categorize strikes
    otm_ce_vol = 0  # Strike > spot
    itm_ce_vol = 0  # Strike < spot
    otm_pe_vol = 0  # Strike < spot
    itm_pe_vol = 0  # Strike > spot

    for strike, data in chain.items():
        ce_vol = data.get("ce_volume", 0)
        pe_vol = data.get("pe_volume", 0)
        if strike > spot + strike_gap * 2:  # Deep OTM CE
            otm_ce_vol += ce_vol
        elif strike < spot - strike_gap * 2:  # Deep ITM CE
            itm_ce_vol += ce_vol
        if strike < spot - strike_gap * 2:  # Deep OTM PE
            otm_pe_vol += pe_vol
        elif strike > spot + strike_gap * 2:  # Deep ITM PE
            itm_pe_vol += pe_vol

    # Retail crowded OTM CE (retail buying calls)
    if otm_ce_vol > itm_ce_vol * 3 and otm_ce_vol > 500000:
        bear += 4
        reasons.append(f"OTM CE volume retail crowded ({otm_ce_vol/1e5:.1f}L) — fade = [4pts bear]")
    elif itm_ce_vol > otm_ce_vol * 1.5 and itm_ce_vol > 100000:
        bull += 4
        reasons.append(f"ITM CE volume institutional ({itm_ce_vol/1e5:.1f}L) → BUY CE [4pts bull]")

    # Retail crowded OTM PE (retail buying puts)
    if otm_pe_vol > itm_pe_vol * 3 and otm_pe_vol > 500000:
        bull += 4
        reasons.append(f"OTM PE volume retail crowded ({otm_pe_vol/1e5:.1f}L) — fade = [4pts bull]")
    elif itm_pe_vol > otm_pe_vol * 1.5 and itm_pe_vol > 100000:
        bear += 4
        reasons.append(f"ITM PE volume institutional ({itm_pe_vol/1e5:.1f}L) → BUY PE [4pts bear]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "otm_ce_vol": otm_ce_vol,
        "itm_ce_vol": itm_ce_vol,
        "otm_pe_vol": otm_pe_vol,
        "itm_pe_vol": itm_pe_vol,
    }


# ══════════════════════════════════════════════════════════════════════════
# MASTER SCORER — All 5 OI Intelligence engines
# ══════════════════════════════════════════════════════════════════════════

def score_all_oi_intel_buyer(engine, idx, is_expiry_day=False):
    """Run all 5 OI intelligence engines."""
    chain = engine.chains.get(idx, {})
    spot_tok = engine.spot_tokens.get(idx)
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
    strike_gap = 50 if idx == "NIFTY" else 100

    if spot <= 0 or not chain:
        return {"bull": 0, "bear": 0, "reasons": [], "engines": {}}

    results = {}
    total_bull = 0
    total_bear = 0
    all_reasons = []

    results["max_pain_drift"] = score_max_pain_drift_buyer(chain, spot, idx, is_expiry_day)
    results["strike_rotation"] = score_strike_rotation_buyer(chain, spot, idx, strike_gap)
    results["delta_adjusted"] = score_delta_adjusted_oi_buyer(chain, spot)
    results["fresh_vs_rolled"] = score_fresh_vs_rolled_buyer(chain, spot, idx, strike_gap)
    results["otm_itm_volume"] = score_otm_itm_volume_buyer(chain, spot, strike_gap)

    for name, res in results.items():
        total_bull += res.get("bull", 0)
        total_bear += res.get("bear", 0)
        all_reasons.extend(res.get("reasons", []))

    # Cap (5 engines × ~10 pts = 50 max)
    total_bull = min(total_bull, 50)
    total_bear = min(total_bear, 50)

    return {
        "bull": total_bull,
        "bear": total_bear,
        "reasons": all_reasons,
        "engines": results,
    }
