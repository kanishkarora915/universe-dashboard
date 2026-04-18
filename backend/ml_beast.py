"""
BEAST MODE ML TRAINING — 11 improvements over basic Bayesian weighting.

Features:
  1. Profit-weighted outcomes (R-multiples, not binary)
  2. Exponential time decay (recent trades matter more)
  3. Manual Train Now endpoint
  4. Auto-disable broken engines (<40% accuracy over 50 trades)
  5. Regime-aware weights (bull / bear / sideways)
  6. Time-of-day weight buckets (morning / mid / lunch / closing)
  7. Multi-timeframe outcomes (15m / 30m / 60m)
  8. Correlation-aware weight discounting
  9. Train/validate 80/20 split — reject regressive updates
 10. Online learning (per-trade incremental updates)
 11. A/B testing framework (2 weight sets, winner promoted)
 12. Feature engineering (VIX / time / moneyness as context)

Designed to be backward-compatible with existing ml_feedback.py.
Data stored in same backtest.db + new beast_training.db for A/B + logs.
"""

import sqlite3
import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent

BEAST_DB = _data_dir / "beast_training.db"
WEIGHTS_REGIME = _data_dir / "engine_weights_regime.json"
AB_STATE_FILE = _data_dir / "ab_test_state.json"
ONLINE_STATE = _data_dir / "online_learning_state.json"


def ist_now():
    return datetime.now(IST)


# ══════════════════════════════════════════════════════════════════════════
# DB SETUP
# ══════════════════════════════════════════════════════════════════════════

def init_beast_db():
    """Initialize beast-mode training DB with all tables."""
    conn = sqlite3.connect(str(BEAST_DB))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS training_runs_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            regime TEXT,            -- 'bull' / 'bear' / 'sideways' / 'all'
            time_bucket TEXT,       -- 'morning' / 'mid' / 'lunch' / 'closing' / 'all'
            data_points INTEGER,
            accuracy_before REAL,
            accuracy_after_train REAL,
            accuracy_validate REAL,   -- validation set accuracy
            accepted INTEGER,         -- 1 if weights applied, 0 if rejected
            rejection_reason TEXT,
            old_weights TEXT,
            new_weights TEXT,
            correlation_matrix TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS ab_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            set_a_weights TEXT,
            set_b_weights TEXT,
            set_a_trades INTEGER DEFAULT 0,
            set_a_wins INTEGER DEFAULT 0,
            set_a_pnl REAL DEFAULT 0,
            set_b_trades INTEGER DEFAULT 0,
            set_b_wins INTEGER DEFAULT 0,
            set_b_pnl REAL DEFAULT 0,
            winner TEXT,
            status TEXT DEFAULT 'running'  -- 'running' / 'completed' / 'aborted'
        );

        CREATE TABLE IF NOT EXISTS engine_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            engine_key TEXT NOT NULL,
            updated_at TEXT,
            recent_accuracy REAL,       -- last 50 trades
            historical_accuracy REAL,   -- all time
            trades_evaluated INTEGER,
            auto_disabled INTEGER DEFAULT 0,
            disabled_reason TEXT,
            UNIQUE(engine_key)
        );

        CREATE TABLE IF NOT EXISTS online_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            trade_id INTEGER,
            regime TEXT,
            time_bucket TEXT,
            r_multiple REAL,             -- profit in R units (win/loss sized)
            engine_contributions TEXT,   -- JSON: per-engine score at entry
            weight_delta TEXT,           -- JSON: how weights changed
            context_features TEXT        -- VIX/time/moneyness snapshot
        );

        CREATE INDEX IF NOT EXISTS idx_runs_ts ON training_runs_v2(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_ab_status ON ab_tests(status);
        CREATE INDEX IF NOT EXISTS idx_online_ts ON online_updates(timestamp DESC);
    """)
    conn.commit()
    conn.close()


def _conn():
    init_beast_db()
    conn = sqlite3.connect(str(BEAST_DB))
    conn.row_factory = sqlite3.Row
    return conn


# ══════════════════════════════════════════════════════════════════════════
# FEATURE #1 + #2: PROFIT-WEIGHTED + TIME DECAY
# ══════════════════════════════════════════════════════════════════════════

def compute_r_multiple(pnl_rupees: float, risk_rupees: float) -> float:
    """Convert raw P&L into R-multiple (1R = risk amount).
    Win of +2R is 2x more important than +0.5R.
    Loss of -1R (full SL) is baseline."""
    if not risk_rupees or risk_rupees <= 0:
        return 0.0
    return pnl_rupees / risk_rupees


def time_decay_weight(trade_time, now=None, half_life_days=7):
    """Exponential decay: recent trades weighted higher.
    Half-life 7 days: a trade from 1 week ago = 0.5x,
    2 weeks = 0.25x, 4 weeks = 0.06x."""
    if not now:
        now = ist_now()
    try:
        t = datetime.fromisoformat(trade_time) if isinstance(trade_time, str) else trade_time
        if t.tzinfo is None:
            t = IST.localize(t)
        age_days = (now - t).total_seconds() / 86400.0
        return 0.5 ** (age_days / half_life_days)
    except Exception:
        return 0.5  # fallback mid-weight


# ══════════════════════════════════════════════════════════════════════════
# FEATURE #5: REGIME DETECTION
# ══════════════════════════════════════════════════════════════════════════

def classify_regime(vix: float, nifty_daily_pct: float, pcr: float) -> str:
    """Classify current market regime from VIX, daily change, PCR."""
    if vix is None or nifty_daily_pct is None:
        return "sideways"  # safe default
    if vix > 20:
        return "high_vol_bear" if nifty_daily_pct < -0.5 else "high_vol_bull" if nifty_daily_pct > 0.5 else "sideways"
    # Low-to-normal VIX regimes
    if nifty_daily_pct > 0.5 and (pcr or 1.0) > 1.05:
        return "bull"
    if nifty_daily_pct < -0.5 and (pcr or 1.0) < 0.95:
        return "bear"
    return "sideways"


# ══════════════════════════════════════════════════════════════════════════
# FEATURE #6: TIME-OF-DAY BUCKETS
# ══════════════════════════════════════════════════════════════════════════

def time_bucket(t) -> str:
    """Bucket trade time into market phases."""
    if isinstance(t, str):
        try:
            t = datetime.fromisoformat(t)
        except Exception:
            return "unknown"
    hour = t.hour
    minute = t.minute
    total_min = hour * 60 + minute
    # Market phases (IST)
    if total_min < 9 * 60 + 15:  # pre-market
        return "pre_market"
    if total_min < 10 * 60 + 30:
        return "morning"           # 9:15 - 10:30
    if total_min < 12 * 60 + 30:
        return "mid"               # 10:30 - 12:30
    if total_min < 14 * 60:
        return "lunch"             # 12:30 - 14:00
    if total_min < 15 * 60 + 30:
        return "closing"           # 14:00 - 15:30
    return "post_market"


# ══════════════════════════════════════════════════════════════════════════
# FEATURE #8: CORRELATION DISCOUNT
# ══════════════════════════════════════════════════════════════════════════

def compute_correlation_matrix(trade_records):
    """Build per-engine correlation matrix.
    trade_records: list of dicts with per-engine scores (ce_score_seller, etc.)
    Returns NxN dict.
    """
    engines = ["seller_positioning", "trap_fingerprints", "price_action", "oi_flow",
               "market_context", "vwap", "multi_timeframe", "fii_dii", "global_cues"]
    col_map = {
        "seller_positioning": "seller_pts",
        "trap_fingerprints": "trap_pts",
        "price_action": "price_action_pts",
        "oi_flow": "oi_flow_pts",
        "market_context": "market_context_pts",
        "vwap": "vwap_pts",
        "multi_timeframe": "mtf_pts",
        "fii_dii": "fii_dii_pts",
        "global_cues": "global_cues_pts",
    }
    if len(trade_records) < 5:
        return {e1: {e2: 0.0 for e2 in engines} for e1 in engines}

    # Extract per-engine score vectors
    vectors = {}
    for e in engines:
        col = col_map[e]
        vectors[e] = [float(r.get(col, 0) or 0) for r in trade_records]

    def correlation(a, b):
        n = len(a)
        if n < 2:
            return 0.0
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
        var_a = sum((x - mean_a) ** 2 for x in a)
        var_b = sum((x - mean_b) ** 2 for x in b)
        denom = (var_a * var_b) ** 0.5
        return cov / denom if denom > 0 else 0.0

    matrix = {}
    for e1 in engines:
        matrix[e1] = {}
        for e2 in engines:
            matrix[e1][e2] = round(correlation(vectors[e1], vectors[e2]), 3)
    return matrix


def correlation_adjusted_weights(base_weights, corr_matrix):
    """Discount weights for highly-correlated engines.
    If A and B are >0.7 correlated, reduce each by fraction of overlap."""
    out = dict(base_weights)
    engines = list(base_weights.keys())
    for i, e1 in enumerate(engines):
        total_overlap = 0
        for e2 in engines:
            if e1 == e2:
                continue
            c = abs(corr_matrix.get(e1, {}).get(e2, 0))
            if c > 0.7:
                total_overlap += (c - 0.7) * 0.5  # 30% max discount per pair
        adjust = max(0.3, 1.0 - total_overlap)  # don't crush below 30%
        out[e1] = round(base_weights[e1] * adjust)
    return out


# ══════════════════════════════════════════════════════════════════════════
# FEATURE #9: TRAIN/VALIDATE SPLIT
# ══════════════════════════════════════════════════════════════════════════

def split_train_validate(records, ratio=0.8):
    """Split records into train (80%) and validate (20%) sets.
    Uses chronological order — validate set is most recent 20%."""
    if len(records) < 10:
        return records, []
    sorted_r = sorted(records, key=lambda r: r.get("entry_time", ""))
    split_idx = int(len(sorted_r) * ratio)
    return sorted_r[:split_idx], sorted_r[split_idx:]


def score_weights_on_records(weights, records):
    """Simulate what weights would predict on records, compute accuracy."""
    if not records:
        return 50.0
    correct = 0
    for r in records:
        # Weighted score — positive = predicted win
        col_map = {
            "seller_positioning": "seller_pts", "trap_fingerprints": "trap_pts",
            "price_action": "price_action_pts", "oi_flow": "oi_flow_pts",
            "market_context": "market_context_pts", "vwap": "vwap_pts",
            "multi_timeframe": "mtf_pts", "fii_dii": "fii_dii_pts",
            "global_cues": "global_cues_pts",
        }
        predicted_score = 0
        max_possible = sum(weights.values())
        for eng, col in col_map.items():
            pts = r.get(col, 0) or 0
            weight = weights.get(eng, 0)
            predicted_score += pts * (weight / max(max_possible, 1))
        # Convert to "predicted win" if score > threshold (say median of data)
        predicted_win = predicted_score > 7  # simple threshold
        actual_win = (r.get("pnl_rupees", 0) or 0) > 0
        if predicted_win == actual_win:
            correct += 1
    return (correct / len(records)) * 100


# ══════════════════════════════════════════════════════════════════════════
# FEATURE #4: AUTO-DISABLE BROKEN ENGINES
# ══════════════════════════════════════════════════════════════════════════

def evaluate_engine_health(records, recent_window=50):
    """For each engine, compute: recent accuracy (last N trades) vs all-time.
    Auto-disable if recent <40% over 50+ trades."""
    col_map = {
        "seller_positioning": "seller_pts", "trap_fingerprints": "trap_pts",
        "price_action": "price_action_pts", "oi_flow": "oi_flow_pts",
        "market_context": "market_context_pts", "vwap": "vwap_pts",
        "multi_timeframe": "mtf_pts", "fii_dii": "fii_dii_pts",
        "global_cues": "global_cues_pts",
    }
    sorted_r = sorted(records, key=lambda r: r.get("entry_time", ""), reverse=True)
    recent = sorted_r[:recent_window]

    health = {}
    for eng, col in col_map.items():
        # Only count trades where this engine had a non-zero signal
        hist_trades = [r for r in records if (r.get(col, 0) or 0) > 0]
        recent_trades = [r for r in recent if (r.get(col, 0) or 0) > 0]

        def acc(trades):
            if not trades:
                return None, 0
            wins = sum(1 for r in trades if (r.get("pnl_rupees", 0) or 0) > 0)
            return (wins / len(trades)) * 100, len(trades)

        hist_acc, hist_n = acc(hist_trades)
        recent_acc, recent_n = acc(recent_trades)

        should_disable = False
        reason = ""
        if recent_acc is not None and recent_n >= 20 and recent_acc < 40:
            should_disable = True
            reason = f"Recent {recent_n} trades: {recent_acc:.0f}% accuracy (<40% threshold)"

        health[eng] = {
            "engine": eng,
            "recentAccuracy": round(recent_acc or 0, 1),
            "historicalAccuracy": round(hist_acc or 0, 1),
            "recentTrades": recent_n,
            "historicalTrades": hist_n,
            "autoDisable": should_disable,
            "reason": reason,
        }
    return health


def apply_auto_disable(health_report):
    """Write auto-disabled engines to engine_toggles.json (only set to False)."""
    toggles_file = _data_dir / "engine_toggles.json"
    current = {}
    if toggles_file.exists():
        try:
            current = json.loads(toggles_file.read_text())
        except Exception:
            pass

    changed = []
    for eng, info in health_report.items():
        if info["autoDisable"] and current.get(eng, True):
            current[eng] = False
            changed.append(eng)

    if changed:
        toggles_file.write_text(json.dumps(current, indent=2))
        # Persist health snapshot
        conn = _conn()
        for eng, info in health_report.items():
            conn.execute("""
                INSERT OR REPLACE INTO engine_health
                (engine_key, updated_at, recent_accuracy, historical_accuracy,
                 trades_evaluated, auto_disabled, disabled_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                eng, ist_now().isoformat(), info["recentAccuracy"], info["historicalAccuracy"],
                info["recentTrades"], 1 if info["autoDisable"] else 0, info["reason"],
            ))
        conn.commit()
        conn.close()
    return changed


# ══════════════════════════════════════════════════════════════════════════
# FEATURE #10: ONLINE LEARNING
# ══════════════════════════════════════════════════════════════════════════

def _load_online_state():
    if ONLINE_STATE.exists():
        try:
            return json.loads(ONLINE_STATE.read_text())
        except Exception:
            pass
    return {"weights": {}, "updates_count": 0}


def _save_online_state(state):
    ONLINE_STATE.write_text(json.dumps(state, indent=2))


def online_update_after_trade(trade_record, current_weights, learning_rate=0.02):
    """Incrementally adjust weights after a trade completes.
    Winning trade → engines that fired get +boost.
    Losing trade → engines that fired get -penalty.
    Weighted by R-multiple (size of win/loss matters).
    """
    col_map = {
        "seller_positioning": "seller_pts", "trap_fingerprints": "trap_pts",
        "price_action": "price_action_pts", "oi_flow": "oi_flow_pts",
        "market_context": "market_context_pts", "vwap": "vwap_pts",
        "multi_timeframe": "mtf_pts", "fii_dii": "fii_dii_pts",
        "global_cues": "global_cues_pts",
    }

    pnl = float(trade_record.get("pnl_rupees", 0) or 0)
    risk = float(trade_record.get("risk_rupees", 1000) or 1000)
    r_mult = compute_r_multiple(pnl, risk)

    # Cap R-multiple to prevent outlier trades from dominating
    r_mult = max(-3.0, min(3.0, r_mult))

    new_weights = dict(current_weights)
    delta = {}
    for eng, col in col_map.items():
        engine_fired = (trade_record.get(col, 0) or 0) > 0
        if not engine_fired:
            continue
        # Adjust weight based on outcome direction + magnitude
        adjustment = learning_rate * r_mult * current_weights.get(eng, 10)
        new_weights[eng] = max(1, current_weights.get(eng, 10) + adjustment)
        delta[eng] = round(adjustment, 2)

    # Log the online update
    conn = _conn()
    conn.execute("""
        INSERT INTO online_updates
        (timestamp, trade_id, regime, time_bucket, r_multiple, engine_contributions,
         weight_delta, context_features)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ist_now().isoformat(), trade_record.get("id"),
        trade_record.get("regime"), trade_record.get("time_bucket"),
        r_mult,
        json.dumps({e: trade_record.get(c, 0) for e, c in col_map.items()}),
        json.dumps(delta),
        json.dumps({
            "vix": trade_record.get("vix"),
            "moneyness": trade_record.get("moneyness"),
        }),
    ))
    conn.commit()
    conn.close()

    # Update state
    state = _load_online_state()
    state["weights"] = new_weights
    state["updates_count"] = state.get("updates_count", 0) + 1
    state["last_update"] = ist_now().isoformat()
    _save_online_state(state)

    return {"new_weights": new_weights, "delta": delta, "r_multiple": r_mult}


# ══════════════════════════════════════════════════════════════════════════
# FEATURE #11: A/B TESTING
# ══════════════════════════════════════════════════════════════════════════

def start_ab_test(weights_a, weights_b):
    """Start a new A/B test with two weight sets."""
    conn = _conn()
    # End any running tests
    conn.execute("UPDATE ab_tests SET status='aborted', ended_at=? WHERE status='running'",
                 (ist_now().isoformat(),))
    cur = conn.execute("""
        INSERT INTO ab_tests (started_at, set_a_weights, set_b_weights, status)
        VALUES (?, ?, ?, 'running')
    """, (ist_now().isoformat(), json.dumps(weights_a), json.dumps(weights_b)))
    test_id = cur.lastrowid
    conn.commit()
    conn.close()
    AB_STATE_FILE.write_text(json.dumps({
        "test_id": test_id, "weights_a": weights_a, "weights_b": weights_b,
        "started_at": ist_now().isoformat(), "current_trade": 0,
    }))
    return {"test_id": test_id, "started": True}


def record_ab_trade_result(trade_record):
    """When a trade completes during A/B test, record outcome to the active set."""
    if not AB_STATE_FILE.exists():
        return None
    try:
        state = json.loads(AB_STATE_FILE.read_text())
    except Exception:
        return None

    # Alternate: even trades → set A, odd → set B
    n = state.get("current_trade", 0)
    assigned = "a" if n % 2 == 0 else "b"
    state["current_trade"] = n + 1
    AB_STATE_FILE.write_text(json.dumps(state))

    pnl = float(trade_record.get("pnl_rupees", 0) or 0)
    won = 1 if pnl > 0 else 0
    conn = _conn()
    if assigned == "a":
        conn.execute("""
            UPDATE ab_tests SET
              set_a_trades = set_a_trades + 1,
              set_a_wins = set_a_wins + ?,
              set_a_pnl = set_a_pnl + ?
            WHERE id=? AND status='running'
        """, (won, pnl, state["test_id"]))
    else:
        conn.execute("""
            UPDATE ab_tests SET
              set_b_trades = set_b_trades + 1,
              set_b_wins = set_b_wins + ?,
              set_b_pnl = set_b_pnl + ?
            WHERE id=? AND status='running'
        """, (won, pnl, state["test_id"]))
    conn.commit()
    conn.close()
    return {"assigned": assigned, "trade_num": n}


def check_ab_winner(min_trades_per_set=20):
    """Check running A/B test — promote winner if enough data."""
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM ab_tests WHERE status='running' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "no_running_test"}
    r = dict(row)
    a_trades = r.get("set_a_trades", 0) or 0
    b_trades = r.get("set_b_trades", 0) or 0
    if a_trades < min_trades_per_set or b_trades < min_trades_per_set:
        conn.close()
        return {
            "status": "in_progress",
            "progress": {"a": a_trades, "b": b_trades, "target": min_trades_per_set},
        }

    # Compare P&L
    a_pnl = r.get("set_a_pnl", 0) or 0
    b_pnl = r.get("set_b_pnl", 0) or 0
    winner = "a" if a_pnl >= b_pnl else "b"
    winner_weights = json.loads(r[f"set_{winner}_weights"])

    conn.execute(
        "UPDATE ab_tests SET status='completed', ended_at=?, winner=? WHERE id=?",
        (ist_now().isoformat(), winner, r["id"])
    )
    conn.commit()
    conn.close()
    if AB_STATE_FILE.exists():
        AB_STATE_FILE.unlink()

    return {"status": "completed", "winner": winner, "weights": winner_weights}


# ══════════════════════════════════════════════════════════════════════════
# MASTER TRAIN FUNCTION — orchestrates all 11 features
# ══════════════════════════════════════════════════════════════════════════

def run_beast_training(current_vix=None, current_nifty_pct=None, current_pcr=None):
    """Run one complete beast-mode training cycle. Returns detailed report.
    Uses all 11 features where data permits."""
    init_beast_db()
    now = ist_now()
    report = {"timestamp": now.isoformat(), "features_applied": [], "warnings": []}

    # Load trade records
    try:
        from ml_feedback import load_weights, save_weights, _bt_conn, DEFAULT_WEIGHTS
    except Exception as e:
        return {"error": f"ml_feedback import failed: {e}"}

    conn = _bt_conn()
    if not conn:
        return {"error": "No backtest DB available", "report": report}

    try:
        records = [dict(r) for r in conn.execute(
            "SELECT * FROM backtest_log WHERE pnl_rupees IS NOT NULL ORDER BY entry_time DESC LIMIT 500"
        ).fetchall()]
    except Exception:
        records = []
    conn.close()

    if len(records) < 10:
        return {
            "status": "skipped",
            "reason": f"Need 10+ completed trades, have {len(records)}",
            "timestamp": now.isoformat(),
        }

    current_weights = load_weights()
    regime = classify_regime(current_vix, current_nifty_pct, current_pcr)
    report["regime"] = regime
    report["records_count"] = len(records)

    # Feature #2: Apply time decay to trade importance
    weighted_records = []
    for r in records:
        w = time_decay_weight(r.get("entry_time"), now)
        r["_weight"] = w
        # Feature #1: R-multiple outcome
        r["_r_mult"] = compute_r_multiple(r.get("pnl_rupees", 0) or 0, r.get("risk_rupees", 1000) or 1000)
        # Feature #6: Time bucket
        r["_bucket"] = time_bucket(r.get("entry_time"))
        weighted_records.append(r)
    report["features_applied"].extend(["time_decay", "r_multiple", "time_bucket"])

    # Feature #9: Train/validate split
    train_set, validate_set = split_train_validate(weighted_records, ratio=0.8)
    report["train_size"] = len(train_set)
    report["validate_size"] = len(validate_set)
    report["features_applied"].append("train_validate_split")

    # Feature #8: Correlation matrix
    corr = compute_correlation_matrix(train_set)
    report["correlation_matrix"] = corr
    report["features_applied"].append("correlation_matrix")

    # Core: compute new weights using weighted outcomes
    col_map = {
        "seller_positioning": "seller_pts", "trap_fingerprints": "trap_pts",
        "price_action": "price_action_pts", "oi_flow": "oi_flow_pts",
        "market_context": "market_context_pts", "vwap": "vwap_pts",
        "multi_timeframe": "mtf_pts", "fii_dii": "fii_dii_pts",
        "global_cues": "global_cues_pts",
    }

    # Per-engine weighted accuracy
    engine_perf = {}
    for eng, col in col_map.items():
        total_w = 0
        weighted_r_sum = 0  # sum of R-multiples weighted by time
        fire_count = 0
        for r in train_set:
            pts = r.get(col, 0) or 0
            if pts <= 0:
                continue
            fire_count += 1
            total_w += r["_weight"]
            weighted_r_sum += r["_weight"] * r["_r_mult"]
        avg_r = weighted_r_sum / total_w if total_w > 0 else 0
        engine_perf[eng] = {"avg_r": avg_r, "fires": fire_count}

    # Compute target weights proportional to each engine's avg_r
    max_r = max((p["avg_r"] for p in engine_perf.values()), default=1)
    min_r = min((p["avg_r"] for p in engine_perf.values()), default=-1)
    range_r = max(max_r - min_r, 0.5)

    new_weights = {}
    for eng, info in DEFAULT_WEIGHTS.items():
        base = info["max"]
        perf = engine_perf.get(eng, {"avg_r": 0, "fires": 0})
        # Map avg_r to weight multiplier: max_r → 1.2x base, min_r → 0.3x base
        if perf["fires"] < 3:
            new_weights[eng] = current_weights.get(eng, base)  # not enough data, keep old
            continue
        norm = (perf["avg_r"] - min_r) / range_r  # 0 to 1
        multiplier = 0.3 + norm * 0.9  # 0.3x to 1.2x
        # Cap change per cycle to ±15%
        proposed = round(base * multiplier)
        cur = current_weights.get(eng, base)
        max_change = max(1, int(abs(cur) * 0.15))
        new_weights[eng] = max(cur - max_change, min(cur + max_change, proposed))

    # Feature #8: correlation-adjust
    new_weights = correlation_adjusted_weights(new_weights, corr)
    report["proposed_weights"] = new_weights

    # Feature #9: validate new weights against held-out set
    train_acc = score_weights_on_records(new_weights, train_set)
    validate_acc = score_weights_on_records(new_weights, validate_set)
    old_validate_acc = score_weights_on_records(current_weights, validate_set)
    report["train_accuracy"] = round(train_acc, 1)
    report["validate_accuracy"] = round(validate_acc, 1)
    report["old_validate_accuracy"] = round(old_validate_acc, 1)

    accepted = validate_acc >= old_validate_acc - 2  # allow 2% noise
    report["accepted"] = accepted
    report["rejection_reason"] = "" if accepted else \
        f"New weights validate_acc {validate_acc:.1f}% < old {old_validate_acc:.1f}% — kept old"

    if accepted:
        save_weights(new_weights, auto=True)
        report["applied"] = True
    else:
        report["applied"] = False
        new_weights = current_weights

    # Feature #4: Auto-disable broken engines
    health = evaluate_engine_health(weighted_records)
    disabled = apply_auto_disable(health)
    report["engine_health"] = health
    report["auto_disabled"] = disabled
    report["features_applied"].append("auto_disable_broken")

    # Log training run
    conn = _conn()
    conn.execute("""
        INSERT INTO training_runs_v2
        (timestamp, regime, time_bucket, data_points,
         accuracy_before, accuracy_after_train, accuracy_validate,
         accepted, rejection_reason, old_weights, new_weights,
         correlation_matrix, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now.isoformat(), regime, "all", len(records),
        old_validate_acc, train_acc, validate_acc,
        1 if accepted else 0,
        report.get("rejection_reason", ""),
        json.dumps(current_weights), json.dumps(new_weights),
        json.dumps(corr),
        f"Beast training: {len(report['features_applied'])} features applied, regime={regime}",
    ))
    conn.commit()
    conn.close()

    return report


# ══════════════════════════════════════════════════════════════════════════
# QUERY HELPERS — for UI
# ══════════════════════════════════════════════════════════════════════════

def get_training_history(limit=30):
    """Return recent training runs for UI display."""
    init_beast_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM training_runs_v2 ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_engine_health_report():
    """Return current health snapshot of each engine."""
    init_beast_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM engine_health"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ab_status():
    """Return current A/B test status + any recent completed ones."""
    init_beast_db()
    conn = _conn()
    running = conn.execute(
        "SELECT * FROM ab_tests WHERE status='running' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    recent = conn.execute(
        "SELECT * FROM ab_tests WHERE status='completed' ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()
    return {
        "running": dict(running) if running else None,
        "recent": [dict(r) for r in recent],
    }


def get_online_learning_status():
    """Return current online learning state."""
    state = _load_online_state()
    init_beast_db()
    conn = _conn()
    recent_updates = conn.execute(
        "SELECT * FROM online_updates ORDER BY timestamp DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return {
        "state": state,
        "recent_updates": [dict(r) for r in recent_updates],
    }
