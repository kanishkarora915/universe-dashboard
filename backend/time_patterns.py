"""
Time-Based Pattern Engines (4) — Buyer-focused intraday rhythm.

Engines:
1. OPENING RANGE BREAKOUT (ORB) — 9:15-9:30 AM high/low breakout
2. POWER HOUR — 14:30-15:00 PM distinct patterns
3. PRE-MARKET GAP ANALYSIS — 8:45-9:15 AM setup classification
4. 0DTE / EXPIRY DAY SPECIALIZATION — Tuesday NIFTY special rules
"""

from collections import defaultdict
from datetime import datetime, timedelta, time as dtime
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


def is_nifty_expiry_day(now=None):
    """NIFTY weekly expiry: Tuesdays (post-2024 NSE rule)."""
    now = now or ist_now()
    return now.weekday() == 1  # Monday=0, Tuesday=1


def is_banknifty_expiry_day(now=None):
    """BANKNIFTY: last Thursday of month (monthly)."""
    now = now or ist_now()
    if now.weekday() != 3:  # Thursday
        return False
    # Check if last Thursday of month
    next_week = now + timedelta(days=7)
    return next_week.month != now.month


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 1: OPENING RANGE BREAKOUT (ORB)
# ══════════════════════════════════════════════════════════════════════════

class ORBState:
    """Track 9:15-9:30 AM high/low per index per day."""
    _ranges = {}  # {(idx, date): {high, low, confirmed_at}}
    _current_candles = defaultdict(list)  # ticks during 9:15-9:30 window

    @classmethod
    def record_tick(cls, idx, price, ts=None):
        ts = ts or ist_now()
        # Only record during 9:15-9:30 window
        market_time = ts.time()
        if dtime(9, 15) <= market_time <= dtime(9, 30):
            cls._current_candles[idx].append(price)

    @classmethod
    def finalize_range(cls, idx, ts=None):
        """Call at 9:30 AM to finalize ORB high/low."""
        ts = ts or ist_now()
        date_key = ts.strftime("%Y-%m-%d")

        if idx in cls._current_candles and cls._current_candles[idx]:
            prices = cls._current_candles[idx]
            cls._ranges[(idx, date_key)] = {
                "high": max(prices),
                "low": min(prices),
                "confirmed_at": ts.isoformat(),
                "width": max(prices) - min(prices),
            }
            # Clear the tick buffer for tomorrow
            cls._current_candles[idx] = []

    @classmethod
    def get_range(cls, idx, ts=None):
        ts = ts or ist_now()
        date_key = ts.strftime("%Y-%m-%d")
        return cls._ranges.get((idx, date_key))


def score_orb_buyer(engine, idx):
    """Score ORB breakout signal.

    Price breaks ORB high with volume → BUY CE (65%+ win historically)
    Price breaks ORB low with volume → BUY PE
    Price inside ORB past 10:30 → chop day signal (penalty)
    """
    bull = 0
    bear = 0
    reasons = []

    now = ist_now()
    market_time = now.time()
    spot_tok = engine.spot_tokens.get(idx)
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0

    if spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    # During 9:15-9:30: record ticks, no signal
    if dtime(9, 15) <= market_time <= dtime(9, 30):
        ORBState.record_tick(idx, spot, now)
        return {"bull": 0, "bear": 0, "reasons": ["ORB forming (9:15-9:30 window)"]}

    # At 9:30: finalize range
    if dtime(9, 30) <= market_time <= dtime(9, 31):
        ORBState.finalize_range(idx, now)

    orb = ORBState.get_range(idx, now)
    if not orb:
        # No range yet OR pre-market
        if market_time < dtime(9, 15):
            return {"bull": 0, "bear": 0, "reasons": ["Pre-market"]}
        # If past 9:30 but no range (missed ticks), skip
        return {"bull": 0, "bear": 0, "reasons": ["No ORB established"]}

    orb_high = orb["high"]
    orb_low = orb["low"]
    orb_width = orb["width"]

    # Breakout detection
    break_margin = orb_width * 0.1  # 10% beyond for confirmation

    if spot > orb_high + break_margin:
        # Bullish breakout
        bull += 10
        target = orb_high + orb_width * 2
        reasons.append(f"ORB BREAKOUT UP: price {spot:.0f} > OR high {orb_high:.0f} | Target {target:.0f} [10pts bull]")
    elif spot < orb_low - break_margin:
        # Bearish breakout
        bear += 10
        target = orb_low - orb_width * 2
        reasons.append(f"ORB BREAKOUT DOWN: price {spot:.0f} < OR low {orb_low:.0f} | Target {target:.0f} [10pts bear]")
    elif market_time > dtime(10, 30):
        # Still inside range past 10:30 = chop day
        reasons.append(f"Inside OR past 10:30 ({orb_low:.0f}-{orb_high:.0f}) = chop day [no trade]")
    else:
        reasons.append(f"Inside OR ({orb_low:.0f}-{orb_high:.0f}) — waiting for break")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_width": orb_width,
        "current": spot,
        "position": "ABOVE" if spot > orb_high else "BELOW" if spot < orb_low else "INSIDE",
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 2: POWER HOUR (14:30-15:00)
# ══════════════════════════════════════════════════════════════════════════

def score_power_hour_buyer(engine, idx):
    """Last 45 minutes has distinct patterns:
    - Institutional squaring off
    - Retail FOMO (contrarian)
    - Momentum continuation or sharp reversal
    """
    bull = 0
    bear = 0
    reasons = []

    now = ist_now()
    market_time = now.time()

    if market_time < dtime(14, 30) or market_time > dtime(15, 15):
        return {"bull": 0, "bear": 0, "reasons": []}

    # Get price velocity + direction for day
    spot_tok = engine.spot_tokens.get(idx)
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
    if spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    # Get day's open
    try:
        live = engine.get_live_data()
        idx_data = live.get(idx.lower(), {})
        day_open = idx_data.get("openPrice", 0)
        day_high = idx_data.get("high", 0)
        day_low = idx_data.get("low", 0)
        day_change_pct = idx_data.get("changePct", 0)
    except Exception:
        return {"bull": 0, "bear": 0, "reasons": []}

    if day_open <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    # Get 1-min velocity
    vel_pct = 0
    ps = getattr(engine, "predictive_state", None)
    if ps:
        vel_pct, _ = ps.spot_velocity(idx, 60)

    # Pattern 1: Late-day rally with retail FOMO → reversal risk
    if day_change_pct > 0.5 and market_time >= dtime(14, 45) and vel_pct > 0.05:
        # Rally continuing into close = often retail FOMO
        bear += 5
        reasons.append(f"Power hour FOMO rally (day +{day_change_pct:.1f}%) → reversal setup [5pts bear]")

    # Pattern 2: Late-day breakdown
    elif day_change_pct < -0.5 and market_time >= dtime(14, 45) and vel_pct < -0.05:
        bull += 5
        reasons.append(f"Power hour sell-off exhaustion (day {day_change_pct:.1f}%) → bounce setup [5pts bull]")

    # Pattern 3: Momentum continuation 14:30-14:45
    elif dtime(14, 30) <= market_time <= dtime(14, 45):
        if day_change_pct > 0.3 and vel_pct > 0.05:
            bull += 3
            reasons.append(f"Power hour bull momentum continuation [3pts bull]")
        elif day_change_pct < -0.3 and vel_pct < -0.05:
            bear += 3
            reasons.append(f"Power hour bear momentum continuation [3pts bear]")

    # Pattern 4: Close-to-high/low strength
    if day_high > 0 and spot >= day_high * 0.998:
        # Near day high in power hour
        bull += 2
        reasons.append(f"Near day high in power hour [2pts bull]")
    elif day_low > 0 and spot <= day_low * 1.002:
        bear += 2
        reasons.append(f"Near day low in power hour [2pts bear]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "time": market_time.strftime("%H:%M"),
        "day_change_pct": day_change_pct,
        "velocity_1m_pct": round(vel_pct, 3),
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 3: PRE-MARKET GAP ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def score_premarket_gap_buyer(engine, idx):
    """Classify opening gap behavior (after market opens).

    Gap & Go (gap + hold first 15 min) → Trend day → BUY direction
    Gap Fade (gap + reverse) → Gap fill → BUY opposite
    Island Gap (gap + quick reversal) → Breakdown/breakout → BUY reversal
    """
    bull = 0
    bear = 0
    reasons = []

    now = ist_now()
    market_time = now.time()

    # Only meaningful 9:15-10:15 AM
    if market_time < dtime(9, 15) or market_time > dtime(10, 30):
        return {"bull": 0, "bear": 0, "reasons": []}

    try:
        live = engine.get_live_data()
        idx_data = live.get(idx.lower(), {})
        spot = idx_data.get("ltp", 0)
        day_open = idx_data.get("openPrice", 0)
        prev_close = idx_data.get("prevClose", 0)
    except Exception:
        return {"bull": 0, "bear": 0, "reasons": []}

    if day_open <= 0 or prev_close <= 0 or spot <= 0:
        return {"bull": 0, "bear": 0, "reasons": []}

    gap_pts = day_open - prev_close
    gap_pct = (gap_pts / prev_close) * 100
    from_open_pts = spot - day_open
    from_open_pct = (from_open_pts / day_open) * 100

    # Strong gap threshold: 0.3%+
    if abs(gap_pct) < 0.2:
        return {"bull": 0, "bear": 0, "reasons": [f"Flat open (gap {gap_pts:+.0f} pts)"]}

    # Gap Up scenarios
    if gap_pct > 0.3:
        if from_open_pct > 0.15:
            # Gap & Go UP — trend day
            bull += 7
            reasons.append(f"GAP & GO UP: +{gap_pct:.1f}% gap, holding +{from_open_pct:.1f}% → trend day [7pts bull]")
        elif from_open_pct < -0.3:
            # Gap fade — expecting fill
            bear += 6
            reasons.append(f"GAP FADE (up → reversed): gap +{gap_pct:.1f}% rejected ({from_open_pct:.1f}%) → gap fill [6pts bear]")
        elif from_open_pct < -0.5:
            # Island gap
            bear += 8
            reasons.append(f"ISLAND GAP (up → strong reversal): {from_open_pct:.1f}% from open → breakdown [8pts bear]")

    # Gap Down scenarios
    elif gap_pct < -0.3:
        if from_open_pct < -0.15:
            # Gap & Go DOWN — trend day
            bear += 7
            reasons.append(f"GAP & GO DOWN: {gap_pct:.1f}% gap, holding {from_open_pct:.1f}% → trend day [7pts bear]")
        elif from_open_pct > 0.3:
            # Gap fade up
            bull += 6
            reasons.append(f"GAP FADE (down → reversed): gap {gap_pct:.1f}% rejected (+{from_open_pct:.1f}%) → gap fill [6pts bull]")
        elif from_open_pct > 0.5:
            bull += 8
            reasons.append(f"ISLAND GAP (down → strong reversal): +{from_open_pct:.1f}% from open → breakout [8pts bull]")

    return {
        "bull": bull,
        "bear": bear,
        "reasons": reasons,
        "gap_pct": round(gap_pct, 2),
        "gap_pts": round(gap_pts, 1),
        "from_open_pct": round(from_open_pct, 2),
        "day_open": day_open,
        "spot": spot,
    }


# ══════════════════════════════════════════════════════════════════════════
# ENGINE 4: 0DTE / EXPIRY DAY SPECIALIZATION
# ══════════════════════════════════════════════════════════════════════════

def score_expiry_day_buyer(engine, idx):
    """Expiry day CONTEXT engine (Tuesday NIFTY, last-Thursday BN).

    PHILOSOPHY: No hardcoded time-based bias. Market can cook anything anytime.
    The engines READ the market — they don't assume "1 PM = explosive" or
    "11 AM = lunch chop". If moves happen, OI/price/momentum engines pick up.

    This engine ONLY detects:
    - Is today an expiry day? (yes/no)
    - What phase of day? (informational metadata — NOT a score bias)

    Real scoring comes from:
    - max_pain_drift (reads actual pin pull)
    - sweep detection (reads actual whale activity)
    - predictive momentum (reads actual velocity)
    - chop filter (reads actual chop)
    - oi_flow / fresh_vs_rolled (reads actual positioning)

    Meta phase can be used by frontend to SHOW what phase we're in — but engine
    won't add arbitrary time-based points anymore.
    """
    bull = 0
    bear = 0
    reasons = []
    meta = {"is_expiry": False, "phase": "NONE"}

    now = ist_now()
    market_time = now.time()

    is_expiry = (idx == "NIFTY" and is_nifty_expiry_day(now)) or \
                (idx == "BANKNIFTY" and is_banknifty_expiry_day(now))
    meta["is_expiry"] = is_expiry

    if not is_expiry:
        return {"bull": 0, "bear": 0, "reasons": [], "meta": meta}

    # Phase classification — INFORMATIONAL ONLY (no score bias)
    if market_time < dtime(11, 0):
        phase = "EARLY"
    elif market_time < dtime(13, 0):
        phase = "BUILD"
    elif market_time < dtime(15, 0):
        phase = "EXPLOSIVE"
    elif market_time < dtime(15, 15):
        phase = "FINAL"
    else:
        phase = "SETTLE"
    meta["phase"] = phase

    # Max pain info (for display/context only — max_pain_drift engine handles actual scoring)
    try:
        from oi_intelligence import compute_max_pain
        chain = engine.chains.get(idx, {})
        spot_tok = engine.spot_tokens.get(idx)
        spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
        mp = compute_max_pain(chain)
        meta["max_pain"] = mp
        meta["spot"] = spot
    except Exception:
        pass

    # Only informational reason (no points)
    reasons.append(f"EXPIRY DAY {phase} phase ({market_time.strftime('%H:%M')}) — engines reading live market state")

    # NO score bias added. Let market-reading engines (max_pain_drift, sweep,
    # momentum, chop filter, OI flow) determine entry based on REAL conditions.
    return {
        "bull": 0,
        "bear": 0,
        "reasons": reasons,
        "meta": meta,
    }


# ══════════════════════════════════════════════════════════════════════════
# MASTER SCORER — All 4 Time-based engines
# ══════════════════════════════════════════════════════════════════════════

def score_all_time_patterns_buyer(engine, idx):
    """Run all 4 time pattern engines."""
    results = {}
    total_bull = 0
    total_bear = 0
    all_reasons = []
    meta = {}

    results["orb"] = score_orb_buyer(engine, idx)
    results["power_hour"] = score_power_hour_buyer(engine, idx)
    results["premarket_gap"] = score_premarket_gap_buyer(engine, idx)
    results["expiry_day"] = score_expiry_day_buyer(engine, idx)

    for name, res in results.items():
        total_bull += res.get("bull", 0)
        total_bear += res.get("bear", 0)
        all_reasons.extend(res.get("reasons", []))
        if name == "expiry_day":
            meta["expiry"] = res.get("meta", {})

    # Cap
    total_bull = min(total_bull, 40)
    total_bear = min(total_bear, 40)

    return {
        "bull": total_bull,
        "bear": total_bear,
        "reasons": all_reasons,
        "engines": results,
        "meta": meta,
    }
