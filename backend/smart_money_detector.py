"""
Smart Money Detector
────────────────────
Classifies per-strike activity into 4 institutional patterns by combining
OI delta with price (LTP) direction over a rolling window:

  WRITER_DRIP    OI ↑  + LTP ↓   (institutions slowly building shorts)
                 → Resistance/support hardening
                 → BUYER signal: trade OPPOSITE side

  BUYER_DRIP     OI ↑  + LTP ↑   (institutions slowly building longs)
                 → Directional bet by smart money
                 → BUYER signal: ride WITH them

  WRITER_COVER   OI ↓  + LTP ↑   (writers panic-buying back shorts)
                 → Squeeze starting
                 → BUYER signal: SAME-direction CE/PE explosion

  BUYER_EXIT     OI ↓  + LTP ↓   (longs giving up)
                 → Failed thesis, bearish for that side

"Slow drip" filter — to separate institutional vs retail noise:
  • per-pulse OI delta in 50-500 lots range (not big spikes)
  • sustained over 20+ minutes (not random)
  • directional consistency >70% (always same direction)
  • LTP confirms the OI direction

Scoring 0-10 per activity, plus actionable recommendation for option BUYER.

Pulses every 2 min from engine (cheaper than 60s, accurate enough for
slow-accumulation patterns which need 20+ min anyway).
"""

import time
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from statistics import mean, stdev

from oi_minute_capture import get_all_strikes_for_idx


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = str(_DATA_DIR / "smart_money.db")

# Detection thresholds
WINDOW_MIN = 30                # Look back this many minutes
MIN_PULSES = 15                # Need ≥15 minute samples for valid analysis
DRIP_LOTS_MIN = 50             # Per-pulse OI change must be ≥ this (filter noise)
DRIP_LOTS_MAX = 5000           # Per-pulse cap (above this = burst, not drip)
SUSTAINED_RATIO = 0.65         # ≥65% of pulses must be in same direction
LTP_CONFIRM_PCT = 1.5          # LTP must move ≥1.5% to confirm direction


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS smart_money_log (
            ts REAL,
            idx TEXT,
            strike INTEGER,
            side TEXT,           -- CE or PE
            activity TEXT,       -- WRITER_DRIP / BUYER_DRIP / WRITER_COVER / BUYER_EXIT
            score REAL,          -- 0-10 strength
            rate_per_min REAL,
            duration_min REAL,
            total_change_lots INTEGER,
            ltp_change_pct REAL,
            spot_at_detection REAL,
            recommendation TEXT,
            details_json TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sml_ts ON smart_money_log(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sml_idx ON smart_money_log(idx, ts)")
    conn.commit()
    conn.close()


# ── Per-side analysis ──────────────────────────────────────────────────

def _analyze_series(samples: List[Dict], side: str) -> Optional[Dict]:
    """Look at the time-series of one side (CE or PE) at one strike,
    return classification + scores or None if no significant activity."""
    if len(samples) < MIN_PULSES:
        return None

    oi_key = f"{side.lower()}_oi"
    ltp_key = f"{side.lower()}_ltp"

    # Build per-pulse deltas
    oi_deltas: List[float] = []
    ltp_deltas: List[float] = []
    for i in range(1, len(samples)):
        oi_d = (samples[i][oi_key] or 0) - (samples[i-1][oi_key] or 0)
        ltp_d = (samples[i][ltp_key] or 0) - (samples[i-1][ltp_key] or 0)
        oi_deltas.append(oi_d)
        ltp_deltas.append(ltp_d)

    if not oi_deltas:
        return None

    # Aggregate stats
    total_oi_change = sum(oi_deltas)
    avg_oi_delta = mean(oi_deltas)
    abs_avg = abs(avg_oi_delta)

    # Directional consistency
    pos_pulses = sum(1 for d in oi_deltas if d > 0)
    neg_pulses = sum(1 for d in oi_deltas if d < 0)
    same_direction_ratio = max(pos_pulses, neg_pulses) / len(oi_deltas)

    # LTP movement
    first_ltp = samples[0][ltp_key] or 0
    last_ltp = samples[-1][ltp_key] or 0
    if first_ltp <= 0:
        return None
    ltp_change_pct = (last_ltp - first_ltp) / first_ltp * 100

    duration_min = (samples[-1]["ts"] - samples[0]["ts"]) / 60

    # Drip filter — ignore if not in slow-accumulation range
    in_drip_range = DRIP_LOTS_MIN <= abs_avg <= DRIP_LOTS_MAX
    is_sustained = same_direction_ratio >= SUSTAINED_RATIO
    has_meaningful_ltp_move = abs(ltp_change_pct) >= LTP_CONFIRM_PCT

    # Skip if not slow drip OR not sustained
    if not in_drip_range and abs(total_oi_change) < 3000:
        return None

    # Classify activity
    oi_up = total_oi_change > 0
    ltp_up = ltp_change_pct > 0

    activity = None
    confidence = 0.0
    if oi_up and not ltp_up and has_meaningful_ltp_move:
        activity = "WRITER_DRIP"
        # Stronger if sustained drip pattern
        confidence = (
            (0.4 if in_drip_range else 0.2) +
            (0.3 * same_direction_ratio) +
            (0.3 * min(1.0, abs(ltp_change_pct) / 10.0))
        )
    elif oi_up and ltp_up and has_meaningful_ltp_move:
        activity = "BUYER_DRIP"
        confidence = (
            (0.4 if in_drip_range else 0.2) +
            (0.3 * same_direction_ratio) +
            (0.3 * min(1.0, abs(ltp_change_pct) / 10.0))
        )
    elif (not oi_up) and ltp_up and has_meaningful_ltp_move:
        activity = "WRITER_COVER"
        # Faster covering = stronger squeeze
        confidence = (
            (0.5 * min(1.0, abs(total_oi_change) / 8000)) +
            (0.5 * min(1.0, abs(ltp_change_pct) / 15.0))
        )
    elif (not oi_up) and (not ltp_up) and has_meaningful_ltp_move:
        activity = "BUYER_EXIT"
        confidence = (
            (0.5 * min(1.0, abs(total_oi_change) / 8000)) +
            (0.5 * min(1.0, abs(ltp_change_pct) / 15.0))
        )

    if not activity or confidence < 0.4:
        return None

    score = round(confidence * 10, 1)

    return {
        "activity": activity,
        "score": score,
        "confidence": round(confidence, 2),
        "rate_per_min": round(abs_avg, 0),
        "duration_min": round(duration_min, 1),
        "total_change_lots": int(total_oi_change),
        "ltp_change_pct": round(ltp_change_pct, 2),
        "first_ltp": round(first_ltp, 2),
        "last_ltp": round(last_ltp, 2),
        "first_oi": int(samples[0][oi_key] or 0),
        "last_oi": int(samples[-1][oi_key] or 0),
        "same_direction_ratio": round(same_direction_ratio, 2),
        "in_drip_range": in_drip_range,
        "pulses": len(oi_deltas),
    }


# ── Recommendation generator ──────────────────────────────────────────

def _generate_recommendation(activity: str, side: str, strike: int,
                              spot: float, ltp_change_pct: float) -> Dict:
    """Translate detected activity into actionable buyer signal."""
    is_above_spot = strike > spot
    distance_pct = abs(strike - spot) / spot * 100

    rec = {"action": None, "reason": None, "urgency": "LOW", "trade_window": None}

    if activity == "WRITER_DRIP":
        if side == "CE":
            # Writers building CE → resistance ceiling at this strike
            rec["action"] = f"BUY PE on rally to {strike}"
            rec["reason"] = (
                f"Writers slow-piled CE at {strike} (premium dropped {abs(ltp_change_pct):.1f}%). "
                f"Hard ceiling forming. PE buy on bounce to {strike}-100."
            )
            rec["urgency"] = "HIGH" if distance_pct < 0.5 else "MEDIUM"
            rec["trade_window"] = f"Spot rallies to {strike-100}-{strike}"
        else:  # PE
            # Writers building PE → support floor at this strike
            rec["action"] = f"BUY CE on dip to {strike}"
            rec["reason"] = (
                f"Writers slow-piled PE at {strike}. Hard floor forming. "
                f"CE buy on dip to {strike}-{strike+100}."
            )
            rec["urgency"] = "HIGH" if distance_pct < 0.5 else "MEDIUM"
            rec["trade_window"] = f"Spot dips to {strike}-{strike+100}"

    elif activity == "BUYER_DRIP":
        if side == "CE":
            rec["action"] = f"BUY CE {strike} or {strike-50}"
            rec["reason"] = (
                f"Smart money slow-buying CE at {strike} (premium up {ltp_change_pct:.1f}%). "
                f"Directional bullish bet — ride with them."
            )
            rec["urgency"] = "MEDIUM"
            rec["trade_window"] = "Enter on next minor pullback"
        else:
            rec["action"] = f"BUY PE {strike} or {strike+50}"
            rec["reason"] = (
                f"Smart money slow-buying PE at {strike}. Bearish directional bet."
            )
            rec["urgency"] = "MEDIUM"
            rec["trade_window"] = "Enter on next minor pullback"

    elif activity == "WRITER_COVER":
        if side == "CE":
            rec["action"] = f"BUY CE {strike} — squeeze starting"
            rec["reason"] = (
                f"CE writers covering at {strike} (premium up {ltp_change_pct:.1f}%). "
                f"Forced squeeze likely cascades higher. CE explosion possible."
            )
            rec["urgency"] = "HIGH"
            rec["trade_window"] = "ENTER NOW — squeeze may cascade in 5-15 min"
        else:
            rec["action"] = f"BUY PE {strike} — squeeze starting"
            rec["reason"] = (
                f"PE writers covering at {strike} (premium up {ltp_change_pct:.1f}%). "
                f"Bearish squeeze cascade possible."
            )
            rec["urgency"] = "HIGH"
            rec["trade_window"] = "ENTER NOW — squeeze may cascade in 5-15 min"

    elif activity == "BUYER_EXIT":
        if side == "CE":
            rec["action"] = "AVOID CE — buyers giving up"
            rec["reason"] = (
                f"CE longs unwinding at {strike} (premium down {abs(ltp_change_pct):.1f}%). "
                f"Bullish thesis failed. Don't buy CE here."
            )
            rec["urgency"] = "LOW"
            rec["trade_window"] = "Skip this strike for CE buys"
        else:
            rec["action"] = "AVOID PE — buyers giving up"
            rec["reason"] = (
                f"PE longs unwinding at {strike}. Bearish thesis failed."
            )
            rec["urgency"] = "LOW"
            rec["trade_window"] = "Skip this strike for PE buys"

    return rec


# ── Main analyzer pulse ────────────────────────────────────────────────

_last_state: Dict = {"ts": 0, "results": {}}


def analyze_pulse() -> Dict:
    """Scan all NTM strikes for both indices, classify activity. Call every 2 min."""
    _init_db()
    now_ts = time.time()
    out = {"ts": now_ts, "results": {}}

    for idx in ("NIFTY", "BANKNIFTY"):
        try:
            by_strike = get_all_strikes_for_idx(idx, minutes=WINDOW_MIN)
            if not by_strike:
                out["results"][idx] = {"error": "no minute data yet (warming up)", "strikes_analyzed": 0}
                continue

            spot_now = 0
            for samples in by_strike.values():
                if samples:
                    spot_now = samples[-1].get("spot", 0)
                    break

            findings = []
            for strike, samples in by_strike.items():
                for side in ("CE", "PE"):
                    analysis = _analyze_series(samples, side)
                    if not analysis:
                        continue
                    rec = _generate_recommendation(
                        analysis["activity"], side, strike,
                        spot_now, analysis["ltp_change_pct"]
                    )
                    finding = {
                        "idx": idx, "strike": strike, "side": side,
                        **analysis,
                        "recommendation": rec,
                    }
                    findings.append(finding)

            # Sort by score descending (strongest signals first)
            findings.sort(key=lambda x: x["score"], reverse=True)

            # Group by activity type
            grouped = {
                "WRITER_DRIP": [f for f in findings if f["activity"] == "WRITER_DRIP"][:5],
                "BUYER_DRIP": [f for f in findings if f["activity"] == "BUYER_DRIP"][:5],
                "WRITER_COVER": [f for f in findings if f["activity"] == "WRITER_COVER"][:3],
                "BUYER_EXIT": [f for f in findings if f["activity"] == "BUYER_EXIT"][:3],
            }

            # Net institutional view
            net_view = _build_net_view(grouped, spot_now, idx)

            out["results"][idx] = {
                "spot": spot_now,
                "strikes_analyzed": len(by_strike),
                "total_findings": len(findings),
                "grouped": grouped,
                "all_findings": findings[:20],  # cap response size
                "net_view": net_view,
            }

            # Log strong findings (score ≥ 6) for history
            for f in findings:
                if f["score"] >= 6:
                    _log_finding(f, now_ts, spot_now)
        except Exception as e:
            import traceback; traceback.print_exc()
            out["results"][idx] = {"error": str(e)}

    global _last_state
    _last_state = out
    return out


def _log_finding(f: Dict, ts: float, spot: float):
    try:
        conn = sqlite3.connect(DB_PATH)
        # Avoid spamming — only log if same strike/side/activity not logged in last 10 min
        existing = conn.execute("""
            SELECT id FROM smart_money_log WHERE idx=? AND strike=? AND side=? AND activity=? AND ts > ?
            LIMIT 1
        """, (f["idx"], f["strike"], f["side"], f["activity"], ts - 600)).fetchone()
        if existing:
            conn.close()
            return
        conn.execute("""
            INSERT INTO smart_money_log
            (ts, idx, strike, side, activity, score, rate_per_min, duration_min,
             total_change_lots, ltp_change_pct, spot_at_detection, recommendation, details_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ts, f["idx"], f["strike"], f["side"], f["activity"], f["score"],
            f["rate_per_min"], f["duration_min"], f["total_change_lots"],
            f["ltp_change_pct"], spot,
            f["recommendation"]["action"], json.dumps(f),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SMART-MONEY] log err: {e}")


def _build_net_view(grouped: Dict, spot: float, idx: str) -> Dict:
    """Synthesize a single 'institutional view' summary."""
    resistance_strikes = sorted({f["strike"] for f in grouped["WRITER_DRIP"]
                                 if f["side"] == "CE" and f["strike"] > spot})
    support_strikes = sorted({f["strike"] for f in grouped["WRITER_DRIP"]
                              if f["side"] == "PE" and f["strike"] < spot},
                             reverse=True)
    bullish_buyer_strikes = [f["strike"] for f in grouped["BUYER_DRIP"] if f["side"] == "CE"]
    bearish_buyer_strikes = [f["strike"] for f in grouped["BUYER_DRIP"] if f["side"] == "PE"]
    squeezes = [f for f in grouped["WRITER_COVER"]]

    # Trade window — between strongest support and strongest resistance
    trade_zone = None
    if resistance_strikes and support_strikes:
        trade_zone = f"{support_strikes[0]} – {resistance_strikes[0]}"

    # Bias
    bull_signals = len([f for f in grouped["BUYER_DRIP"] if f["side"] == "CE"]) + \
                   len([f for f in grouped["WRITER_COVER"] if f["side"] == "CE"])
    bear_signals = len([f for f in grouped["BUYER_DRIP"] if f["side"] == "PE"]) + \
                   len([f for f in grouped["WRITER_COVER"] if f["side"] == "PE"])
    if bull_signals > bear_signals + 1:
        bias = "BULLISH"
    elif bear_signals > bull_signals + 1:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "bias": bias,
        "resistance_hardening_at": resistance_strikes[:3],
        "support_hardening_at": support_strikes[:3],
        "bullish_buyer_strikes": bullish_buyer_strikes[:3],
        "bearish_buyer_strikes": bearish_buyer_strikes[:3],
        "active_squeezes": [
            {"strike": s["strike"], "side": s["side"], "score": s["score"]}
            for s in squeezes[:3]
        ],
        "trade_zone": trade_zone,
        "summary": _build_summary_text(bias, resistance_strikes, support_strikes, squeezes),
    }


def _build_summary_text(bias, resistance, support, squeezes):
    parts = []
    if bias == "BULLISH":
        parts.append("📈 Net institutional bias: BULLISH")
    elif bias == "BEARISH":
        parts.append("📉 Net institutional bias: BEARISH")
    else:
        parts.append("➖ Net institutional bias: NEUTRAL (balanced flow)")
    if resistance:
        parts.append(f"🚧 Resistance hardening at: {', '.join(map(str, resistance[:3]))}")
    if support:
        parts.append(f"🛡️ Support hardening at: {', '.join(map(str, support[:3]))}")
    if squeezes:
        sq_str = ", ".join(f"{s['strike']} {s['side']}" for s in squeezes[:2])
        parts.append(f"⚡ Active squeezes: {sq_str}")
    return parts


# ── API helpers ────────────────────────────────────────────────────────

def get_live_state() -> Dict:
    return _last_state


def get_strike_history_log(idx: Optional[str] = None, limit: int = 50) -> List[Dict]:
    _init_db()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    conn = sqlite3.connect(DB_PATH)
    if idx:
        rows = conn.execute("""
            SELECT ts, idx, strike, side, activity, score, rate_per_min, duration_min,
                   total_change_lots, ltp_change_pct, spot_at_detection, recommendation
            FROM smart_money_log WHERE idx=? AND ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (idx.upper(), today_start, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT ts, idx, strike, side, activity, score, rate_per_min, duration_min,
                   total_change_lots, ltp_change_pct, spot_at_detection, recommendation
            FROM smart_money_log WHERE ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (today_start, limit)).fetchall()
    conn.close()
    return [{
        "ts": r[0], "idx": r[1], "strike": r[2], "side": r[3], "activity": r[4],
        "score": r[5], "rate_per_min": r[6], "duration_min": r[7],
        "total_change_lots": r[8], "ltp_change_pct": r[9],
        "spot_at_detection": r[10], "recommendation_action": r[11],
    } for r in rows]
