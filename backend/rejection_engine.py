"""
Rejection Zone Engine — institutional level detection for option BUYERS.

Concept:
  - Track day H/L + intraday wicks across last 10 days
  - Cluster nearby levels → STRONG zones (multi-day touches)
  - Around each zone: deep OI analysis (today change + total) + hidden activity
  - Hidden activity = sudden 100+ lot moves, stealth builds, smart exits

Usage:
  - capture_price_sample(engine) every 5 min
  - capture_oi_snapshot(engine) every 5 min (per ATM±10 strikes)
  - get_rejection_zones(engine, idx) → full analysis with verdict

DB stored on /data persistent disk (survives Render deploys).
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
import pytz
from collections import defaultdict

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "rejection_zones.db"

# Lot sizes (used for hidden activity threshold) — current as of 2025
LOT_SIZE = {"NIFTY": 75, "BANKNIFTY": 35}
HIDDEN_LOT_THRESHOLD = 100   # 100+ lots in 5-min window = institutional
STEALTH_OI_THRESHOLD_PCT = 20  # 20%+ OI move with flat price = stealth


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    # Daily H/L for rejection zone detection
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            idx TEXT NOT NULL,
            day_high REAL,
            day_low REAL,
            day_close REAL,
            day_open REAL,
            UNIQUE(date, idx)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dl_date ON day_levels(date)")

    # Intraday price samples (every 5 min) for live wick detection
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            idx TEXT NOT NULL,
            ltp REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ps_date_idx ON price_samples(date, idx)")

    # OI snapshots every 5 min — per ATM±10 strikes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_5min (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            idx TEXT NOT NULL,
            strike INTEGER NOT NULL,
            ce_oi INTEGER, ce_ltp REAL, ce_volume INTEGER,
            pe_oi INTEGER, pe_ltp REAL, pe_volume INTEGER,
            spot REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oi5_lookup ON oi_5min(date, idx, strike, time)")

    # Detected hidden activity events (alerts feed)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hidden_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            idx TEXT NOT NULL,
            strike INTEGER NOT NULL,
            side TEXT NOT NULL,
            event_type TEXT NOT NULL,
            lots_moved INTEGER,
            oi_delta INTEGER,
            premium_delta REAL,
            description TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_he_time ON hidden_events(time DESC)")
    conn.commit()
    conn.close()


def _conn():
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════════════════
# CAPTURE — called from engine loop
# ═══════════════════════════════════════════════════════════════

def capture_price_sample(engine):
    """Capture 5-min price snapshot for both indices. Call every 5 min."""
    init_db()
    now = ist_now()
    if not (9 <= now.hour <= 15 and (now.hour < 15 or now.minute < 30)):
        return
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    conn = _conn()
    for idx in ["NIFTY", "BANKNIFTY"]:
        spot_token = engine.spot_tokens.get(idx)
        if not spot_token:
            continue
        ltp = engine.prices.get(spot_token, {}).get("ltp", 0)
        if ltp > 0:
            conn.execute(
                "INSERT INTO price_samples (date, time, idx, ltp) VALUES (?,?,?,?)",
                (date_str, time_str, idx, ltp)
            )
    conn.commit()
    conn.close()


def capture_oi_snapshot(engine):
    """Capture per-strike OI for ATM±10 strikes. Call every 5 min."""
    from engine import INDEX_CONFIG
    init_db()
    now = ist_now()
    if not (9 <= now.hour <= 15 and (now.hour < 15 or now.minute < 30)):
        return
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    conn = _conn()

    for idx in ["NIFTY", "BANKNIFTY"]:
        cfg = INDEX_CONFIG[idx]
        chain = engine.chains.get(idx, {})
        spot_token = engine.spot_tokens.get(idx)
        spot = engine.prices.get(spot_token, {}).get("ltp", 0)
        if spot <= 0 or not chain:
            continue
        atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"]

        for offset in range(-10, 11):
            strike = atm + offset * cfg["strike_gap"]
            d = chain.get(strike, {})
            if not d:
                continue
            conn.execute("""
                INSERT INTO oi_5min (date, time, idx, strike,
                    ce_oi, ce_ltp, ce_volume, pe_oi, pe_ltp, pe_volume, spot)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                date_str, time_str, idx, strike,
                d.get("ce_oi", 0), d.get("ce_ltp", 0), d.get("ce_volume", 0),
                d.get("pe_oi", 0), d.get("pe_ltp", 0), d.get("pe_volume", 0),
                spot
            ))

            # Detect hidden activity by comparing with snapshot 5 min ago
            _detect_hidden_for_strike(conn, idx, strike, d, time_str, date_str)

    conn.commit()
    conn.close()


def capture_eod_levels(engine):
    """Save day H/L at EOD (3:25 PM) for next day's rejection analysis."""
    init_db()
    today = ist_now().strftime("%Y-%m-%d")
    conn = _conn()
    for idx in ["NIFTY", "BANKNIFTY"]:
        try:
            live = engine.get_live_data()
            d = live.get(idx.lower(), {})
            high = d.get("high", 0) or d.get("dayHigh", 0)
            low = d.get("low", 0) or d.get("dayLow", 0)
            close = d.get("ltp", 0)
            open_p = d.get("openPrice", 0) or d.get("open", 0)
            if high > 0 and low > 0:
                conn.execute("""
                    INSERT OR REPLACE INTO day_levels (date, idx, day_high, day_low, day_close, day_open)
                    VALUES (?,?,?,?,?,?)
                """, (today, idx, high, low, close, open_p))
        except Exception as e:
            print(f"[REJECTION] EOD capture {idx} error: {e}")
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# HIDDEN ACTIVITY DETECTION
# ═══════════════════════════════════════════════════════════════

def _detect_hidden_for_strike(conn, idx, strike, current, time_str, date_str):
    """Compare current snapshot vs 5 min ago snapshot. Log hidden activity events."""
    prev = conn.execute("""
        SELECT * FROM oi_5min WHERE idx=? AND strike=? AND date=? AND time<?
        ORDER BY time DESC LIMIT 1
    """, (idx, strike, date_str, time_str)).fetchone()
    if not prev:
        return

    lot_size = LOT_SIZE.get(idx, 25)

    for side in ["CE", "PE"]:
        oi_now = current.get(f"{side.lower()}_oi", 0)
        ltp_now = current.get(f"{side.lower()}_ltp", 0)
        oi_prev = prev[f"{side.lower()}_oi"] or 0
        ltp_prev = prev[f"{side.lower()}_ltp"] or 0

        if oi_prev == 0 or ltp_prev == 0:
            continue

        oi_delta = oi_now - oi_prev
        oi_pct = (oi_delta / oi_prev * 100) if oi_prev > 0 else 0
        ltp_delta = ltp_now - ltp_prev
        lots_moved = abs(oi_delta) / lot_size

        # 1. Sudden mass entry/exit (100+ lots in 5 min)
        if lots_moved >= HIDDEN_LOT_THRESHOLD:
            if oi_delta > 0 and ltp_delta > 0:
                etype = "MASS BUY ENTRY"
                desc = f"{int(lots_moved)} lots BOUGHT {side} @ {strike} — bullish bet on {side}"
            elif oi_delta > 0 and ltp_delta <= 0:
                etype = "MASS WRITE"
                desc = f"{int(lots_moved)} lots WRITTEN {side} @ {strike} — sellers building wall"
            elif oi_delta < 0 and ltp_delta > 0:
                etype = "MASS COVER"
                desc = f"{int(lots_moved)} lots COVERED {side} @ {strike} — sellers exiting (squeeze)"
            else:
                etype = "MASS UNWIND"
                desc = f"{int(lots_moved)} lots UNWOUND {side} @ {strike} — buyers giving up"

            conn.execute("""
                INSERT INTO hidden_events (time, idx, strike, side, event_type,
                    lots_moved, oi_delta, premium_delta, description)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                f"{date_str} {time_str}", idx, strike, side, etype,
                int(lots_moved), int(oi_delta), round(ltp_delta, 2), desc
            ))

        # 2. Stealth build — 20%+ OI change but premium flat (<2% move)
        elif abs(oi_pct) >= STEALTH_OI_THRESHOLD_PCT and ltp_prev > 0:
            ltp_pct = abs(ltp_delta / ltp_prev * 100)
            if ltp_pct < 2:
                etype = "STEALTH BUILD" if oi_delta > 0 else "STEALTH UNWIND"
                desc = f"{side} OI {oi_pct:+.0f}% @ {strike} but premium flat ({ltp_pct:.1f}%) — hidden positioning"
                conn.execute("""
                    INSERT INTO hidden_events (time, idx, strike, side, event_type,
                        lots_moved, oi_delta, premium_delta, description)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    f"{date_str} {time_str}", idx, strike, side, etype,
                    int(lots_moved), int(oi_delta), round(ltp_delta, 2), desc
                ))


def get_recent_hidden_events(idx=None, hours=2, limit=30):
    """Recent hidden activity feed."""
    init_db()
    cutoff = (ist_now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    conn = _conn()
    if idx:
        rows = conn.execute(
            "SELECT * FROM hidden_events WHERE time>=? AND idx=? ORDER BY time DESC LIMIT ?",
            (cutoff, idx, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM hidden_events WHERE time>=? ORDER BY time DESC LIMIT ?",
            (cutoff, limit)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
# REJECTION ZONE DETECTION
# ═══════════════════════════════════════════════════════════════

def _cluster_levels(levels, gap):
    """Cluster nearby levels (within strike_gap*2) into single zones."""
    if not levels:
        return []
    levels_sorted = sorted(levels, key=lambda x: x["level"])
    clusters = [[levels_sorted[0]]]
    for lv in levels_sorted[1:]:
        if abs(lv["level"] - clusters[-1][-1]["level"]) <= gap * 2:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])
    out = []
    for cluster in clusters:
        avg_level = sum(c["level"] for c in cluster) / len(cluster)
        touches = len(cluster)
        types = set(c["type"] for c in cluster)
        last_seen = max(c["date"] for c in cluster)
        out.append({
            "level": round(avg_level),
            "touches": touches,
            "types": list(types),
            "last_seen": last_seen,
            "raw": cluster,
        })
    return out


def find_rejection_zones(engine, idx, days=10):
    """Identify upside + downside rejection zones from last N days."""
    from engine import INDEX_CONFIG
    init_db()
    cfg = INDEX_CONFIG[idx]
    strike_gap = cfg["strike_gap"]

    spot_token = engine.spot_tokens.get(idx)
    spot = engine.prices.get(spot_token, {}).get("ltp", 0) if spot_token else 0
    if spot <= 0:
        return {"error": "No spot price"}

    cutoff = (ist_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _conn()

    # 1. Day H/L from history
    rows = conn.execute(
        "SELECT date, day_high, day_low FROM day_levels WHERE idx=? AND date>=? ORDER BY date DESC",
        (idx, cutoff)
    ).fetchall()

    levels = []
    for r in rows:
        if r["day_high"]:
            levels.append({"level": r["day_high"], "type": "DAY_HIGH", "date": r["date"]})
        if r["day_low"]:
            levels.append({"level": r["day_low"], "type": "DAY_LOW", "date": r["date"]})

    # 2. Today's intraday wicks (max/min from 5-min samples)
    today = ist_now().strftime("%Y-%m-%d")
    today_rows = conn.execute(
        "SELECT MAX(ltp) as h, MIN(ltp) as l FROM price_samples WHERE idx=? AND date=?",
        (idx, today)
    ).fetchone()
    if today_rows and today_rows["h"]:
        levels.append({"level": today_rows["h"], "type": "TODAY_HIGH", "date": today})
        levels.append({"level": today_rows["l"], "type": "TODAY_LOW", "date": today})

    conn.close()

    # 3. Cluster nearby levels (multi-day = stronger)
    clustered = _cluster_levels(levels, strike_gap)

    # 4. Split upside vs downside relative to spot
    upside = [c for c in clustered if c["level"] > spot]
    downside = [c for c in clustered if c["level"] <= spot]

    # Sort: upside ascending (closest first), downside descending (closest first)
    upside.sort(key=lambda x: x["level"])
    downside.sort(key=lambda x: -x["level"])

    # Snap to strike grid
    def to_strike(lv):
        return round(lv["level"] / strike_gap) * strike_gap

    upside = upside[:5]
    downside = downside[:5]
    for c in upside:
        c["strike"] = to_strike(c)
    for c in downside:
        c["strike"] = to_strike(c)

    return {
        "idx": idx,
        "spot": spot,
        "strike_gap": strike_gap,
        "upside": upside,
        "downside": downside,
        "lookback_days": days,
    }


def _strength_label(touches):
    if touches >= 4:
        return "MEGA"
    if touches >= 3:
        return "STRONG"
    if touches >= 2:
        return "MEDIUM"
    return "WEAK"


# ═══════════════════════════════════════════════════════════════
# ZONE DEEP-DIVE — OI today + total + hidden
# ═══════════════════════════════════════════════════════════════

def _zone_oi_analysis(engine, idx, zone_strike):
    """For zone strike + ±2 strikes, compute today OI change + total OI."""
    from engine import INDEX_CONFIG
    cfg = INDEX_CONFIG[idx]
    chain = engine.chains.get(idx, {})

    surrounding = []
    today_ce = today_pe = total_ce = total_pe = 0
    for offset in range(-2, 3):
        s = zone_strike + offset * cfg["strike_gap"]
        d = chain.get(s, {})
        if not d:
            continue
        ce_oi = d.get("ce_oi", 0)
        pe_oi = d.get("pe_oi", 0)

        ce_token = pe_token = None
        for tok, info in engine.token_to_info.items():
            if info["index"] == idx and info["strike"] == s:
                if info["opt_type"] == "CE":
                    ce_token = tok
                else:
                    pe_token = tok
        ce_init = engine.initial_oi.get(ce_token, ce_oi) if ce_token else ce_oi
        pe_init = engine.initial_oi.get(pe_token, pe_oi) if pe_token else pe_oi
        ce_chg = ce_oi - ce_init
        pe_chg = pe_oi - pe_init

        today_ce += ce_chg
        today_pe += pe_chg
        total_ce += ce_oi
        total_pe += pe_oi

        surrounding.append({
            "strike": s,
            "ce_oi": ce_oi, "pe_oi": pe_oi,
            "ce_change": ce_chg, "pe_change": pe_chg,
            "ce_ltp": d.get("ce_ltp", 0), "pe_ltp": d.get("pe_ltp", 0),
        })

    return {
        "today_ce_change": today_ce,
        "today_pe_change": today_pe,
        "total_ce_oi": total_ce,
        "total_pe_oi": total_pe,
        "surrounding": surrounding,
    }


def _zone_hidden_events(idx, zone_strike, strike_gap, hours=4):
    """Hidden events near this zone (within ±2 strikes, last 4 hours)."""
    cutoff = (ist_now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    low = zone_strike - 2 * strike_gap
    high = zone_strike + 2 * strike_gap
    conn = _conn()
    rows = conn.execute("""
        SELECT * FROM hidden_events
        WHERE idx=? AND strike BETWEEN ? AND ? AND time>=?
        ORDER BY time DESC LIMIT 10
    """, (idx, low, high, cutoff)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def analyze_zone(engine, idx, zone, side):
    """Deep dive on one zone — strength, OI, hidden activity, signal."""
    strike = zone["strike"]
    strike_gap = engine.__class__.__module__  # placeholder
    from engine import INDEX_CONFIG
    strike_gap = INDEX_CONFIG[idx]["strike_gap"]

    oi = _zone_oi_analysis(engine, idx, strike)
    hidden = _zone_hidden_events(idx, strike, strike_gap)

    today_ce = oi["today_ce_change"]
    today_pe = oi["today_pe_change"]

    # Zone strength interpretation
    zone_signal = "NEUTRAL"
    zone_reason = ""

    if side == "UPSIDE":
        # Resistance zone — CE writers should be defending if strong
        if today_ce > 200000:  # 2L+ added to CE
            zone_signal = "STRENGTHENING"
            zone_reason = f"CE writers adding {today_ce/100000:.1f}L → resistance defending"
        elif today_ce < -200000:
            zone_signal = "WEAKENING"
            zone_reason = f"CE covering {abs(today_ce)/100000:.1f}L → resistance breaking, BUY CE possible"
        elif today_pe > today_ce * 1.5 and today_pe > 0:
            zone_signal = "WEAKENING"
            zone_reason = f"PE writing dominant near resistance → upside likely"
        else:
            zone_signal = "HOLDING"
            zone_reason = "Mixed activity, watch for break"
    else:
        # Support zone — PE writers should be defending if strong
        if today_pe > 200000:
            zone_signal = "STRENGTHENING"
            zone_reason = f"PE writers adding {today_pe/100000:.1f}L → support defending"
        elif today_pe < -200000:
            zone_signal = "WEAKENING"
            zone_reason = f"PE covering {abs(today_pe)/100000:.1f}L → support breaking, BUY PE possible"
        elif today_ce > today_pe * 1.5 and today_ce > 0:
            zone_signal = "WEAKENING"
            zone_reason = f"CE writing dominant near support → downside likely"
        else:
            zone_signal = "HOLDING"
            zone_reason = "Mixed activity"

    return {
        "level": zone["level"],
        "strike": strike,
        "touches": zone["touches"],
        "strength": _strength_label(zone["touches"]),
        "last_seen": zone["last_seen"],
        "types": zone["types"],
        "side": side,
        "signal": zone_signal,
        "reason": zone_reason,
        "oi": {
            "today_ce_change": int(today_ce),
            "today_pe_change": int(today_pe),
            "total_ce_oi": int(oi["total_ce_oi"]),
            "total_pe_oi": int(oi["total_pe_oi"]),
        },
        "surrounding": oi["surrounding"],
        "hidden_events": hidden,
        "hidden_count": len(hidden),
    }


# ═══════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════

def compute_verdict(idx, spot, upside_zones, downside_zones):
    """Combine zone signals → BUY CE / BUY PE / WAIT."""
    bull_score = 0
    bear_score = 0
    reasons = []

    # Nearest upside zone (resistance)
    if upside_zones:
        up0 = upside_zones[0]
        if up0["signal"] == "WEAKENING":
            bull_score += 3
            reasons.append(f"⚡ Nearest resistance {up0['strike']} ({up0['strength']}) WEAKENING — {up0['reason']}")
        elif up0["signal"] == "STRENGTHENING":
            bear_score += 2
            reasons.append(f"🛑 Nearest resistance {up0['strike']} ({up0['strength']}) STRENGTHENING — {up0['reason']}")

    # Nearest downside zone (support)
    if downside_zones:
        dn0 = downside_zones[0]
        if dn0["signal"] == "WEAKENING":
            bear_score += 3
            reasons.append(f"⚡ Nearest support {dn0['strike']} ({dn0['strength']}) WEAKENING — {dn0['reason']}")
        elif dn0["signal"] == "STRENGTHENING":
            bull_score += 2
            reasons.append(f"🛡 Nearest support {dn0['strike']} ({dn0['strength']}) STRENGTHENING — {dn0['reason']}")

    # Hidden activity influence
    all_hidden = []
    for z in upside_zones[:2] + downside_zones[:2]:
        all_hidden.extend(z.get("hidden_events", []))
    if all_hidden:
        # Count direction skew
        bullish_events = sum(1 for h in all_hidden if h["event_type"] in ("MASS BUY ENTRY", "MASS COVER")
                             and h["side"] == "CE")
        bullish_events += sum(1 for h in all_hidden if h["event_type"] == "MASS WRITE" and h["side"] == "PE")
        bearish_events = sum(1 for h in all_hidden if h["event_type"] in ("MASS BUY ENTRY", "MASS COVER")
                             and h["side"] == "PE")
        bearish_events += sum(1 for h in all_hidden if h["event_type"] == "MASS WRITE" and h["side"] == "CE")
        if bullish_events > bearish_events + 1:
            bull_score += 2
            reasons.append(f"🐋 {bullish_events} bullish hidden events vs {bearish_events} bearish")
        elif bearish_events > bullish_events + 1:
            bear_score += 2
            reasons.append(f"🐋 {bearish_events} bearish hidden events vs {bullish_events} bullish")

    # Decide
    if bull_score >= bear_score + 2:
        signal = "BUY CE"
        confidence = min(85, 50 + (bull_score - bear_score) * 8)
    elif bear_score >= bull_score + 2:
        signal = "BUY PE"
        confidence = min(85, 50 + (bear_score - bull_score) * 8)
    else:
        signal = "WAIT"
        confidence = max(bull_score, bear_score) * 10

    # Targets/SL based on zones
    target = sl = None
    if signal == "BUY CE" and upside_zones:
        target = upside_zones[0]["strike"]
        if downside_zones:
            sl = downside_zones[0]["strike"]
    elif signal == "BUY PE" and downside_zones:
        target = downside_zones[0]["strike"]
        if upside_zones:
            sl = upside_zones[0]["strike"]

    return {
        "signal": signal,
        "confidence": confidence,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "reasons": reasons,
        "target": target,
        "sl": sl,
    }


# ═══════════════════════════════════════════════════════════════
# MAIN API — full analysis
# ═══════════════════════════════════════════════════════════════

def get_zones_analysis(engine, idx, days=10):
    """Top-level: zones + per-zone deep dive + verdict."""
    zones_raw = find_rejection_zones(engine, idx, days=days)
    if "error" in zones_raw:
        return zones_raw

    spot = zones_raw["spot"]
    upside_analyzed = [analyze_zone(engine, idx, z, "UPSIDE") for z in zones_raw["upside"]]
    downside_analyzed = [analyze_zone(engine, idx, z, "DOWNSIDE") for z in zones_raw["downside"]]
    verdict = compute_verdict(idx, spot, upside_analyzed, downside_analyzed)

    return {
        "idx": idx,
        "spot": spot,
        "lookback_days": days,
        "upside_zones": upside_analyzed,
        "downside_zones": downside_analyzed,
        "verdict": verdict,
        "timestamp": ist_now().isoformat(),
    }


def get_chart_data(engine, idx, days=5):
    """Time-series price + zone overlay data for chart."""
    init_db()
    cutoff = (ist_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _conn()
    rows = conn.execute(
        "SELECT date, time, ltp FROM price_samples WHERE idx=? AND date>=? ORDER BY date, time",
        (idx, cutoff)
    ).fetchall()
    conn.close()

    series = []
    for r in rows:
        try:
            dt = datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M")
            series.append({"time": int(dt.timestamp()), "value": r["ltp"]})
        except Exception:
            continue

    zones = get_zones_analysis(engine, idx)
    upside_levels = [{"price": z["level"], "strength": z["strength"], "strike": z["strike"]}
                     for z in zones.get("upside_zones", [])]
    downside_levels = [{"price": z["level"], "strength": z["strength"], "strike": z["strike"]}
                       for z in zones.get("downside_zones", [])]

    return {
        "idx": idx,
        "series": series,
        "upside_levels": upside_levels,
        "downside_levels": downside_levels,
        "spot": zones.get("spot", 0),
    }
