"""
OI Delta Tracker
────────────────
Tracks rolling CE/PE OI deltas at NEAR-THE-MONEY strikes (ATM ±3) every
minute, so we can detect:

  • CE WRITER COVERING — CE OI dropping at NTM strikes = bullish reversal
    (writers buying back shorts pulls premium up, drags spot up via delta hedge)

  • PE WRITER COVERING — PE OI dropping at NTM = bearish reversal

  • CE WRITER ADDING — CE OI building rapidly = bearish (new ceiling forming)
  • PE WRITER ADDING — PE OI building rapidly = bullish (new floor forming)

  • PCR delta — live ratio drift since open

In-memory rolling samples (60 min retention, ~120 samples at 30s pulse).
"""

import time
from collections import deque, defaultdict
from typing import Dict, List, Optional, Any


class OIDeltaTracker:
    def __init__(self, retention_min: int = 60):
        # idx -> deque of {ts, atm_strike, ce_oi[strike], pe_oi[strike], pcr, max_pain}
        self.samples: Dict[str, deque] = defaultdict(lambda: deque(maxlen=retention_min * 2))
        # Day open snapshot per idx (for full-day deltas)
        self.day_open: Dict[str, Dict] = {}

    def push(self, idx: str, atm_strike: int, chain: Dict, pcr: float, max_pain: float,
             ts: Optional[float] = None):
        """Capture NTM snapshot. chain is the engine's chain dict for this idx."""
        if ts is None:
            ts = time.time()
        if not chain or atm_strike <= 0:
            return

        # Pull NTM ±3 strikes
        # Estimate strike gap from chain keys
        try:
            keys_sorted = sorted([k for k in chain.keys() if isinstance(k, (int, float))])
            if len(keys_sorted) >= 2:
                gap = keys_sorted[1] - keys_sorted[0]
            else:
                gap = 50 if "NIFTY" in idx.upper() and "BANK" not in idx.upper() else 100
        except Exception:
            gap = 50 if "NIFTY" in idx.upper() and "BANK" not in idx.upper() else 100

        ce_oi = {}
        pe_oi = {}
        for offset in (-3, -2, -1, 0, 1, 2, 3):
            k = int(atm_strike + offset * gap)
            sd = chain.get(k) or chain.get(str(k)) or {}
            if isinstance(sd, dict):
                ce_oi[k] = sd.get("ce_oi", 0) or 0
                pe_oi[k] = sd.get("pe_oi", 0) or 0

        snapshot = {
            "ts": ts,
            "atm_strike": atm_strike,
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
            "pcr": float(pcr or 0),
            "max_pain": float(max_pain or 0),
        }
        self.samples[idx.upper()].append(snapshot)

        # Capture first sample of the day as day_open
        if idx.upper() not in self.day_open:
            self.day_open[idx.upper()] = snapshot

    def reset_day(self, idx: str):
        """Call at market open to reset day-open snapshot."""
        idx = idx.upper()
        if idx in self.day_open:
            del self.day_open[idx]
        self.samples[idx].clear()

    def _window(self, idx: str, minutes: int) -> List[Dict]:
        s = self.samples.get(idx.upper())
        if not s:
            return []
        cutoff = time.time() - minutes * 60
        return [x for x in s if x["ts"] >= cutoff]

    def assess(self, idx: str) -> Dict:
        """
        Returns rolling deltas + signal interpretation.
        {
          ce_oi_delta_15m_pct, pe_oi_delta_15m_pct,
          ce_oi_delta_day_pct, pe_oi_delta_day_pct,
          pcr_now, pcr_15m_ago, pcr_day_open, pcr_delta_15m,
          max_pain_now, max_pain_day_open,
          signals: {
            ce_writer_covering: bool,    # bullish reversal
            ce_writer_adding: bool,      # bearish
            pe_writer_covering: bool,    # bearish reversal
            pe_writer_adding: bool,      # bullish
            pcr_bullish_flip: bool,
            pcr_bearish_flip: bool,
            max_pain_rising: bool,
            max_pain_falling: bool,
          }
        }
        """
        idx = idx.upper()
        out = {
            "ce_oi_delta_15m_pct": None,
            "pe_oi_delta_15m_pct": None,
            "ce_oi_delta_day_pct": None,
            "pe_oi_delta_day_pct": None,
            "pcr_now": None,
            "pcr_15m_ago": None,
            "pcr_day_open": None,
            "pcr_delta_15m": None,
            "pcr_delta_day": None,
            "max_pain_now": None,
            "max_pain_day_open": None,
            "max_pain_shift": None,
            "atm_strike": None,
            "signals": {
                "ce_writer_covering": False,
                "ce_writer_adding": False,
                "pe_writer_covering": False,
                "pe_writer_adding": False,
                "pcr_bullish_flip": False,
                "pcr_bearish_flip": False,
                "max_pain_rising": False,
                "max_pain_falling": False,
            },
            "samples_count": len(self.samples.get(idx, [])),
        }

        s = list(self.samples.get(idx, []))
        if not s:
            return out

        latest = s[-1]
        out["pcr_now"] = latest["pcr"]
        out["max_pain_now"] = latest["max_pain"]
        out["atm_strike"] = latest["atm_strike"]

        # 15-min deltas
        win15 = self._window(idx, 15)
        if win15:
            past = win15[0]
            # Sum CE OI across NTM strikes for past + present
            past_ce = sum(past["ce_oi"].values())
            past_pe = sum(past["pe_oi"].values())
            now_ce = sum(latest["ce_oi"].values())
            now_pe = sum(latest["pe_oi"].values())
            if past_ce > 0:
                out["ce_oi_delta_15m_pct"] = round((now_ce - past_ce) / past_ce * 100, 2)
            if past_pe > 0:
                out["pe_oi_delta_15m_pct"] = round((now_pe - past_pe) / past_pe * 100, 2)
            out["pcr_15m_ago"] = past["pcr"]
            if past["pcr"] > 0:
                out["pcr_delta_15m"] = round(latest["pcr"] - past["pcr"], 2)

        # Day deltas
        day_open = self.day_open.get(idx)
        if day_open:
            day_ce = sum(day_open["ce_oi"].values())
            day_pe = sum(day_open["pe_oi"].values())
            now_ce = sum(latest["ce_oi"].values())
            now_pe = sum(latest["pe_oi"].values())
            if day_ce > 0:
                out["ce_oi_delta_day_pct"] = round((now_ce - day_ce) / day_ce * 100, 2)
            if day_pe > 0:
                out["pe_oi_delta_day_pct"] = round((now_pe - day_pe) / day_pe * 100, 2)
            out["pcr_day_open"] = day_open["pcr"]
            out["pcr_delta_day"] = round(latest["pcr"] - day_open["pcr"], 2) if day_open["pcr"] > 0 else None
            out["max_pain_day_open"] = day_open["max_pain"]
            if day_open["max_pain"] > 0:
                out["max_pain_shift"] = round(latest["max_pain"] - day_open["max_pain"], 0)

        # Signal interpretation
        ce15 = out["ce_oi_delta_15m_pct"]
        pe15 = out["pe_oi_delta_15m_pct"]
        if ce15 is not None and ce15 <= -5:
            out["signals"]["ce_writer_covering"] = True  # bullish reversal
        if ce15 is not None and ce15 >= 8:
            out["signals"]["ce_writer_adding"] = True   # bearish
        if pe15 is not None and pe15 <= -5:
            out["signals"]["pe_writer_covering"] = True  # bearish reversal
        if pe15 is not None and pe15 >= 8:
            out["signals"]["pe_writer_adding"] = True   # bullish

        pcr_d15 = out["pcr_delta_15m"]
        if pcr_d15 is not None and pcr_d15 >= 0.15:
            out["signals"]["pcr_bullish_flip"] = True
        if pcr_d15 is not None and pcr_d15 <= -0.15:
            out["signals"]["pcr_bearish_flip"] = True

        mps = out["max_pain_shift"]
        gap = 100 if "BANK" in idx else 50
        if mps is not None and mps >= gap:
            out["signals"]["max_pain_rising"] = True
        if mps is not None and mps <= -gap:
            out["signals"]["max_pain_falling"] = True

        return out


# Singleton
_tracker: Optional[OIDeltaTracker] = None


def get_tracker() -> OIDeltaTracker:
    global _tracker
    if _tracker is None:
        _tracker = OIDeltaTracker()
    return _tracker


def push(idx, atm_strike, chain, pcr, max_pain):
    get_tracker().push(idx, atm_strike, chain, pcr, max_pain)


def assess(idx):
    return get_tracker().assess(idx)
