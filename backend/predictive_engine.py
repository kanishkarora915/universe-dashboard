"""
Predictive Engine — PREDICTIVE signals (not lagging).

Purpose: Catch reversals and momentum BEFORE position-based engines notice.
Most existing engines are POSITION-based (what IS in the market now).
Predictive engines are VELOCITY-based (what's CHANGING rapidly).

Signals added:
1. Premium Velocity      — CE/PE price rate of change (pts/min)
2. OI Velocity           — OI rate of change (contracts/min)
3. Price Momentum        — spot velocity over 5-min window
4. Premium Divergence    — CE rising/PE falling = strong bull vs weak bull
5. OI-Price Divergence   — price moves opposite to OI direction = reversal ahead
6. Exhaustion Detection  — momentum slowing at extremes = reversal imminent
"""

import time
from collections import deque
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


class PredictiveState:
    """Maintains rolling history per index for velocity + divergence computations.
    Engine holds one instance; updated on each tick process."""

    # Window sizes in seconds
    WINDOW_SHORT = 60     # 1 min — velocity
    WINDOW_MED = 300      # 5 min — momentum
    WINDOW_LONG = 900     # 15 min — trend

    def __init__(self):
        # Per-index rolling buffers: deque of (timestamp, value)
        self.spot_history = {"NIFTY": deque(maxlen=900), "BANKNIFTY": deque(maxlen=900)}
        self.atm_ce_ltp_history = {"NIFTY": deque(maxlen=900), "BANKNIFTY": deque(maxlen=900)}
        self.atm_pe_ltp_history = {"NIFTY": deque(maxlen=900), "BANKNIFTY": deque(maxlen=900)}
        self.total_ce_oi_history = {"NIFTY": deque(maxlen=900), "BANKNIFTY": deque(maxlen=900)}
        self.total_pe_oi_history = {"NIFTY": deque(maxlen=900), "BANKNIFTY": deque(maxlen=900)}
        self.last_update = {"NIFTY": 0, "BANKNIFTY": 0}

    def record(self, index, spot, ce_ltp, pe_ltp, total_ce_oi, total_pe_oi):
        """Called every ~1s from engine tick loop. Appends snapshot."""
        now = time.time()
        # Throttle: skip if <5s since last record (reduces memory churn)
        if now - self.last_update.get(index, 0) < 5:
            return
        self.last_update[index] = now

        self.spot_history[index].append((now, spot))
        self.atm_ce_ltp_history[index].append((now, ce_ltp))
        self.atm_pe_ltp_history[index].append((now, pe_ltp))
        self.total_ce_oi_history[index].append((now, total_ce_oi))
        self.total_pe_oi_history[index].append((now, total_pe_oi))

    def _velocity(self, buf, window_sec):
        """Value change per minute over the given window. Returns (pct_change, abs_change)."""
        if len(buf) < 2:
            return 0.0, 0.0
        now = time.time()
        # Find oldest entry within window
        oldest = None
        for ts, val in buf:
            if now - ts <= window_sec:
                oldest = (ts, val)
                break
        if not oldest:
            return 0.0, 0.0
        newest_ts, newest_val = buf[-1]
        old_ts, old_val = oldest
        elapsed = max(newest_ts - old_ts, 1)
        abs_change = newest_val - old_val
        pct_change = (abs_change / old_val * 100) if old_val != 0 else 0.0
        # Normalize to per-minute rate
        per_min = abs_change / (elapsed / 60.0) if elapsed > 0 else 0
        per_min_pct = pct_change / (elapsed / 60.0) if elapsed > 0 else 0
        return per_min_pct, per_min

    def spot_velocity(self, index, window_sec=WINDOW_SHORT):
        """Spot price rate of change per minute (pct, abs pts)."""
        return self._velocity(self.spot_history.get(index, deque()), window_sec)

    def ce_premium_velocity(self, index, window_sec=WINDOW_SHORT):
        return self._velocity(self.atm_ce_ltp_history.get(index, deque()), window_sec)

    def pe_premium_velocity(self, index, window_sec=WINDOW_SHORT):
        return self._velocity(self.atm_pe_ltp_history.get(index, deque()), window_sec)

    def ce_oi_velocity(self, index, window_sec=WINDOW_SHORT):
        return self._velocity(self.total_ce_oi_history.get(index, deque()), window_sec)

    def pe_oi_velocity(self, index, window_sec=WINDOW_SHORT):
        return self._velocity(self.total_pe_oi_history.get(index, deque()), window_sec)


def score_predictive(state: PredictiveState, index: str, current_ltp: float):
    """Compute predictive engine scores for a given index.
    Returns {bull_score, bear_score, reasons, predictive_engines_dict}.

    Max bull points: ~25
    Max bear points: ~25
    These are ADDITIONAL to the existing 140-point system.
    """
    bull = 0
    bear = 0
    reasons_bull = []
    reasons_bear = []
    engines = {
        "momentum": 0,
        "premium_velocity": 0,
        "oi_velocity": 0,
        "divergence": 0,
        "exhaustion": 0,
    }

    # Need at least 5 min of data for meaningful velocity
    if len(state.spot_history.get(index, [])) < 10:
        return {"bullScore": 0, "bearScore": 0, "reasons": [], "engines": engines,
                "momentum": "NEUTRAL"}

    # ── 1. PRICE MOMENTUM (5 pts each side) ──
    spot_pct_1m, spot_abs_1m = state.spot_velocity(index, 60)
    spot_pct_5m, spot_abs_5m = state.spot_velocity(index, 300)

    # Strong 5-min move
    if spot_pct_5m >= 0.15:
        pts = 5
        bull += pts
        engines["momentum"] = pts
        reasons_bull.append(f"Price momentum UP: +{spot_abs_5m:.0f}pts in 5min [{pts}pts]")
    elif spot_pct_5m <= -0.15:
        pts = 5
        bear += pts
        engines["momentum"] = pts
        reasons_bear.append(f"Price momentum DOWN: {spot_abs_5m:.0f}pts in 5min [{pts}pts]")

    # ── 2. PREMIUM VELOCITY (5 pts each side) ──
    ce_pct_m, ce_abs_m = state.ce_premium_velocity(index, 60)
    pe_pct_m, pe_abs_m = state.pe_premium_velocity(index, 60)

    # CE rising faster than PE (1 min window)
    if ce_pct_m > 2 and pe_pct_m < -1:
        pts = 5
        bull += pts
        engines["premium_velocity"] = pts
        reasons_bull.append(f"CE premium +{ce_pct_m:.1f}%/min, PE {pe_pct_m:.1f}%/min [{pts}pts]")
    elif pe_pct_m > 2 and ce_pct_m < -1:
        pts = 5
        bear += pts
        engines["premium_velocity"] = pts
        reasons_bear.append(f"PE premium +{pe_pct_m:.1f}%/min, CE {ce_pct_m:.1f}%/min [{pts}pts]")

    # ── 3. OI VELOCITY (5 pts each side) ──
    ce_oi_pct_m, ce_oi_abs_m = state.ce_oi_velocity(index, 60)
    pe_oi_pct_m, pe_oi_abs_m = state.pe_oi_velocity(index, 60)

    # PE OI adding fast while price rising = sellers confident = bullish support building
    # CE OI adding fast while price rising = sellers defending = bearish resistance
    if ce_oi_pct_m >= 1.0 and spot_pct_1m > 0:
        # CE writing at higher prices = resistance building → BEARISH
        pts = 5
        bear += pts
        engines["oi_velocity"] = pts
        reasons_bear.append(f"CE OI +{ce_oi_pct_m:.1f}%/min while price up = sellers defending [{pts}pts]")
    elif pe_oi_pct_m >= 1.0 and spot_pct_1m > 0:
        # PE writing at higher prices = support rising → BULLISH
        pts = 5
        bull += pts
        engines["oi_velocity"] = pts
        reasons_bull.append(f"PE OI +{pe_oi_pct_m:.1f}%/min as price rises = support rising [{pts}pts]")
    elif pe_oi_pct_m >= 1.0 and spot_pct_1m < 0:
        # PE writing during fall = traders selling into decline → BEARISH ahead
        pts = 5
        bear += pts
        engines["oi_velocity"] = pts
        reasons_bear.append(f"PE OI +{pe_oi_pct_m:.1f}%/min while price falls = fresh puts [{pts}pts]")
    elif ce_oi_pct_m >= 1.0 and spot_pct_1m < 0:
        # CE writing as price falls = sellers confident of more downside
        pts = 5
        bear += pts
        engines["oi_velocity"] = pts
        reasons_bear.append(f"CE OI +{ce_oi_pct_m:.1f}%/min as price falls = pressure [{pts}pts]")

    # ── 4. PREMIUM vs PRICE DIVERGENCE (5 pts each side) ──
    # Price up but CE premium not confirming = weak bull → reversal ahead
    if spot_pct_1m > 0.05 and ce_pct_m < -0.5:
        pts = 5
        bear += pts
        engines["divergence"] = pts
        reasons_bear.append(f"DIVERGENCE: spot up but CE premium falling = distribution [{pts}pts]")
    elif spot_pct_1m < -0.05 and pe_pct_m < -0.5:
        # Price down but PE premium not confirming = weak bear → bounce ahead
        pts = 5
        bull += pts
        engines["divergence"] = pts
        reasons_bull.append(f"DIVERGENCE: spot down but PE premium falling = short covering [{pts}pts]")

    # ── 5. EXHAUSTION (5 pts each side) ──
    # After big move, momentum slowing = reversal likely
    # Check: 5-min move big but 1-min move small
    if spot_pct_5m >= 0.30 and abs(spot_pct_1m) < 0.02:
        # Exhausted rally
        pts = 5
        bear += pts
        engines["exhaustion"] = pts
        reasons_bear.append(f"EXHAUSTION: +{spot_abs_5m:.0f}pts in 5min but stalling [{pts}pts]")
    elif spot_pct_5m <= -0.30 and abs(spot_pct_1m) < 0.02:
        # Exhausted decline
        pts = 5
        bull += pts
        engines["exhaustion"] = pts
        reasons_bull.append(f"EXHAUSTION: {spot_abs_5m:.0f}pts in 5min but stalling [{pts}pts]")

    # Summary
    momentum_label = "STRONG_UP" if spot_pct_5m > 0.3 else \
                     "UP" if spot_pct_5m > 0.1 else \
                     "STRONG_DOWN" if spot_pct_5m < -0.3 else \
                     "DOWN" if spot_pct_5m < -0.1 else "NEUTRAL"

    reasons = reasons_bull if bull >= bear else reasons_bear
    return {
        "bullScore": bull,
        "bearScore": bear,
        "reasons": reasons,
        "engines": engines,
        "momentum": momentum_label,
        "spot_velocity_5m_pct": round(spot_pct_5m, 3),
        "spot_velocity_5m_abs": round(spot_abs_5m, 1),
        "ce_premium_velocity_1m_pct": round(ce_pct_m, 2),
        "pe_premium_velocity_1m_pct": round(pe_pct_m, 2),
        "ce_oi_velocity_1m_pct": round(ce_oi_pct_m, 2),
        "pe_oi_velocity_1m_pct": round(pe_oi_pct_m, 2),
    }


def should_fast_confirm(predictive_result: dict, action: str) -> bool:
    """Returns True if predictive signals are STRONG enough to skip 60s confirmation.
    Used to enter FASTER when momentum + predictive confluence clearly agrees."""
    if not predictive_result:
        return False
    bull = predictive_result.get("bullScore", 0)
    bear = predictive_result.get("bearScore", 0)
    momentum = predictive_result.get("momentum", "NEUTRAL")

    if "CE" in action:
        # Need bull score >= 10 AND momentum UP/STRONG_UP to fast confirm
        return bull >= 10 and momentum in ("UP", "STRONG_UP")
    if "PE" in action:
        return bear >= 10 and momentum in ("DOWN", "STRONG_DOWN")
    return False
