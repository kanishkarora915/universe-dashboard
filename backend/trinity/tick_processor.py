"""
Tick processor — aggregates raw ticks into 1-sec bars + maintains ring buffers.

Pulls live data from engine.prices + engine.chains every second.
No new WebSocket — reuses engine's existing tick stream.

Maintains:
  - 1-sec snapshots (for chart, last 6 hours)
  - 100ms ring buffer (for trap detection velocity)
  - 5-min EMA of future premium (baseline tracker)
"""

import time
from collections import deque
from threading import Lock


class TickRingBuffer:
    """Time-series ring buffer with bounded size."""

    def __init__(self, maxlen=21600):  # 6 hours of 1-sec bars
        self.buf = deque(maxlen=maxlen)
        self.lock = Lock()

    def push(self, snapshot):
        with self.lock:
            self.buf.append(snapshot)

    def latest(self):
        with self.lock:
            return self.buf[-1] if self.buf else None

    def last_n(self, n):
        with self.lock:
            return list(self.buf)[-n:] if self.buf else []

    def all(self):
        with self.lock:
            return list(self.buf)

    def __len__(self):
        return len(self.buf)


class EMA:
    """Exponential Moving Average — for premium baseline."""

    def __init__(self, period_secs):
        self.alpha = 2.0 / (period_secs + 1)
        self.value = None

    def update(self, x):
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1 - self.alpha) * self.value
        return self.value

    def get(self):
        return self.value if self.value is not None else 0.0

    def reset(self):
        self.value = None


class TrinityState:
    """Holds full Trinity state — ring buffers, EMAs, current snapshot."""

    def __init__(self):
        self.bar_buffer = TickRingBuffer(maxlen=21600)   # 6h of 1-sec bars
        self.fast_buffer = TickRingBuffer(maxlen=600)    # 60s of 100ms samples
        self.premium_ema_5min = EMA(period_secs=300)
        self.premium_ema_3min = EMA(period_secs=180)     # expiry day fallback
        self.last_bar_ts = 0
        self.regime_history = deque(maxlen=200)          # last 200 regime states
        self.current_regime = "UNKNOWN"
        self.regime_started_at = time.time()
        self.last_signal_at = 0
        self.degraded = False                            # marked when stale ticks
        self.last_tick_check_at = time.time()
        self.fut_token = None
        self.fut_meta = None

    def regime_duration_secs(self):
        return time.time() - self.regime_started_at

    def transition_regime(self, new_regime):
        if new_regime != self.current_regime:
            self.current_regime = new_regime
            self.regime_started_at = time.time()
            self.regime_history.append({
                "regime": new_regime,
                "ts": int(time.time() * 1000),
            })


# Singleton state across the engine
_STATE = TrinityState()


def get_state():
    return _STATE


def aggregate_1sec_bar(spot, future, synthetic, deviation, premium,
                      regime=None, confidence=None):
    """Build a snapshot dict for 1-sec bar."""
    return {
        "ts": int(time.time() * 1000),
        "spot": spot,
        "future": future,
        "synthetic": synthetic,
        "deviation": deviation,
        "premium": premium,
        "regime": regime,
        "confidence": confidence,
    }


def compute_velocities(state, lookback_secs=1):
    """Compute velocities from ring buffer.
    Returns dict: spot_velocity, premium_velocity, synthetic_velocity, deviation_velocity.
    Velocity = change per second."""
    bars = state.bar_buffer.last_n(lookback_secs + 1)
    if len(bars) < 2:
        return {
            "spot_velocity": 0.0, "premium_velocity": 0.0,
            "synthetic_velocity": 0.0, "deviation_velocity": 0.0,
        }
    a, b = bars[0], bars[-1]
    dt = max((b["ts"] - a["ts"]) / 1000.0, 0.001)
    return {
        "spot_velocity": ((b.get("spot") or 0) - (a.get("spot") or 0)) / dt,
        "premium_velocity": ((b.get("premium") or 0) - (a.get("premium") or 0)) / dt,
        "synthetic_velocity": ((b.get("synthetic") or 0) - (a.get("synthetic") or 0)) / dt,
        "deviation_velocity": ((b.get("deviation") or 0) - (a.get("deviation") or 0)) / dt,
    }


def detect_news_spike(state, threshold_pct=0.3, window_secs=10):
    """Returns True if spot moved >threshold_pct in last window_secs.
    Per spec §10.6 — pause signals 60s on news spike."""
    bars = state.bar_buffer.last_n(window_secs + 1)
    if len(bars) < 2:
        return False
    spots = [b.get("spot") for b in bars if b.get("spot")]
    if len(spots) < 2:
        return False
    pct = abs(spots[-1] - spots[0]) / spots[0] * 100
    return pct >= threshold_pct


def is_lunch_hour():
    """12:00-13:00 IST — tighter thresholds per spec §10.5."""
    import pytz
    from datetime import datetime
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    return now.hour == 12 or (now.hour == 13 and now.minute == 0)


def is_first_5min():
    """Market just opened (9:15-9:20 IST) — no signals per spec §10.2."""
    import pytz
    from datetime import datetime
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    if now.hour != 9:
        return False
    return 15 <= now.minute < 20


def is_expiry_day():
    """NIFTY weekly expiry = Tuesday."""
    import pytz
    from datetime import datetime
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    return now.weekday() == 1
