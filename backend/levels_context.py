"""
levels_context — at-any-moment snapshot of where spot is relative to key
intraday + prior-day reference levels.

What gets returned for a given (idx, spot, engine):
  prev_day_close          — yesterday's close
  prev_day_high           — yesterday's high
  prev_day_low            — yesterday's low
  day_open                — today's first tick
  day_high                — today's running high
  day_low                 — today's running low
  gap_up_pct              — (day_open - prev_close) / prev_close * 100
  vs_day_open_pct         — (spot - day_open) / day_open * 100
  dist_pdc_pct            — distance to PDC as %
  dist_pdh_pct            — distance to PDH as %
  dist_pdl_pct            — distance to PDL as %
  dist_day_high_pct       — distance to today's high as %
  dist_day_low_pct        — distance to today's low as %
  nearest_level           — label of closest reference (e.g. 'PDH', 'DAY_LOW')
  nearest_level_dist_pct  — distance to nearest reference
  zone                    — 'NEAR_PDH' | 'NEAR_PDL' | 'NEAR_PDC' |
                            'NEAR_DAY_HIGH' | 'NEAR_DAY_LOW' | 'MID_RANGE'

WHY THIS EXISTS
User observation: "system takes expensive entries — buys CE near day_high
or PE near day_low, then loses on pullback". The system has all the OI/IV
data but does not consider WHERE in the day's range it's entering.
Level context surfaces this so we can:
  1. SAVE it per trade (for post-hoc analysis)
  2. SHOW it on dashboards
  3. Eventually GATE on it (block CE entries near PDH, etc)

Reads day_levels table from rejection_engine.py + engine._spot_history for
today's running OHLC.
"""
from __future__ import annotations
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import pytz

IST = pytz.timezone("Asia/Kolkata")

# rejection_engine writes to /data/rejection_zones.db on Render
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_REJECTION_DB = _data_dir / "rejection_zones.db"


def _ist_now() -> datetime:
    return datetime.now(IST)


# ── Prior-day H/L/C lookup ──────────────────────────────────────────────

def _prev_day_levels(idx: str) -> Dict:
    """Read day_levels table for the most recent prior trading day."""
    out = {"prev_day_close": None, "prev_day_high": None, "prev_day_low": None,
           "prev_day_date": None}
    if not _REJECTION_DB.exists():
        return out
    today_str = _ist_now().strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(_REJECTION_DB), timeout=5.0)
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            "SELECT date, day_high, day_low, day_close FROM day_levels "
            "WHERE idx=? AND date < ? ORDER BY date DESC LIMIT 1",
            (idx, today_str)
        ).fetchone()
        conn.close()
        if r:
            out["prev_day_date"] = r["date"]
            out["prev_day_close"] = r["day_close"]
            out["prev_day_high"] = r["day_high"]
            out["prev_day_low"] = r["day_low"]
    except Exception as e:
        print(f"[LEVELS] prev day read failed for {idx}: {e}")
    return out


# ── Today's running OHLC from engine spot history ──────────────────────

def _today_ohlc(engine, idx: str) -> Dict:
    """Compute today's open/high/low/last from engine._spot_history."""
    out = {"day_open": None, "day_high": None, "day_low": None, "day_last": None}
    hist = (getattr(engine, "_spot_history", {}) or {}).get(idx, []) or []
    if not hist:
        return out
    today_str = _ist_now().strftime("%Y-%m-%d")
    todays = []
    for h in hist:
        t = h.get("t")
        if not t:
            continue
        try:
            t_str = t if isinstance(t, str) else t.isoformat()
        except Exception:
            continue
        if t_str[:10] != today_str:
            continue
        ltp = h.get("ltp") or 0
        if ltp > 0:
            todays.append(ltp)
    if not todays:
        return out
    out["day_open"] = todays[0]
    out["day_high"] = max(todays)
    out["day_low"] = min(todays)
    out["day_last"] = todays[-1]
    return out


# ── Composite snapshot ─────────────────────────────────────────────────

def get_levels_context(engine, idx: str, spot: Optional[float] = None) -> Dict:
    """Return full level-context dict for current spot. Safe (returns empty
    fields if data missing) — never raises.
    """
    out = {
        "idx": idx, "spot": spot, "captured_at": _ist_now().isoformat(),
        "prev_day_close": None, "prev_day_high": None, "prev_day_low": None,
        "prev_day_date": None,
        "day_open": None, "day_high": None, "day_low": None,
        "gap_up_pct": None, "vs_day_open_pct": None,
        "dist_pdc_pct": None, "dist_pdh_pct": None, "dist_pdl_pct": None,
        "dist_day_high_pct": None, "dist_day_low_pct": None,
        "nearest_level": None, "nearest_level_dist_pct": None,
        "zone": "UNKNOWN",
    }
    try:
        pdl_data = _prev_day_levels(idx)
        out.update(pdl_data)
        ohlc = _today_ohlc(engine, idx)
        out.update(ohlc)

        # Use the supplied spot (current tick) — fall back to day_last
        s = spot if (spot and spot > 0) else ohlc.get("day_last")
        out["spot"] = s
        if not s or s <= 0:
            return out

        # Distances (positive = level above spot, negative = below)
        def _pct(target):
            return ((target - s) / s * 100) if (target and s > 0) else None

        if out["prev_day_close"]:
            out["dist_pdc_pct"] = round(_pct(out["prev_day_close"]), 3)
        if out["prev_day_high"]:
            out["dist_pdh_pct"] = round(_pct(out["prev_day_high"]), 3)
        if out["prev_day_low"]:
            out["dist_pdl_pct"] = round(_pct(out["prev_day_low"]), 3)
        if out["day_high"]:
            out["dist_day_high_pct"] = round(_pct(out["day_high"]), 3)
        if out["day_low"]:
            out["dist_day_low_pct"] = round(_pct(out["day_low"]), 3)

        if out["day_open"] and out["prev_day_close"]:
            out["gap_up_pct"] = round(
                (out["day_open"] - out["prev_day_close"]) / out["prev_day_close"] * 100, 3
            )
        if out["day_open"]:
            out["vs_day_open_pct"] = round(_pct(out["day_open"]), 3)

        # Nearest reference (within typical intraday distance)
        candidates = [
            ("PDC", out["dist_pdc_pct"]),
            ("PDH", out["dist_pdh_pct"]),
            ("PDL", out["dist_pdl_pct"]),
            ("DAY_HIGH", out["dist_day_high_pct"]),
            ("DAY_LOW", out["dist_day_low_pct"]),
            ("DAY_OPEN", out["vs_day_open_pct"]),
        ]
        valid = [(label, abs(d)) for (label, d) in candidates if d is not None]
        if valid:
            valid.sort(key=lambda x: x[1])
            out["nearest_level"] = valid[0][0]
            out["nearest_level_dist_pct"] = round(valid[0][1], 3)

        # Zone classification (within 0.15% counts as "near")
        NEAR_THRESHOLD_PCT = 0.15
        if out["nearest_level_dist_pct"] is not None and out["nearest_level_dist_pct"] <= NEAR_THRESHOLD_PCT:
            out["zone"] = f"NEAR_{out['nearest_level']}"
        elif out["dist_day_high_pct"] is not None and out["dist_day_low_pct"] is not None:
            # spot is between day high and day low — figure out which half
            mid = (out["day_high"] + out["day_low"]) / 2
            if s > mid:
                out["zone"] = "UPPER_RANGE"
            elif s < mid:
                out["zone"] = "LOWER_RANGE"
            else:
                out["zone"] = "MID_RANGE"
    except Exception as e:
        print(f"[LEVELS] context build failed for {idx}: {e}")
    return out


# ── Day open recorder (call once at ~09:15:30) ─────────────────────────

def record_day_open(idx: str, spot: float) -> None:
    """Write today's open to day_levels (idempotent — won't overwrite)."""
    if spot is None or spot <= 0:
        return
    try:
        today = _ist_now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(str(_REJECTION_DB), timeout=5.0)
        conn.execute(
            "INSERT OR IGNORE INTO day_levels (date, idx, day_open) VALUES (?, ?, ?)",
            (today, idx, spot)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[LEVELS] record_day_open failed: {e}")
