"""
Forecast Engine — predictive narrative builder
─────────────────────────────────────────────────
Takes the 10+ existing intelligence engines and combines them into ONE
actionable forecast that says:
  • Bias direction + persistence (how long will this hold)
  • Key levels to watch (resistance/support/magnet)
  • Expected path (drift / breakout / reversal / pin)
  • Time horizon (15-90 min)
  • Buyer action plan (entry / target / SL / max hold)
  • Why (3-5 reason bullets)
  • Confidence score 0-10

Pulses every 60s from engine scheduler. Cached in-memory.

Inputs (existing engines):
  ✓ engine.get_live_data()           → spot, max_pain, big walls, PCR
  ✓ engine.get_trap_verdict()        → bull_pct, bear_pct, smartBias
  ✓ engine.get_multi_timeframe()     → 5m/15m/1h confluence
  ✓ capitulation_engine.get_live_state() → reversal score
  ✓ oi_delta_tracker.assess()        → 15m OI delta + writer signals
  ✓ smart_money_detector.get_live_state() → drip patterns
  ✓ volatility_detector.classify_regime() → regime + time window
  ✓ market_structure (optional)      → confirmed S/R levels
"""

import time
from typing import Dict, Optional, List


# In-memory cache of latest forecast per index
_forecast_cache: Dict[str, Dict] = {}


def _get_engine_data(engine, idx: str) -> Dict:
    """Pull all needed data from the engine in ONE place. Defensive — never raise."""
    out = {
        "spot": 0, "ltp": 0, "pcr": 1.0, "vix": 18,
        "max_pain": 0, "big_ce_wall": 0, "big_pe_wall": 0,
        "trend": "NEUTRAL", "regime": "NORMAL",
        "from_open_pct": 0, "range_pos": 50,
        "high": 0, "low": 0, "open_price": 0,
        "atm_strike": 0,
    }
    try:
        live = engine.get_live_data() if hasattr(engine, "get_live_data") else {}
        d = live.get(idx.lower(), {}) or {}
        out["ltp"] = out["spot"] = d.get("ltp", 0) or 0
        out["pcr"] = d.get("pcr", 1.0) or 1.0
        out["vix"] = d.get("vix", 18) or 18
        out["max_pain"] = d.get("maxPain", 0) or 0
        out["big_ce_wall"] = d.get("bigCallStrike", 0) or 0
        out["big_pe_wall"] = d.get("bigPutStrike", 0) or 0
        out["trend"] = d.get("trend", "NEUTRAL")
        out["regime"] = d.get("regime", "NORMAL")
        out["from_open_pct"] = d.get("fromOpenPct", 0) or 0
        out["range_pos"] = d.get("rangePosition", 50) or 50
        out["high"] = d.get("high", 0) or 0
        out["low"] = d.get("low", 0) or 0
        out["open_price"] = d.get("openPrice", 0) or 0
        # ATM
        gap = 50 if "NIFTY" in idx and "BANK" not in idx.upper() else 100
        out["atm_strike"] = round(out["spot"] / gap) * gap if out["spot"] > 0 else 0
    except Exception:
        pass

    # Multi-timeframe confluence
    try:
        mtf = engine.get_multi_timeframe() if hasattr(engine, "get_multi_timeframe") else {}
        mt = mtf.get(idx.lower(), {})
        out["mtf_confluence"] = mt.get("confluence", "")
        out["mtf_score"] = mt.get("confScore", 0) or 0
    except Exception:
        out["mtf_confluence"] = ""
        out["mtf_score"] = 0

    # Verdict (bull/bear pct)
    try:
        verdict = engine.get_trap_verdict() if hasattr(engine, "get_trap_verdict") else {}
        v = verdict.get(idx.lower(), {})
        out["bull_pct"] = v.get("bullPct", 50) or 50
        out["bear_pct"] = v.get("bearPct", 50) or 50
        out["win_prob"] = v.get("winProbability", 0) or 0
        out["action"] = v.get("action", "NO TRADE")
        out["smart_bias"] = v.get("smartBias", {})
    except Exception:
        out["bull_pct"] = 50
        out["bear_pct"] = 50
        out["win_prob"] = 0
        out["action"] = "NO TRADE"
        out["smart_bias"] = {}

    # Capitulation
    try:
        from capitulation_engine import get_live_state
        cap = get_live_state() or {}
        cap_idx = (cap.get("results") or {}).get(idx, {})
        out["cap_bull"] = (cap_idx.get("bullish") or {}).get("score", 0)
        out["cap_bear"] = (cap_idx.get("bearish") or {}).get("score", 0)
        out["cap_bull_verdict"] = (cap_idx.get("bullish") or {}).get("verdict", "QUIET")
        out["cap_bear_verdict"] = (cap_idx.get("bearish") or {}).get("verdict", "QUIET")
    except Exception:
        out["cap_bull"] = 0
        out["cap_bear"] = 0
        out["cap_bull_verdict"] = "QUIET"
        out["cap_bear_verdict"] = "QUIET"

    # OI delta tracker
    try:
        from oi_delta_tracker import assess as _oi_assess
        oi = _oi_assess(idx) or {}
        out["ce_oi_15m_pct"] = oi.get("ce_oi_delta_15m_pct", 0) or 0
        out["pe_oi_15m_pct"] = oi.get("pe_oi_delta_15m_pct", 0) or 0
        out["pcr_delta_15m"] = oi.get("pcr_delta_15m", 0) or 0
        out["oi_signals"] = oi.get("signals", {})
    except Exception:
        out["ce_oi_15m_pct"] = 0
        out["pe_oi_15m_pct"] = 0
        out["pcr_delta_15m"] = 0
        out["oi_signals"] = {}

    # Time window + regime recommendation
    try:
        from volatility_detector import classify_regime
        regime = classify_regime(engine) or {}
        out["time_window"] = regime.get("time_window", "")
        out["is_expiry"] = bool(regime.get("is_expiry"))
        out["regime_full"] = regime.get("regime", "NORMAL")
    except Exception:
        out["time_window"] = ""
        out["is_expiry"] = False
        out["regime_full"] = "NORMAL"

    return out


def _pick_path(d: Dict) -> Dict:
    """Decide expected path: drift_to_pain / bounce / continuation / pin / breakout."""
    spot = d["spot"]
    pain = d["max_pain"]
    range_pos = d["range_pos"]
    cap_bull = d["cap_bull"]
    cap_bear = d["cap_bear"]
    mtf = d["mtf_confluence"]
    is_expiry = d["is_expiry"]
    pcr = d["pcr"]
    sigs = d["oi_signals"]

    # ── Pin scenario (near max pain) ──
    # Expiry day: 0.3% threshold (theta crush is brutal, pinning likely)
    # Non-expiry: 0.15% threshold (closer pin needed without expiry math)
    if pain > 0:
        dist_pct = abs(spot - pain) / max(pain, 1) * 100
        pin_threshold = 0.3 if is_expiry else 0.15
        if dist_pct < pin_threshold:
            return {
                "type": "PIN",
                "label": ("Expiry pin near max pain" if is_expiry
                          else "Pin near max pain"),
                "magnet": pain,
                "narrative": (f"Spot ₹{spot:.0f} pinned near max pain ₹{pain:.0f} — "
                              f"{'theta crush dominant' if is_expiry else 'gravitational hold'}, "
                              "range-bound expected"),
            }

    # ── V-bottom forming (capit bull + range_pos low) ──
    if cap_bull >= 4 and range_pos < 30:
        return {
            "type": "V_BOTTOM",
            "label": "V-bottom forming",
            "narrative": f"Capit bull score {cap_bull}/10 + spot near day low ({range_pos}%) → reversal forming",
            "expected_target": d["high"] * 0.998 if d["high"] > 0 else None,
        }

    # ── Inverted-V top (capit bear + range_pos high) ──
    if cap_bear >= 4 and range_pos > 70:
        return {
            "type": "INVERTED_V_TOP",
            "label": "Inverted-V top forming",
            "narrative": f"Capit bear score {cap_bear}/10 + spot near day high ({range_pos}%) → pullback forming",
            "expected_target": d["low"] * 1.002 if d["low"] > 0 else None,
        }

    # ── Drift to max pain (when far from pain on expiry/near-expiry) ──
    if pain > 0 and abs(spot - pain) / pain * 100 > 0.5:
        direction = "DOWN" if spot > pain else "UP"
        return {
            "type": "DRIFT_TO_PAIN",
            "label": f"Drift {direction} to max pain",
            "magnet": pain,
            "narrative": f"Spot ₹{spot:.0f} {((spot-pain)/pain*100):+.2f}% from pain ₹{pain:.0f} → gravitational pull expected",
        }

    # ── Trend continuation (multi-TF aligned + no exhaustion) ──
    if "BULLISH" in mtf and range_pos < 70 and cap_bear < 4:
        return {
            "type": "CONTINUATION_UP",
            "label": "Bullish continuation",
            "narrative": f"Multi-TF {mtf} + range pos {range_pos}% (room to run)",
        }
    if "BEARISH" in mtf and range_pos > 30 and cap_bull < 4:
        return {
            "type": "CONTINUATION_DOWN",
            "label": "Bearish continuation",
            "narrative": f"Multi-TF {mtf} + range pos {range_pos}% (room to fall)",
        }

    # ── Range-bound (writers active, no clear direction) ──
    if sigs.get("ce_writer_adding") and sigs.get("pe_writer_adding"):
        return {
            "type": "RANGE_BOUND",
            "label": "Range-bound (both walls hardening)",
            "narrative": "CE + PE writers both adding → spot stuck between walls",
        }

    return {
        "type": "UNCLEAR",
        "label": "Unclear setup",
        "narrative": "Multiple signals conflict — wait for confirmation",
    }


def _pick_levels(d: Dict) -> Dict:
    """Identify resistance / support / magnet levels."""
    spot = d["spot"]
    big_ce = d["big_ce_wall"]
    big_pe = d["big_pe_wall"]
    pain = d["max_pain"]
    high = d["high"]
    low = d["low"]
    open_p = d["open_price"]

    # Resistance candidates: walls/highs above spot
    resistance = []
    for lvl in (big_ce, high, open_p):
        if lvl > spot and lvl not in resistance:
            resistance.append(lvl)
    resistance = sorted([r for r in resistance if r > 0])[:3]

    # Support candidates: walls/lows below spot
    support = []
    for lvl in (big_pe, low, open_p):
        if 0 < lvl < spot and lvl not in support:
            support.append(lvl)
    support = sorted([s for s in support if s > 0], reverse=True)[:3]

    return {
        "resistance": resistance,
        "support": support,
        "magnet": pain if pain > 0 else None,
    }


def _pick_horizon(d: Dict) -> int:
    """Estimate time horizon in minutes for forecast validity."""
    tw = d["time_window"]
    if tw == "OPENING_FIRST_5MIN":
        return 15
    if tw == "MORNING_TREND":
        return 60
    if tw == "MID_MORNING":
        return 45
    if tw == "LUNCH_CHOP":
        return 30  # short — chop reverses fast
    if tw == "AFTERNOON":
        return 60
    if tw == "POWER_HOUR":
        return 30
    if tw == "CLOSING":
        return 15
    return 45  # default


def _build_action_plan(d: Dict, path: Dict, levels: Dict) -> Dict:
    """Concrete buyer action: wait_for / then_buy / target / sl / max_hold."""
    spot = d["spot"]
    atm = d["atm_strike"]
    cap_bull = d["cap_bull"]
    cap_bear = d["cap_bear"]
    bull_pct = d["bull_pct"]
    bear_pct = d["bear_pct"]
    is_expiry = d["is_expiry"]

    # Expiry uses ATM strikes (not OTM)
    strike_offset = 0 if is_expiry else 50  # OTM bias on regular days

    # Helper: pick CE/PE strike
    gap = 50 if "NIFTY" in d.get("idx", "NIFTY") and "BANK" not in d.get("idx", "").upper() else 100

    if path["type"] == "V_BOTTOM":
        target_spot = path.get("expected_target") or (spot + (d["high"] - spot) * 0.6)
        return {
            "wait_for": f"Confirmation candle (spot bounces +0.1%)",
            "then_buy": f"BUY {atm} CE",
            "target_premium_pct": "+15-25%",
            "target_spot": round(target_spot, 1),
            "sl": f"Spot below ₹{spot - gap:.0f} (CE → 0)",
            "max_hold_min": _pick_horizon(d),
            "qty": "Half size (V-bottom can fail)",
        }
    if path["type"] == "INVERTED_V_TOP":
        target_spot = path.get("expected_target") or (spot - (spot - d["low"]) * 0.6)
        return {
            "wait_for": f"Confirmation candle (spot drops -0.1%)",
            "then_buy": f"BUY {atm} PE",
            "target_premium_pct": "+15-25%",
            "target_spot": round(target_spot, 1),
            "sl": f"Spot above ₹{spot + gap:.0f} (PE → 0)",
            "max_hold_min": _pick_horizon(d),
            "qty": "Half size (V-top can fail)",
        }
    if path["type"] == "DRIFT_TO_PAIN":
        # Buy in direction of drift
        if spot > path["magnet"]:
            # Drifting down to pain
            return {
                "wait_for": "Spot below VWAP / 9:45 high",
                "then_buy": f"BUY {atm - gap} PE (one ITM)",
                "target_premium_pct": "+10-15%",
                "target_spot": round(path["magnet"], 1),
                "sl": f"Spot back above ₹{spot + gap:.0f}",
                "max_hold_min": _pick_horizon(d),
                "qty": "Standard",
            }
        else:
            return {
                "wait_for": "Spot above VWAP / 9:45 low",
                "then_buy": f"BUY {atm + gap} CE (one ITM)",
                "target_premium_pct": "+10-15%",
                "target_spot": round(path["magnet"], 1),
                "sl": f"Spot back below ₹{spot - gap:.0f}",
                "max_hold_min": _pick_horizon(d),
                "qty": "Standard",
            }
    if path["type"] == "PIN":
        return {
            "wait_for": "Don't buy directional",
            "then_buy": "AVOID — theta crush dominant",
            "target_premium_pct": "—",
            "target_spot": None,
            "sl": "—",
            "max_hold_min": 0,
            "qty": "ZERO — sit out the pin",
        }
    if path["type"] in ("CONTINUATION_UP",):
        return {
            "wait_for": "Pullback to nearest support",
            "then_buy": f"BUY {atm} CE on bounce",
            "target_premium_pct": "+15-30%",
            "target_spot": round(levels["resistance"][0] if levels["resistance"] else spot * 1.005, 1),
            "sl": f"Spot below ₹{(levels['support'][0] if levels['support'] else spot - gap):.0f}",
            "max_hold_min": _pick_horizon(d),
            "qty": "Standard",
        }
    if path["type"] == "CONTINUATION_DOWN":
        return {
            "wait_for": "Pullback to nearest resistance",
            "then_buy": f"BUY {atm} PE on rejection",
            "target_premium_pct": "+15-30%",
            "target_spot": round(levels["support"][0] if levels["support"] else spot * 0.995, 1),
            "sl": f"Spot above ₹{(levels['resistance'][0] if levels['resistance'] else spot + gap):.0f}",
            "max_hold_min": _pick_horizon(d),
            "qty": "Standard",
        }
    if path["type"] == "RANGE_BOUND":
        return {
            "wait_for": "Spot near support/resistance edge",
            "then_buy": "Sell theta or fade extremes",
            "target_premium_pct": "Quick scalp +10%",
            "target_spot": None,
            "sl": "Tight SL — wide range = whipsaw",
            "max_hold_min": 20,
            "qty": "Quarter size",
        }

    # UNCLEAR
    return {
        "wait_for": "Wait for clearer signal",
        "then_buy": "—",
        "target_premium_pct": "—",
        "target_spot": None,
        "sl": "—",
        "max_hold_min": 0,
        "qty": "ZERO — no trade",
    }


def _calc_confidence(d: Dict, path: Dict) -> float:
    """0-10 confidence based on signal alignment."""
    score = 5.0
    # Path-type baseline
    if path["type"] == "PIN":
        score = 8.0  # high confidence (theta math)
    elif path["type"] in ("V_BOTTOM", "INVERTED_V_TOP"):
        score = max(d["cap_bull"], d["cap_bear"])  # use capit score directly
    elif path["type"] == "DRIFT_TO_PAIN":
        score = 6.5
    elif path["type"] in ("CONTINUATION_UP", "CONTINUATION_DOWN"):
        score = 5.0 + d["mtf_score"] * 0.3  # boost by multi-TF strength
    elif path["type"] == "RANGE_BOUND":
        score = 4.0
    else:  # UNCLEAR
        score = 2.5

    # Adjustments
    if d["is_expiry"]:
        score += 1.0 if path["type"] == "PIN" else -0.5  # pin good, others bad
    if d["regime_full"] == "EXTREME":
        score -= 2.0
    if d["time_window"] in ("MORNING_TREND", "POWER_HOUR"):
        score += 0.5

    return round(max(0.0, min(10.0, score)), 1)


def _build_why(d: Dict, path: Dict, levels: Dict) -> List[str]:
    """3-5 reason bullets."""
    why = []
    # Multi-TF
    if d["mtf_confluence"]:
        why.append(f"Multi-TF: {d['mtf_confluence']} (score {d['mtf_score']})")
    # Capitulation
    if d["cap_bull"] >= 3:
        why.append(f"Capit BULL {d['cap_bull']:.1f}/10 — reversal up forming")
    if d["cap_bear"] >= 3:
        why.append(f"Capit BEAR {d['cap_bear']:.1f}/10 — reversal down forming")
    # OI signals
    sigs = d["oi_signals"]
    if sigs.get("ce_writer_covering"):
        why.append(f"CE writers covering ({d['ce_oi_15m_pct']:+.1f}%) — ceiling breaking")
    if sigs.get("ce_writer_adding"):
        why.append(f"CE writers adding ({d['ce_oi_15m_pct']:+.1f}%) — ceiling forming")
    if sigs.get("pe_writer_covering"):
        why.append(f"PE writers covering ({d['pe_oi_15m_pct']:+.1f}%) — floor breaking")
    if sigs.get("pe_writer_adding"):
        why.append(f"PE writers adding ({d['pe_oi_15m_pct']:+.1f}%) — floor forming")
    # Max pain
    if d["max_pain"] > 0:
        dist = abs(d["spot"] - d["max_pain"]) / d["max_pain"] * 100
        if dist < 0.5:
            why.append(f"Pinned near max pain ₹{d['max_pain']:.0f} ({dist:.2f}%)")
        else:
            direction = "above" if d["spot"] > d["max_pain"] else "below"
            why.append(f"Spot {direction} max pain ₹{d['max_pain']:.0f} ({dist:.2f}% gap)")
    # Range position
    if d["range_pos"] < 25:
        why.append(f"Spot near day low (range pos {d['range_pos']}%) — bounce zone")
    elif d["range_pos"] > 75:
        why.append(f"Spot near day high (range pos {d['range_pos']}%) — pullback zone")
    # Time window
    if d["time_window"] in ("LUNCH_CHOP", "OPENING_FIRST_5MIN", "CLOSING"):
        why.append(f"Time window {d['time_window']} — trade with caution")

    return why[:5]  # cap at 5


def build_forecast(engine, idx: str) -> Dict:
    """Build the full forecast dict for one index. ALWAYS returns dict, never raises."""
    try:
        d = _get_engine_data(engine, idx)
        d["idx"] = idx

        path = _pick_path(d)
        levels = _pick_levels(d)
        horizon_min = _pick_horizon(d)
        action = _build_action_plan(d, path, levels)
        why = _build_why(d, path, levels)
        confidence = _calc_confidence(d, path)

        # Bias label
        if path["type"] in ("CONTINUATION_UP", "V_BOTTOM"):
            bias = "BULLISH"
        elif path["type"] in ("CONTINUATION_DOWN", "INVERTED_V_TOP"):
            bias = "BEARISH"
        elif path["type"] == "DRIFT_TO_PAIN":
            bias = "DOWN-to-pain" if d["spot"] > d["max_pain"] else "UP-to-pain"
        elif path["type"] == "PIN":
            bias = "PIN"
        elif path["type"] == "RANGE_BOUND":
            bias = "RANGE"
        else:
            bias = "UNCLEAR"

        return {
            "idx": idx,
            "spot": d["spot"],
            "atm": d["atm_strike"],
            "bias": bias,
            "confidence": confidence,
            "horizon_min": horizon_min,
            "path": path,
            "key_levels": levels,
            "buyer_action": action,
            "why": why,
            "context": {
                "regime": d["regime_full"],
                "time_window": d["time_window"],
                "is_expiry": d["is_expiry"],
                "vix": d["vix"],
                "pcr": d["pcr"],
                "range_pos": d["range_pos"],
                "from_open_pct": d["from_open_pct"],
                "mtf": d["mtf_confluence"],
                "cap_bull": d["cap_bull"],
                "cap_bear": d["cap_bear"],
                "bull_pct": d["bull_pct"],
                "bear_pct": d["bear_pct"],
            },
            "ts": time.time(),
        }
    except Exception as e:
        import traceback
        return {
            "idx": idx,
            "error": str(e),
            "trace": traceback.format_exc(),
            "ts": time.time(),
        }


def pulse(engine) -> Dict:
    """Pulse function — called every 60s from engine scheduler.
    Builds + caches forecast for both indices."""
    out = {"ts": time.time(), "results": {}}
    for idx in ("NIFTY", "BANKNIFTY"):
        try:
            forecast = build_forecast(engine, idx)
            _forecast_cache[idx] = forecast
            out["results"][idx] = forecast
        except Exception as e:
            out["results"][idx] = {"error": str(e)}
    return out


def get_live_state() -> Dict:
    """Return cached forecasts (for API consumption)."""
    return {
        "ts": time.time(),
        "results": dict(_forecast_cache),
    }


def get_forecast(idx: str) -> Optional[Dict]:
    """Get cached forecast for a single index."""
    return _forecast_cache.get(idx)
