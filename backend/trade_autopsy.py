"""
Trade Autopsy — Snapshot ATM±6 strikes at every trade entry/exit.
Compares winning vs losing trades to find patterns.
Gap Prediction — Correlates EOD OI with next-day gap up/down.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "trade_autopsy.db"


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    # Trade snapshots — captured at entry and exit
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            snapshot_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            idx TEXT NOT NULL,
            spot REAL,
            atm INTEGER,
            strikes_json TEXT,
            pcr REAL,
            max_pain INTEGER,
            ce_wall INTEGER,
            pe_wall INTEGER,
            total_ce_oi INTEGER,
            total_pe_oi INTEGER,
            ce_volume_total INTEGER,
            pe_volume_total INTEGER,
            premium_ratio REAL,
            net_ce_oi_change INTEGER,
            net_pe_oi_change INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_tid ON trade_snapshots(trade_id)")

    # Gap tracking — EOD snapshot + next day gap
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gap_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            idx TEXT NOT NULL,
            eod_spot REAL,
            eod_pcr REAL,
            eod_max_pain INTEGER,
            eod_ce_wall INTEGER,
            eod_pe_wall INTEGER,
            eod_total_ce_oi INTEGER,
            eod_total_pe_oi INTEGER,
            eod_net_ce_change INTEGER,
            eod_net_pe_change INTEGER,
            eod_ce_writing INTEGER,
            eod_pe_writing INTEGER,
            eod_strikes_json TEXT,
            next_open REAL DEFAULT 0,
            gap_pts REAL DEFAULT 0,
            gap_pct REAL DEFAULT 0,
            gap_type TEXT DEFAULT 'PENDING'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gt_date ON gap_tracker(date)")

    # Learned patterns
    conn.execute("""
        CREATE TABLE IF NOT EXISTS learned_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT NOT NULL,
            description TEXT,
            condition_json TEXT,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            total_count INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            last_updated TEXT
        )
    """)

    conn.commit()
    conn.close()
    print(f"[AUTOPSY] DB initialized at {DB_PATH}")


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ══════════════════════════════════════════════════════════════════════════
# TRADE SNAPSHOTS — Capture at entry and exit
# ══════════════════════════════════════════════════════════════════════════

def capture_trade_snapshot(engine, trade_id, idx, snapshot_type="ENTRY"):
    """Capture ATM±6 strikes snapshot at trade entry or exit."""
    from engine import INDEX_CONFIG, compute_max_pain, find_big_walls

    init_db()
    cfg = INDEX_CONFIG[idx]
    chain = engine.chains.get(idx, {})
    spot_token = engine.spot_tokens.get(idx)
    spot = engine.prices.get(spot_token, {}).get("ltp", 0)
    if spot <= 0 or not chain:
        print(f"[AUTOPSY] {snapshot_type} SKIP trade #{trade_id} {idx} — spot={spot}, chain_size={len(chain)}")
        return

    atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"]

    # ATM ±6 strikes
    strikes_data = []
    for offset in range(-6, 7):
        s = atm + offset * cfg["strike_gap"]
        d = chain.get(s, {})
        ce_oi = d.get("ce_oi", 0)
        pe_oi = d.get("pe_oi", 0)

        # OI change from initial
        ce_change = 0
        pe_change = 0
        for tok, info in engine.token_to_info.items():
            if info["index"] == idx and info["strike"] == s:
                if info["opt_type"] == "CE":
                    ce_change = ce_oi - engine.initial_oi.get(tok, ce_oi)
                elif info["opt_type"] == "PE":
                    pe_change = pe_oi - engine.initial_oi.get(tok, pe_oi)

        strikes_data.append({
            "strike": s, "isATM": s == atm,
            "ceOI": ce_oi, "peOI": pe_oi,
            "ceOIChange": ce_change, "peOIChange": pe_change,
            "ceLTP": d.get("ce_ltp", 0), "peLTP": d.get("pe_ltp", 0),
            "ceVol": d.get("ce_volume", 0), "peVol": d.get("pe_volume", 0),
        })

    total_ce = sum(d.get("ce_oi", 0) for d in chain.values())
    total_pe = sum(d.get("pe_oi", 0) for d in chain.values())
    pcr = round(total_pe / max(total_ce, 1), 2)
    max_pain = compute_max_pain(chain, spot)
    ce_wall, pe_wall = find_big_walls(chain)
    ce_vol = sum(d.get("ce_volume", 0) for d in chain.values())
    pe_vol = sum(d.get("pe_volume", 0) for d in chain.values())
    prem_ratio = round(
        chain.get(atm, {}).get("ce_ltp", 1) / max(chain.get(atm, {}).get("pe_ltp", 1), 0.01), 2
    )
    net_ce_chg = sum(s["ceOIChange"] for s in strikes_data)
    net_pe_chg = sum(s["peOIChange"] for s in strikes_data)

    conn = _conn()
    conn.execute("""
        INSERT INTO trade_snapshots (trade_id, snapshot_type, timestamp, idx, spot, atm,
            strikes_json, pcr, max_pain, ce_wall, pe_wall,
            total_ce_oi, total_pe_oi, ce_volume_total, pe_volume_total,
            premium_ratio, net_ce_oi_change, net_pe_oi_change)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (trade_id, snapshot_type, ist_now().isoformat(), idx, spot, atm,
          json.dumps(strikes_data), pcr, max_pain, ce_wall, pe_wall,
          total_ce, total_pe, ce_vol, pe_vol, prem_ratio, net_ce_chg, net_pe_chg))
    conn.commit()
    conn.close()
    print(f"[AUTOPSY] {snapshot_type} snapshot for trade #{trade_id} — {idx} ATM={atm}")


# ══════════════════════════════════════════════════════════════════════════
# TRADE ANALYSIS — Compare winning vs losing patterns
# ══════════════════════════════════════════════════════════════════════════

def get_trade_autopsy(trade_id=None):
    """Get autopsy data for specific trade or all trades with patterns."""
    init_db()
    conn = _conn()

    if trade_id:
        snaps = conn.execute(
            "SELECT * FROM trade_snapshots WHERE trade_id=? ORDER BY timestamp", (trade_id,)
        ).fetchall()
        conn.close()
        if not snaps:
            return {"error": "No snapshots for this trade"}
        return {
            "tradeId": trade_id,
            "snapshots": [_format_snapshot(dict(s)) for s in snaps],
        }

    # All snapshots with trade results
    snaps = conn.execute("SELECT * FROM trade_snapshots ORDER BY timestamp DESC LIMIT 200").fetchall()
    conn.close()
    return {"snapshots": [_format_snapshot(dict(s)) for s in snaps]}


def _format_snapshot(s):
    s["strikes"] = json.loads(s.get("strikes_json", "[]"))
    del s["strikes_json"]
    return s


def get_win_loss_patterns():
    """Compare entry snapshots of winning vs losing trades."""
    init_db()

    # Get trade results from trades.db
    trades_db = _data_dir / "trades.db"
    if not trades_db.exists():
        return {"error": "No trades database"}

    tconn = sqlite3.connect(str(trades_db))
    tconn.row_factory = sqlite3.Row
    trades = tconn.execute(
        "SELECT id, idx, action, pnl_rupees, status FROM trades WHERE status != 'OPEN'"
    ).fetchall()
    tconn.close()

    if not trades:
        return {"error": "No closed trades", "winPatterns": [], "lossPatterns": []}

    win_ids = [t["id"] for t in trades if (t["pnl_rupees"] or 0) > 0]
    loss_ids = [t["id"] for t in trades if (t["pnl_rupees"] or 0) <= 0]

    conn = _conn()

    def get_entry_patterns(trade_ids):
        if not trade_ids:
            return []
        placeholders = ",".join(str(i) for i in trade_ids)
        entries = conn.execute(
            f"SELECT * FROM trade_snapshots WHERE trade_id IN ({placeholders}) AND snapshot_type='ENTRY'"
        ).fetchall()
        return [dict(e) for e in entries]

    win_entries = get_entry_patterns(win_ids)
    loss_entries = get_entry_patterns(loss_ids)
    conn.close()

    # Analyze patterns
    win_patterns = _analyze_patterns(win_entries, "WIN")
    loss_patterns = _analyze_patterns(loss_entries, "LOSS")

    # Find key differences
    insights = _find_insights(win_entries, loss_entries)

    return {
        "totalWins": len(win_ids),
        "totalLosses": len(loss_ids),
        "winPatterns": win_patterns,
        "lossPatterns": loss_patterns,
        "insights": insights,
        "winEntries": len(win_entries),
        "lossEntries": len(loss_entries),
    }


def _analyze_patterns(entries, label):
    """Extract average OI/volume/premium patterns from entries."""
    if not entries:
        return {"label": label, "count": 0}

    avg_pcr = sum(e.get("pcr", 1) for e in entries) / len(entries)
    avg_prem_ratio = sum(e.get("premium_ratio", 1) for e in entries) / len(entries)
    avg_ce_vol = sum(e.get("ce_volume_total", 0) for e in entries) / len(entries)
    avg_pe_vol = sum(e.get("pe_volume_total", 0) for e in entries) / len(entries)
    avg_ce_chg = sum(e.get("net_ce_oi_change", 0) for e in entries) / len(entries)
    avg_pe_chg = sum(e.get("net_pe_oi_change", 0) for e in entries) / len(entries)

    ce_decreasing = sum(1 for e in entries if e.get("net_ce_oi_change", 0) < 0)
    pe_increasing = sum(1 for e in entries if e.get("net_pe_oi_change", 0) > 0)

    return {
        "label": label,
        "count": len(entries),
        "avgPCR": round(avg_pcr, 2),
        "avgPremiumRatio": round(avg_prem_ratio, 2),
        "avgCEVolume": round(avg_ce_vol),
        "avgPEVolume": round(avg_pe_vol),
        "avgCEOIChange": round(avg_ce_chg),
        "avgPEOIChange": round(avg_pe_chg),
        "volRatio": round(avg_ce_vol / max(avg_pe_vol, 1), 2),
        "ceDecreasingPct": round(ce_decreasing / max(len(entries), 1) * 100),
        "peIncreasingPct": round(pe_increasing / max(len(entries), 1) * 100),
    }


def _find_insights(win_entries, loss_entries):
    """Find what's DIFFERENT between winning and losing trades."""
    insights = []
    if not win_entries or not loss_entries:
        return ["Need more trade data (both wins and losses) for insights"]

    # Minimum sample size for statistical reliability
    MIN_SAMPLE = 5
    if len(win_entries) < MIN_SAMPLE or len(loss_entries) < MIN_SAMPLE:
        return [f"Sample too small — {len(win_entries)} wins, {len(loss_entries)} losses. Need {MIN_SAMPLE}+ of each for reliable insights."]

    wp = _analyze_patterns(win_entries, "WIN")
    lp = _analyze_patterns(loss_entries, "LOSS")

    # PCR comparison (meaningful diff = 0.2+)
    if wp["avgPCR"] > lp["avgPCR"] + 0.2:
        insights.append(f"Winning trades had higher PCR ({wp['avgPCR']}) vs losing ({lp['avgPCR']}). Higher PCR = more support = better CE trades.")
    elif lp["avgPCR"] > wp["avgPCR"] + 0.2:
        insights.append(f"Losing trades had higher PCR ({lp['avgPCR']}). System may be entering CE when PE support is too strong (contrarian).")

    # Volume ratio (meaningful diff = 0.5+)
    if wp["volRatio"] > lp["volRatio"] + 0.5:
        insights.append(f"Winning trades had CE/PE volume ratio {wp['volRatio']}x vs losing {lp['volRatio']}x. Higher CE volume = stronger conviction.")
    elif lp["volRatio"] > wp["volRatio"] + 0.5:
        insights.append(f"Losing trades had higher CE/PE volume {lp['volRatio']}x vs wins {wp['volRatio']}x. High CE volume alone not predictive — check direction.")

    # CE OI direction (meaningful diff = 20%+)
    if wp["ceDecreasingPct"] > lp["ceDecreasingPct"] + 20:
        insights.append(f"In {wp['ceDecreasingPct']}% of wins, CE OI was DECREASING at entry (resistance weakening). Only {lp['ceDecreasingPct']}% in losses. Enter CE when CE OI is unwinding.")

    # PE OI direction (meaningful diff = 20%+)
    if wp["peIncreasingPct"] > lp["peIncreasingPct"] + 20:
        insights.append(f"In {wp['peIncreasingPct']}% of wins, PE OI was INCREASING at entry (support building). Only {lp['peIncreasingPct']}% in losses. Enter CE when PE OI is building.")

    # Premium ratio (meaningful diff = 0.2+)
    if wp["avgPremiumRatio"] > lp["avgPremiumRatio"] + 0.2:
        insights.append(f"Winning trades had premium ratio {wp['avgPremiumRatio']} vs losing {lp['avgPremiumRatio']}. Higher CE premium relative to PE = market already pricing upside.")

    if not insights:
        insights.append(f"No strong divergence found across {len(win_entries)} wins and {len(loss_entries)} losses. Strategy patterns may be consistent — check individual trades for timing/entry issues.")

    return insights


# ══════════════════════════════════════════════════════════════════════════
# GAP PREDICTION — EOD OI → Next day gap correlation
# ══════════════════════════════════════════════════════════════════════════

def save_eod_snapshot(engine, index):
    """Save end-of-day OI data for gap prediction. Called at 3:25 PM."""
    from engine import INDEX_CONFIG, compute_max_pain, find_big_walls
    init_db()

    cfg = INDEX_CONFIG[index]
    chain = engine.chains.get(index, {})
    spot_token = engine.spot_tokens.get(index)
    spot = engine.prices.get(spot_token, {}).get("ltp", 0)
    # Fallback: if LTP is 0 (market closed after 3:30), use last known close
    if spot <= 0:
        spot = getattr(engine, "prev_close", {}).get(index, 0) or engine.market_open_price.get(index, 0)
    if spot <= 0 or not chain:
        print(f"[GAP] EOD SKIP {index} — spot={spot}, chain_size={len(chain)}")
        return

    atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"]
    max_pain = compute_max_pain(chain, spot)
    ce_wall, pe_wall = find_big_walls(chain)
    total_ce = sum(d.get("ce_oi", 0) for d in chain.values())
    total_pe = sum(d.get("pe_oi", 0) for d in chain.values())
    pcr = round(total_pe / max(total_ce, 1), 2)

    # Net OI changes from open
    net_ce_chg = 0
    net_pe_chg = 0
    ce_writing = 0
    pe_writing = 0
    strikes_data = []

    for s in range(atm - 6 * cfg["strike_gap"], atm + 7 * cfg["strike_gap"], cfg["strike_gap"]):
        d = chain.get(s, {})
        ce_oi = d.get("ce_oi", 0)
        pe_oi = d.get("pe_oi", 0)
        ce_chg = 0
        pe_chg = 0
        for tok, info in engine.token_to_info.items():
            if info["index"] == index and info["strike"] == s:
                if info["opt_type"] == "CE":
                    ce_chg = ce_oi - engine.initial_oi.get(tok, ce_oi)
                elif info["opt_type"] == "PE":
                    pe_chg = pe_oi - engine.initial_oi.get(tok, pe_oi)
        net_ce_chg += ce_chg
        net_pe_chg += pe_chg
        if ce_chg > 0:
            ce_writing += ce_chg
        if pe_chg > 0:
            pe_writing += pe_chg
        strikes_data.append({"strike": s, "ceOI": ce_oi, "peOI": pe_oi, "ceChg": ce_chg, "peChg": pe_chg})

    today = ist_now().strftime("%Y-%m-%d")
    conn = _conn()
    conn.execute("DELETE FROM gap_tracker WHERE date=? AND idx=?", (today, index))
    conn.execute("""
        INSERT INTO gap_tracker (date, idx, eod_spot, eod_pcr, eod_max_pain,
            eod_ce_wall, eod_pe_wall, eod_total_ce_oi, eod_total_pe_oi,
            eod_net_ce_change, eod_net_pe_change, eod_ce_writing, eod_pe_writing,
            eod_strikes_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (today, index, spot, pcr, max_pain, ce_wall, pe_wall,
          total_ce, total_pe, net_ce_chg, net_pe_chg, ce_writing, pe_writing,
          json.dumps(strikes_data)))
    conn.commit()
    conn.close()
    print(f"[GAP] EOD snapshot saved for {index} — PCR={pcr}, CE writing={ce_writing}, PE writing={pe_writing}")


def check_gap_outcome(engine, index):
    """Called at 9:20 AM — check if previous day's prediction was correct."""
    init_db()
    spot_token = engine.spot_tokens.get(index)
    open_price = engine.market_open_price.get(index, 0)
    if open_price <= 0:
        return

    conn = _conn()
    # Find most recent EOD snapshot that doesn't have outcome yet
    row = conn.execute(
        "SELECT * FROM gap_tracker WHERE idx=? AND gap_type='PENDING' ORDER BY date DESC LIMIT 1",
        (index,)
    ).fetchone()

    if not row:
        conn.close()
        return

    eod_spot = row["eod_spot"]
    gap_pts = round(open_price - eod_spot, 1)
    gap_pct = round(gap_pts / max(eod_spot, 1) * 100, 2)

    if gap_pct > 0.3:
        gap_type = "GAP_UP"
    elif gap_pct < -0.3:
        gap_type = "GAP_DOWN"
    else:
        gap_type = "FLAT"

    conn.execute(
        "UPDATE gap_tracker SET next_open=?, gap_pts=?, gap_pct=?, gap_type=? WHERE id=?",
        (open_price, gap_pts, gap_pct, gap_type, row["id"])
    )
    conn.commit()
    conn.close()
    print(f"[GAP] {index} gap outcome: {gap_type} {gap_pct:+.2f}% ({gap_pts:+.0f} pts)")


def get_gap_prediction(engine, index):
    """Predict next day gap based on historical EOD patterns."""
    init_db()
    conn = _conn()

    # Get all completed gap records
    rows = conn.execute(
        "SELECT * FROM gap_tracker WHERE idx=? AND gap_type != 'PENDING' ORDER BY date DESC",
        (index,)
    ).fetchall()

    # Get today's EOD snapshot (if available)
    today = ist_now().strftime("%Y-%m-%d")
    today_eod = conn.execute(
        "SELECT * FROM gap_tracker WHERE idx=? AND date=?", (index, today)
    ).fetchone()
    conn.close()

    if not rows:
        return {
            "prediction": "NEED DATA",
            "confidence": 0,
            "message": "Need at least 10 days of gap data for predictions",
            "dataPoints": 0,
            "todayEOD": dict(today_eod) if today_eod else None,
        }

    completed = [dict(r) for r in rows]
    total = len(completed)

    # Analyze patterns
    gap_ups = [r for r in completed if r["gap_type"] == "GAP_UP"]
    gap_downs = [r for r in completed if r["gap_type"] == "GAP_DOWN"]
    flats = [r for r in completed if r["gap_type"] == "FLAT"]

    # Find correlations
    correlations = []

    # PCR correlation
    high_pcr_days = [r for r in completed if r["eod_pcr"] > 1.2]
    if len(high_pcr_days) >= 3:
        gap_up_pct = len([r for r in high_pcr_days if r["gap_type"] == "GAP_UP"]) / len(high_pcr_days) * 100
        correlations.append({
            "condition": "PCR > 1.2 at EOD",
            "gapUpPct": round(gap_up_pct),
            "count": len(high_pcr_days),
        })

    low_pcr_days = [r for r in completed if r["eod_pcr"] < 0.85]
    if len(low_pcr_days) >= 3:
        gap_down_pct = len([r for r in low_pcr_days if r["gap_type"] == "GAP_DOWN"]) / len(low_pcr_days) * 100
        correlations.append({
            "condition": "PCR < 0.85 at EOD",
            "gapDownPct": round(gap_down_pct),
            "count": len(low_pcr_days),
        })

    # PE writing heavy
    heavy_pe = [r for r in completed if r["eod_pe_writing"] > r["eod_ce_writing"] * 1.5]
    if len(heavy_pe) >= 3:
        gap_up_pct = len([r for r in heavy_pe if r["gap_type"] == "GAP_UP"]) / len(heavy_pe) * 100
        correlations.append({
            "condition": "PE writing > 1.5x CE writing at EOD",
            "gapUpPct": round(gap_up_pct),
            "count": len(heavy_pe),
        })

    # CE writing heavy
    heavy_ce = [r for r in completed if r["eod_ce_writing"] > r["eod_pe_writing"] * 1.5]
    if len(heavy_ce) >= 3:
        gap_down_pct = len([r for r in heavy_ce if r["gap_type"] == "GAP_DOWN"]) / len(heavy_ce) * 100
        correlations.append({
            "condition": "CE writing > 1.5x PE writing at EOD",
            "gapDownPct": round(gap_down_pct),
            "count": len(heavy_ce),
        })

    # Today's prediction (if EOD data available)
    prediction = "NEED MORE DATA"
    confidence = 0
    reasons = []

    if today_eod:
        te = dict(today_eod)
        bull_score = 0
        bear_score = 0

        if te["eod_pcr"] > 1.2:
            bull_score += 25
            reasons.append(f"PCR {te['eod_pcr']} > 1.2 = bullish")
        elif te["eod_pcr"] < 0.85:
            bear_score += 25
            reasons.append(f"PCR {te['eod_pcr']} < 0.85 = bearish")

        if te["eod_pe_writing"] > te["eod_ce_writing"] * 1.3:
            bull_score += 20
            reasons.append(f"PE writing dominant ({te['eod_pe_writing']//100000}L vs CE {te['eod_ce_writing']//100000}L)")
        elif te["eod_ce_writing"] > te["eod_pe_writing"] * 1.3:
            bear_score += 20
            reasons.append(f"CE writing dominant ({te['eod_ce_writing']//100000}L vs PE {te['eod_pe_writing']//100000}L)")

        if te["eod_net_pe_change"] > 300000:
            bull_score += 15
            reasons.append(f"Net PE OI added {te['eod_net_pe_change']//100000}L = support building")
        if te["eod_net_ce_change"] > 300000:
            bear_score += 15
            reasons.append(f"Net CE OI added {te['eod_net_ce_change']//100000}L = resistance building")

        if te["eod_spot"] > te["eod_max_pain"]:
            bull_score += 10
            reasons.append(f"Closed above max pain {te['eod_max_pain']}")
        elif te["eod_spot"] < te["eod_max_pain"]:
            bear_score += 10
            reasons.append(f"Closed below max pain {te['eod_max_pain']}")

        total_score = bull_score + bear_score
        if total_score > 0:
            if bull_score > bear_score:
                prediction = "GAP UP"
                confidence = min(85, round(bull_score / max(total_score, 1) * 100))
            elif bear_score > bull_score:
                prediction = "GAP DOWN"
                confidence = min(85, round(bear_score / max(total_score, 1) * 100))
            else:
                prediction = "FLAT"
                confidence = 40

    return {
        "prediction": prediction,
        "confidence": confidence,
        "reasons": reasons,
        "dataPoints": total,
        "history": {
            "gapUps": len(gap_ups),
            "gapDowns": len(gap_downs),
            "flats": len(flats),
            "avgGapUp": round(sum(r["gap_pct"] for r in gap_ups) / max(len(gap_ups), 1), 2) if gap_ups else 0,
            "avgGapDown": round(sum(r["gap_pct"] for r in gap_downs) / max(len(gap_downs), 1), 2) if gap_downs else 0,
        },
        "correlations": correlations,
        "recentGaps": [{"date": r["date"], "gapPct": r["gap_pct"], "gapType": r["gap_type"],
                        "eodPCR": r["eod_pcr"]} for r in completed[:10]],
        "todayEOD": dict(today_eod) if today_eod else None,
    }


def get_gap_history(index, limit=30):
    """Get gap history for display."""
    init_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM gap_tracker WHERE idx=? ORDER BY date DESC LIMIT ?",
        (index, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
