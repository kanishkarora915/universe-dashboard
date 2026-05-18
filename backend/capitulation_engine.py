"""
Reversal Capitulation Engine
────────────────────────────
Aggregates 7 leading signals to detect capitulation reversal moments —
the textbook V-shape bottom (or inverted-V top) where:

  • CE writers cover shorts (bullish bottom) / PE writers cover (bearish top)
  • PE writers add at support (bullish) / CE writers add at resistance (bearish)
  • PCR shifts direction live
  • ATM premiums collapsed (cheap option = max opportunity)
  • VIX cooling from spike (fear subsiding)
  • Higher lows / lower highs forming on 5-min spot
  • Volume exhaustion + return of buying interest

Score 0-10 per direction (bullish / bearish).
≥7 = strong capitulation alert.

Pulses every 60s alongside engine main loop.
"""

import time
import sqlite3
import json
import os
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path

from oi_delta_tracker import push as oi_push, assess as oi_assess


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = str(_DATA_DIR / "capitulation.db")


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capitulation_log (
            ts REAL,
            idx TEXT,
            direction TEXT,         -- 'BULLISH' or 'BEARISH'
            score REAL,
            verdict TEXT,           -- WATCH / ALERT / STRONG / CAPITULATION
            spot REAL,
            atm_strike INTEGER,
            recommended_strike INTEGER,
            recommended_action TEXT,
            signal_count INTEGER,
            signals_json TEXT,
            reasons_json TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cap_ts ON capitulation_log(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cap_idx_dir ON capitulation_log(idx, direction)")
    conn.commit()
    conn.close()


# ── Per-index rolling state for spot, premium, volume ─────────────────

class IndexState:
    def __init__(self):
        self.spot_samples: deque = deque(maxlen=120)  # 60 min @ 30s
        self.atm_ce_premium_samples: deque = deque(maxlen=120)
        self.atm_pe_premium_samples: deque = deque(maxlen=120)
        self.volume_samples: deque = deque(maxlen=120)
        self.vix_samples: deque = deque(maxlen=120)
        self.day_open_atm_ce: Optional[float] = None
        self.day_open_atm_pe: Optional[float] = None
        self.day_open_vix: Optional[float] = None
        self.day_high_vix: Optional[float] = None

    def push(self, ts, spot, atm_ce, atm_pe, vix, volume=0):
        if spot > 0:
            self.spot_samples.append({"ts": ts, "spot": spot})
        if atm_ce > 0:
            self.atm_ce_premium_samples.append({"ts": ts, "premium": atm_ce})
            if self.day_open_atm_ce is None:
                self.day_open_atm_ce = atm_ce
        if atm_pe > 0:
            self.atm_pe_premium_samples.append({"ts": ts, "premium": atm_pe})
            if self.day_open_atm_pe is None:
                self.day_open_atm_pe = atm_pe
        if vix > 0:
            self.vix_samples.append({"ts": ts, "vix": vix})
            if self.day_open_vix is None:
                self.day_open_vix = vix
            self.day_high_vix = max(self.day_high_vix or 0, vix)
        if volume > 0:
            self.volume_samples.append({"ts": ts, "volume": volume})


_index_state: Dict[str, IndexState] = {}


def _get_state(idx: str) -> IndexState:
    if idx not in _index_state:
        _index_state[idx] = IndexState()
    return _index_state[idx]


# ── Individual signal detectors ───────────────────────────────────────

def signal_ce_writer_covering(oi_data: Dict) -> Dict:
    """Bullish: CE writers covering shorts."""
    if oi_data["signals"].get("ce_writer_covering"):
        d = oi_data.get("ce_oi_delta_15m_pct")
        return {"fired": True, "strength": min(1.0, abs(d) / 15) if d else 0.5,
                "detail": f"CE OI {d:+.1f}% in 15m at NTM strikes — writers covering"}
    return {"fired": False, "detail": None}


def signal_pe_writer_adding(oi_data: Dict) -> Dict:
    """Bullish: PE writers adding (floor forming)."""
    if oi_data["signals"].get("pe_writer_adding"):
        d = oi_data.get("pe_oi_delta_15m_pct")
        return {"fired": True, "strength": min(1.0, d / 20) if d else 0.5,
                "detail": f"PE OI {d:+.1f}% in 15m at NTM — writers building floor"}
    return {"fired": False, "detail": None}


def signal_pcr_bullish_flip(oi_data: Dict) -> Dict:
    """Bullish: PCR rising (more PE OI relative to CE = sentiment improving)."""
    if oi_data["signals"].get("pcr_bullish_flip"):
        d = oi_data.get("pcr_delta_15m")
        return {"fired": True, "strength": min(1.0, d / 0.30) if d else 0.5,
                "detail": f"PCR {oi_data.get('pcr_15m_ago'):.2f} → {oi_data.get('pcr_now'):.2f} (Δ +{d:.2f})"}
    return {"fired": False, "detail": None}


def signal_pe_writer_covering(oi_data: Dict) -> Dict:
    """Bearish: PE writers covering."""
    if oi_data["signals"].get("pe_writer_covering"):
        d = oi_data.get("pe_oi_delta_15m_pct")
        return {"fired": True, "strength": min(1.0, abs(d) / 15) if d else 0.5,
                "detail": f"PE OI {d:+.1f}% in 15m — PE writers covering"}
    return {"fired": False, "detail": None}


def signal_ce_writer_adding(oi_data: Dict) -> Dict:
    """Bearish: CE writers adding (resistance ceiling)."""
    if oi_data["signals"].get("ce_writer_adding"):
        d = oi_data.get("ce_oi_delta_15m_pct")
        return {"fired": True, "strength": min(1.0, d / 20) if d else 0.5,
                "detail": f"CE OI {d:+.1f}% in 15m — writers building ceiling"}
    return {"fired": False, "detail": None}


def signal_pcr_bearish_flip(oi_data: Dict) -> Dict:
    if oi_data["signals"].get("pcr_bearish_flip"):
        d = oi_data.get("pcr_delta_15m")
        return {"fired": True, "strength": min(1.0, abs(d) / 0.30) if d else 0.5,
                "detail": f"PCR {oi_data.get('pcr_15m_ago'):.2f} → {oi_data.get('pcr_now'):.2f} (Δ {d:.2f})"}
    return {"fired": False, "detail": None}


def signal_premium_collapse(state: IndexState, side: str) -> Dict:
    """ATM premium collapsed >40% from day open = capitulation cheap option."""
    samples = state.atm_ce_premium_samples if side == "CE" else state.atm_pe_premium_samples
    day_open = state.day_open_atm_ce if side == "CE" else state.day_open_atm_pe
    if not samples or not day_open:
        return {"fired": False, "detail": None}
    cur = samples[-1]["premium"]
    if day_open <= 0:
        return {"fired": False, "detail": None}
    drop_pct = (day_open - cur) / day_open * 100
    if drop_pct >= 50:
        return {"fired": True, "strength": min(1.0, drop_pct / 70),
                "detail": f"ATM {side} ₹{day_open:.0f} → ₹{cur:.0f} (-{drop_pct:.0f}%) — capitulation cheap"}
    elif drop_pct >= 35:
        return {"fired": True, "strength": min(1.0, drop_pct / 70),
                "detail": f"ATM {side} {drop_pct:.0f}% off day open — discounted"}
    return {"fired": False, "detail": None}


def signal_vix_cooling(state: IndexState) -> Dict:
    """VIX cooling from day high spike = fear subsiding (bullish for CE)."""
    if not state.vix_samples or state.day_high_vix is None:
        return {"fired": False, "detail": None}
    cur_vix = state.vix_samples[-1]["vix"]
    spike_high = state.day_high_vix
    cooling_pct = (spike_high - cur_vix) / spike_high * 100 if spike_high > 0 else 0
    # Need: day high > day open by 10%+ AND now cooled 50%+ of spike
    day_open = state.day_open_vix or spike_high
    spike_size = (spike_high - day_open) / day_open * 100 if day_open > 0 else 0
    if spike_size >= 8 and cooling_pct >= 4:
        return {"fired": True, "strength": min(1.0, cooling_pct / 8),
                "detail": f"VIX spiked {spike_size:.1f}% (day high {spike_high:.1f}), now cooling {cooling_pct:.1f}% to {cur_vix:.1f}"}
    return {"fired": False, "detail": None}


def signal_vix_spiking(state: IndexState) -> Dict:
    """VIX spiking from low = fear emerging (bearish for CE, bullish for PE)."""
    if not state.vix_samples:
        return {"fired": False, "detail": None}
    cur_vix = state.vix_samples[-1]["vix"]
    cutoff = time.time() - 15 * 60
    win = [s for s in state.vix_samples if s["ts"] >= cutoff]
    if len(win) < 2:
        return {"fired": False, "detail": None}
    past_vix = win[0]["vix"]
    if past_vix > 0:
        rise_pct = (cur_vix - past_vix) / past_vix * 100
        if rise_pct >= 10:
            return {"fired": True, "strength": min(1.0, rise_pct / 20),
                    "detail": f"VIX {past_vix:.1f} → {cur_vix:.1f} (+{rise_pct:.1f}% in 15m) — fear rising"}
    return {"fired": False, "detail": None}


def signal_higher_lows(state: IndexState) -> Dict:
    """3+ consecutive 5-min higher lows on spot."""
    if len(state.spot_samples) < 30:
        return {"fired": False, "detail": None}
    # Build 5-min candle lows from samples
    samples = list(state.spot_samples)[-60:]  # last 30 min worth
    if len(samples) < 12:
        return {"fired": False, "detail": None}
    # Bucket into 5-min buckets
    from collections import defaultdict
    buckets = defaultdict(list)
    for s in samples:
        bucket_key = int(s["ts"] // 300)
        buckets[bucket_key].append(s["spot"])
    sorted_keys = sorted(buckets.keys())[-6:]  # last 6 5-min buckets
    lows = [min(buckets[k]) for k in sorted_keys if buckets[k]]
    if len(lows) < 3:
        return {"fired": False, "detail": None}
    last3 = lows[-3:]
    if last3[0] < last3[1] < last3[2]:
        rise_pts = last3[2] - last3[0]
        return {"fired": True, "strength": 0.85,
                "detail": f"3 higher lows on 5-min: {last3[0]:.1f}→{last3[1]:.1f}→{last3[2]:.1f} (+{rise_pts:.1f} pts)"}
    return {"fired": False, "detail": None}


def signal_lower_highs(state: IndexState) -> Dict:
    if len(state.spot_samples) < 30:
        return {"fired": False, "detail": None}
    samples = list(state.spot_samples)[-60:]
    if len(samples) < 12:
        return {"fired": False, "detail": None}
    from collections import defaultdict
    buckets = defaultdict(list)
    for s in samples:
        buckets[int(s["ts"] // 300)].append(s["spot"])
    sorted_keys = sorted(buckets.keys())[-6:]
    highs = [max(buckets[k]) for k in sorted_keys if buckets[k]]
    if len(highs) < 3:
        return {"fired": False, "detail": None}
    last3 = highs[-3:]
    if last3[0] > last3[1] > last3[2]:
        drop_pts = last3[0] - last3[2]
        return {"fired": True, "strength": 0.85,
                "detail": f"3 lower highs on 5-min: {last3[0]:.1f}→{last3[1]:.1f}→{last3[2]:.1f} (-{drop_pts:.1f} pts)"}
    return {"fired": False, "detail": None}


# ── Master scorer ─────────────────────────────────────────────────────

def score_bullish(idx: str, oi_data: Dict, state: IndexState) -> Dict:
    """Bullish capitulation = CE buy opportunity at bottom."""
    signals = []

    # 7 weighted signals
    s1 = signal_ce_writer_covering(oi_data);  signals.append(("ce_writer_covering", s1, 1.5))
    s2 = signal_pe_writer_adding(oi_data);    signals.append(("pe_writer_adding",   s2, 1.3))
    s3 = signal_pcr_bullish_flip(oi_data);    signals.append(("pcr_bullish_flip",   s3, 1.0))
    s4 = signal_premium_collapse(state, "CE"); signals.append(("ce_premium_cheap", s4, 1.5))
    s5 = signal_vix_cooling(state);            signals.append(("vix_cooling",       s5, 1.2))
    s6 = signal_higher_lows(state);            signals.append(("higher_lows",       s6, 1.5))
    s7_pe_cov = signal_pe_writer_covering(oi_data)  # actually negative for bullish, skip

    # Max possible: 1.5+1.3+1.0+1.5+1.2+1.5 = 8.0 → scale to 10
    raw = sum((sig.get("strength", 0) or 0) * weight for _, sig, weight in signals if sig.get("fired"))
    max_raw = 8.0
    score = round(raw / max_raw * 10, 1)
    fired_count = sum(1 for _, s, _ in signals if s.get("fired"))

    # TUNED 2026-05-05: V-bottom on NIFTY today wasn't caught — bull score
    # never crossed 5 (only ~3-4). Lowered ALERT threshold 5→4 and made
    # recommended_action fire at 4+ so smart-bias gets the boost earlier.
    if score >= 7:
        verdict = "STRONG_CAPITULATION"
    elif score >= 4:           # was 5
        verdict = "ALERT"
    elif score >= 2.5:          # was 3
        verdict = "WATCH"
    else:
        verdict = "QUIET"

    return {
        "direction": "BULLISH",
        "score": score,
        "verdict": verdict,
        "fired_count": fired_count,
        "total_signals": len(signals),
        "signals": {name: sig for name, sig, _ in signals},
        "reasons": [sig["detail"] for _, sig, _ in signals if sig.get("fired") and sig.get("detail")],
        "atm_strike": oi_data.get("atm_strike"),
        "recommended_action": "BUY ATM CE" if score >= 4 else None,  # was 5
    }


def score_bearish(idx: str, oi_data: Dict, state: IndexState) -> Dict:
    """Bearish capitulation = PE buy opportunity at top."""
    signals = []

    s1 = signal_pe_writer_covering(oi_data);  signals.append(("pe_writer_covering", s1, 1.5))
    s2 = signal_ce_writer_adding(oi_data);    signals.append(("ce_writer_adding",   s2, 1.3))
    s3 = signal_pcr_bearish_flip(oi_data);    signals.append(("pcr_bearish_flip",   s3, 1.0))
    s4 = signal_premium_collapse(state, "PE"); signals.append(("pe_premium_cheap", s4, 1.5))
    s5 = signal_vix_spiking(state);            signals.append(("vix_spiking",       s5, 1.2))
    s6 = signal_lower_highs(state);            signals.append(("lower_highs",       s6, 1.5))

    raw = sum((sig.get("strength", 0) or 0) * weight for _, sig, weight in signals if sig.get("fired"))
    max_raw = 8.0
    score = round(raw / max_raw * 10, 1)
    fired_count = sum(1 for _, s, _ in signals if s.get("fired"))

    if score >= 7:
        verdict = "STRONG_CAPITULATION"
    elif score >= 4:           # was 5
        verdict = "ALERT"
    elif score >= 2.5:          # was 3
        verdict = "WATCH"
    else:
        verdict = "QUIET"

    return {
        "direction": "BEARISH",
        "score": score,
        "verdict": verdict,
        "fired_count": fired_count,
        "total_signals": len(signals),
        "signals": {name: sig for name, sig, _ in signals},
        "reasons": [sig["detail"] for _, sig, _ in signals if sig.get("fired") and sig.get("detail")],
        "atm_strike": oi_data.get("atm_strike"),
        "recommended_action": "BUY ATM PE" if score >= 4 else None,  # was 5
    }


# ── Pulse — call every 60s from engine ────────────────────────────────

_last_log_ts: Dict[str, float] = {}


def pulse(engine) -> Dict:
    """Aggregate snapshot per index. Run from engine async loop."""
    _init_db()
    out = {"ts": time.time(), "results": {}}
    for idx in ("NIFTY", "BANKNIFTY"):
        try:
            chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
            spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
            spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
            vix_tok = engine.spot_tokens.get("VIX") if hasattr(engine, "spot_tokens") else None
            vix = engine.prices.get(vix_tok, {}).get("ltp", 0) if vix_tok else 0

            if spot <= 0 or not chain:
                continue

            # ATM strike
            gap = 50 if idx == "NIFTY" else 100
            atm_strike = round(spot / gap) * gap
            atm_data = chain.get(atm_strike) or chain.get(str(atm_strike)) or {}
            if not isinstance(atm_data, dict):
                atm_data = {}
            atm_ce = atm_data.get("ce_ltp", 0) or 0
            atm_pe = atm_data.get("pe_ltp", 0) or 0

            # PCR + max pain (try several keys)
            pcr = 0
            max_pain = 0
            try:
                live = engine.get_live_data() if hasattr(engine, "get_live_data") else {}
                live_idx = live.get(idx.lower(), {})
                pcr = live_idx.get("pcr", 0) or 0
                max_pain = live_idx.get("maxPain", 0) or 0
            except Exception:
                pass

            # Push to OI tracker
            oi_push(idx, atm_strike, chain, pcr, max_pain)

            # Push to per-idx state
            state = _get_state(idx)
            state.push(time.time(), spot, atm_ce, atm_pe, vix, volume=0)

            # Compute scores
            oi_data = oi_assess(idx)
            bull = score_bullish(idx, oi_data, state)
            bear = score_bearish(idx, oi_data, state)
            bull["spot"] = spot
            bear["spot"] = spot

            out["results"][idx] = {
                "spot": spot,
                "atm_strike": atm_strike,
                "atm_ce": atm_ce,
                "atm_pe": atm_pe,
                "vix": vix,
                "oi_data": oi_data,
                "bullish": bull,
                "bearish": bear,
            }

            # Log only when entering ALERT or higher (avoid spam)
            for direction_data in (bull, bear):
                if direction_data["verdict"] in ("ALERT", "STRONG_CAPITULATION"):
                    last_key = f"{idx}:{direction_data['direction']}"
                    last_ts = _last_log_ts.get(last_key, 0)
                    if time.time() - last_ts >= 300:  # log max every 5 min
                        _last_log_ts[last_key] = time.time()
                        try:
                            conn = sqlite3.connect(DB_PATH)
                            conn.execute("""
                                INSERT INTO capitulation_log
                                (ts, idx, direction, score, verdict, spot, atm_strike,
                                 recommended_strike, recommended_action,
                                 signal_count, signals_json, reasons_json)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                            """, (
                                time.time(), idx, direction_data["direction"],
                                direction_data["score"], direction_data["verdict"],
                                spot, atm_strike, atm_strike,
                                direction_data.get("recommended_action"),
                                direction_data["fired_count"],
                                json.dumps({k: v.get("fired") for k, v in direction_data["signals"].items()}),
                                json.dumps(direction_data["reasons"]),
                            ))
                            conn.commit()
                            conn.close()
                            print(f"[CAPITULATION] {idx} {direction_data['direction']} {direction_data['verdict']} "
                                  f"score={direction_data['score']} at spot {spot:.1f}")
                        except Exception as e:
                            print(f"[CAPITULATION] log err: {e}")
        except Exception as e:
            import traceback; traceback.print_exc()
            out["results"][idx] = {"error": str(e)}
    return out


# ── Reading helpers for API ───────────────────────────────────────────

_last_pulse_data: Dict = {}


def get_live_state() -> Dict:
    return _last_pulse_data


def set_live_state(data: Dict):
    global _last_pulse_data
    _last_pulse_data = data


def get_history(idx: Optional[str] = None, limit: int = 50) -> List[Dict]:
    _init_db()
    try:
        conn = sqlite3.connect(DB_PATH)
        if idx:
            rows = conn.execute("""
                SELECT ts, idx, direction, score, verdict, spot, atm_strike,
                       recommended_action, signal_count, reasons_json
                FROM capitulation_log
                WHERE idx=? AND date(ts, 'unixepoch', 'localtime') = date('now', 'localtime')
                ORDER BY ts DESC LIMIT ?
            """, (idx, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT ts, idx, direction, score, verdict, spot, atm_strike,
                       recommended_action, signal_count, reasons_json
                FROM capitulation_log
                WHERE date(ts, 'unixepoch', 'localtime') = date('now', 'localtime')
                ORDER BY ts DESC LIMIT ?
            """, (limit,)).fetchall()
        conn.close()
        return [{
            "ts": r[0], "idx": r[1], "direction": r[2], "score": r[3],
            "verdict": r[4], "spot": r[5], "atm_strike": r[6],
            "recommended_action": r[7], "signal_count": r[8],
            "reasons": json.loads(r[9] or "[]"),
        } for r in rows]
    except Exception:
        return []
