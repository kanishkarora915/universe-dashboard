"""
Smart Autopsy Mind — Pattern-Learning + Predictive Memory

The "brain" of autopsy that:
  1. Fingerprints each market day as a vector (gap, PCR, VIX, OI, etc.)
  2. Stores daily pattern memory (survives across deploys via /data disk)
  3. Finds similar past days (cosine similarity search)
  4. Predicts today's likely outcome based on past similar days
  5. Narrates WHY market moved (OI changes, wall shifts)
  6. Alerts proactively when similar-to-past pattern forms

"Aaj ka din Apr 17 ke jaisa lag raha — us din +130 pts NIFTY up gaya tha."
"""

import sqlite3
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
MIND_DB = _data_dir / "autopsy_mind.db"


def ist_now():
    return datetime.now(IST)


def init_mind_db():
    """Daily pattern memory — survives deploys via persistent disk.

    BUG FIX (2026-05-05): Old schema had `date TEXT NOT NULL UNIQUE`
    WITHOUT idx in unique constraint. Result: BANKNIFTY EOD recording
    each day OVERWROTE the NIFTY recording (same date). User saw 6
    BANKNIFTY days but 0 NIFTY days. Migration below converts to
    composite (date, idx) uniqueness.
    """
    conn = sqlite3.connect(str(MIND_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS day_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            idx TEXT NOT NULL,

            -- Opening context (9:15-9:30)
            prev_close REAL,
            open_price REAL,
            gap_pct REAL,
            open_type TEXT,

            -- Market state at 10:00 AM
            pcr_10am REAL,
            vix_10am REAL,
            ivr_10am REAL,
            max_pain_10am INTEGER,
            big_ce_wall INTEGER,
            big_pe_wall INTEGER,

            -- Day outcome
            day_high REAL,
            day_low REAL,
            day_close REAL,
            day_range_pct REAL,
            day_change_pct REAL,
            direction TEXT,  -- TREND_UP, TREND_DOWN, SIDEWAYS, V_REVERSAL

            -- OI character
            ce_oi_change INTEGER,
            pe_oi_change INTEGER,
            pcr_shift REAL,
            max_pain_shift INTEGER,

            -- FII / global context
            fii_net REAL,
            global_bias TEXT,

            -- Best opportunities (which strike won most)
            best_strike_ce INTEGER,
            best_strike_ce_pnl_pct REAL,
            best_strike_pe INTEGER,
            best_strike_pe_pnl_pct REAL,

            -- Summary narrative
            what_happened TEXT,
            why_happened TEXT,

            created_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dp_date ON day_patterns(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dp_idx ON day_patterns(idx)")

    # Migration: drop old single-column UNIQUE constraint on `date` if present.
    # SQLite UNIQUE constraints are enforced via auto-created indexes — we
    # detect the auto index name `sqlite_autoindex_day_patterns_*` and rebuild
    # the table without it. Composite UNIQUE(date, idx) replaces it.
    try:
        # Check if old auto-unique index exists on date column alone
        rows = conn.execute("""
            SELECT name FROM sqlite_master
             WHERE type='index' AND tbl_name='day_patterns'
               AND name LIKE 'sqlite_autoindex_day_patterns_%'
        """).fetchall()
        if rows:
            # Old schema present — migrate via table copy
            print(f"[MIND] migrating day_patterns schema (drop UNIQUE date) — {len(rows)} stale auto-index")
            conn.execute("ALTER TABLE day_patterns RENAME TO day_patterns_old")
            conn.execute("""
                CREATE TABLE day_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    idx TEXT NOT NULL,
                    prev_close REAL, open_price REAL, gap_pct REAL, open_type TEXT,
                    pcr_10am REAL, vix_10am REAL, ivr_10am REAL, max_pain_10am INTEGER,
                    big_ce_wall INTEGER, big_pe_wall INTEGER,
                    day_high REAL, day_low REAL, day_close REAL,
                    day_range_pct REAL, day_change_pct REAL, direction TEXT,
                    ce_oi_change INTEGER, pe_oi_change INTEGER,
                    pcr_shift REAL, max_pain_shift INTEGER,
                    fii_net REAL, global_bias TEXT,
                    best_strike_ce INTEGER, best_strike_ce_pnl_pct REAL,
                    best_strike_pe INTEGER, best_strike_pe_pnl_pct REAL,
                    what_happened TEXT, why_happened TEXT,
                    created_at TEXT,
                    UNIQUE(date, idx)
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO day_patterns
                SELECT * FROM day_patterns_old
            """)
            conn.execute("DROP TABLE day_patterns_old")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dp_date ON day_patterns(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dp_idx ON day_patterns(idx)")
            print("[MIND] migration complete — composite UNIQUE(date, idx) installed")
        else:
            # Already migrated or fresh table — ensure composite unique exists
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_dp_date_idx
                ON day_patterns(date, idx)
            """)
    except Exception as _e:
        print(f"[MIND] schema migration warning: {_e}")

    # Pattern similarity cache (computed on demand, cached for session)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pattern_matches (
            today_date TEXT NOT NULL,
            match_date TEXT NOT NULL,
            idx TEXT NOT NULL,
            similarity_score REAL,
            match_reasons TEXT,
            created_at TEXT,
            PRIMARY KEY(today_date, match_date, idx)
        )
    """)
    conn.commit()
    conn.close()


def _conn():
    init_mind_db()
    conn = sqlite3.connect(str(MIND_DB))
    conn.row_factory = sqlite3.Row
    return conn


# ══════════════════════════════════════════════════════════════════
# PATTERN FINGERPRINTING
# ══════════════════════════════════════════════════════════════════

def fingerprint_today(engine, idx):
    """Extract today's pattern vector for similarity matching."""
    try:
        live = engine.get_live_data()
        d = live.get(idx.lower(), {})
        return {
            "gap_pct": d.get("fromOpenPct", 0),
            "pcr": d.get("pcr", 1.0),
            "vix": d.get("vix", 18),
            "ivr": d.get("ivr", 50),
            "max_pain": d.get("maxPain", 0),
            "day_change_pct": d.get("changePct", 0),
            "day_range_pct": d.get("dayRange", 0) / max(d.get("ltp", 1), 1) * 100,
        }
    except Exception as e:
        print(f"[MIND] fingerprint error: {e}")
        return None


def _pattern_distance(p1, p2):
    """Weighted Euclidean distance between two pattern vectors.
    Lower = more similar. Returns 0-100 distance score."""
    if not p1 or not p2:
        return 100

    # Weighted features (key drivers of market behavior)
    weights = {
        "gap_pct": 2.0,       # Gap direction most predictive
        "pcr": 1.5,           # PCR regime
        "vix": 1.0,           # Vol regime
        "ivr": 0.8,           # Premium context
        "day_change_pct": 1.5, # Intraday direction
        "day_range_pct": 0.5,  # Vol realized
    }

    squared_diff = 0
    total_weight = 0
    for key, w in weights.items():
        v1 = p1.get(key, 0) or 0
        v2 = p2.get(key, 0) or 0
        # Normalize: PCR around 1, gap around 0, VIX around 15-25
        normalizer = {
            "gap_pct": 1.0, "pcr": 0.3, "vix": 5.0,
            "ivr": 20.0, "day_change_pct": 1.0, "day_range_pct": 0.5
        }.get(key, 1.0)
        diff = (v1 - v2) / normalizer
        squared_diff += w * (diff ** 2)
        total_weight += w

    return math.sqrt(squared_diff / total_weight)


def similarity_score(distance):
    """Convert distance to 0-100 similarity percentage."""
    # Distance 0 → 100% sim, distance 3+ → 0% sim
    return max(0, min(100, 100 * math.exp(-distance)))


# ══════════════════════════════════════════════════════════════════
# DAILY PATTERN RECORDING (called at EOD)
# ══════════════════════════════════════════════════════════════════

def record_day_pattern(engine, idx):
    """Called at 3:25 PM daily. Records today's full day pattern."""
    init_mind_db()
    try:
        now = ist_now()
        date_str = now.strftime("%Y-%m-%d")
        live = engine.get_live_data()
        d = live.get(idx.lower(), {})

        # Basic market state
        open_px = d.get("openPrice", 0)
        prev_close = d.get("prevClose", 0)
        ltp = d.get("ltp", 0)
        high = d.get("high", 0)
        low = d.get("low", 0)

        gap_pct = ((open_px - prev_close) / prev_close * 100) if prev_close else 0
        day_change_pct = d.get("changePct", 0)
        day_range_pct = ((high - low) / max(open_px, 1)) * 100

        # Classify direction
        if day_change_pct > 0.5 and abs(day_change_pct) > abs(gap_pct) * 2:
            direction = "TREND_UP"
        elif day_change_pct < -0.5 and abs(day_change_pct) > abs(gap_pct) * 2:
            direction = "TREND_DOWN"
        elif abs(day_change_pct) < 0.3:
            direction = "SIDEWAYS"
        else:
            direction = "V_REVERSAL" if (gap_pct > 0) != (day_change_pct > 0) else "MILD_MOVE"

        # Get best-performing shadow strikes
        best_ce = {"strike": 0, "pnl_pct": 0}
        best_pe = {"strike": 0, "pnl_pct": 0}
        try:
            from shadow_autopsy import _conn as shadow_conn
            sc = shadow_conn()
            best_ce_row = sc.execute("""
                SELECT strike, pnl_pct FROM shadow_trades
                WHERE date=? AND idx=? AND side='CE' AND status='CLOSED'
                ORDER BY pnl_pct DESC LIMIT 1
            """, (date_str, idx)).fetchone()
            if best_ce_row:
                best_ce = {"strike": best_ce_row[0], "pnl_pct": best_ce_row[1] or 0}

            best_pe_row = sc.execute("""
                SELECT strike, pnl_pct FROM shadow_trades
                WHERE date=? AND idx=? AND side='PE' AND status='CLOSED'
                ORDER BY pnl_pct DESC LIMIT 1
            """, (date_str, idx)).fetchone()
            if best_pe_row:
                best_pe = {"strike": best_pe_row[0], "pnl_pct": best_pe_row[1] or 0}
            sc.close()
        except Exception as e:
            print(f"[MIND] best strike fetch error: {e}")

        # FII / Global context
        fii_net = 0
        global_bias = "NEUTRAL"
        try:
            fii = engine.get_fii_dii()
            fii_net = fii.get("fiiNet", 0)
            gc = engine.get_global_cues()
            global_bias = gc.get("signal", "NEUTRAL")
        except Exception:
            pass

        # Build narrative
        what = f"{idx} opened {'+' if gap_pct>=0 else ''}{gap_pct:.2f}%, closed {'+' if day_change_pct>=0 else ''}{day_change_pct:.2f}%. Direction: {direction}."
        why_parts = []
        if direction == "V_REVERSAL":
            why_parts.append("Gap faded — likely distribution at open or institutional buying at lows.")
        if best_ce.get("pnl_pct", 0) > 30:
            why_parts.append(f"Best CE opportunity: {best_ce['strike']} (+{best_ce['pnl_pct']:.0f}%).")
        if best_pe.get("pnl_pct", 0) > 30:
            why_parts.append(f"Best PE opportunity: {best_pe['strike']} (+{best_pe['pnl_pct']:.0f}%).")
        if fii_net > 1000:
            why_parts.append(f"FII buying ₹{fii_net:.0f}Cr supported market.")
        elif fii_net < -1000:
            why_parts.append(f"FII selling ₹{fii_net:.0f}Cr pressured market.")
        why = " ".join(why_parts) if why_parts else "Normal session — no extreme drivers."

        # Insert or replace (if day already recorded, update)
        conn = _conn()
        conn.execute("""
            INSERT OR REPLACE INTO day_patterns (
                date, idx, prev_close, open_price, gap_pct, open_type,
                pcr_10am, vix_10am, ivr_10am, max_pain_10am,
                big_ce_wall, big_pe_wall,
                day_high, day_low, day_close, day_range_pct, day_change_pct,
                direction,
                fii_net, global_bias,
                best_strike_ce, best_strike_ce_pnl_pct,
                best_strike_pe, best_strike_pe_pnl_pct,
                what_happened, why_happened, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            date_str, idx, prev_close, open_px, gap_pct, d.get("openType", ""),
            d.get("pcr", 1.0), d.get("vix", 18), d.get("ivr", 50), d.get("maxPain", 0),
            int(d.get("bigCallStrike", 0)), int(d.get("bigPutStrike", 0)),
            high, low, ltp, day_range_pct, day_change_pct,
            direction,
            fii_net, global_bias,
            best_ce.get("strike", 0), best_ce.get("pnl_pct", 0),
            best_pe.get("strike", 0), best_pe.get("pnl_pct", 0),
            what, why, now.isoformat()
        ))
        conn.commit()
        conn.close()
        print(f"[MIND] Recorded day pattern for {idx} {date_str}: {direction}")
        return True
    except Exception as e:
        print(f"[MIND] record_day_pattern error: {e}")
        import traceback
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════════
# SIMILARITY SEARCH — find past days matching today
# ══════════════════════════════════════════════════════════════════

def find_similar_days(engine, idx, top_n=5):
    """Given today's live state, find N most similar past days."""
    init_mind_db()
    today_fp = fingerprint_today(engine, idx)
    if not today_fp:
        return []

    conn = _conn()
    past_days = conn.execute(
        "SELECT * FROM day_patterns WHERE idx=? ORDER BY date DESC LIMIT 100",
        (idx,)
    ).fetchall()
    conn.close()

    if not past_days:
        return []

    today_str = ist_now().strftime("%Y-%m-%d")
    matches = []
    for row in past_days:
        r = dict(row)
        if r["date"] == today_str:
            continue  # Skip today itself
        past_fp = {
            "gap_pct": r.get("gap_pct", 0),
            "pcr": r.get("pcr_10am", 1.0),
            "vix": r.get("vix_10am", 18),
            "ivr": r.get("ivr_10am", 50),
            "day_change_pct": r.get("day_change_pct", 0),
            "day_range_pct": r.get("day_range_pct", 0),
        }
        dist = _pattern_distance(today_fp, past_fp)
        sim = similarity_score(dist)
        matches.append({
            "date": r["date"],
            "similarity": round(sim, 1),
            "distance": round(dist, 3),
            "gap_pct": r.get("gap_pct", 0),
            "direction": r.get("direction", "UNKNOWN"),
            "day_change_pct": r.get("day_change_pct", 0),
            "best_strike_ce": r.get("best_strike_ce", 0),
            "best_strike_ce_pnl_pct": r.get("best_strike_ce_pnl_pct", 0),
            "best_strike_pe": r.get("best_strike_pe", 0),
            "best_strike_pe_pnl_pct": r.get("best_strike_pe_pnl_pct", 0),
            "what_happened": r.get("what_happened", ""),
            "why_happened": r.get("why_happened", ""),
        })

    matches.sort(key=lambda x: x["similarity"], reverse=True)
    return matches[:top_n]


# ══════════════════════════════════════════════════════════════════
# TODAY'S PREDICTION (based on similar days)
# ══════════════════════════════════════════════════════════════════

def predict_today(engine, idx):
    """Based on similar past days, predict today's likely outcome."""
    init_mind_db()
    similar = find_similar_days(engine, idx, top_n=5)

    if not similar:
        return {
            "status": "NEED_MORE_DATA",
            "message": "Not enough historical patterns yet. Data builds over 10+ trading days.",
            "similar_days": [],
        }

    # Filter high-similarity matches (>70%)
    strong = [m for m in similar if m["similarity"] >= 70]

    if not strong:
        return {
            "status": "NO_STRONG_MATCH",
            "message": f"Today's pattern is unique. Top match {similar[0]['similarity']:.0f}% ({similar[0]['date']})",
            "similar_days": similar[:3],
        }

    # Analyze outcomes of strong matches
    directions = [m["direction"] for m in strong]
    avg_change = sum(m["day_change_pct"] for m in strong) / len(strong)

    direction_counts = defaultdict(int)
    for d in directions:
        direction_counts[d] += 1
    likely_direction = max(direction_counts, key=direction_counts.get)
    confidence = direction_counts[likely_direction] / len(strong) * 100

    # Best strike suggestions
    best_ce_strikes = [m["best_strike_ce"] for m in strong if m.get("best_strike_ce_pnl_pct", 0) > 20]
    best_pe_strikes = [m["best_strike_pe"] for m in strong if m.get("best_strike_pe_pnl_pct", 0) > 20]

    # Narrative
    top_match = strong[0]
    narrative = (
        f"Today's market pattern is {top_match['similarity']:.0f}% similar to {top_match['date']}. "
        f"On that day: {top_match['what_happened']} "
    )
    if len(strong) > 1:
        narrative += f"Found {len(strong)} similar historical days. "
    narrative += f"Most likely direction: {likely_direction} ({confidence:.0f}% of matches). Expected avg move: {'+' if avg_change>=0 else ''}{avg_change:.2f}%."

    return {
        "status": "PREDICTION_READY",
        "likely_direction": likely_direction,
        "confidence_pct": round(confidence, 1),
        "avg_change_pct": round(avg_change, 2),
        "narrative": narrative,
        "strong_match_count": len(strong),
        "best_ce_suggestions": best_ce_strikes[:3],
        "best_pe_suggestions": best_pe_strikes[:3],
        "top_match": top_match,
        "similar_days": similar[:5],
    }


# ══════════════════════════════════════════════════════════════════
# NARRATIVE ENGINE — explain WHY things happened
# ══════════════════════════════════════════════════════════════════

def explain_move(engine, idx, hours=2):
    """Explain last N hours of price action with OI context."""
    try:
        live = engine.get_live_data()
        d = live.get(idx.lower(), {})
        change = d.get("changePct", 0)
        pcr = d.get("pcr", 1.0)
        max_pain = d.get("maxPain", 0)
        ltp = d.get("ltp", 0)

        story = []

        # Direction
        if change > 0.3:
            story.append(f"📈 {idx} up +{change:.2f}% today")
        elif change < -0.3:
            story.append(f"📉 {idx} down {change:.2f}% today")
        else:
            story.append(f"➡️ {idx} flat ({change:+.2f}%)")

        # PCR context
        if pcr > 1.2:
            story.append(f"PCR {pcr:.2f} high = heavy put writing = bullish support")
        elif pcr < 0.8:
            story.append(f"PCR {pcr:.2f} low = heavy call writing = bearish resistance")

        # Max pain pull
        if max_pain > 0:
            dist = ltp - max_pain
            pct_off = (dist / max_pain) * 100
            if abs(pct_off) > 0.3:
                direction = "above" if dist > 0 else "below"
                story.append(f"Price {abs(pct_off):.2f}% {direction} max pain {max_pain} — pull {'up' if dist > 0 else 'down'} expected")

        # Smart money
        try:
            from smart_money import score_smart_money
            sm = getattr(engine, "smart_money_state", None)
            if sm:
                result = score_smart_money(sm, engine, idx)
                reasons = result.get("reasons", [])
                if reasons:
                    story.append(f"🐋 Whale activity: {reasons[0][:100]}")
        except Exception:
            pass

        # Expiry context
        if ist_now().weekday() == 1 and idx == "NIFTY":
            story.append("⚠️ NIFTY EXPIRY DAY — theta + pin risk active")

        return {
            "narrative": " | ".join(story),
            "change_pct": change,
            "pcr": pcr,
            "max_pain": max_pain,
            "timestamp": ist_now().isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# SUMMARY API
# ══════════════════════════════════════════════════════════════════

def get_mind_summary(engine, idx):
    """All-in-one: today's pattern, similar days, prediction, narrative."""
    return {
        "idx": idx,
        "today_fingerprint": fingerprint_today(engine, idx),
        "prediction": predict_today(engine, idx),
        "narrative": explain_move(engine, idx),
        "timestamp": ist_now().isoformat(),
    }


def get_recorded_days():
    """List all days recorded in mind."""
    init_mind_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT date, idx, direction, gap_pct, day_change_pct, best_strike_ce, best_strike_pe FROM day_patterns ORDER BY date DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
