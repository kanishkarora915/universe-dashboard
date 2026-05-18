"""
Per-Day Training System (B1+B2+B3) — Real per-weekday learning.

User insight: "Monday market != Tuesday expiry != Wednesday consolidation"
Same engine logic for all days = mistake. Each day needs OWN training.

Components:
  B1: Daily Profile Capture — EOD snapshot per weekday
  B2: Past-Day Comparison — find similar past Monday/Tuesday/etc.
  B3: Per-Day Engine Weights — different weights for each weekday

Workflow:
  9:00 AM Monday → predict_today() loads past Mondays → predicts pattern
  All day → live updates to today's profile
  3:30 PM → capture_today_profile() snapshots full day
  4:00 PM → update_day_specific_weights() adjusts MONDAY-only weights
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "daily_training.db"

DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))

    # B1: Daily profiles (full day snapshot)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            day_of_week TEXT,
            day_index INTEGER,

            -- Setup
            open_price REAL,
            prev_close REAL,
            gap_pct REAL,
            open_type TEXT,
            vix_open REAL,
            vix_close REAL,
            fii_net REAL,

            -- Movement
            day_high REAL,
            day_low REAL,
            day_close REAL,
            day_range_pct REAL,
            morning_trend TEXT,
            afternoon_trend TEXT,

            -- Engine performance JSON {engine: {win, loss, accuracy}}
            engine_performance TEXT,

            -- Time window perf JSON {window: {trades, wins, losses, win_rate}}
            time_window_performance TEXT,

            -- OI patterns
            oi_shifts_count INTEGER,
            seller_morning TEXT,
            seller_afternoon TEXT,

            -- Trades summary
            total_trades INTEGER,
            wins INTEGER,
            losses INTEGER,
            net_pnl REAL,

            -- Notable events JSON
            unusual_events TEXT,

            -- Narrative
            summary TEXT,
            created_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dp_day ON daily_profiles(day_index)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dp_date ON daily_profiles(date)")

    # B3: Per-day engine weights
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day_specific_weights (
            day_index INTEGER PRIMARY KEY,
            day_name TEXT,
            weights_json TEXT,
            samples_used INTEGER,
            avg_accuracy REAL,
            last_updated TEXT
        )
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────
# B1: Daily Profile Capture
# ─────────────────────────────────────────────────────────────────

def capture_today_profile(engine):
    """Called at 3:30 PM IST. Builds full day profile."""
    init_db()
    now = ist_now()
    today = now.strftime("%Y-%m-%d")
    day_idx = now.weekday()

    profile = {
        "date": today,
        "day_of_week": DAY_NAMES[day_idx],
        "day_index": day_idx,
        "created_at": now.isoformat(),
    }

    # Get live data
    try:
        live = engine.get_live_data()
        nifty = live.get("nifty", {})
        profile["open_price"] = nifty.get("openPrice") or nifty.get("open") or 0
        profile["prev_close"] = nifty.get("prevClose", 0)
        profile["day_high"] = nifty.get("high") or nifty.get("dayHigh") or 0
        profile["day_low"] = nifty.get("low") or nifty.get("dayLow") or 0
        profile["day_close"] = nifty.get("ltp", 0)
        profile["vix_close"] = nifty.get("vix", 0)

        if profile["prev_close"] > 0 and profile["open_price"] > 0:
            profile["gap_pct"] = round((profile["open_price"] - profile["prev_close"]) / profile["prev_close"] * 100, 2)
        else:
            profile["gap_pct"] = 0

        profile["open_type"] = (
            "GAP_UP" if profile["gap_pct"] > 0.3 else
            "GAP_DOWN" if profile["gap_pct"] < -0.3 else
            "FLAT"
        )

        if profile["open_price"] > 0:
            profile["day_range_pct"] = round(
                (profile["day_high"] - profile["day_low"]) / profile["open_price"] * 100, 2
            )
        else:
            profile["day_range_pct"] = 0

        # Trend classification
        change_pct = (profile["day_close"] - profile["open_price"]) / max(profile["open_price"], 1) * 100
        if change_pct > 0.4:
            profile["afternoon_trend"] = "TREND_UP"
        elif change_pct < -0.4:
            profile["afternoon_trend"] = "TREND_DOWN"
        else:
            profile["afternoon_trend"] = "SIDEWAYS"

        # Morning trend (use 10:30 AM ish marker — approx by gap direction)
        if profile["gap_pct"] > 0.3 and profile["day_close"] > profile["open_price"]:
            profile["morning_trend"] = "TREND_UP"
        elif profile["gap_pct"] < -0.3 and profile["day_close"] < profile["open_price"]:
            profile["morning_trend"] = "TREND_DOWN"
        else:
            profile["morning_trend"] = "MIXED"
    except Exception as e:
        print(f"[DAILY-TRAIN] live data fetch error: {e}")

    # FII / VIX open
    try:
        fii = engine.get_fii_dii() if hasattr(engine, "get_fii_dii") else {}
        profile["fii_net"] = fii.get("fiiNet", 0) if fii else 0
        profile["vix_open"] = profile.get("vix_close", 0)  # approx, store close as open too
    except Exception:
        profile["fii_net"] = 0
        profile["vix_open"] = 0

    # Today's trades from main P&L + scalper
    profile.update(_compute_today_trade_metrics())

    # Time window performance
    profile["time_window_performance"] = json.dumps(_compute_time_window_perf())

    # Engine performance from backtest_log if available
    profile["engine_performance"] = json.dumps(_compute_engine_perf_today())

    # OI shifts count
    try:
        from oi_shift_detector import get_recent_shifts
        shifts = get_recent_shifts(idx="NIFTY", hours=8)
        profile["oi_shifts_count"] = len(shifts)
    except Exception:
        profile["oi_shifts_count"] = 0

    # Unusual events
    profile["unusual_events"] = json.dumps(_collect_unusual_events_today())

    # Seller behavior
    profile["seller_morning"] = "PE_DOMINANT" if profile.get("morning_trend") == "TREND_UP" else "CE_DOMINANT"
    profile["seller_afternoon"] = "PE_DOMINANT" if profile.get("afternoon_trend") == "TREND_UP" else "CE_DOMINANT"

    # Narrative summary
    profile["summary"] = _build_summary(profile)

    # Save (insert or replace today)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT OR REPLACE INTO daily_profiles (
            date, day_of_week, day_index,
            open_price, prev_close, gap_pct, open_type, vix_open, vix_close, fii_net,
            day_high, day_low, day_close, day_range_pct, morning_trend, afternoon_trend,
            engine_performance, time_window_performance, oi_shifts_count,
            seller_morning, seller_afternoon,
            total_trades, wins, losses, net_pnl,
            unusual_events, summary, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        profile["date"], profile["day_of_week"], profile["day_index"],
        profile.get("open_price", 0), profile.get("prev_close", 0),
        profile.get("gap_pct", 0), profile.get("open_type", ""),
        profile.get("vix_open", 0), profile.get("vix_close", 0),
        profile.get("fii_net", 0),
        profile.get("day_high", 0), profile.get("day_low", 0),
        profile.get("day_close", 0), profile.get("day_range_pct", 0),
        profile.get("morning_trend", ""), profile.get("afternoon_trend", ""),
        profile["engine_performance"], profile["time_window_performance"],
        profile["oi_shifts_count"],
        profile["seller_morning"], profile["seller_afternoon"],
        profile.get("total_trades", 0), profile.get("wins", 0),
        profile.get("losses", 0), profile.get("net_pnl", 0),
        profile["unusual_events"], profile["summary"],
        profile["created_at"],
    ))
    conn.commit()
    conn.close()

    # B3: Update day-specific weights
    update_day_specific_weights(day_idx)

    print(f"[DAILY-TRAIN] Profile captured for {profile['date']} ({profile['day_of_week']})")
    return profile


def _compute_today_trade_metrics():
    """Aggregate today's trades from both main + scalper."""
    today = ist_now().strftime("%Y-%m-%d")
    out = {"total_trades": 0, "wins": 0, "losses": 0, "net_pnl": 0}

    for db_name in ["trades.db", "scalper_trades.db"]:
        db_path = Path(f"/data/{db_name}") if Path(f"/data/{db_name}").exists() \
                  else Path(__file__).parent / db_name
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            table = "trades" if "trades.db" == db_name else "scalper_trades"
            rows = conn.execute(f"""
                SELECT status, pnl_rupees FROM {table}
                WHERE date(entry_time) = ? AND status != 'OPEN'
            """, (today,)).fetchall()
            conn.close()

            for r in rows:
                out["total_trades"] += 1
                if r["status"] in ("T1_HIT", "T2_HIT", "TRAIL_EXIT"):
                    out["wins"] += 1
                elif r["status"] in ("SL_HIT", "REVERSAL_EXIT"):
                    out["losses"] += 1
                out["net_pnl"] += (r["pnl_rupees"] or 0)
        except Exception as e:
            print(f"[DAILY-TRAIN] {db_name} error: {e}")

    out["net_pnl"] = round(out["net_pnl"], 2)
    return out


def _compute_time_window_perf():
    """Win rate per time window from today's trades."""
    today = ist_now().strftime("%Y-%m-%d")
    windows = {
        "MORNING_TREND": {"trades": 0, "wins": 0, "losses": 0},
        "MID_MORNING": {"trades": 0, "wins": 0, "losses": 0},
        "LUNCH_CHOP": {"trades": 0, "wins": 0, "losses": 0},
        "AFTERNOON": {"trades": 0, "wins": 0, "losses": 0},
        "POWER_HOUR": {"trades": 0, "wins": 0, "losses": 0},
    }

    for db_name in ["trades.db", "scalper_trades.db"]:
        db_path = Path(f"/data/{db_name}") if Path(f"/data/{db_name}").exists() \
                  else Path(__file__).parent / db_name
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            table = "trades" if "trades.db" == db_name else "scalper_trades"
            rows = conn.execute(f"""
                SELECT status, entry_time FROM {table}
                WHERE date(entry_time) = ? AND status != 'OPEN'
            """, (today,)).fetchall()
            conn.close()

            for r in rows:
                try:
                    et = datetime.fromisoformat(r["entry_time"])
                    hm = et.hour * 100 + et.minute
                    if hm < 1030: w = "MORNING_TREND"
                    elif hm < 1130: w = "MID_MORNING"
                    elif hm < 1230: w = "LUNCH_CHOP"
                    elif hm < 1400: w = "AFTERNOON"
                    elif hm < 1515: w = "POWER_HOUR"
                    else: continue
                    windows[w]["trades"] += 1
                    if r["status"] in ("T1_HIT", "T2_HIT", "TRAIL_EXIT"):
                        windows[w]["wins"] += 1
                    elif r["status"] in ("SL_HIT", "REVERSAL_EXIT"):
                        windows[w]["losses"] += 1
                except Exception:
                    pass
        except Exception:
            pass

    for w in windows:
        t = windows[w]["trades"]
        windows[w]["win_rate"] = round(windows[w]["wins"] / max(t, 1) * 100, 1) if t else 0
    return windows


def _compute_engine_perf_today():
    """Per-engine accuracy from today's backtest_log."""
    out = {}
    try:
        from ml_feedback import get_engine_accuracy
        acc = get_engine_accuracy(days=1)
        for e in acc.get("engines", []):
            out[e["name"]] = {
                "accuracy": e.get("accuracy", 0),
                "data_points": e.get("dataPoints", 0),
            }
    except Exception as e:
        print(f"[DAILY-TRAIN] engine perf error: {e}")
    return out


def _collect_unusual_events_today():
    """Aggregate unusual events from rejection_engine if available."""
    try:
        from rejection_engine import get_recent_hidden_events
        events = get_recent_hidden_events(hours=8)
        return [{
            "time": e.get("time"),
            "event_type": e.get("event_type"),
            "strike": e.get("strike"),
            "side": e.get("side"),
            "lots": e.get("lots_moved"),
            "description": e.get("description", "")[:200],
        } for e in events[:30]]
    except Exception:
        return []


def _build_summary(p):
    """Human narrative."""
    parts = []
    parts.append(f"{p.get('day_of_week')} {p.get('date')}: opened {p.get('open_type', '?')} ({p.get('gap_pct', 0):+.2f}%)")
    parts.append(f"Range {p.get('day_range_pct', 0):.2f}%")
    parts.append(f"VIX close {p.get('vix_close', 0):.1f}")
    if p.get("net_pnl") is not None:
        parts.append(f"P&L ₹{p['net_pnl']:+,.0f} ({p.get('wins', 0)}W/{p.get('losses', 0)}L)")
    return " · ".join(parts)


# ─────────────────────────────────────────────────────────────────
# B2: Past-Day Comparison
# ─────────────────────────────────────────────────────────────────

def find_similar_past_days(engine, days_back=4):
    """Find past days of same weekday similar to today's setup.

    Returns list of {date, similarity_pct, profile}.
    """
    init_db()
    now = ist_now()
    today_idx = now.weekday()

    # Get today's setup snapshot
    try:
        live = engine.get_live_data()
        nifty = live.get("nifty", {})
        today_setup = {
            "gap_pct": ((nifty.get("openPrice", 0) or 0) - (nifty.get("prevClose", 0) or 0))
                       / max(nifty.get("prevClose", 1) or 1, 1) * 100,
            "vix": nifty.get("vix", 0),
        }
        try:
            fii = engine.get_fii_dii()
            today_setup["fii_net"] = fii.get("fiiNet", 0) if fii else 0
        except Exception:
            today_setup["fii_net"] = 0
    except Exception as e:
        print(f"[DAILY-TRAIN] today setup error: {e}")
        return []

    # Load past N same-weekday profiles
    cutoff = (now - timedelta(weeks=days_back + 4)).strftime("%Y-%m-%d")
    today_str = now.strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM daily_profiles
        WHERE day_index = ? AND date < ? AND date >= ?
        ORDER BY date DESC LIMIT ?
    """, (today_idx, today_str, cutoff, days_back)).fetchall()
    conn.close()

    if not rows:
        return []

    # Score similarity
    matches = []
    for r in rows:
        d = dict(r)
        # Distance: gap, vix, fii (weighted)
        gap_diff = abs((d.get("gap_pct") or 0) - today_setup["gap_pct"])
        vix_diff = abs((d.get("vix_open") or 0) - today_setup["vix"]) if today_setup["vix"] else 0
        fii_diff = abs((d.get("fii_net") or 0) - today_setup["fii_net"]) / 1000 if today_setup["fii_net"] else 0

        # Normalize + invert for similarity
        gap_sim = max(0, 100 - gap_diff * 50)  # 1% diff = 50pt penalty
        vix_sim = max(0, 100 - vix_diff * 10)  # 1pt diff = 10pt penalty
        fii_sim = max(0, 100 - fii_diff * 2)   # 1000Cr diff = 2pt penalty

        sim_pct = (gap_sim * 0.5) + (vix_sim * 0.3) + (fii_sim * 0.2)

        matches.append({
            "date": d["date"],
            "day_of_week": d["day_of_week"],
            "similarity_pct": round(sim_pct, 1),
            "summary": d.get("summary", ""),
            "gap_pct": d.get("gap_pct"),
            "vix_open": d.get("vix_open"),
            "fii_net": d.get("fii_net"),
            "morning_trend": d.get("morning_trend"),
            "afternoon_trend": d.get("afternoon_trend"),
            "net_pnl": d.get("net_pnl"),
            "wins": d.get("wins"),
            "losses": d.get("losses"),
        })

    matches.sort(key=lambda x: x["similarity_pct"], reverse=True)
    return matches[:days_back]


# ─────────────────────────────────────────────────────────────────
# B3: Per-Day Engine Weights
# ─────────────────────────────────────────────────────────────────

def update_day_specific_weights(day_idx):
    """Compute weights specific to this weekday using past N same-weekday profiles."""
    init_db()
    cutoff = (ist_now() - timedelta(weeks=8)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT engine_performance FROM daily_profiles
        WHERE day_index = ? AND date >= ?
        ORDER BY date DESC LIMIT 8
    """, (day_idx, cutoff)).fetchall()
    conn.close()

    if not rows:
        return

    # Aggregate engine accuracy across all past same-weekday profiles
    engine_acc = {}
    for r in rows:
        try:
            data = json.loads(r["engine_performance"] or "{}")
            for eng, perf in data.items():
                if eng not in engine_acc:
                    engine_acc[eng] = {"acc_sum": 0, "samples": 0}
                acc = perf.get("accuracy", 0) or 0
                if acc > 0:
                    engine_acc[eng]["acc_sum"] += acc
                    engine_acc[eng]["samples"] += 1
        except Exception:
            pass

    if not engine_acc:
        return

    # Compute average accuracy per engine on this weekday
    avg_accuracy = {}
    for eng, d in engine_acc.items():
        if d["samples"] >= 2:
            avg_accuracy[eng] = round(d["acc_sum"] / d["samples"], 1)

    if not avg_accuracy:
        return

    # Build weights: higher accuracy → higher weight
    overall = sum(avg_accuracy.values()) / len(avg_accuracy)
    weights = {}
    for eng, acc in avg_accuracy.items():
        # Default weight 20, scaled by relative accuracy
        ratio = acc / max(overall, 1)
        weights[eng] = max(5, min(50, round(20 * ratio)))

    samples_count = max((d["samples"] for d in engine_acc.values()), default=0)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT OR REPLACE INTO day_specific_weights
        (day_index, day_name, weights_json, samples_used, avg_accuracy, last_updated)
        VALUES (?,?,?,?,?,?)
    """, (
        day_idx, DAY_NAMES[day_idx],
        json.dumps(weights),
        samples_count,
        round(overall, 1),
        ist_now().isoformat(),
    ))
    conn.commit()
    conn.close()
    print(f"[DAILY-TRAIN] Day-specific weights updated for {DAY_NAMES[day_idx]} (avg {overall:.1f}%)")


def get_day_weights(day_idx=None):
    """Get weights for a specific weekday."""
    init_db()
    if day_idx is None:
        day_idx = ist_now().weekday()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM day_specific_weights WHERE day_index = ?", (day_idx,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["weights"] = json.loads(d["weights_json"] or "{}")
    except Exception:
        d["weights"] = {}
    return d


# ─────────────────────────────────────────────────────────────────
# Public API helpers
# ─────────────────────────────────────────────────────────────────

def get_today_profile():
    init_db()
    today = ist_now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM daily_profiles WHERE date=?", (today,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    for k in ("engine_performance", "time_window_performance", "unusual_events"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except Exception:
            d[k] = None
    return d


def get_profile_for_day(day_name):
    """Get all past profiles for a weekday (e.g. all past Mondays)."""
    init_db()
    if day_name.upper() not in DAY_NAMES:
        return []
    day_idx = DAY_NAMES.index(day_name.upper())
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM daily_profiles WHERE day_index = ? ORDER BY date DESC LIMIT 12
    """, (day_idx,)).fetchall()
    conn.close()
    profiles = []
    for r in rows:
        d = dict(r)
        for k in ("engine_performance", "time_window_performance", "unusual_events"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else None
            except Exception:
                d[k] = None
        profiles.append(d)
    return profiles
