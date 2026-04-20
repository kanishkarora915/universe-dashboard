"""
Market Structure Engines (5) — Buyer-focused structural analysis.

Engines:
1. SWEEP DETECTION — Multi-strike institutional orders (follow whales)
2. PIN RISK — Expiry day price pinning to high OI strikes
3. STOP HUNT ZONES — Where retail stops cluster (smart money hunts)
4. CROSS-ASSET DIVERGENCE — NIFTY vs BN vs USD/INR alignment
5. SECTORAL LEADER — Which sector leading the market
"""

import time
from collections import deque, defaultdict
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 1: SWEEP DETECTION
# ══════════════════════════════════════════════════════════════════════════

class SweepTracker:
    """Track volume spikes across multiple strikes in short windows."""
    _volume_history = defaultdict(lambda: deque(maxlen=60))  # {(idx, strike, side): deque of (ts, vol)}

    @classmethod
    def record(cls, idx, strike, side, volume, ts=None):
        ts = ts or ist_now()
        cls._volume_history[(idx, strike, side)].append((ts, volume))

    @classmethod
    def get_recent_surge(cls, idx, strike, side, window_sec=120):
        """Returns volume increase over last N seconds."""
        hist = cls._volume_history.get((idx, strike, side), deque())
        if len(hist) < 2:
            return 0
        now = ist_now()
        for (ts, vol) in hist:
            if (now - ts).total_seconds() <= window_sec:
                return hist[-1][1] - vol
        return 0


def score_sweep_detection_buyer(chain, spot, idx, strike_gap):
    """Detect institutional sweeps (multi-strike simultaneous orders).

    CE sweep = big institutional bullish → BUY CE
    PE sweep = big institutional bearish → BUY PE
    70%+ follow directional move
    """
    bull = 0
    bear = 0
    reasons = []

    if not chain or spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    atm = round(spot / strike_gap) * strike_gap

    # Record current volumes
    for offset in range(-3, 4):
        strike = atm + offset * strike_gap
        data = chain.get(strike, {})
        SweepTracker.record(idx, strike, "CE", data.get("ce_volume", 0))
        SweepTracker.record(idx, strike, "PE", data.get("pe_volume", 0))

    # Check for sweep: 3+ strikes with >50% volume surge in last 2 min
    ce_sweep_strikes = []
    pe_sweep_strikes = []

    for offset in range(-3, 4):
        strike = atm + offset * strike_gap
        data = chain.get(strike, {})
        ce_surge = SweepTracker.get_recent_surge(idx, strike, "CE", 120)
        pe_surge = SweepTracker.get_recent_surge(idx, strike, "PE", 120)
        ce_base_vol = max(data.get("ce_volume", 0) - ce_surge, 1)
        pe_base_vol = max(data.get("pe_volume", 0) - pe_surge, 1)

        # Surge threshold: 50k contracts in 2 min at single strike
        if ce_surge >= 50000 and ce_surge / ce_base_vol > 0.3:
            ce_sweep_strikes.append((strike, ce_surge))
        if pe_surge >= 50000 and pe_surge / pe_base_vol > 0.3:
            pe_sweep_strikes.append((strike, pe_surge))

    # 3+ strikes with simultaneous surge = SWEEP
    if len(ce_sweep_strikes) >= 3:
        total_vol = sum(v for _, v in ce_sweep_strikes)
        bull += 10
        strikes_str = ", ".join(str(s) for s, _ in ce_sweep_strikes)
        reasons.append(f"CE SWEEP detected: {len(ce_sweep_strikes)} strikes [{strikes_str}] total +{total_vol:,} vol → INSTITUTIONAL BUY [10pts bull]")
    elif len(ce_sweep_strikes) == 2:
        bull += 5
        reasons.append(f"CE mini-sweep: 2 strikes surge [5pts bull]")

    if len(pe_sweep_strikes) >= 3:
        total_vol = sum(v for _, v in pe_sweep_strikes)
        bear += 10
        strikes_str = ", ".join(str(s) for s, _ in pe_sweep_strikes)
        reasons.append(f"PE SWEEP detected: {len(pe_sweep_strikes)} strikes [{strikes_str}] total +{total_vol:,} vol → INSTITUTIONAL SELL [10pts bear]")
    elif len(pe_sweep_strikes) == 2:
        bear += 5
        reasons.append(f"PE mini-sweep: 2 strikes surge [5pts bear]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "ce_sweep_count": len(ce_sweep_strikes),
        "pe_sweep_count": len(pe_sweep_strikes),
        "ce_sweeps": [(s, v) for s, v in ce_sweep_strikes],
        "pe_sweeps": [(s, v) for s, v in pe_sweep_strikes],
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 2: PIN RISK (Expiry Day)
# ══════════════════════════════════════════════════════════════════════════

def score_pin_risk_buyer(chain, spot, idx, is_expiry_day):
    """On expiry day, price pins to strike with biggest combined CE+PE OI.

    For BUYER:
    - If price FAR from pin (>100 pts NIFTY, >200 pts BN) → price will drift TOWARDS pin
    - If price AT pin → stays pinned (NO BUY — options decay)
    - Strike far from pin in direction away → those options worthless
    """
    bull = 0
    bear = 0
    reasons = []

    if not is_expiry_day or not chain or spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": [], "pin_strike": 0}

    # Find pin strike (max combined OI)
    pin_strike = 0
    max_combined = 0
    for strike, data in chain.items():
        combined = data.get("ce_oi", 0) + data.get("pe_oi", 0)
        if combined > max_combined:
            max_combined = combined
            pin_strike = strike

    if pin_strike == 0:
        return {"bull": 0, "bear": 0, "reasons": [], "pin_strike": 0}

    distance = pin_strike - spot
    strike_gap = 50 if idx == "NIFTY" else 100

    # Price pulled towards pin
    if abs(distance) >= strike_gap * 3:
        if distance > 0:
            bull += 8
            reasons.append(f"PIN PULL UP: price {spot:.0f} pulled to pin {pin_strike:.0f} (+{distance:.0f}pts) → BUY CE [8pts bull]")
        else:
            bear += 8
            reasons.append(f"PIN PULL DOWN: price {spot:.0f} pulled to pin {pin_strike:.0f} ({distance:.0f}pts) → BUY PE [8pts bear]")
    elif abs(distance) <= strike_gap:
        # Already at pin — danger zone for buyers
        reasons.append(f"AT PIN {pin_strike:.0f} — theta death zone, avoid long options")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "pin_strike": pin_strike,
        "distance": int(distance),
        "max_combined_oi": max_combined,
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 3: STOP HUNT ZONES
# ══════════════════════════════════════════════════════════════════════════

class StopHuntZones:
    """Track previous day H/L + round numbers + swing points."""
    _pdh_pdl = {}  # {(idx, date): {pdh, pdl}}

    @classmethod
    def set_pdh_pdl(cls, idx, date_str, pdh, pdl):
        cls._pdh_pdl[(idx, date_str)] = {"pdh": pdh, "pdl": pdl}

    @classmethod
    def get(cls, idx, date_str):
        return cls._pdh_pdl.get((idx, date_str), {})


def score_stop_hunt_buyer(engine, idx):
    """Identify stop hunt zones where retail stops cluster.

    Smart money hunts these, buyer benefits by:
    - Buying AT the hunt zone (after wick test) instead of chasing
    - Avoiding entries just above/below these levels (they'll get hit)
    """
    bull = 0
    bear = 0
    reasons = []
    zones = []

    spot_tok = engine.spot_tokens.get(idx)
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
    if spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": [], "zones": []}

    try:
        live = engine.get_live_data()
        idx_data = live.get(idx.lower(), {})
        prev_close = idx_data.get("prevClose", 0)
        day_high = idx_data.get("high", 0)
        day_low = idx_data.get("low", 0)
    except Exception:
        prev_close = day_high = day_low = 0

    strike_gap = 50 if idx == "NIFTY" else 100
    hunt_proximity = strike_gap * 1.5  # Within 75 pts (NIFTY) / 150 pts (BN)

    # Zone 1: Previous day close proximity
    if prev_close > 0:
        pdc_dist = spot - prev_close
        zones.append({"type": "PDC", "level": prev_close, "distance": pdc_dist})
        if abs(pdc_dist) <= hunt_proximity:
            reasons.append(f"Near PDC {prev_close:.0f} ({pdc_dist:+.0f}pts) — watch for hunt")

    # Zone 2: Today's H/L
    if day_low > 0 and spot <= day_low * 1.003:
        # Near day low — hunt likely, then bounce
        bull += 4
        reasons.append(f"Near day LOW {day_low:.0f} — hunt zone, expect bounce → BUY CE [4pts bull]")
    elif day_high > 0 and spot >= day_high * 0.997:
        bear += 4
        reasons.append(f"Near day HIGH {day_high:.0f} — hunt zone, expect rejection → BUY PE [4pts bear]")

    # Zone 3: Round numbers (psychological levels)
    round_level_gap = 100 if idx == "NIFTY" else 500
    nearest_round = round(spot / round_level_gap) * round_level_gap
    if abs(spot - nearest_round) <= strike_gap:
        # Near round number — hunt likely
        direction = "BULL" if spot < nearest_round else "BEAR"
        if direction == "BULL":
            reasons.append(f"Near round {nearest_round} below — test/break soon")
        else:
            reasons.append(f"Near round {nearest_round} above — test/rejection soon")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "zones": zones,
        "spot": spot,
        "day_high": day_high,
        "day_low": day_low,
        "prev_close": prev_close,
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 4: CROSS-ASSET DIVERGENCE
# ══════════════════════════════════════════════════════════════════════════

def score_cross_asset_buyer(engine, idx):
    """Compare NIFTY vs BANKNIFTY vs USD/INR (via global cues).

    Aligned bullish → strong BUY CE confidence
    Diverging → fake signal → reduce confidence
    USD/INR up fast + NIFTY flat → FII selling → BUY PE bias
    """
    bull = 0
    bear = 0
    reasons = []

    try:
        live = engine.get_live_data()
        nifty = live.get("nifty", {})
        bn = live.get("banknifty", {})
        nifty_pct = nifty.get("changePct", 0)
        bn_pct = bn.get("changePct", 0)
    except Exception:
        return {"bull": 0, "bear": 0, "reasons": []}

    # Both indices aligned strongly (> 0.3% or < -0.3%)
    if nifty_pct > 0.3 and bn_pct > 0.3:
        bull += 5
        reasons.append(f"NIFTY +{nifty_pct:.2f}%, BN +{bn_pct:.2f}% aligned BULLISH [5pts bull]")
    elif nifty_pct < -0.3 and bn_pct < -0.3:
        bear += 5
        reasons.append(f"NIFTY {nifty_pct:.2f}%, BN {bn_pct:.2f}% aligned BEARISH [5pts bear]")
    # Divergence: NIFTY up but BN down = fake rally
    elif nifty_pct > 0.3 and bn_pct < -0.2:
        reasons.append(f"DIVERGENCE: NIFTY +{nifty_pct:.2f}% but BN {bn_pct:.2f}% = fake rally → caution")
        bull = max(0, bull - 3)
    elif nifty_pct < -0.3 and bn_pct > 0.2:
        reasons.append(f"DIVERGENCE: NIFTY down but BN up = mixed market")
        bear = max(0, bear - 3)

    # Global cues alignment
    try:
        gc = engine.get_global_cues()
        gc_signal = gc.get("signal", "NEUTRAL")
        if gc_signal == "BULLISH" and nifty_pct > 0.2:
            bull += 3
            reasons.append(f"Cross-asset: Global BULLISH + NIFTY up [3pts bull]")
        elif gc_signal == "BEARISH" and nifty_pct < -0.2:
            bear += 3
            reasons.append(f"Cross-asset: Global BEARISH + NIFTY down [3pts bear]")
    except Exception:
        pass

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "nifty_pct": nifty_pct,
        "bn_pct": bn_pct,
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 5: SECTORAL LEADER
# ══════════════════════════════════════════════════════════════════════════

# Sector ETF proxies (NSE tokens — simplified; real impl would use sector index tokens)
# For now use Nifty Bank (already tracked as BANKNIFTY) + we need IT/Auto/Pharma
# Simplified: use NIFTY vs BANKNIFTY relative strength as proxy for sector divergence

def score_sectoral_leader_buyer(engine, idx):
    """Detect sector leadership (simplified without full sector data).

    NIFTY moving up but BANKNIFTY flat = IT/other sectors leading = broader bull
    BANKNIFTY leading + NIFTY flat = financial rally (index will catch up)
    Both flat = range day (BUYER DEATH)
    """
    bull = 0
    bear = 0
    reasons = []

    try:
        live = engine.get_live_data()
        nifty = live.get("nifty", {})
        bn = live.get("banknifty", {})
        nifty_pct = nifty.get("changePct", 0)
        bn_pct = bn.get("changePct", 0)
    except Exception:
        return {"bull": 0, "bear": 0, "reasons": []}

    diff = nifty_pct - bn_pct

    # Clear leader
    if idx == "NIFTY":
        # NIFTY specific — check its own momentum vs BN
        if nifty_pct > 0.3 and diff > 0.2:
            # NIFTY leading (IT/pharma sectors strong)
            bull += 4
            reasons.append(f"NIFTY leading (+{nifty_pct:.2f}% vs BN +{bn_pct:.2f}%) = IT/pharma strength [4pts bull]")
        elif nifty_pct < -0.3 and diff < -0.2:
            bear += 4
            reasons.append(f"NIFTY lagging — broad weakness [4pts bear]")
    elif idx == "BANKNIFTY":
        if bn_pct > 0.3 and diff < -0.2:
            # BANKNIFTY leading = financial sector
            bull += 4
            reasons.append(f"BANKNIFTY leading (+{bn_pct:.2f}% vs NIFTY +{nifty_pct:.2f}%) = financial strength [4pts bull]")
        elif bn_pct < -0.3 and diff > 0.2:
            bear += 4
            reasons.append(f"BANKNIFTY dragging — financial weakness [4pts bear]")

    # Chop detection (both flat)
    if abs(nifty_pct) < 0.15 and abs(bn_pct) < 0.15:
        reasons.append(f"Both indices flat (NIFTY {nifty_pct:+.2f}% BN {bn_pct:+.2f}%) — range day, buyer caution")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "nifty_pct": nifty_pct,
        "bn_pct": bn_pct,
        "diff": round(diff, 2),
    }


# ══════════════════════════════════════════════════════════════════════════
# MASTER SCORER — All 5 Market Structure engines
# ══════════════════════════════════════════════════════════════════════════

def score_all_market_structure_buyer(engine, idx, is_expiry_day=False):
    """Run all 5 market structure engines."""
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

    results["sweep"] = score_sweep_detection_buyer(chain, spot, idx, strike_gap)
    results["pin_risk"] = score_pin_risk_buyer(chain, spot, idx, is_expiry_day)
    results["stop_hunt"] = score_stop_hunt_buyer(engine, idx)
    results["cross_asset"] = score_cross_asset_buyer(engine, idx)
    results["sectoral"] = score_sectoral_leader_buyer(engine, idx)

    for name, res in results.items():
        total_bull += res.get("bull", 0)
        total_bear += res.get("bear", 0)
        all_reasons.extend(res.get("reasons", []))

    # Cap (5 engines × ~10 pts = 50)
    total_bull = min(total_bull, 50)
    total_bear = min(total_bear, 50)

    return {
        "bull": total_bull,
        "bear": total_bear,
        "reasons": all_reasons,
        "engines": results,
    }
