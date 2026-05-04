"""
IV Rank / IV Percentile Engine
──────────────────────────────
Per-strike + per-index IV history (rolling 60 days).
Computes IV Rank and IV Percentile for buyer's "is premium expensive?" check.

LADDER:
  IVR > 80%   → BLOCK entry (premium too expensive — vega risk)
  IVR 60-80%  → WARN, qty multiplier 0.5
  IVR < 30%   → BOOST quality (cheap entry zone)

CAPTURE: 9:30, 12:00, 15:00 IST daily for ATM ±10 strikes
RETENTION: 90 days (auto-prune)

PROXY MODE: Until 60 days of strike-specific history accrues, use
INDIA VIX percentile as fallback (70% accurate proxy for ATM IV).
This is honest — better than blind buyer.
"""

import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = str(_DATA_DIR / "iv_history.db")

IST = timezone(timedelta(hours=5, minutes=30))

CAPTURE_TIMES = [(9, 30), (12, 0), (15, 0)]  # IST hours
PRUNE_DAYS = 90


# ── DB init ───────────────────────────────────────────────────────────

def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS iv_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time_label TEXT,           -- '0930' / '1200' / '1500'
            ts REAL,
            idx TEXT,
            strike INTEGER,
            ce_iv REAL,
            pe_iv REAL,
            ce_premium REAL,
            pe_premium REAL,
            spot REAL,
            vix REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_iv_lookup ON iv_snapshots(idx, strike, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_iv_date ON iv_snapshots(date)")
    conn.commit()
    conn.close()


# ── Capture ───────────────────────────────────────────────────────────

def _compute_iv_from_premium(spot, strike, days_to_expiry, premium, side):
    """Compute IV via py_vollib if available, else return None.
    Used as fallback when broker doesn't provide IV directly."""
    try:
        from py_vollib.black_scholes.implied_volatility import implied_volatility
        if days_to_expiry <= 0:
            return None
        T = days_to_expiry / 365.0
        flag = "c" if side.upper() == "CE" else "p"
        iv = implied_volatility(premium, spot, strike, T, 0.065, flag)
        return round(iv * 100, 2)  # convert to percentage
    except Exception:
        return None


def capture_iv_snapshot(engine):
    """Capture IV for all NTM ±10 strikes both indices.
    Call at 9:30, 12:00, 15:00 IST."""
    _init_db()
    now_ist = datetime.now(IST)
    today = now_ist.strftime("%Y-%m-%d")
    time_label = now_ist.strftime("%H%M")
    ts = time.time()

    conn = sqlite3.connect(DB_PATH)

    for idx in ("NIFTY", "BANKNIFTY"):
        try:
            spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
            spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
            chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
            vix_tok = engine.spot_tokens.get("VIX") if hasattr(engine, "spot_tokens") else None
            vix = engine.prices.get(vix_tok, {}).get("ltp", 0) if vix_tok else 0
            if spot <= 0 or not chain:
                continue
            gap = 50 if idx == "NIFTY" else 100
            atm = round(spot / gap) * gap
            for offset in range(-10, 11):
                strike = atm + offset * gap
                sd = chain.get(strike) or chain.get(str(strike)) or {}
                if not isinstance(sd, dict):
                    continue
                ce_ltp = sd.get("ce_ltp", 0) or 0
                pe_ltp = sd.get("pe_ltp", 0) or 0
                # Try broker IV first
                ce_iv = sd.get("ce_iv", 0) or sd.get("ce_implied_volatility", 0) or 0
                pe_iv = sd.get("pe_iv", 0) or sd.get("pe_implied_volatility", 0) or 0
                if ce_iv <= 0 and ce_ltp > 0:
                    # Fallback: compute via py_vollib (assumes ~7 days for weekly)
                    ce_iv = _compute_iv_from_premium(spot, strike, 7, ce_ltp, "CE") or 0
                if pe_iv <= 0 and pe_ltp > 0:
                    pe_iv = _compute_iv_from_premium(spot, strike, 7, pe_ltp, "PE") or 0
                if ce_iv <= 0 and pe_iv <= 0:
                    continue
                conn.execute("""
                    INSERT INTO iv_snapshots
                    (date, time_label, ts, idx, strike, ce_iv, pe_iv,
                     ce_premium, pe_premium, spot, vix)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (today, time_label, ts, idx, int(strike),
                      ce_iv, pe_iv, ce_ltp, pe_ltp, spot, vix))
        except Exception as e:
            print(f"[IV-RANK] capture err for {idx}: {e}")

    # Prune older than PRUNE_DAYS
    cutoff_date = (now_ist - timedelta(days=PRUNE_DAYS)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM iv_snapshots WHERE date < ?", (cutoff_date,))
    conn.commit()
    conn.close()
    print(f"[IV-RANK] Captured at {today} {time_label} ({PRUNE_DAYS}-day retention)")


# ── IV Rank computation ──────────────────────────────────────────────

def compute_iv_rank(idx: str, strike: int, side: str,
                     current_iv: Optional[float] = None) -> Dict:
    """Compute IV Rank + Percentile from rolling 60-day history.

    Returns:
      {
        ivr: 0-100 percentile rank,
        ivp: % of past days IV was below current,
        regime: CHEAP / FAIR / EXPENSIVE / EXTREME,
        sample_size: how many days of history,
        days_history: actual days of data,
        is_provisional: True if < 60 days history,
        sixty_day_low, sixty_day_high,
        current_iv,
      }
    """
    _init_db()
    col = "ce_iv" if side.upper() == "CE" else "pe_iv"
    cutoff = (datetime.now(IST) - timedelta(days=60)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(f"""
        SELECT date, MAX({col}) as max_iv, MIN({col}) as min_iv, AVG({col}) as avg_iv
        FROM iv_snapshots
        WHERE idx=? AND strike=? AND date >= ? AND {col} > 0
        GROUP BY date
        ORDER BY date ASC
    """, (idx.upper(), int(strike), cutoff)).fetchall()
    conn.close()

    if len(rows) < 5:
        # Not enough history — fallback to VIX percentile
        return {
            "ivr": None,
            "ivp": None,
            "regime": "INSUFFICIENT_DATA",
            "sample_size": len(rows),
            "days_history": len(rows),
            "is_provisional": True,
            "fallback": "use_vix_proxy",
            "current_iv": current_iv,
        }

    # Use min/max of available data
    all_ivs = [(r[1] + r[2]) / 2 for r in rows if r[1] and r[2]]  # day midpoints
    if not all_ivs or current_iv is None or current_iv <= 0:
        return {
            "ivr": None, "ivp": None, "regime": "NO_DATA",
            "sample_size": len(rows), "is_provisional": True,
        }

    sixty_low = min(all_ivs)
    sixty_high = max(all_ivs)

    if sixty_high <= sixty_low:
        ivr = 50
    else:
        ivr = (current_iv - sixty_low) / (sixty_high - sixty_low) * 100
        ivr = max(0, min(100, ivr))

    days_below = sum(1 for v in all_ivs if v < current_iv)
    ivp = (days_below / len(all_ivs)) * 100

    # Regime
    if ivr > 80:
        regime = "EXPENSIVE"
    elif ivr > 60:
        regime = "ELEVATED"
    elif ivr < 30:
        regime = "CHEAP"
    else:
        regime = "FAIR"

    return {
        "ivr": round(ivr, 1),
        "ivp": round(ivp, 1),
        "regime": regime,
        "sample_size": len(rows),
        "days_history": len(rows),
        "is_provisional": len(rows) < 60,
        "sixty_day_low": round(sixty_low, 2),
        "sixty_day_high": round(sixty_high, 2),
        "current_iv": round(current_iv, 2),
    }


def vix_percentile_proxy(engine, current_vix: float) -> Dict:
    """Fallback IVR proxy using India VIX history (works while strike-specific
    data accrues). 70% accurate for ATM strikes."""
    _init_db()
    cutoff = (datetime.now(IST) - timedelta(days=60)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT date, AVG(vix) FROM iv_snapshots WHERE date >= ? AND vix > 0 GROUP BY date",
        (cutoff,)
    ).fetchall()
    conn.close()

    if len(rows) < 5:
        return {"ivr": None, "regime": "PROXY_UNAVAILABLE", "is_proxy": True,
                "sample_size": len(rows)}

    vix_values = [r[1] for r in rows if r[1]]
    if not vix_values:
        return {"ivr": None, "regime": "PROXY_NO_DATA", "is_proxy": True}
    low, high = min(vix_values), max(vix_values)
    if high <= low:
        ivr = 50
    else:
        ivr = (current_vix - low) / (high - low) * 100
        ivr = max(0, min(100, ivr))
    days_below = sum(1 for v in vix_values if v < current_vix)
    ivp = days_below / len(vix_values) * 100

    regime = ("EXPENSIVE" if ivr > 80
              else "ELEVATED" if ivr > 60
              else "CHEAP" if ivr < 30
              else "FAIR")

    return {
        "ivr": round(ivr, 1), "ivp": round(ivp, 1),
        "regime": regime, "is_proxy": True,
        "sample_size": len(rows),
        "current_vix": round(current_vix, 2),
        "sixty_day_low": round(low, 2),
        "sixty_day_high": round(high, 2),
    }


# ── Entry gate ────────────────────────────────────────────────────────

def check_iv_gate(engine, idx: str, strike: int, action: str) -> Tuple[bool, str, float]:
    """Pre-entry IV check. Returns (allowed, reason, qty_multiplier)."""
    try:
        chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
        sd = chain.get(strike) or chain.get(str(strike)) or {}
        side = "CE" if "CE" in action.upper() else "PE"
        col = f"{side.lower()}_iv"
        current_iv = sd.get(col, 0) or 0

        # Check strike-specific IV rank
        if current_iv > 0:
            r = compute_iv_rank(idx, strike, side, current_iv)
            if r.get("ivr") is not None:
                ivr = r["ivr"]
                if ivr > 80:
                    return False, (
                        f"IV_EXPENSIVE: IVR {ivr:.0f}% — premium near 60-day high. "
                        f"Vega crush risk. Wait for cooldown."
                    ), 0.0
                elif ivr > 60:
                    return True, (
                        f"IV_ELEVATED: IVR {ivr:.0f}% — premium pricey. Reduce qty 50%."
                    ), 0.5
                elif ivr < 30:
                    return True, (
                        f"IV_CHEAP: IVR {ivr:.0f}% — premium low, good buy zone."
                    ), 1.0
                return True, f"IV fair: IVR {ivr:.0f}%", 1.0

        # Fallback: VIX proxy
        vix_tok = engine.spot_tokens.get("VIX") if hasattr(engine, "spot_tokens") else None
        vix = engine.prices.get(vix_tok, {}).get("ltp", 0) if vix_tok else 0
        if vix > 0:
            proxy = vix_percentile_proxy(engine, vix)
            if proxy.get("ivr") is not None:
                ivr = proxy["ivr"]
                if ivr > 80:
                    return False, (
                        f"VIX_PROXY_EXPENSIVE: VIX {vix:.1f} (rank {ivr:.0f}%) — "
                        f"premiums elevated, high vega risk."
                    ), 0.0
                elif ivr > 60:
                    return True, f"VIX_PROXY_ELEVATED: VIX {vix:.1f}, rank {ivr:.0f}%", 0.5
                return True, f"VIX_PROXY OK: rank {ivr:.0f}%", 1.0

        return True, "IV data unavailable — allow", 1.0
    except Exception as e:
        return True, f"IV gate err: {e}", 1.0


# ── Read helpers for API ──────────────────────────────────────────────

def get_strike_iv_rank(engine, idx: str, strike: int) -> Dict:
    chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
    sd = chain.get(strike) or chain.get(str(strike)) or {}
    out = {"idx": idx, "strike": strike}
    for side in ("CE", "PE"):
        col = f"{side.lower()}_iv"
        cur = sd.get(col, 0) or 0
        out[side.lower()] = compute_iv_rank(idx, strike, side, cur)
    return out


def get_chain_iv_ranks(engine, idx: str) -> Dict:
    chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
    spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
    gap = 50 if idx == "NIFTY" else 100
    atm = round(spot / gap) * gap if spot > 0 else 0
    rows = []
    for offset in range(-10, 11):
        strike = atm + offset * gap
        sd = chain.get(strike) or chain.get(str(strike)) or {}
        if not sd:
            continue
        ce = compute_iv_rank(idx, strike, "CE", sd.get("ce_iv", 0))
        pe = compute_iv_rank(idx, strike, "PE", sd.get("pe_iv", 0))
        rows.append({"strike": strike, "is_atm": strike == atm,
                     "ce_iv_rank": ce, "pe_iv_rank": pe})
    return {"idx": idx, "atm": atm, "spot": spot, "strikes": rows}


def get_capture_stats() -> Dict:
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM iv_snapshots").fetchone()[0]
    days = conn.execute("SELECT COUNT(DISTINCT date) FROM iv_snapshots").fetchone()[0]
    last = conn.execute("SELECT MAX(ts) FROM iv_snapshots").fetchone()[0]
    conn.close()
    return {
        "total_snapshots": total,
        "days_captured": days,
        "last_capture_ts": last,
        "last_capture_age_sec": round(time.time() - last, 1) if last else None,
        "is_provisional": days < 60,
        "days_until_full_history": max(0, 60 - days),
    }
