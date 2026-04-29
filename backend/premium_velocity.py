"""
Premium Velocity Tracker
────────────────────────
Compares premium movement to spot movement to detect "theta winning" —
i.e. premium decaying despite stable / favorable spot.

Core insight:
  CE buy expects: spot ↑ → premium ↑ (delta * spot_move)
  CE in trouble:  spot flat or ↑ but premium ↓ → theta + IV crush eating gains

Per-position rolling tracker (entry_price, entry_spot, current_premium,
current_spot, samples). Designed to be light — keep last ~30 minutes per trade.
"""

import time
from collections import defaultdict, deque
from typing import Dict, Optional, List


class PremiumVelocityTracker:
    """Per-trade premium vs spot tracker."""

    def __init__(self, retention_min: int = 60):
        # trade_id -> deque of {ts, premium, spot}
        self.samples: Dict[str, deque] = defaultdict(lambda: deque(maxlen=retention_min * 4))
        self.entry_data: Dict[str, Dict] = {}

    def register_entry(self, trade_id: str, entry_price: float, entry_spot: float,
                       action: str = "BUY_CE"):
        """Call once when a trade is created."""
        self.entry_data[str(trade_id)] = {
            "entry_price": float(entry_price),
            "entry_spot": float(entry_spot),
            "action": action,
            "ts": time.time(),
        }

    def push(self, trade_id: str, premium: float, spot: float, ts: Optional[float] = None):
        if premium <= 0 or spot <= 0:
            return
        if ts is None:
            ts = time.time()
        self.samples[str(trade_id)].append({
            "ts": ts, "premium": float(premium), "spot": float(spot),
        })

    def _window(self, trade_id: str, minutes: int) -> List[Dict]:
        s = self.samples.get(str(trade_id))
        if not s:
            return []
        cutoff = time.time() - minutes * 60
        return [x for x in s if x["ts"] >= cutoff]

    def assess(self, trade_id: str, action: Optional[str] = None) -> Dict:
        """
        Assess premium velocity for a position.
        Returns: {
          severity, warning, score_penalty,
          spot_change_10m_pct, premium_change_10m_pct,
          theta_winning: bool
        }
        """
        sid = str(trade_id)
        meta = self.entry_data.get(sid, {})
        if not action:
            action = meta.get("action", "BUY_CE")

        is_ce = "CE" in action.upper()
        out = {
            "severity": "NONE",
            "warning": None,
            "score_penalty": 0,
            "spot_change_10m_pct": None,
            "premium_change_10m_pct": None,
            "theta_winning": False,
        }

        win10 = self._window(sid, 10)
        if len(win10) < 2:
            return out

        spot_first = win10[0]["spot"]
        spot_last = win10[-1]["spot"]
        prem_first = win10[0]["premium"]
        prem_last = win10[-1]["premium"]

        if spot_first <= 0 or prem_first <= 0:
            return out

        spot_chg = (spot_last - spot_first) / spot_first * 100
        prem_chg = (prem_last - prem_first) / prem_first * 100

        out["spot_change_10m_pct"] = round(spot_chg, 3)
        out["premium_change_10m_pct"] = round(prem_chg, 2)

        # CE: spot flat or up, but premium ↓ >2-3% in 10min = theta winning
        if is_ce:
            if abs(spot_chg) < 0.1 and prem_chg < -3.0:
                out["theta_winning"] = True
                out["severity"] = "HIGH"
                out["warning"] = (f"Theta winning: spot flat ({spot_chg:+.2f}%), "
                                  f"premium {prem_chg:+.1f}% in 10m")
                out["score_penalty"] = 3
            elif spot_chg > 0 and prem_chg < -2.0:
                # Spot up but premium down — IV crushing harder than delta gain
                out["theta_winning"] = True
                out["severity"] = "HIGH"
                out["warning"] = (f"DELTA-IV mismatch: spot +{spot_chg:.2f}% but "
                                  f"premium {prem_chg:+.1f}% — IV crush dominant")
                out["score_penalty"] = 3
            elif abs(spot_chg) < 0.05 and prem_chg < -1.5:
                out["severity"] = "MEDIUM"
                out["warning"] = f"Premium leaking {prem_chg:+.1f}% on flat spot"
                out["score_penalty"] = 2
        else:
            # PE: spot flat or down but premium ↓ = theta winning on PE
            if abs(spot_chg) < 0.1 and prem_chg < -3.0:
                out["theta_winning"] = True
                out["severity"] = "HIGH"
                out["warning"] = (f"Theta winning (PE): spot flat, premium {prem_chg:+.1f}% in 10m")
                out["score_penalty"] = 3
            elif spot_chg < 0 and prem_chg < -2.0:
                out["theta_winning"] = True
                out["severity"] = "HIGH"
                out["warning"] = (f"DELTA-IV mismatch: spot {spot_chg:+.2f}% but "
                                  f"PE premium {prem_chg:+.1f}%")
                out["score_penalty"] = 3

        return out

    def cleanup(self, trade_id: str):
        sid = str(trade_id)
        self.samples.pop(sid, None)
        self.entry_data.pop(sid, None)

    def snapshot(self, trade_id: str) -> Dict:
        sid = str(trade_id)
        meta = self.entry_data.get(sid, {})
        s = list(self.samples.get(sid, []))
        return {
            "trade_id": sid,
            "entry": meta,
            "sample_count": len(s),
            "first": s[0] if s else None,
            "last": s[-1] if s else None,
        }


# Singleton
_tracker: Optional[PremiumVelocityTracker] = None


def get_tracker() -> PremiumVelocityTracker:
    global _tracker
    if _tracker is None:
        _tracker = PremiumVelocityTracker()
    return _tracker


def register(trade_id, entry_price, entry_spot, action):
    get_tracker().register_entry(trade_id, entry_price, entry_spot, action)


def push(trade_id, premium, spot):
    get_tracker().push(trade_id, premium, spot)


def assess(trade_id, action=None):
    return get_tracker().assess(trade_id, action)
