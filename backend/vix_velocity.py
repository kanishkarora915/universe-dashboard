"""
VIX Velocity Tracker
────────────────────
Tracks rolling VIX deltas to flag IV crush (CE killer) or IV spike (PE killer)
for OPEN positions.

Logic:
  • CE buyer dies when VIX drops fast (premium gets crushed by IV decline)
  • PE buyer dies when VIX rises fast (already overpriced, mean-reverts)
  • Theta + IV crush combo = silent killer

Stores rolling samples in memory (lightweight, no DB needed).
30-second polling, retains last 60 minutes.
"""

import time
from collections import deque
from typing import Dict, Optional, List
from datetime import datetime, timedelta


class VIXVelocityTracker:
    """In-memory rolling VIX tracker."""

    def __init__(self, retention_minutes: int = 60):
        self.samples: deque = deque(maxlen=retention_minutes * 4)  # 15s samples
        self.retention = retention_minutes

    def push(self, vix_value: float, ts: Optional[float] = None):
        """Record a VIX sample."""
        if vix_value <= 0:
            return
        if ts is None:
            ts = time.time()
        self.samples.append({"ts": ts, "vix": float(vix_value)})

    def _samples_in_window(self, minutes: int) -> List[Dict]:
        if not self.samples:
            return []
        cutoff = time.time() - minutes * 60
        return [s for s in self.samples if s["ts"] >= cutoff]

    def delta_pct(self, window_min: int = 15) -> Optional[float]:
        """% change in VIX over the last N minutes."""
        in_window = self._samples_in_window(window_min)
        if len(in_window) < 2:
            return None
        first = in_window[0]["vix"]
        last = in_window[-1]["vix"]
        if first <= 0:
            return None
        return round((last - first) / first * 100, 2)

    def current(self) -> Optional[float]:
        if not self.samples:
            return None
        return self.samples[-1]["vix"]

    def assess(self, position_action: str = "BUY_CE") -> Dict:
        """
        Assess VIX risk for an open position.
        Returns: {
          severity: "NONE"|"LOW"|"MEDIUM"|"HIGH",
          delta_15m, delta_30m,
          warning: str,
          score_penalty: 0-3  (subtracted from health score)
        }
        """
        d15 = self.delta_pct(15)
        d30 = self.delta_pct(30)
        d5 = self.delta_pct(5)
        cur = self.current()

        is_ce = "CE" in position_action.upper()
        is_pe = "PE" in position_action.upper()

        out = {
            "current_vix": cur,
            "delta_5m": d5,
            "delta_15m": d15,
            "delta_30m": d30,
            "severity": "NONE",
            "warning": None,
            "score_penalty": 0,
        }

        if d15 is None:
            return out

        # CE-killer: VIX dropping fast = IV crush
        if is_ce:
            if d15 <= -3.0 or (d5 is not None and d5 <= -2.0):
                out["severity"] = "HIGH"
                out["warning"] = f"VIX CRUSH: {d15:.1f}% drop in 15m → CE premium evaporating"
                out["score_penalty"] = 3
            elif d15 <= -2.0:
                out["severity"] = "MEDIUM"
                out["warning"] = f"VIX falling {d15:.1f}% in 15m → IV decay risk"
                out["score_penalty"] = 2
            elif d15 <= -1.0:
                out["severity"] = "LOW"
                out["warning"] = f"VIX softening {d15:.1f}% — minor IV pressure"
                out["score_penalty"] = 1

        # PE-killer: VIX rising fast = mean-revert risk
        if is_pe:
            if d15 >= 5.0:
                out["severity"] = "HIGH"
                out["warning"] = f"VIX SPIKE: +{d15:.1f}% in 15m → mean-revert imminent, PE risk"
                out["score_penalty"] = 3
            elif d15 >= 3.0:
                out["severity"] = "MEDIUM"
                out["warning"] = f"VIX up {d15:.1f}% in 15m → premium overstretched"
                out["score_penalty"] = 2

        # Both — extreme volatility regime
        if cur is not None and cur > 25:
            if out["severity"] == "NONE":
                out["severity"] = "MEDIUM"
                out["warning"] = f"VIX {cur:.1f} elevated — wide swings possible"
                out["score_penalty"] = max(out["score_penalty"], 1)

        return out

    def snapshot(self) -> Dict:
        """Full snapshot for debugging / API."""
        return {
            "samples_count": len(self.samples),
            "current": self.current(),
            "delta_5m": self.delta_pct(5),
            "delta_15m": self.delta_pct(15),
            "delta_30m": self.delta_pct(30),
            "delta_60m": self.delta_pct(60),
        }


# Singleton
_tracker: Optional[VIXVelocityTracker] = None


def get_tracker() -> VIXVelocityTracker:
    global _tracker
    if _tracker is None:
        _tracker = VIXVelocityTracker()
    return _tracker


def push_vix(vix_value: float):
    """Convenience: push a VIX sample."""
    get_tracker().push(vix_value)


def assess_for_position(position_action: str) -> Dict:
    return get_tracker().assess(position_action)
