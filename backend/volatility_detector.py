"""
Volatility Detector — Real-time market regime classifier.

Classifies current market into one of 6 regimes:
  - NORMAL          : VIX 12-18, normal day, normal trading
  - HIGH-VOL        : VIX 18-25, tighten SL/T1/T2, raise threshold
  - EXTREME         : VIX >25 or panic, PAUSE main P&L (scalper only)
  - EXPIRY-DAY      : Tuesday NIFTY, special handling
  - LUNCH-CHOP      : 11:30-12:30 IST chop window
  - POWER-HOUR      : 14:00-15:15 IST volatility opportunity

Output drives: SL multipliers, target multipliers, threshold adjustments.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "volatility.db"


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS regime_log (
            ts TEXT PRIMARY KEY,
            vix REAL,
            regime TEXT,
            atr_ratio REAL,
            day_range_pct REAL,
            time_window TEXT,
            notes TEXT
        )
    """)
    conn.commit()
    conn.close()


def is_expiry_day():
    """NIFTY weekly expiry = Tuesday."""
    return ist_now().weekday() == 1


def get_time_window():
    """Classify current time of day."""
    now = ist_now()
    h, m = now.hour, now.minute
    hm = h * 100 + m

    if hm < 915:
        return "PRE_MARKET"
    if hm < 920:
        return "OPENING_FIRST_5MIN"
    if hm < 1030:
        return "MORNING_TREND"
    if hm < 1130:
        return "MID_MORNING"
    if hm < 1230:
        return "LUNCH_CHOP"
    if hm < 1400:
        return "AFTERNOON"
    if hm < 1515:
        return "POWER_HOUR"
    if hm <= 1530:
        return "CLOSING"
    return "POST_MARKET"


def compute_day_range_pct(engine, idx="NIFTY"):
    """Today's high-low range as % of open."""
    try:
        live = engine.get_live_data()
        d = live.get(idx.lower(), {})
        high = d.get("high") or d.get("dayHigh") or 0
        low = d.get("low") or d.get("dayLow") or 0
        open_p = d.get("openPrice") or d.get("open") or d.get("ltp", 0)
        if open_p > 0 and high > 0 and low > 0:
            return round((high - low) / open_p * 100, 2)
    except Exception:
        pass
    return 0.0


def get_atr_ratio(engine, idx="NIFTY"):
    """Today's range / 14-day average range. Higher = more volatile."""
    try:
        # Try smart_autopsy_mind which stores day patterns
        from smart_autopsy_mind import _conn as mind_conn
        conn = mind_conn()
        cutoff = (ist_now() - timedelta(days=20)).strftime("%Y-%m-%d")
        rows = conn.execute("""
            SELECT day_high, day_low, day_close
            FROM day_patterns
            WHERE idx=? AND date >= ?
            ORDER BY date DESC LIMIT 14
        """, (idx, cutoff)).fetchall()
        conn.close()

        if len(rows) < 5:
            return 1.0  # Not enough data

        ranges = []
        for r in rows:
            r = dict(r)
            if r.get("day_high") and r.get("day_low") and r.get("day_close"):
                tr = r["day_high"] - r["day_low"]
                ranges.append(tr)

        if not ranges:
            return 1.0
        avg_range = sum(ranges) / len(ranges)

        # Today's range
        live = engine.get_live_data()
        d = live.get(idx.lower(), {})
        today_high = d.get("high") or d.get("dayHigh") or 0
        today_low = d.get("low") or d.get("dayLow") or 0
        if today_high > 0 and today_low > 0 and avg_range > 0:
            today_range = today_high - today_low
            return round(today_range / avg_range, 2)
    except Exception as e:
        print(f"[VOL] ATR calc error: {e}")
    return 1.0


def get_vix(engine):
    """Get current India VIX from live data."""
    try:
        live = engine.get_live_data()
        # VIX usually stored in nifty data
        d = live.get("nifty", {})
        return d.get("vix", 0) or 18  # Default 18 if not found
    except Exception:
        return 18


def classify_regime(engine):
    """Main classifier — returns regime + recommendations."""
    if not engine:
        return {"regime": "UNKNOWN", "error": "Engine not running"}

    vix = get_vix(engine)
    atr_ratio = get_atr_ratio(engine, "NIFTY")
    day_range = compute_day_range_pct(engine, "NIFTY")
    expiry = is_expiry_day()
    time_window = get_time_window()

    # ── Determine primary regime ──
    regime = "NORMAL"
    notes = []

    if vix >= 25:
        regime = "EXTREME"
        notes.append(f"VIX {vix} extreme — panic conditions")
    elif vix >= 18:
        regime = "HIGH-VOL"
        notes.append(f"VIX {vix} elevated")
    elif vix < 12:
        regime = "LOW-VOL"
        notes.append(f"VIX {vix} compressed")

    # ATR override
    if atr_ratio >= 2.0:
        if regime == "NORMAL":
            regime = "HIGH-VOL"
        notes.append(f"Day range {atr_ratio}x avg — volatile")
    elif atr_ratio >= 1.5:
        notes.append(f"Day range {atr_ratio}x avg — above normal")

    # Day range override
    if day_range > 1.5:
        notes.append(f"Day range {day_range}% — wide")

    # Time-window override
    if time_window == "OPENING_FIRST_5MIN":
        notes.append("First 5 min — synthetic stabilizing, no signals")
    elif time_window == "LUNCH_CHOP":
        notes.append("Lunch chop window — low conviction")
    elif time_window == "POWER_HOUR":
        notes.append("Power hour — last-day volatility")
    elif time_window in ("PRE_MARKET", "POST_MARKET", "CLOSING"):
        notes.append(f"{time_window} — no new entries")

    # Expiry day flag
    if expiry:
        if regime == "NORMAL":
            regime = "EXPIRY-DAY"
        else:
            regime = f"EXPIRY-{regime}"
        notes.append("EXPIRY DAY — theta crush 5x, whipsaws likely")

    # ── Build recommendations ──
    rec = build_recommendations(regime, time_window, vix, atr_ratio, expiry)

    return {
        "regime": regime,
        "vix": vix,
        "atr_ratio": atr_ratio,
        "day_range_pct": day_range,
        "is_expiry": expiry,
        "time_window": time_window,
        "notes": notes,
        "recommend": rec,
        "ts": ist_now().isoformat(),
    }


def build_recommendations(regime, time_window, vix, atr_ratio, expiry):
    """Generate trading recommendations based on regime."""

    # Defaults (NORMAL regime, normal time window)
    rec = {
        "trade_allowed": True,
        "scalper_allowed": True,
        "main_pnl_allowed": True,
        "min_probability": 50,
        "sl_multiplier": 1.0,
        "target_multiplier": 1.0,
        "qty_multiplier": 1.0,
        "warnings": [],
    }

    # ── EXTREME regime ──
    if regime == "EXTREME":
        rec["trade_allowed"] = False
        rec["main_pnl_allowed"] = False
        rec["scalper_allowed"] = False  # too risky
        rec["warnings"].append("EXTREME volatility — all trading paused")
        return rec

    # ── HIGH-VOL ──
    # TUNED 2026-05-05: backtest showed Volatility filter at 28.6% accuracy
    # (worst of all filters). 71% of blocks were winners. Lowered min_prob
    # 70 → 60 (still strict). Other safety measures (qty 0.7×, wider SL)
    # already manage risk.
    if "HIGH-VOL" in regime:
        rec["min_probability"] = 60  # was 70
        rec["sl_multiplier"] = 1.5
        rec["target_multiplier"] = 1.5
        rec["qty_multiplier"] = 0.7
        rec["warnings"].append(f"High volatility (VIX {vix}) — wider SL, larger targets, smaller qty")

    # ── EXPIRY DAY ──
    # TUNED: 70% min was killing valid expiry signals. 65% still strict
    # but more achievable. SL tightening + qty halving still protects.
    if "EXPIRY" in regime:
        rec["min_probability"] = 65  # was 70
        rec["sl_multiplier"] = 0.7  # tighter SL on expiry (theta crush)
        rec["target_multiplier"] = 0.7  # smaller targets (less time)
        rec["qty_multiplier"] = 0.5
        # User-configurable: allow PnL trades on expiry if user opts in.
        # Default ON (safer), but can be overridden via env var or DB toggle.
        # Env: ALLOW_PNL_ON_EXPIRY=1 → don't pause PnL on expiry days.
        import os
        allow_pnl_expiry = os.getenv("ALLOW_PNL_ON_EXPIRY", "").lower() in ("1", "true", "yes")
        if not allow_pnl_expiry:
            rec["main_pnl_allowed"] = False  # only scalper on expiry
            rec["warnings"].append("EXPIRY DAY — main P&L paused, scalper only with tight SL")
        else:
            rec["warnings"].append("EXPIRY DAY — PnL allowed (user override) but min_prob 70%, qty 50%")

    # ── Time-window adjustments ──
    if time_window == "OPENING_FIRST_5MIN":
        rec["trade_allowed"] = False
        rec["warnings"].append("First 5 min — wait for stabilization")
    elif time_window == "LUNCH_CHOP":
        # TUNED: 70 was over-blocking valid lunch trades. 60 keeps quality
        # bar high while capturing post-lunch trend setups.
        rec["min_probability"] = max(rec["min_probability"], 60)  # was 70
        rec["qty_multiplier"] *= 0.6
        rec["warnings"].append("Lunch chop — higher conviction needed (60%+)")
    elif time_window == "POWER_HOUR":
        rec["min_probability"] = max(rec["min_probability"], 60)
        rec["target_multiplier"] *= 1.3  # bigger moves possible
    elif time_window in ("PRE_MARKET", "POST_MARKET", "CLOSING"):
        rec["trade_allowed"] = False
        rec["warnings"].append(f"{time_window} — no new trades")

    return rec


def log_regime(regime_data):
    """Log regime to history table."""
    try:
        init_db()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT OR REPLACE INTO regime_log
            (ts, vix, regime, atr_ratio, day_range_pct, time_window, notes)
            VALUES (?,?,?,?,?,?,?)
        """, (
            regime_data["ts"],
            regime_data["vix"],
            regime_data["regime"],
            regime_data["atr_ratio"],
            regime_data["day_range_pct"],
            regime_data["time_window"],
            "; ".join(regime_data["notes"]),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[VOL] Log error: {e}")


def get_regime_history(hours=4):
    """Last N hours of regime changes."""
    init_db()
    cutoff = (ist_now() - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM regime_log WHERE ts > ? ORDER BY ts DESC LIMIT 100",
        (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
