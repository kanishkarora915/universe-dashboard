"""
theta_gate — block scalper entries when theta will eat the trade.

WHY THIS MODULE EXISTS

Audit 2026-05-19 found VELOCITY_EXIT scalper trades:
  19 trades, 0 wins, -₹212,386
  Pattern: "Theta winning, spot flat, premium -3% in 10m"
  Bought option in flat market → theta ate premium before spot moved

THE MATH (real Black-Scholes, not heuristics)

  THETA (from engine.bs_greeks, real BS formula):
    Annual theta from BS → ÷ 252 trading days = per-day theta
    Per-day theta × (N / 375)                = per-N-market-minute theta
    (375 = NSE market minutes 09:15-15:30)

  EXPECTED SPOT MOVE (from realized history):
    Use last 30-min realized RANGE (max - min) of spot ticks
    Assume next N minutes won't exceed this range significantly
    expected_move_N_min = realized_30min_range × (N / 30)

  EXPECTED PREMIUM MOVE (delta-converted):
    expected_premium_move = expected_spot_move × |delta|

  GATE LOGIC:
    if expected_premium_move < |theta_loss| × 2:
        BLOCK entry (theta drag too big for likely gain)

THE × 2 SAFETY FACTOR
  Need premium gain to be at least 2× theta loss for viable trade.
  At 1×, you break even on a perfect entry. At 2×, you have room.

ENV FLAG
  THETA_GATE_ENABLED=on   → blocks failing trades
  THETA_GATE_ENABLED=off  → shadow logs only (default for safety)
  THETA_GATE_SHADOW=on    → always shadow-log (default on)

ROLLBACK: flip env var, restart container.

WHAT THIS DOES NOT DO
  • Does NOT change exit logic (existing VELOCITY_EXIT still works)
  • Does NOT modify open positions
  • Only blocks NEW entries when math doesn't work
"""

from __future__ import annotations
import math
import os
from datetime import datetime
from typing import Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")

# NSE market minutes per trading day (09:15 to 15:30)
NSE_MINUTES_PER_DAY = 375

# Trading days per year (matches engine.py TRADING_DAYS constant)
TRADING_DAYS = 252


# ── Env flags ──────────────────────────────────────────────────────────

def is_theta_gate_enabled() -> bool:
    """Default OFF — only block when explicitly enabled."""
    return os.environ.get("THETA_GATE_ENABLED", "off").lower() == "on"


def is_shadow_logging_enabled() -> bool:
    """Default ON — log comparison even when gate is off."""
    return os.environ.get("THETA_GATE_SHADOW", "on").lower() == "on"


# ── Realized spot move (from spot history) ────────────────────────────

def compute_realized_range(spot_history: list, lookback_minutes: int = 30) -> dict:
    """Compute spot range over last N minutes.

    Args:
        spot_history: list of {"t": iso_timestamp, "ltp": float}
                      ordered oldest-to-newest
        lookback_minutes: window for range calc

    Returns:
        dict {
          "ltp_count": int,        # number of ticks in window
          "high": float,           # max ltp
          "low": float,            # min ltp
          "range": float,          # high - low
          "current": float,        # latest ltp
          "lookback_seconds": int, # actual window size
        }
    """
    if not spot_history:
        return {"ltp_count": 0, "high": 0, "low": 0, "range": 0, "current": 0, "lookback_seconds": 0}

    try:
        now = datetime.now(IST)
        cutoff_secs = lookback_minutes * 60

        recent = []
        for entry in reversed(spot_history):
            try:
                t = datetime.fromisoformat(entry["t"])
                if t.tzinfo is None:
                    t = IST.localize(t)
                age = (now - t).total_seconds()
                if age > cutoff_secs:
                    break
                recent.append(entry)
            except Exception:
                continue

        if not recent:
            return {"ltp_count": 0, "high": 0, "low": 0, "range": 0, "current": 0, "lookback_seconds": 0}

        ltps = [r["ltp"] for r in recent if r.get("ltp", 0) > 0]
        if len(ltps) < 2:
            return {"ltp_count": len(ltps), "high": 0, "low": 0, "range": 0, "current": ltps[0] if ltps else 0, "lookback_seconds": 0}

        # window age = oldest to newest
        oldest_t = datetime.fromisoformat(recent[-1]["t"])
        newest_t = datetime.fromisoformat(recent[0]["t"])
        if oldest_t.tzinfo is None:
            oldest_t = IST.localize(oldest_t)
        if newest_t.tzinfo is None:
            newest_t = IST.localize(newest_t)
        window_secs = (newest_t - oldest_t).total_seconds()

        return {
            "ltp_count": len(ltps),
            "high": max(ltps),
            "low": min(ltps),
            "range": max(ltps) - min(ltps),
            "current": ltps[0],
            "lookback_seconds": window_secs,
        }
    except Exception:
        return {"ltp_count": 0, "high": 0, "low": 0, "range": 0, "current": 0, "lookback_seconds": 0}


def compute_expected_move(
    realized_range: float,
    realized_window_min: float = 30,
    target_window_min: float = 30,
) -> float:
    """Project expected spot move in target_window_min based on what
    actually happened in realized_window_min.

    Conservative: scales linearly with sqrt-of-time (random walk).
        E[range_T] ≈ realized_range × sqrt(T / realized_window)
    """
    if realized_range <= 0 or realized_window_min <= 0:
        return 0.0
    ratio = target_window_min / realized_window_min
    return realized_range * math.sqrt(ratio)


# ── Theta loss conversion ──────────────────────────────────────────────

def theta_loss_over_minutes(daily_theta: float, minutes: int) -> float:
    """Convert a per-trading-day theta into expected loss over N market minutes.

    Args:
        daily_theta: theta per trading day (negative). E.g. -15.0 means
                     option loses ₹15 per day from time decay.
        minutes: how many market minutes the trade is expected to hold

    Returns:
        Expected theta loss in same units (always returned as POSITIVE
        for "loss magnitude" — caller compares against expected gain).
    """
    if daily_theta == 0:
        return 0.0
    # Theta is given per day. NSE has 375 minutes per market day.
    per_min = abs(daily_theta) / NSE_MINUTES_PER_DAY
    return per_min * minutes


# ── Gate decision ──────────────────────────────────────────────────────

def check_theta_gate(
    *,
    option_premium: float,
    daily_theta: float,
    option_delta: float,
    realized_spot_range: float,
    realized_window_min: int = 30,
    hold_minutes: int = 15,
    safety_factor: float = 2.0,
) -> dict:
    """Decide if the trade passes the theta gate.

    Args:
        option_premium: entry premium (₹)
        daily_theta: theta per trading day (negative number from BS)
        option_delta: delta (±0..1). Will be absolute-valued.
        realized_spot_range: max - min spot over realized window
        realized_window_min: window used for realized range
        hold_minutes: expected trade hold time
        safety_factor: minimum gain-to-theta ratio required (default 2.0)

    Returns:
        dict {
          "passes": bool,
          "reason": str,
          "expected_premium_move": float,
          "theta_loss": float,
          "ratio": float,         # premium_move / theta_loss
          "required_ratio": float,
        }
    """
    if option_premium <= 0:
        return {
            "passes": False,
            "reason": "invalid_premium",
            "expected_premium_move": 0,
            "theta_loss": 0,
            "ratio": 0,
            "required_ratio": safety_factor,
        }

    # Project expected spot move over hold window
    expected_spot_move = compute_expected_move(
        realized_range=realized_spot_range,
        realized_window_min=realized_window_min,
        target_window_min=hold_minutes,
    )

    # Convert to expected premium move via delta
    abs_delta = abs(option_delta)
    expected_premium_move = expected_spot_move * abs_delta

    # Compute theta loss over hold window
    theta_loss = theta_loss_over_minutes(daily_theta, hold_minutes)

    # If no theta info, can't decide — be permissive (pass)
    if theta_loss <= 0:
        return {
            "passes": True,
            "reason": "no_theta_data_pass_through",
            "expected_premium_move": expected_premium_move,
            "theta_loss": 0,
            "ratio": float("inf"),
            "required_ratio": safety_factor,
        }

    # If no movement data, can't decide — be permissive
    if realized_spot_range <= 0 or abs_delta <= 0:
        return {
            "passes": True,
            "reason": "no_movement_data_pass_through",
            "expected_premium_move": expected_premium_move,
            "theta_loss": theta_loss,
            "ratio": float("inf"),
            "required_ratio": safety_factor,
        }

    ratio = expected_premium_move / theta_loss

    if ratio < safety_factor:
        return {
            "passes": False,
            "reason": (
                f"THETA_GATE_BLOCK: expected_move ₹{expected_premium_move:.2f} "
                f"(spot {expected_spot_move:.1f}pts × Δ {abs_delta:.2f}) "
                f"< {safety_factor}× theta_loss ₹{theta_loss:.2f} over {hold_minutes}m"
            ),
            "expected_premium_move": round(expected_premium_move, 2),
            "theta_loss": round(theta_loss, 2),
            "ratio": round(ratio, 2),
            "required_ratio": safety_factor,
        }

    return {
        "passes": True,
        "reason": (
            f"THETA_GATE_PASS: expected_move ₹{expected_premium_move:.2f} "
            f"≥ {safety_factor}× theta_loss ₹{theta_loss:.2f} (ratio {ratio:.2f})"
        ),
        "expected_premium_move": round(expected_premium_move, 2),
        "theta_loss": round(theta_loss, 2),
        "ratio": round(ratio, 2),
        "required_ratio": safety_factor,
    }


# ── Integrated helper: compute everything from engine state ───────────

def assess_with_engine(
    *,
    engine,
    idx: str,
    strike: int,
    side: str,  # "CE" or "PE"
    option_premium: float,
    expiry_date: Optional[str] = None,  # "YYYY-MM-DD"
    hold_minutes: int = 15,
    realized_window_min: int = 30,
    safety_factor: float = 2.0,
    risk_free_rate: float = 0.07,
) -> dict:
    """Full theta gate assessment given engine instance + trade params.

    Pulls spot, spot history, computes IV from premium, then theta from
    real Black-Scholes. Returns the gate decision + all underlying numbers.
    """
    try:
        # Lazy import to avoid circular import at module load
        from engine import bs_greeks, implied_vol
    except Exception:
        return {
            "passes": True,
            "reason": "bs_unavailable_pass_through",
            "error": "engine.bs_greeks not importable",
        }

    try:
        spot_token = engine.spot_tokens.get(idx)
        spot_ltp = engine.prices.get(spot_token, {}).get("ltp", 0) if spot_token else 0
        if spot_ltp <= 0:
            return {
                "passes": True,
                "reason": "no_spot_pass_through",
                "spot": 0,
            }

        # Compute time to expiry (in years)
        T = _years_to_expiry(expiry_date)
        if T <= 0:
            return {
                "passes": True,
                "reason": "expiry_unknown_pass_through",
                "spot": spot_ltp,
                "T_years": T,
            }

        # Solve IV from premium
        iv = implied_vol(option_premium, spot_ltp, strike, T, risk_free_rate, side)
        if iv <= 0:
            return {
                "passes": True,
                "reason": "iv_solve_failed_pass_through",
                "spot": spot_ltp,
                "T_years": T,
            }

        # Get greeks
        greeks = bs_greeks(spot_ltp, strike, T, risk_free_rate, iv, side)
        daily_theta = greeks.get("theta", 0)
        delta = greeks.get("delta", 0)

        # Realized spot range
        spot_hist = getattr(engine, "_spot_history", {}).get(idx, [])
        realized = compute_realized_range(spot_hist, lookback_minutes=realized_window_min)

        # Gate decision
        decision = check_theta_gate(
            option_premium=option_premium,
            daily_theta=daily_theta,
            option_delta=delta,
            realized_spot_range=realized["range"],
            realized_window_min=realized_window_min,
            hold_minutes=hold_minutes,
            safety_factor=safety_factor,
        )

        # Decorate with debug info
        decision["spot"] = spot_ltp
        decision["iv_pct"] = round(iv * 100, 2)
        decision["daily_theta"] = round(daily_theta, 2)
        decision["delta"] = round(delta, 4)
        decision["realized_range"] = round(realized["range"], 2)
        decision["realized_lookback_min"] = realized_window_min
        decision["hold_min"] = hold_minutes
        decision["T_days"] = round(T * 365, 1)

        return decision
    except Exception as e:
        return {
            "passes": True,
            "reason": f"gate_exception_pass_through: {e}",
            "error": str(e),
        }


# ── Helpers ────────────────────────────────────────────────────────────

def _years_to_expiry(expiry_date: Optional[str]) -> float:
    """Compute years to expiry from 'YYYY-MM-DD' string.
    Assumes 3:30 PM IST close on expiry day."""
    if not expiry_date:
        return 0.0
    try:
        exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        exp_dt = IST.localize(exp_dt.replace(hour=15, minute=30))
        now = datetime.now(IST)
        seconds = (exp_dt - now).total_seconds()
        if seconds <= 0:
            return 0.0
        years = seconds / (365 * 24 * 3600)
        return max(years, 1 / (365 * 24 * 6))  # min 10 min in years (safety)
    except Exception:
        return 0.0


# ── Shadow logging ─────────────────────────────────────────────────────

def shadow_log(
    *,
    decision: dict,
    idx: str,
    strike: int,
    action: str,
    source: str,
):
    """Log gate decision with all the math, regardless of whether gate is on."""
    if not is_shadow_logging_enabled():
        return

    status = "PASS" if decision.get("passes") else "BLOCK"
    print(
        f"[THETA_GATE_SHADOW] {source} {action} {idx} {strike} → {status} "
        f"reason='{decision.get('reason', '?')[:140]}' "
        f"spot={decision.get('spot', 0)} "
        f"theta={decision.get('daily_theta', 0)} "
        f"delta={decision.get('delta', 0)} "
        f"realized_range={decision.get('realized_range', 0)} "
        f"hold_min={decision.get('hold_min', 0)} "
        f"ratio={decision.get('ratio', 0)}"
    )


# ── Compact public API ─────────────────────────────────────────────────

def gate_or_pass(
    *,
    engine,
    idx: str,
    strike: int,
    side: str,
    option_premium: float,
    expiry_date: Optional[str],
    action: str,
    hold_minutes: int = 15,
    source: str = "scalper",
) -> dict:
    """Main entry point — returns decision dict + always shadow logs.

    If THETA_GATE_ENABLED=on AND decision.passes is False → caller should
    block the entry. Otherwise let the trade proceed.

    Returns the FULL decision dict so caller can log it / show in UI.
    Caller checks `decision["passes"]` to know whether to fire.
    """
    decision = assess_with_engine(
        engine=engine,
        idx=idx,
        strike=strike,
        side=side,
        option_premium=option_premium,
        expiry_date=expiry_date,
        hold_minutes=hold_minutes,
    )

    shadow_log(decision=decision, idx=idx, strike=strike, action=action, source=source)

    # Caller should check is_theta_gate_enabled() + decision["passes"]
    return decision
