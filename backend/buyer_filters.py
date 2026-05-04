"""
Buyer-Specific Pre-Entry Filters
─────────────────────────────────
Three filters that catch buyer's most expensive mistakes:

1. VEGA + THETA WARNING
   Compute theta burn forecast + vega exposure pre-entry.
   Block ATM buys when VIX > 25 (vega bomb).
   Warn when theta cost > 3% in 15 min.

2. PREMIUM PUMP DETECTOR
   Block strike if premium pumped >25% from day open
   (chasing late = top entry, immediate decay).

3. MAX PAIN MAGNETISM (expiry day)
   Block ATM buys on expiry day if spot within 0.3% of max pain
   AND time > 1 PM (high pin probability).

All filters return (allowed, reason, qty_multiplier).
Same pattern as spread_filter — read-only quality checks.
"""

import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional, List


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DAY_OPEN_DB = str(_DATA_DIR / "day_open_premiums.db")

IST = timezone(timedelta(hours=5, minutes=30))


# ── Day open premium capture (for pump detection) ─────────────────────

def _init_day_open_db():
    conn = sqlite3.connect(DAY_OPEN_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day_open_premiums (
            date TEXT,
            idx TEXT,
            strike INTEGER,
            ce_open REAL,
            pe_open REAL,
            spot_open REAL,
            captured_at TEXT,
            PRIMARY KEY (date, idx, strike)
        )
    """)
    conn.commit()
    conn.close()


def capture_day_open(engine):
    """Call once at 9:15:30 IST to capture all NTM strike premiums.
    Used by premium pump detector to compute pump_pct."""
    _init_day_open_db()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    captured_at = datetime.now(IST).isoformat()
    conn = sqlite3.connect(DAY_OPEN_DB)

    for idx in ("NIFTY", "BANKNIFTY"):
        try:
            spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
            spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
            chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
            if spot <= 0 or not chain:
                continue
            gap = 50 if idx == "NIFTY" else 100
            atm = round(spot / gap) * gap
            for offset in range(-15, 16):
                strike = atm + offset * gap
                sd = chain.get(strike) or chain.get(str(strike)) or {}
                if not isinstance(sd, dict):
                    continue
                ce_ltp = sd.get("ce_ltp", 0) or 0
                pe_ltp = sd.get("pe_ltp", 0) or 0
                if ce_ltp <= 0 and pe_ltp <= 0:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO day_open_premiums
                    (date, idx, strike, ce_open, pe_open, spot_open, captured_at)
                    VALUES (?,?,?,?,?,?,?)
                """, (today, idx, int(strike), ce_ltp, pe_ltp, spot, captured_at))
        except Exception as e:
            print(f"[BUYER-FILTERS] day-open capture err for {idx}: {e}")

    conn.commit()
    conn.close()
    print(f"[BUYER-FILTERS] Day open premiums captured at {captured_at}")


def get_day_open_premium(idx: str, strike: int, side: str) -> Optional[float]:
    """Get the captured 9:15 premium for a strike."""
    _init_day_open_db()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DAY_OPEN_DB)
    col = "ce_open" if side.upper() == "CE" else "pe_open"
    row = conn.execute(
        f"SELECT {col} FROM day_open_premiums WHERE date=? AND idx=? AND strike=?",
        (today, idx.upper(), int(strike))
    ).fetchone()
    conn.close()
    if row and row[0] and row[0] > 0:
        return row[0]
    return None


# ── Filter 1: Premium Pump Detector ───────────────────────────────────

def check_premium_pump(idx: str, strike: int, side: str,
                       current_premium: float) -> Tuple[bool, str, float]:
    """Block if premium pumped >25% from day open."""
    open_prem = get_day_open_premium(idx, strike, side)
    if not open_prem or current_premium <= 0:
        return True, "no day-open data — allow", 1.0

    pump_pct = (current_premium - open_prem) / open_prem * 100
    abs_pump = abs(pump_pct)

    if abs_pump >= 25:
        return False, (
            f"PREMIUM_PUMP: {idx} {strike} {side} pumped {pump_pct:+.1f}% "
            f"from open ₹{open_prem} → now ₹{current_premium}. Top entry, decay risk."
        ), 0.0
    if abs_pump >= 15:
        return True, (
            f"PUMP_WARN: premium {pump_pct:+.1f}% from open. Wait for retracement to <10%."
        ), 0.5
    return True, "premium OK vs day open", 1.0


# ── Filter 2: Max Pain Magnetism (expiry day) ─────────────────────────

def is_expiry_day_for(idx: str, engine) -> bool:
    """Best-effort detection — Tuesday for NIFTY, similar weekly for BN."""
    now = datetime.now(IST)
    weekday = now.weekday()
    # NIFTY weekly expiry has changed multiple times — Tuesday most recent
    # Conservative: treat Tuesday as NIFTY weekly expiry
    if idx.upper() == "NIFTY" and weekday == 1:  # Tuesday
        return True
    # Last Thursday of month = monthly for both
    next_week = now + timedelta(days=7)
    if weekday == 3 and next_week.month != now.month:  # last Thursday
        return True
    return False


def check_max_pain_magnetism(engine, idx: str, action: str,
                              strike: int) -> Tuple[bool, str, float]:
    """Block ATM buys on expiry day near max pain."""
    if not is_expiry_day_for(idx, engine):
        return True, "not expiry day", 1.0

    now = datetime.now(IST)
    if now.hour < 13:
        return True, "expiry day but before 1 PM", 1.0

    try:
        live = engine.get_live_data() if hasattr(engine, "get_live_data") else {}
        idx_data = live.get(idx.lower(), {})
        spot = idx_data.get("ltp", 0) or 0
        max_pain = idx_data.get("maxPain", 0) or 0
    except Exception:
        return True, "live data unavailable", 1.0

    if spot <= 0 or max_pain <= 0:
        return True, "spot or max pain missing", 1.0

    # Distance from spot to max pain
    dist_pct = abs(spot - max_pain) / spot * 100

    # Strike proximity to spot (ATM = within ±0.5%)
    strike_dist_pct = abs(strike - spot) / spot * 100
    is_atm = strike_dist_pct < 0.5

    if not is_atm:
        return True, f"strike {strike} not ATM (spot {spot}, dist {strike_dist_pct:.2f}%)", 1.0

    # ATM buy on expiry day, near max pain → magnet kills you
    if dist_pct < 0.3:
        return False, (
            f"MAX_PAIN_MAGNET: expiry day, spot {spot:.1f} within "
            f"{dist_pct:.2f}% of max pain {max_pain:.0f}. ATM buys disabled "
            f"till 3:25 PM (theta + pin death)."
        ), 0.0
    if dist_pct < 0.5:
        return True, (
            f"MAX_PAIN_WARN: spot {spot:.1f} within {dist_pct:.2f}% of "
            f"max pain {max_pain:.0f}. Reduce qty 50%."
        ), 0.5
    return True, "max pain distance OK", 1.0


# ── Filter 3: Vega + Theta Pre-Entry Warning ──────────────────────────

def check_vega_theta(engine, idx: str, action: str, strike: int,
                     current_premium: float) -> Dict:
    """Compute theta drag forecast + vega risk. Returns dict with warnings.

    NOT a hard blocker — informational + optional VIX>25 ATM block.
    """
    out = {
        "allowed": True,
        "qty_multiplier": 1.0,
        "warnings": [],
        "theta_per_min": None,
        "theta_15min_loss_rs": None,
        "theta_15min_loss_pct": None,
        "vega": None,
        "vega_risk_pct": None,
        "vix": None,
        "regime": "SAFE",
    }
    try:
        # VIX from engine
        vix_tok = engine.spot_tokens.get("VIX") if hasattr(engine, "spot_tokens") else None
        vix = engine.prices.get(vix_tok, {}).get("ltp", 0) if vix_tok else 0
        out["vix"] = round(vix, 2) if vix else None

        # Try to pull greeks if module exists
        theta = None
        vega = None
        try:
            from options_greeks import compute_greeks_for_strike
            greeks = compute_greeks_for_strike(engine, idx, strike, action)
            theta = greeks.get("theta") if greeks else None
            vega = greeks.get("vega") if greeks else None
        except Exception:
            pass

        # Theta forecast (₹/min decay)
        if theta is not None:
            theta_per_min = abs(theta) / 375  # market minutes per day
            forecast_15min = theta_per_min * 15
            forecast_pct = (forecast_15min / current_premium * 100) if current_premium > 0 else 0
            out["theta_per_min"] = round(theta_per_min, 3)
            out["theta_15min_loss_rs"] = round(forecast_15min, 2)
            out["theta_15min_loss_pct"] = round(forecast_pct, 2)
            if forecast_pct > 5:
                out["warnings"].append(
                    f"THETA_DRAG: {forecast_pct:.1f}% loss in 15min if no spot move (₹{forecast_15min:.1f}/15m)"
                )
                out["regime"] = "CAUTION"

        # Vega risk
        if vega is not None and current_premium > 0:
            # vega_risk = % of premium attributed to volatility component
            vega_risk = abs(vega) / current_premium * 100
            out["vega"] = round(vega, 3)
            out["vega_risk_pct"] = round(vega_risk, 2)

        # Hard rule: VIX > 25 + ATM buy → block (vega bomb)
        spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
        spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
        if spot > 0:
            strike_dist_pct = abs(strike - spot) / spot * 100
            is_atm = strike_dist_pct < 0.5
            if vix > 25 and is_atm:
                out["allowed"] = False
                out["regime"] = "DANGER"
                out["warnings"].append(
                    f"VEGA_BOMB: VIX {vix:.1f} > 25 + ATM buy. IV crush risk extreme. BLOCKED."
                )
            elif vix > 22 and is_atm:
                out["qty_multiplier"] = 0.5
                out["regime"] = "CAUTION"
                out["warnings"].append(
                    f"VEGA_RISK: VIX {vix:.1f} elevated + ATM. Reduce qty 50%."
                )
    except Exception as e:
        out["warnings"].append(f"vega/theta calc err: {e}")
    return out


# ── Combined gate for trade_logger ────────────────────────────────────

def check_buyer_filters(engine, idx: str, action: str, strike: int,
                         current_premium: float) -> Tuple[bool, str, float, Dict]:
    """Run all 3 buyer filters. Return combined decision.

    Returns:
      (allowed, reasons_list, qty_multiplier, details)
    """
    reasons = []
    qty_mult = 1.0
    details = {}

    side = "CE" if "CE" in action.upper() else "PE"

    # Filter 1: Premium pump
    allow_pump, pump_reason, pump_mult = check_premium_pump(
        idx, strike, side, current_premium
    )
    details["premium_pump"] = {"allowed": allow_pump, "reason": pump_reason, "qty_mult": pump_mult}
    if not allow_pump:
        return False, pump_reason, 0.0, details
    qty_mult = min(qty_mult, pump_mult)
    if pump_mult < 1.0:
        reasons.append(pump_reason)

    # Filter 2: Max pain magnetism
    allow_mp, mp_reason, mp_mult = check_max_pain_magnetism(
        engine, idx, action, strike
    )
    details["max_pain"] = {"allowed": allow_mp, "reason": mp_reason, "qty_mult": mp_mult}
    if not allow_mp:
        return False, mp_reason, 0.0, details
    qty_mult = min(qty_mult, mp_mult)
    if mp_mult < 1.0:
        reasons.append(mp_reason)

    # Filter 3: Vega + theta
    vt = check_vega_theta(engine, idx, action, strike, current_premium)
    details["vega_theta"] = vt
    if not vt["allowed"]:
        reasons.append("; ".join(vt["warnings"]))
        return False, "; ".join(reasons), 0.0, details
    qty_mult = min(qty_mult, vt["qty_multiplier"])
    if vt["qty_multiplier"] < 1.0:
        reasons.extend(vt["warnings"])

    if not reasons:
        return True, "all buyer filters OK", 1.0, details
    return True, "; ".join(reasons), qty_mult, details
