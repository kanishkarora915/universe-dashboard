"""
Quality Score (A8) — Per-trade quality grading beyond just confidence %.

Measures 5 dimensions:
  1. Engine alignment   (how many engines vote same direction)
  2. Signal strength    (weighted score vs unweighted)
  3. Time-of-day fit    (is current time historically good for this signal?)
  4. Volatility fit     (does regime support this trade?)
  5. OI confirmation    (does OI direction match action?)

Output: 0-10 stars.

Use: Only enter trades with quality >= 6 stars.
"""

from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


def _get_time_window():
    now = ist_now()
    hm = now.hour * 100 + now.minute
    if hm < 920: return "OPENING_FIRST_5MIN"
    if hm < 1030: return "MORNING_TREND"
    if hm < 1130: return "MID_MORNING"
    if hm < 1230: return "LUNCH_CHOP"
    if hm < 1400: return "AFTERNOON"
    if hm < 1515: return "POWER_HOUR"
    return "CLOSING"


# Time window ratings per action (historical pattern)
TIME_QUALITY = {
    "MORNING_TREND": 9,        # Best
    "POWER_HOUR": 8,
    "AFTERNOON": 7,
    "MID_MORNING": 5,
    "LUNCH_CHOP": 2,            # Worst
    "OPENING_FIRST_5MIN": 1,
    "CLOSING": 3,
}


def calculate_quality(verdict_data, action, idx, engine=None):
    """Calculate quality score 0-10.

    verdict_data: full verdict dict with engineScores, bullPct, bearPct
    action: BUY CE / BUY PE
    idx: NIFTY / BANKNIFTY
    engine: market engine reference (for OI / volatility lookup)
    """
    score = 0
    breakdown = {}
    reasons = []

    is_ce = "CE" in (action or "")

    # ── 1. ENGINE ALIGNMENT (0-3 stars) ──
    eng_scores = verdict_data.get("engineScores", {})
    bull_engines = sum(1 for v in eng_scores.values() if (v or 0) > 0)
    bear_engines = sum(1 for v in eng_scores.values() if (v or 0) < 0)
    total_voting = bull_engines + bear_engines

    if total_voting > 0:
        agreement = bull_engines / total_voting if is_ce else bear_engines / total_voting
        if agreement >= 0.85:
            align_score = 3.0
            reasons.append(f"Strong alignment: {bull_engines if is_ce else bear_engines}/{total_voting} engines")
        elif agreement >= 0.70:
            align_score = 2.0
            reasons.append(f"Good alignment: {bull_engines if is_ce else bear_engines}/{total_voting}")
        elif agreement >= 0.55:
            align_score = 1.0
        else:
            align_score = 0
            reasons.append(f"Weak alignment ({agreement*100:.0f}%)")
    else:
        align_score = 0
    breakdown["alignment"] = align_score
    score += align_score

    # ── 2. SIGNAL STRENGTH (0-2.5 stars) ──
    bullPct = verdict_data.get("bullPct", 0)
    bearPct = verdict_data.get("bearPct", 0)
    target_pct = bullPct if is_ce else bearPct

    if target_pct >= 80:
        strength_score = 2.5
        reasons.append(f"Very strong signal {target_pct}%")
    elif target_pct >= 70:
        strength_score = 2.0
    elif target_pct >= 60:
        strength_score = 1.5
    elif target_pct >= 55:
        strength_score = 1.0
    else:
        strength_score = 0.5
    breakdown["strength"] = strength_score
    score += strength_score

    # ── 3. TIME-OF-DAY FIT (0-2 stars) ──
    tw = _get_time_window()
    tw_rating = TIME_QUALITY.get(tw, 5)
    time_score = (tw_rating / 10) * 2.0
    breakdown["time_window"] = time_score
    breakdown["time_window_name"] = tw
    if tw_rating < 4:
        reasons.append(f"⚠️ Poor time window: {tw}")
    elif tw_rating >= 8:
        reasons.append(f"Good time: {tw}")
    score += time_score

    # ── 4. VOLATILITY FIT (0-1.5 stars) ──
    vol_score = 1.5  # default if can't check
    if engine:
        try:
            from volatility_detector import classify_regime
            regime = classify_regime(engine)
            r = regime.get("regime", "NORMAL")
            if r == "EXTREME":
                vol_score = 0
                reasons.append(f"EXTREME volatility — block")
            elif "EXPIRY" in r:
                vol_score = 0.5
                reasons.append("Expiry day — high risk")
            elif "HIGH-VOL" in r:
                vol_score = 0.7
                reasons.append("High volatility")
            else:
                vol_score = 1.5
        except Exception:
            pass
    breakdown["volatility"] = vol_score
    score += vol_score

    # ── 5. OI CONFIRMATION (0-1 star) ──
    oi_score = 0.5  # neutral default
    if engine:
        try:
            chain = engine.chains.get(idx, {})
            spot_token = engine.spot_tokens.get(idx)
            spot = engine.prices.get(spot_token, {}).get("ltp", 0) if spot_token else 0
            if spot > 0 and chain:
                from engine import INDEX_CONFIG
                gap = INDEX_CONFIG[idx]["strike_gap"]
                atm = round(spot / gap) * gap
                # Simple OI check: PE writers > CE writers → bullish, vice versa
                ce_oi_atm = sum(chain.get(atm + i*gap, {}).get("ce_oi", 0) for i in range(-3, 4))
                pe_oi_atm = sum(chain.get(atm + i*gap, {}).get("pe_oi", 0) for i in range(-3, 4))
                if pe_oi_atm > 0 and ce_oi_atm > 0:
                    pcr = pe_oi_atm / ce_oi_atm
                    if is_ce and pcr > 1.2:
                        oi_score = 1.0
                        reasons.append(f"OI bullish (PCR {pcr:.2f})")
                    elif not is_ce and pcr < 0.85:
                        oi_score = 1.0
                        reasons.append(f"OI bearish (PCR {pcr:.2f})")
                    elif (is_ce and pcr < 0.85) or (not is_ce and pcr > 1.2):
                        oi_score = 0
                        reasons.append(f"⚠️ OI against (PCR {pcr:.2f})")
                    else:
                        oi_score = 0.5
        except Exception:
            pass
    breakdown["oi_confirmation"] = oi_score
    score += oi_score

    # Cap at 10
    score = min(10, max(0, round(score, 1)))

    # Classify
    if score >= 8:
        grade = "EXCELLENT"
    elif score >= 6.5:
        grade = "GOOD"
    elif score >= 5:
        grade = "OK"
    elif score >= 3.5:
        grade = "WEAK"
    else:
        grade = "AVOID"

    return {
        "score": score,
        "grade": grade,
        "breakdown": breakdown,
        "reasons": reasons,
        "min_recommended": 6.0,  # below this = skip trade
        "passes": score >= 6.0,
    }
