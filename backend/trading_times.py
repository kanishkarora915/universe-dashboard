"""
Trading Times Engine — Market regime detection for BUYERS.
Captures 5-min snapshots of OI, premiums, velocity, institutional footprint.
Detects: SIDEWAYS | TRENDING | PRE_BLAST | BLAST | EXHAUSTION
Remembers yesterday's OI positions, tracks changes against them.
Generates daily/weekly/monthly reports.
"""

import sqlite3
import json
import time
import math
import threading
from datetime import datetime, timedelta, date
from pathlib import Path
from collections import defaultdict
import pytz

IST = pytz.timezone("Asia/Kolkata")

_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "trading_times.db"

# ── In-memory state for rolling calculations ────────────────────────────
# Lock protects concurrent access from tick thread + FastAPI request threads
_state_lock = threading.Lock()

_prev_snapshots = {}  # {index: last_snapshot_dict}
_accumulation_tracker = {}  # {index: {ce_blocks: int, pe_blocks: int, history: []}}
_prev_pcr = {}  # {index: float}
_prev_max_pain = {}  # {index: int}
_prev_ce_wall = {}  # {index: int}
_prev_pe_wall = {}  # {index: int}
_prev_velocity = {}  # {index: float}


def ist_now():
    return datetime.now(IST)


# ══════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            idx TEXT NOT NULL,
            spot REAL, spot_change_5min REAL, spot_change_pct REAL,
            atm_ce_ltp REAL, atm_pe_ltp REAL,
            ce_premium_change REAL, pe_premium_change REAL, premium_ratio REAL,
            ce_iv REAL, pe_iv REAL, iv_skew REAL,
            ce_volume_total INTEGER, pe_volume_total INTEGER, volume_ratio REAL,
            ce_oi_net_change INTEGER, pe_oi_net_change INTEGER,
            pcr REAL, pcr_change REAL,
            max_pain INTEGER, max_pain_shift INTEGER,
            top_ce_wall INTEGER, top_pe_wall INTEGER,
            ce_wall_shift INTEGER, pe_wall_shift INTEGER,
            vwap REAL, spot_vs_vwap REAL,
            velocity_score REAL, acceleration REAL,
            ce_accum_blocks INTEGER, pe_accum_blocks INTEGER,
            hedge_ratio REAL, hedge_trend TEXT, conviction TEXT,
            ce_unwinding INTEGER, pe_unwinding INTEGER,
            oi_cog REAL, cog_shift REAL,
            hedge_flip INTEGER,
            window_type TEXT, blast_direction TEXT, confidence INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tt_ts ON market_snapshots(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tt_idx ON market_snapshots(idx)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS yesterday_oi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            idx TEXT NOT NULL,
            strike INTEGER NOT NULL,
            ce_oi INTEGER DEFAULT 0,
            pe_oi INTEGER DEFAULT 0,
            ce_ltp REAL DEFAULT 0,
            pe_ltp REAL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_yoi_date ON yesterday_oi(date, idx)")

    conn.commit()
    conn.close()
    # Purge >60 days
    _purge_old_data()
    print(f"[TRADING-TIMES] DB initialized at {DB_PATH}")


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _purge_old_data():
    cutoff = (ist_now() - timedelta(days=60)).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM market_snapshots WHERE timestamp < ?", (cutoff,))
    cutoff_date = (ist_now() - timedelta(days=60)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM yesterday_oi WHERE date < ?", (cutoff_date,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
# CORE: CAPTURE SNAPSHOT (called every 5 min by engine)
# ══════════════════════════════════════════════════════════════════════════

def capture_snapshot(engine, index):
    """Capture full market state and classify window type."""
    from engine import compute_max_pain, find_big_walls, INDEX_CONFIG

    now = ist_now()
    # Only capture during market hours (9:15 - 15:30)
    if now.hour < 9 or (now.hour == 9 and now.minute < 15) or now.hour >= 16:
        return None
    if now.hour == 15 and now.minute > 30:
        return None

    cfg = INDEX_CONFIG[index]
    key = index.lower()
    chain = engine.chains.get(index, {})
    spot_token = engine.spot_tokens.get(index)
    spot = engine.prices.get(spot_token, {}).get("ltp", 0)
    if spot <= 0 or not chain:
        return None

    atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"]
    atm_data = chain.get(atm, {})
    prev = _prev_snapshots.get(key, {})

    # ── Premium Data ──
    atm_ce_ltp = atm_data.get("ce_ltp", 0)
    atm_pe_ltp = atm_data.get("pe_ltp", 0)
    ce_prem_change = atm_ce_ltp - prev.get("atm_ce_ltp", atm_ce_ltp)
    pe_prem_change = atm_pe_ltp - prev.get("atm_pe_ltp", atm_pe_ltp)
    prem_ratio = round(atm_ce_ltp / max(atm_pe_ltp, 0.01), 2)

    # ── IV Data ──
    ce_iv = 0
    pe_iv = 0
    try:
        ce_iv = engine._get_atm_iv(index, spot) or 0
    except Exception:
        pass
    iv_skew = round(ce_iv - pe_iv, 2)  # PE IV approximated as CE IV - skew

    # ── Volume ──
    ce_vol_total = sum(d.get("ce_volume", 0) for d in chain.values())
    pe_vol_total = sum(d.get("pe_volume", 0) for d in chain.values())
    vol_ratio = round(ce_vol_total / max(pe_vol_total, 1), 2)

    # ── OI Flow ──
    total_ce_oi_change = 0
    total_pe_oi_change = 0
    total_ce_oi = 0
    total_pe_oi = 0
    for strike_val, data in chain.items():
        ce_oi = data.get("ce_oi", 0)
        pe_oi = data.get("pe_oi", 0)
        total_ce_oi += ce_oi
        total_pe_oi += pe_oi
        # OI change from initial
        for tok, info in engine.token_to_info.items():
            if info["index"] == index and info["strike"] == strike_val:
                if info["opt_type"] == "CE":
                    total_ce_oi_change += ce_oi - engine.initial_oi.get(tok, ce_oi)
                elif info["opt_type"] == "PE":
                    total_pe_oi_change += pe_oi - engine.initial_oi.get(tok, pe_oi)

    pcr = round(total_pe_oi / max(total_ce_oi, 1), 2)
    pcr_change = round(pcr - _prev_pcr.get(key, pcr), 3)

    # ── Max Pain + Walls ──
    max_pain = compute_max_pain(chain, spot)
    mp_shift = max_pain - _prev_max_pain.get(key, max_pain)
    ce_wall, pe_wall = find_big_walls(chain)
    ce_wall_shift = ce_wall - _prev_ce_wall.get(key, ce_wall)
    pe_wall_shift = pe_wall - _prev_pe_wall.get(key, pe_wall)

    # ── VWAP ──
    vwap = 0
    try:
        vwap = engine._get_vwap(index) or 0
    except Exception:
        pass
    spot_vs_vwap = round(spot - vwap, 1) if vwap > 0 else 0

    # ── Spot Change ──
    spot_change = round(spot - prev.get("spot", spot), 1)
    spot_change_pct = round(abs(spot_change) / max(prev.get("spot", spot), 1) * 100, 4)

    # ── Velocity (0-10 scale) ──
    velocity = min(10, round(spot_change_pct * 30, 1))  # 0.33% = 10
    prev_vel = _prev_velocity.get(key, velocity)
    acceleration = round(velocity - prev_vel, 2)

    # ── Accumulation Detection (rolling blocks) ──
    tracker = _accumulation_tracker.get(key, {"ce_blocks": 0, "pe_blocks": 0, "ce_hist": [], "pe_hist": []})

    if total_ce_oi_change > prev.get("ce_oi_net_change", 0):
        tracker["ce_blocks"] += 1
        tracker["ce_hist"].append(total_ce_oi_change - prev.get("ce_oi_net_change", 0))
    else:
        tracker["ce_blocks"] = 0
        tracker["ce_hist"] = []

    if total_pe_oi_change > prev.get("pe_oi_net_change", 0):
        tracker["pe_blocks"] += 1
        tracker["pe_hist"].append(total_pe_oi_change - prev.get("pe_oi_net_change", 0))
    else:
        tracker["pe_blocks"] = 0
        tracker["pe_hist"] = []

    # Keep max 12 blocks (1 hour)
    tracker["ce_hist"] = tracker["ce_hist"][-12:]
    tracker["pe_hist"] = tracker["pe_hist"][-12:]
    _accumulation_tracker[key] = tracker

    # ── Hedge Ratio & Conviction ──
    ce_oi_delta = max(total_ce_oi_change, 0)
    pe_oi_delta = max(total_pe_oi_change, 0)
    if ce_oi_delta > 0 and pe_oi_delta > 0:
        hedge_ratio = round(max(ce_oi_delta, pe_oi_delta) / min(ce_oi_delta, pe_oi_delta), 1)
    elif ce_oi_delta > 0:
        hedge_ratio = 999
    elif pe_oi_delta > 0:
        hedge_ratio = 999
    else:
        hedge_ratio = 1

    prev_hr = prev.get("hedge_ratio", hedge_ratio)
    if hedge_ratio > prev_hr + 1:
        hedge_trend = "REDUCING"  # Hedge reducing = more conviction
    elif hedge_ratio < prev_hr - 1:
        hedge_trend = "INCREASING"
    else:
        hedge_trend = "STABLE"

    if hedge_ratio >= 10:
        conviction = "MAX"
    elif hedge_ratio >= 5:
        conviction = "HIGH"
    elif hedge_ratio >= 2.5:
        conviction = "MEDIUM"
    else:
        conviction = "LOW"

    # ── Unwinding Detection ──
    ce_unwinding = 1 if total_ce_oi_change < -100000 else 0
    pe_unwinding = 1 if total_pe_oi_change < -100000 else 0

    # ── OI Center of Gravity ──
    weighted_sum = 0
    oi_sum = 0
    for strike_val, data in chain.items():
        total = data.get("ce_oi", 0) + data.get("pe_oi", 0)
        weighted_sum += strike_val * total
        oi_sum += total
    oi_cog = round(weighted_sum / max(oi_sum, 1))
    cog_shift = oi_cog - prev.get("oi_cog", oi_cog)

    # ── Hedge Flip Detection ──
    # PE was building → PE now unwinding WHILE CE still building
    prev_pe_change = prev.get("pe_oi_net_change", 0)
    hedge_flip = 0
    if prev_pe_change > 50000 and total_pe_oi_change < prev_pe_change and total_ce_oi_change > 50000:
        hedge_flip = 1
    if prev.get("ce_oi_net_change", 0) > 50000 and total_ce_oi_change < prev.get("ce_oi_net_change", 0) and total_pe_oi_change > 50000:
        hedge_flip = 1

    # ══════════════════════════════════════════════════
    # WINDOW CLASSIFICATION
    # ══════════════════════════════════════════════════

    window_type = "SIDEWAYS"
    blast_direction = "NONE"
    confidence = 30

    # Check for BLAST first
    if spot_change_pct > 0.25 and velocity > 6:
        window_type = "BLAST"
        confidence = min(95, 60 + int(velocity * 3))
        if spot_change > 0:
            blast_direction = "BULLISH"
        else:
            blast_direction = "BEARISH"

    # Check for PRE-BLAST
    elif (tracker["ce_blocks"] >= 3 or tracker["pe_blocks"] >= 3) and hedge_trend == "REDUCING" and acceleration > 0.5:
        window_type = "PRE_BLAST"
        confidence = min(85, 50 + tracker["ce_blocks"] * 5 + tracker["pe_blocks"] * 5 + int(acceleration * 10))
        if tracker["pe_blocks"] > tracker["ce_blocks"] and pcr > 1.1:
            blast_direction = "BULLISH"  # PE building = support = bullish for buyer
        elif tracker["ce_blocks"] > tracker["pe_blocks"] and pcr < 0.9:
            blast_direction = "BEARISH"
        elif ce_unwinding and tracker["pe_blocks"] >= 3:
            blast_direction = "BULLISH"  # CE unwinding + PE building = bullish
        elif pe_unwinding and tracker["ce_blocks"] >= 3:
            blast_direction = "BEARISH"

    # Check for EXHAUSTION (after blast)
    elif ce_unwinding and pe_unwinding:
        window_type = "EXHAUSTION"
        confidence = 60
        blast_direction = "NONE"

    # Check for TRENDING
    elif spot_change_pct > 0.08 or velocity > 2:
        window_type = "TRENDING"
        confidence = min(70, 40 + int(velocity * 5))
        if spot_change > 0:
            blast_direction = "BULLISH"
        else:
            blast_direction = "BEARISH"

    # Boost confidence with hedge flip
    if hedge_flip:
        confidence = min(95, confidence + 15)

    # Boost with volume confirmation
    if vol_ratio > 3 and blast_direction == "BULLISH":
        confidence = min(95, confidence + 5)
    elif vol_ratio < 0.33 and blast_direction == "BEARISH":
        confidence = min(95, confidence + 5)

    # ── Store snapshot ──
    snapshot = {
        "timestamp": now.isoformat(),
        "idx": index,
        "spot": spot, "spot_change_5min": spot_change, "spot_change_pct": spot_change_pct,
        "atm_ce_ltp": atm_ce_ltp, "atm_pe_ltp": atm_pe_ltp,
        "ce_premium_change": ce_prem_change, "pe_premium_change": pe_prem_change,
        "premium_ratio": prem_ratio,
        "ce_iv": round(ce_iv, 1), "pe_iv": round(pe_iv, 1), "iv_skew": iv_skew,
        "ce_volume_total": ce_vol_total, "pe_volume_total": pe_vol_total,
        "volume_ratio": vol_ratio,
        "ce_oi_net_change": total_ce_oi_change, "pe_oi_net_change": total_pe_oi_change,
        "pcr": pcr, "pcr_change": pcr_change,
        "max_pain": max_pain, "max_pain_shift": mp_shift,
        "top_ce_wall": ce_wall, "top_pe_wall": pe_wall,
        "ce_wall_shift": ce_wall_shift, "pe_wall_shift": pe_wall_shift,
        "vwap": round(vwap, 1), "spot_vs_vwap": spot_vs_vwap,
        "velocity_score": velocity, "acceleration": acceleration,
        "ce_accum_blocks": tracker["ce_blocks"], "pe_accum_blocks": tracker["pe_blocks"],
        "hedge_ratio": hedge_ratio, "hedge_trend": hedge_trend, "conviction": conviction,
        "ce_unwinding": ce_unwinding, "pe_unwinding": pe_unwinding,
        "oi_cog": oi_cog, "cog_shift": cog_shift,
        "hedge_flip": hedge_flip,
        "window_type": window_type, "blast_direction": blast_direction,
        "confidence": confidence,
    }

    # Save to DB
    cols = list(snapshot.keys())
    vals = list(snapshot.values())
    placeholders = ", ".join(["?"] * len(vals))
    col_str = ", ".join(cols)
    conn = _conn()
    conn.execute(f"INSERT INTO market_snapshots ({col_str}) VALUES ({placeholders})", vals)
    conn.commit()
    conn.close()

    # Update prev state
    _prev_snapshots[key] = snapshot
    _prev_pcr[key] = pcr
    _prev_max_pain[key] = max_pain
    _prev_ce_wall[key] = ce_wall
    _prev_pe_wall[key] = pe_wall
    _prev_velocity[key] = velocity

    signal_str = f"{window_type}"
    if blast_direction != "NONE":
        signal_str += f" {blast_direction}"
    print(f"[TRADING-TIMES] {index} {now.strftime('%H:%M')} | {signal_str} | conf={confidence}% | vel={velocity} | PCR={pcr} | hedge={hedge_ratio}")

    return snapshot


# ══════════════════════════════════════════════════════════════════════════
# YESTERDAY OI — Save at EOD, compare next day
# ══════════════════════════════════════════════════════════════════════════

def save_yesterday_oi(engine):
    """Save end-of-day OI for all strikes. Called at ~3:25 PM."""
    today = ist_now().strftime("%Y-%m-%d")
    conn = _conn()
    # Clear today's data if re-saving
    conn.execute("DELETE FROM yesterday_oi WHERE date = ?", (today,))

    for index in ["NIFTY", "BANKNIFTY"]:
        chain = engine.chains.get(index, {})
        for strike_val, data in chain.items():
            conn.execute(
                "INSERT INTO yesterday_oi (date, idx, strike, ce_oi, pe_oi, ce_ltp, pe_ltp) VALUES (?,?,?,?,?,?,?)",
                (today, index, strike_val,
                 data.get("ce_oi", 0), data.get("pe_oi", 0),
                 data.get("ce_ltp", 0), data.get("pe_ltp", 0))
            )
    conn.commit()
    conn.close()
    print(f"[TRADING-TIMES] Yesterday OI saved for {today}")


def get_yesterday_vs_today(engine, index):
    """Compare yesterday's EOD OI with today's current OI."""
    # Get yesterday's date (last trading day)
    today = ist_now().date()
    conn = _conn()

    # Find most recent saved date
    row = conn.execute(
        "SELECT DISTINCT date FROM yesterday_oi WHERE idx = ? ORDER BY date DESC LIMIT 1",
        (index,)
    ).fetchone()

    if not row:
        conn.close()
        return {"error": "No yesterday data", "strikes": []}

    prev_date = row["date"]
    prev_rows = conn.execute(
        "SELECT * FROM yesterday_oi WHERE date = ? AND idx = ?",
        (prev_date, index)
    ).fetchall()
    conn.close()

    chain = engine.chains.get(index, {})
    from engine import INDEX_CONFIG
    cfg = INDEX_CONFIG[index]
    spot_token = engine.spot_tokens.get(index)
    spot = engine.prices.get(spot_token, {}).get("ltp", 0)
    atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"] if spot > 0 else 0

    # Build yesterday lookup
    yesterday = {r["strike"]: dict(r) for r in prev_rows}

    strikes = []
    # ATM ± 10 strikes
    for offset in range(-10, 11):
        s = atm + offset * cfg["strike_gap"]
        yd = yesterday.get(s, {})
        td = chain.get(s, {})

        ye_ce = yd.get("ce_oi", 0)
        ye_pe = yd.get("pe_oi", 0)
        td_ce = td.get("ce_oi", 0)
        td_pe = td.get("pe_oi", 0)

        strikes.append({
            "strike": s,
            "isATM": s == atm,
            "yesterdayCE": ye_ce,
            "todayCE": td_ce,
            "ceChange": td_ce - ye_ce,
            "cePctChange": round((td_ce - ye_ce) / max(ye_ce, 1) * 100, 1) if ye_ce > 0 else 0,
            "yesterdayPE": ye_pe,
            "todayPE": td_pe,
            "peChange": td_pe - ye_pe,
            "pePctChange": round((td_pe - ye_pe) / max(ye_pe, 1) * 100, 1) if ye_pe > 0 else 0,
        })

    return {
        "previousDate": prev_date,
        "index": index,
        "atm": atm,
        "spot": spot,
        "strikes": strikes,
    }


# ══════════════════════════════════════════════════════════════════════════
# LIVE DASHBOARD — Current state for frontend
# ══════════════════════════════════════════════════════════════════════════

def get_live_dashboard(engine, index):
    """Get current trading times state with all layers."""
    conn = _conn()
    today = ist_now().strftime("%Y-%m-%d")

    # Last 7 snapshots (35 min of data)
    rows = conn.execute(
        "SELECT * FROM market_snapshots WHERE idx = ? AND timestamp > ? ORDER BY timestamp DESC LIMIT 7",
        (index, today)
    ).fetchall()
    conn.close()

    snapshots = [dict(r) for r in rows]
    latest = snapshots[0] if snapshots else None

    # Yesterday comparison
    yesterday = get_yesterday_vs_today(engine, index)

    # Build signal
    signal = {
        "windowType": "NO_DATA",
        "blastDirection": "NONE",
        "confidence": 0,
        "message": "Waiting for data...",
    }

    if latest:
        wt = latest["window_type"]
        bd = latest["blast_direction"]
        conf = latest["confidence"]

        if wt == "BLAST" and bd == "BULLISH":
            msg = "BULLISH BLAST — CE buyers rushing, buy now!"
        elif wt == "BLAST" and bd == "BEARISH":
            msg = "BEARISH BLAST — PE buyers rushing, buy PE now!"
        elif wt == "PRE_BLAST" and bd == "BULLISH":
            msg = "PRE-BLAST BULLISH — Accumulation detected, blast coming UP"
        elif wt == "PRE_BLAST" and bd == "BEARISH":
            msg = "PRE-BLAST BEARISH — Distribution detected, blast coming DOWN"
        elif wt == "TRENDING" and bd == "BULLISH":
            msg = "TRENDING UP — Steady bullish flow"
        elif wt == "TRENDING" and bd == "BEARISH":
            msg = "TRENDING DOWN — Steady bearish flow"
        elif wt == "EXHAUSTION":
            msg = "EXHAUSTION — Move over, both sides unwinding. AVOID."
        else:
            msg = "SIDEWAYS — No clear direction, patience."

        signal = {
            "windowType": wt,
            "blastDirection": bd,
            "confidence": conf,
            "message": msg,
        }

    return {
        "signal": signal,
        "latest": latest,
        "history": snapshots,
        "yesterday": yesterday,
        "timestamp": ist_now().strftime("%I:%M:%S %p IST"),
    }


# ══════════════════════════════════════════════════════════════════════════
# TIMELINE — Today's full window history
# ══════════════════════════════════════════════════════════════════════════

def get_today_timeline(index):
    """Get all snapshots for today as a timeline."""
    conn = _conn()
    today = ist_now().strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT * FROM market_snapshots WHERE idx = ? AND timestamp > ? ORDER BY timestamp ASC",
        (index, today)
    ).fetchall()
    conn.close()

    snapshots = [dict(r) for r in rows]

    # Count window types
    counts = defaultdict(int)
    blasts = []
    for s in snapshots:
        counts[s["window_type"]] += 1
        if s["window_type"] == "BLAST":
            blasts.append({
                "time": s["timestamp"],
                "direction": s["blast_direction"],
                "confidence": s["confidence"],
                "velocity": s["velocity_score"],
                "spotChange": s["spot_change_5min"],
            })

    return {
        "index": index,
        "date": today,
        "snapshots": snapshots,
        "summary": {
            "total": len(snapshots),
            "sideways": counts.get("SIDEWAYS", 0),
            "trending": counts.get("TRENDING", 0),
            "preBlast": counts.get("PRE_BLAST", 0),
            "blast": counts.get("BLAST", 0),
            "exhaustion": counts.get("EXHAUSTION", 0),
        },
        "blasts": blasts,
    }


# ══════════════════════════════════════════════════════════════════════════
# REPORTS — Daily, Weekly, Monthly
# ══════════════════════════════════════════════════════════════════════════

def get_daily_report(report_date=None):
    """Daily report — all windows, blasts, patterns for one day."""
    if not report_date:
        report_date = ist_now().strftime("%Y-%m-%d")

    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM market_snapshots WHERE timestamp LIKE ? ORDER BY timestamp ASC",
        (f"{report_date}%",)
    ).fetchall()
    conn.close()

    if not rows:
        return {"date": report_date, "error": "No data for this date"}

    # Group by index
    by_index = defaultdict(list)
    for r in rows:
        by_index[r["idx"]].append(dict(r))

    report = {"date": report_date, "indices": {}}

    for idx, snaps in by_index.items():
        counts = defaultdict(int)
        blasts = []
        best_blast = None
        total_velocity = 0

        for s in snaps:
            counts[s["window_type"]] += 1
            total_velocity += s["velocity_score"]
            if s["window_type"] == "BLAST":
                blasts.append(s)
                if not best_blast or abs(s["spot_change_5min"]) > abs(best_blast["spot_change_5min"]):
                    best_blast = s

        report["indices"][idx.lower()] = {
            "totalSnapshots": len(snaps),
            "windows": dict(counts),
            "blastCount": len(blasts),
            "bestBlast": {
                "time": best_blast["timestamp"].split("T")[1][:5] if best_blast else None,
                "direction": best_blast["blast_direction"] if best_blast else None,
                "move": best_blast["spot_change_5min"] if best_blast else 0,
                "confidence": best_blast["confidence"] if best_blast else 0,
            } if best_blast else None,
            "avgVelocity": round(total_velocity / max(len(snaps), 1), 1),
            "snapshots": snaps,
        }

    return report


def get_weekly_report():
    """Weekly report — 5 trading days aggregated."""
    now = ist_now()
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM market_snapshots WHERE timestamp > ? ORDER BY timestamp ASC",
        (week_start,)
    ).fetchall()
    conn.close()

    if not rows:
        return {"error": "No data for this week", "period": f"{week_start} to {now.strftime('%Y-%m-%d')}"}

    # Group by date and index
    by_date = defaultdict(lambda: defaultdict(list))
    for r in rows:
        d = r["timestamp"][:10]
        by_date[d][r["idx"]].append(dict(r))

    total_blasts = 0
    total_snapshots = 0
    blast_hours = defaultdict(int)
    best_blast = None

    daily_summaries = []
    for d in sorted(by_date.keys()):
        day_blasts = 0
        day_total = 0
        for idx, snaps in by_date[d].items():
            day_total += len(snaps)
            for s in snaps:
                if s["window_type"] == "BLAST":
                    day_blasts += 1
                    total_blasts += 1
                    h = datetime.fromisoformat(s["timestamp"]).hour
                    blast_hours[h] += 1
                    if not best_blast or abs(s["spot_change_5min"]) > abs(best_blast["spot_change_5min"]):
                        best_blast = s
            total_snapshots += len(snaps)

        daily_summaries.append({"date": d, "snapshots": day_total, "blasts": day_blasts})

    # Best blast hour
    best_hour = max(blast_hours.items(), key=lambda x: x[1]) if blast_hours else (0, 0)

    return {
        "period": f"{week_start} to {now.strftime('%Y-%m-%d')}",
        "totalSnapshots": total_snapshots,
        "totalBlasts": total_blasts,
        "avgBlastsPerDay": round(total_blasts / max(len(by_date), 1), 1),
        "bestBlastHour": f"{best_hour[0]}:00" if best_hour[1] > 0 else "N/A",
        "bestBlast": {
            "date": best_blast["timestamp"][:10] if best_blast else None,
            "time": best_blast["timestamp"].split("T")[1][:5] if best_blast else None,
            "direction": best_blast["blast_direction"] if best_blast else None,
            "move": best_blast["spot_change_5min"] if best_blast else 0,
        } if best_blast else None,
        "dailySummaries": daily_summaries,
        "blastByHour": dict(blast_hours),
    }


def get_monthly_report(year=None, month=None):
    """Monthly report — full month analysis."""
    now = ist_now()
    if not year:
        year = now.year
    if not month:
        month = now.month

    month_start = f"{year}-{month:02d}-01"
    if month == 12:
        month_end = f"{year + 1}-01-01"
    else:
        month_end = f"{year}-{month + 1:02d}-01"

    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM market_snapshots WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp ASC",
        (month_start, month_end)
    ).fetchall()
    conn.close()

    if not rows:
        return {"error": "No data for this month", "month": f"{year}-{month:02d}"}

    # Aggregate
    by_week = defaultdict(lambda: {"blasts": 0, "total": 0})
    blast_directions = defaultdict(int)
    window_counts = defaultdict(int)
    total_blasts = 0

    for r in rows:
        dt = datetime.fromisoformat(r["timestamp"])
        week_num = dt.isocalendar()[1]
        by_week[week_num]["total"] += 1
        window_counts[r["window_type"]] += 1
        if r["window_type"] == "BLAST":
            by_week[week_num]["blasts"] += 1
            total_blasts += 1
            blast_directions[r["blast_direction"]] += 1

    return {
        "month": f"{year}-{month:02d}",
        "totalSnapshots": len(rows),
        "totalBlasts": total_blasts,
        "windowBreakdown": dict(window_counts),
        "blastDirections": dict(blast_directions),
        "weeklyBreakdown": [{"week": w, **d} for w, d in sorted(by_week.items())],
        "tradingDays": len(set(r["timestamp"][:10] for r in rows)),
    }
