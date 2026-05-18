"""
7-Regime Classification Engine.

Implements spec §4 + §9.1 pseudocode.

Regimes:
  REAL_RALLY     — sv>0.5, pv>0, syn_v>0, |td|<10, duration>30s
  BULL_TRAP      — sv>0.3, pd<-5, td<-15
  REAL_CRASH     — sv<-0.5, pv<0, syn_v<0, |td|<10, duration>30s
  BEAR_TRAP      — sv<-0.3, pd>5, td>15
  DISTRIBUTION   — spot at recent high, td decreasing 5-15 min
  ACCUMULATION   — spot at recent low, td increasing 5-15 min
  CHURN          — all velocities ~0
  TRANSITIONING  — none of the above

Confidence: 0-100%.
"""

import time
from collections import deque


# Threshold constants (per spec §4)
SV_REAL_THRESHOLD = 0.5
SV_TRAP_THRESHOLD = 0.3
TD_ALIGNED_LIMIT = 10
TD_TRAP_LIMIT = 15
TD_EXTREME = 30
PD_TRAP_THRESHOLD = 5      # |premium_delta| for trap detection
PD_EXTREME = 20            # used in trap confidence formula
DURATION_REAL_MIN_SEC = 30
DURATION_DIVERGENCE_MIN_MIN = 5
DURATION_DIVERGENCE_MAX_MIN = 15
VELOCITY_ZERO_TOL = 0.05


def all_velocities_near_zero(v):
    return (abs(v.get("spot_velocity", 0)) < VELOCITY_ZERO_TOL
            and abs(v.get("premium_velocity", 0)) < VELOCITY_ZERO_TOL
            and abs(v.get("synthetic_velocity", 0)) < VELOCITY_ZERO_TOL)


def _spot_at_recent_extreme(state, side, lookback_bars=300):
    """Check if current spot is near recent high (UPSIDE) or low (DOWNSIDE)."""
    bars = state.bar_buffer.last_n(lookback_bars)
    if len(bars) < 30:
        return False
    spots = [b.get("spot") for b in bars if b.get("spot")]
    if not spots:
        return False
    cur = spots[-1]
    if side == "HIGH":
        return cur >= max(spots) * 0.9985  # within 0.15% of recent high
    return cur <= min(spots) * 1.0015      # within 0.15% of recent low


def _td_trend_for_window(state, direction, window_min=5):
    """Check if trinity_deviation has been steadily moving in direction over window_min minutes.
    direction: 'DECREASING' or 'INCREASING'."""
    bars = state.bar_buffer.last_n(window_min * 60)
    if len(bars) < window_min * 30:
        return False
    devs = [b.get("deviation") for b in bars if b.get("deviation") is not None]
    if len(devs) < 30:
        return False
    # Linear regression slope check
    n = len(devs)
    x = list(range(n))
    mean_x = sum(x) / n
    mean_y = sum(devs) / n
    num = sum((x[i] - mean_x) * (devs[i] - mean_y) for i in range(n))
    den = sum((xi - mean_x) ** 2 for xi in x) or 1
    slope = num / den
    if direction == "DECREASING":
        return slope < -0.005
    return slope > 0.005


def calc_trap_confidence(td, pd_val, duration_secs, oi_concentration_score=0.5):
    """Per spec §5.3 — trap confidence formula.
    trap_confidence = (
        abs(trinity_deviation) / 30 * 0.4 +
        abs(premium_delta) / 20 * 0.3 +
        duration_in_state_minutes / 10 * 0.2 +
        oi_concentration_score * 0.1
    ) * 100  (cap 95)"""
    duration_min = duration_secs / 60.0
    score = (
        min(abs(td) / TD_EXTREME, 1.0) * 0.4 +
        min(abs(pd_val) / PD_EXTREME, 1.0) * 0.3 +
        min(duration_min / 10.0, 1.0) * 0.2 +
        min(oi_concentration_score, 1.0) * 0.1
    ) * 100
    return min(95, round(score, 1))


def classify_regime(state, snapshot, velocities, premium_delta,
                    oi_concentration_score=0.5, expiry_day=False):
    """Main classifier. Returns (regime, confidence, reasons[])."""
    sv = velocities.get("spot_velocity", 0)
    pv = velocities.get("premium_velocity", 0)
    syn_v = velocities.get("synthetic_velocity", 0)
    td = snapshot.get("deviation") or 0
    pd_val = premium_delta or 0
    duration = state.regime_duration_secs()

    reasons = []

    # ── REGIME 1: REAL RALLY ──
    if sv > SV_REAL_THRESHOLD and pv > 0 and syn_v > 0 and abs(td) < TD_ALIGNED_LIMIT:
        if duration > DURATION_REAL_MIN_SEC:
            conf = min(95, 70 + duration / 3.0)
            reasons.append(f"All 3 streams up >30s · sv={sv:.2f} pv={pv:.2f} syn_v={syn_v:.2f} td={td:.1f}")
            return "REAL_RALLY", round(conf, 1), reasons
        return "TRANSITIONING", 50, [f"Bullish align building ({duration:.0f}s/30s)"]

    # ── REGIME 3: REAL CRASH ──
    if sv < -SV_REAL_THRESHOLD and pv < 0 and syn_v < 0 and abs(td) < TD_ALIGNED_LIMIT:
        if duration > DURATION_REAL_MIN_SEC:
            conf = min(95, 70 + duration / 3.0)
            reasons.append(f"All 3 streams down >30s · sv={sv:.2f} pv={pv:.2f} syn_v={syn_v:.2f} td={td:.1f}")
            return "REAL_CRASH", round(conf, 1), reasons
        return "TRANSITIONING", 50, [f"Bearish align building ({duration:.0f}s/30s)"]

    # ── REGIME 2: BULL TRAP ──
    if sv > SV_TRAP_THRESHOLD and pd_val < -PD_TRAP_THRESHOLD and td < -TD_TRAP_LIMIT:
        conf = calc_trap_confidence(td, pd_val, duration, oi_concentration_score)
        reasons.append(f"Spot rising but future premium contracting (pd={pd_val:.1f}) and synthetic lagging (td={td:.1f})")
        reasons.append("Smart money distributing into retail buying — reversal expected")
        return "BULL_TRAP", conf, reasons

    # ── REGIME 4: BEAR TRAP ──
    if sv < -SV_TRAP_THRESHOLD and pd_val > PD_TRAP_THRESHOLD and td > TD_TRAP_LIMIT:
        conf = calc_trap_confidence(td, pd_val, duration, oi_concentration_score)
        reasons.append(f"Spot falling but future premium expanding (pd={pd_val:.1f}) and synthetic leading (td={td:.1f})")
        reasons.append("Sellers exhausted, smart money covering — bounce expected")
        return "BEAR_TRAP", conf, reasons

    # ── REGIME 5: DISTRIBUTION (top forming) ──
    if _spot_at_recent_extreme(state, "HIGH") and _td_trend_for_window(state, "DECREASING"):
        if pd_val < 0:
            reasons.append("Spot near recent high but synthetic stress decreasing + premium turning negative")
            return "DISTRIBUTION", 70.0, reasons

    # ── REGIME 6: ACCUMULATION (bottom forming) ──
    if _spot_at_recent_extreme(state, "LOW") and _td_trend_for_window(state, "INCREASING"):
        if pd_val > 0:
            reasons.append("Spot near recent low but synthetic stress increasing + premium turning positive")
            return "ACCUMULATION", 70.0, reasons

    # ── REGIME 7: CHURN ──
    if all_velocities_near_zero(velocities):
        reasons.append("All velocities near zero — theta zone")
        return "CHURN", 100.0, reasons

    # Default
    reasons.append(f"Mixed signals (sv={sv:.2f} pd={pd_val:.1f} td={td:.1f})")
    return "TRANSITIONING", 50.0, reasons


def compute_oi_concentration_score(per_strike_data):
    """Helper for trap confidence — measure OI clustering at single strike (0-1).
    High concentration = strong wall = high score."""
    total_oi = sum((s.get("ce_oi", 0) or 0) + (s.get("pe_oi", 0) or 0)
                   for s in per_strike_data.values())
    if total_oi <= 0:
        return 0.0
    max_oi = max((s.get("ce_oi", 0) or 0) + (s.get("pe_oi", 0) or 0)
                 for s in per_strike_data.values())
    # Single strike concentration ratio
    ratio = max_oi / total_oi
    # Normalize: 1/9 (perfect spread) = 0, ≥0.4 = 1.0
    norm = max(0.0, min(1.0, (ratio - 1.0/9) / (0.4 - 1.0/9)))
    return norm
