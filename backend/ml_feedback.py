"""
ML Feedback Engine — Self-learning weight system for UNIVERSE.
Analyzes backtest outcomes with PER-ENGINE score breakdowns to:
1. Calculate real per-engine accuracy (not estimated)
2. Auto-adjust weights via Bayesian updating
3. Detect best/worst trading windows
4. Generate weekly training reports
5. Auto-train every Sunday 8 PM IST
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
import pytz

IST = pytz.timezone("Asia/Kolkata")

# Data dir — persistent on Render, local otherwise
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
WEIGHTS_FILE = _data_dir / "engine_weights.json"
BACKTEST_DB = _data_dir / "backtest.db"
TRADES_DB = _data_dir / "trades.db"

# ── Default weights (hardcoded baseline) ─────────────────────────────────

DEFAULT_WEIGHTS = {
    "seller_positioning": {"max": 30, "description": "PE/CE writing & short covering"},
    "trap_fingerprints": {"max": 20, "description": "Institutional hidden positioning"},
    "price_action": {"max": 20, "description": "Premium momentum & ratio"},
    "oi_flow": {"max": 15, "description": "PCR & OI unwinding"},
    "market_context": {"max": 15, "description": "Gap type, max pain, VIX"},
    "vwap": {"max": 5, "description": "Price vs VWAP"},
    "multi_timeframe": {"max": 15, "description": "5m+15m+1hr confluence"},
    "fii_dii": {"max": 10, "description": "FII/DII net flows"},
    "global_cues": {"max": 10, "description": "Dow, global sentiment"},
}

# Column name in backtest_log → engine key
COL_TO_ENGINE = {
    "seller_pts": "seller_positioning",
    "trap_pts": "trap_fingerprints",
    "price_action_pts": "price_action",
    "oi_flow_pts": "oi_flow",
    "market_context_pts": "market_context",
    "vwap_pts": "vwap",
    "mtf_pts": "multi_timeframe",
    "fii_dii_pts": "fii_dii",
    "global_cues_pts": "global_cues",
}

TOTAL_MAX = sum(v["max"] for v in DEFAULT_WEIGHTS.values())  # 140


def ist_now():
    return datetime.now(IST)


def _bt_conn():
    if not BACKTEST_DB.exists():
        return None
    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _trades_conn():
    if not TRADES_DB.exists():
        return None
    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _has_engine_scores(row):
    """Check if a backtest row has per-engine scores (new format)."""
    try:
        return any(row[col] and row[col] > 0 for col in COL_TO_ENGINE.keys())
    except (IndexError, KeyError):
        return False


# ══════════════════════════════════════════════════════════════════════════
# 1. ENGINE ACCURACY — Real per-engine win correlation
# ══════════════════════════════════════════════════════════════════════════

def get_engine_accuracy(days=30):
    """Analyze per-engine accuracy using real score breakdowns from backtest_log.

    For each engine: when it contributed >0 points AND the verdict was WIN,
    that engine's signal was correct. This gives us TRUE per-engine accuracy.
    """
    conn = _bt_conn()
    if not conn:
        return {"error": "No backtest data available", "engines": []}

    cutoff = (ist_now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM backtest_log WHERE timestamp > ? AND checked = 1",
        (cutoff,)
    ).fetchall()
    conn.close()

    if not rows:
        return {"error": "No checked backtest data in range", "engines": [], "total": 0}

    total = len(rows)

    # Overall accuracy by timeframe
    def win_rate(field):
        valid = [r for r in rows if r[field] in ("WIN", "LOSS")]
        if not valid:
            return {"rate": 0, "total": 0, "wins": 0}
        wins = sum(1 for r in valid if r[field] == "WIN")
        return {"rate": round(wins / len(valid) * 100, 1), "total": len(valid), "wins": wins}

    # Probability band accuracy
    prob_bands = {"60-70": [], "70-80": [], "80-90": [], "90-100": []}
    for r in rows:
        p = r["verdict_probability"] or 0
        if p < 70:
            prob_bands["60-70"].append(r)
        elif p < 80:
            prob_bands["70-80"].append(r)
        elif p < 90:
            prob_bands["80-90"].append(r)
        else:
            prob_bands["90-100"].append(r)

    band_accuracy = {}
    for band, band_rows in prob_bands.items():
        valid = [r for r in band_rows if r["outcome_30min"] in ("WIN", "LOSS")]
        if valid:
            wins = sum(1 for r in valid if r["outcome_30min"] == "WIN")
            band_accuracy[band] = {"rate": round(wins / len(valid) * 100, 1), "count": len(valid)}
        else:
            band_accuracy[band] = {"rate": 0, "count": 0}

    # Direction accuracy
    ce_rows = [r for r in rows if "CE" in (r["verdict_action"] or "")]
    pe_rows = [r for r in rows if "PE" in (r["verdict_action"] or "")]

    def dir_rate(subset):
        valid = [r for r in subset if r["outcome_30min"] in ("WIN", "LOSS")]
        if not valid:
            return {"rate": 0, "total": 0}
        wins = sum(1 for r in valid if r["outcome_30min"] == "WIN")
        return {"rate": round(wins / len(valid) * 100, 1), "total": len(valid)}

    # ── REAL per-engine accuracy ──
    # Only use rows that have per-engine score data (new format)
    scored_rows = [r for r in rows if _has_engine_scores(r)]
    overall_30m = win_rate("outcome_30min")["rate"]

    engines = []
    weights = load_weights()
    for col, engine_name in COL_TO_ENGINE.items():
        info = DEFAULT_WEIGHTS[engine_name]
        current_max = weights.get(engine_name, info["max"])

        if scored_rows:
            # Real per-engine accuracy: when this engine scored >0, what was the outcome?
            active = [r for r in scored_rows if r[col] and r[col] > 0]
            if len(active) >= 3:
                valid = [r for r in active if r["outcome_30min"] in ("WIN", "LOSS")]
                wins = sum(1 for r in valid if r["outcome_30min"] == "WIN")
                accuracy = round(wins / max(len(valid), 1) * 100, 1)
                # Also check: when engine scored HIGH (>50% of max), win rate
                high_threshold = info["max"] * 0.5
                high_active = [r for r in active if r[col] >= high_threshold]
                high_valid = [r for r in high_active if r["outcome_30min"] in ("WIN", "LOSS")]
                high_wins = sum(1 for r in high_valid if r["outcome_30min"] == "WIN")
                high_accuracy = round(high_wins / max(len(high_valid), 1) * 100, 1) if high_valid else 0
            else:
                accuracy = overall_30m  # Not enough data, use overall
                high_accuracy = 0
        else:
            accuracy = overall_30m  # No scored rows yet
            high_accuracy = 0

        engines.append({
            "name": engine_name,
            "description": info["description"],
            "currentWeight": current_max,
            "defaultWeight": info["max"],
            "weightPct": round(current_max / TOTAL_MAX * 100, 1),
            "accuracy": accuracy,
            "highSignalAccuracy": high_accuracy,
            "dataPoints": len([r for r in scored_rows if r[col] and r[col] > 0]) if scored_rows else 0,
            "hasRealData": len(scored_rows) >= 10,
        })

    # 7-day rolling trend
    week_cutoff = (ist_now() - timedelta(days=7)).isoformat()
    recent = [r for r in rows if r["timestamp"] > week_cutoff]
    recent_valid = [r for r in recent if r["outcome_30min"] in ("WIN", "LOSS")]
    recent_rate = round(sum(1 for r in recent_valid if r["outcome_30min"] == "WIN") / max(len(recent_valid), 1) * 100, 1)

    older = [r for r in rows if r["timestamp"] <= week_cutoff]
    older_valid = [r for r in older if r["outcome_30min"] in ("WIN", "LOSS")]
    older_rate = round(sum(1 for r in older_valid if r["outcome_30min"] == "WIN") / max(len(older_valid), 1) * 100, 1)

    return {
        "total": total,
        "scoredRows": len(scored_rows),
        "days": days,
        "overall": {
            "15min": win_rate("outcome_15min"),
            "30min": win_rate("outcome_30min"),
            "1hr": win_rate("outcome_1hr"),
        },
        "byProbability": band_accuracy,
        "byDirection": {
            "CE": dir_rate(ce_rows),
            "PE": dir_rate(pe_rows),
        },
        "trend": {
            "recent7d": recent_rate,
            "older": older_rate,
            "improving": recent_rate > older_rate,
        },
        "engines": engines,
    }


# ══════════════════════════════════════════════════════════════════════════
# 2. OPTIMAL WEIGHTS — Bayesian-style per-engine adjustment
# ══════════════════════════════════════════════════════════════════════════

def get_optimal_weights():
    """Calculate recommended weights using REAL per-engine accuracy.

    Bayesian update: new_weight = default_weight × (engine_accuracy / avg_accuracy)
    Capped at ±15% per cycle for stability. Normalized to keep total ~140.
    """
    accuracy = get_engine_accuracy(days=30)
    if "error" in accuracy and not accuracy.get("engines"):
        return {"error": accuracy["error"], "current": {}, "recommended": {}}

    current = load_weights()
    engine_data = accuracy.get("engines", [])

    # Check if we have real per-engine data
    has_real = any(e.get("hasRealData") for e in engine_data)

    if not has_real or not engine_data:
        # Not enough per-engine data — return current weights as recommended
        return {
            "current": {k: current.get(k, v["max"]) for k, v in DEFAULT_WEIGHTS.items()},
            "recommended": {k: current.get(k, v["max"]) for k, v in DEFAULT_WEIGHTS.items()},
            "totalCurrent": sum(current.get(k, v["max"]) for k, v in DEFAULT_WEIGHTS.items()),
            "totalRecommended": sum(current.get(k, v["max"]) for k, v in DEFAULT_WEIGHTS.items()),
            "changes": [],
            "overallAccuracy": accuracy.get("overall", {}).get("30min", {}).get("rate", 0),
            "dataPoints": accuracy.get("total", 0),
            "hasRealData": False,
        }

    # Calculate average accuracy across engines with real data
    real_engines = [e for e in engine_data if e["dataPoints"] >= 3]
    if not real_engines:
        avg_accuracy = 50
    else:
        avg_accuracy = sum(e["accuracy"] for e in real_engines) / len(real_engines)
    avg_accuracy = max(avg_accuracy, 1)  # Prevent division by zero

    recommended = {}
    changes = []

    for eng in engine_data:
        name = eng["name"]
        info = DEFAULT_WEIGHTS[name]
        curr = current.get(name, info["max"])

        if eng["dataPoints"] >= 3:
            # Real Bayesian update
            ratio = eng["accuracy"] / avg_accuracy
            new_weight = round(info["max"] * ratio)

            # Cap at ±15% of default
            max_change = max(1, round(info["max"] * 0.15))
            new_weight = max(info["max"] - max_change, min(info["max"] + max_change, new_weight))
            new_weight = max(1, new_weight)  # Never go below 1
        else:
            # Not enough data — keep current
            new_weight = curr

        recommended[name] = new_weight

        diff = new_weight - curr
        if diff != 0:
            changes.append({
                "engine": name,
                "from": curr,
                "to": new_weight,
                "change": diff,
                "accuracy": eng["accuracy"],
                "dataPoints": eng["dataPoints"],
                "reason": f"Accuracy {eng['accuracy']}% vs avg {avg_accuracy:.0f}%"
            })

    # Normalize to keep total near TOTAL_MAX
    raw_total = sum(recommended.values())
    if raw_total > 0:
        scale = TOTAL_MAX / raw_total
        recommended = {k: max(1, round(v * scale)) for k, v in recommended.items()}

    return {
        "current": {k: current.get(k, v["max"]) for k, v in DEFAULT_WEIGHTS.items()},
        "recommended": recommended,
        "totalCurrent": sum(current.get(k, v["max"]) for k, v in DEFAULT_WEIGHTS.items()),
        "totalRecommended": sum(recommended.values()),
        "changes": changes,
        "overallAccuracy": accuracy.get("overall", {}).get("30min", {}).get("rate", 0),
        "dataPoints": accuracy.get("total", 0),
        "avgAccuracy": round(avg_accuracy, 1),
        "hasRealData": True,
    }


# ══════════════════════════════════════════════════════════════════════════
# 3. HOURLY ANALYSIS — Trading window detection
# ══════════════════════════════════════════════════════════════════════════

def get_hourly_analysis(days=30):
    """Analyze performance by hour — find best/worst trading windows."""
    conn = _bt_conn()
    if not conn:
        return {"error": "No backtest data", "hours": []}

    cutoff = (ist_now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM backtest_log WHERE timestamp > ? AND checked = 1",
        (cutoff,)
    ).fetchall()
    conn.close()

    # Also get trade P&L by hour
    trades_conn = _trades_conn()
    trade_pnl_by_hour = {}
    if trades_conn:
        trades = trades_conn.execute(
            "SELECT * FROM trades WHERE status != 'OPEN' AND entry_time > ?",
            (cutoff,)
        ).fetchall()
        trades_conn.close()
        for t in trades:
            try:
                h = datetime.fromisoformat(t["entry_time"]).hour
                if h not in trade_pnl_by_hour:
                    trade_pnl_by_hour[h] = {"total_pnl": 0, "count": 0}
                trade_pnl_by_hour[h]["total_pnl"] += t["pnl_rupees"] or 0
                trade_pnl_by_hour[h]["count"] += 1
            except Exception:
                pass

    # Build hourly stats
    hourly = {}
    for r in rows:
        try:
            h = datetime.fromisoformat(r["timestamp"]).hour
        except Exception:
            continue

        if h not in hourly:
            hourly[h] = {"wins": 0, "losses": 0, "total": 0, "spot_moves": []}

        hourly[h]["total"] += 1
        if r["outcome_30min"] == "WIN":
            hourly[h]["wins"] += 1
        elif r["outcome_30min"] == "LOSS":
            hourly[h]["losses"] += 1

        spot_entry = r["spot_at_verdict"]
        spot_30m = r["spot_30min"]
        if spot_entry > 0 and spot_30m > 0:
            move_pct = abs(spot_30m - spot_entry) / spot_entry * 100
            hourly[h]["spot_moves"].append(move_pct)

    hours = []
    for h in range(9, 16):
        data = hourly.get(h, {"wins": 0, "losses": 0, "total": 0, "spot_moves": []})
        total = data["wins"] + data["losses"]
        win_rate = round(data["wins"] / max(total, 1) * 100, 1)

        avg_move = sum(data["spot_moves"]) / max(len(data["spot_moves"]), 1)
        if avg_move > 0.3:
            market_type = "BLAST"
        elif avg_move > 0.15:
            market_type = "TRENDING"
        else:
            market_type = "SIDEWAYS"

        pnl_data = trade_pnl_by_hour.get(h, {"total_pnl": 0, "count": 0})
        avg_pnl = round(pnl_data["total_pnl"] / max(pnl_data["count"], 1))

        hours.append({
            "hour": h, "label": f"{h}:00", "trades": total,
            "wins": data["wins"], "losses": data["losses"],
            "winRate": win_rate, "avgPnl": avg_pnl,
            "marketType": market_type, "avgMovePct": round(avg_move, 3),
        })

    valid_hours = [h for h in hours if h["trades"] >= 3]
    best = max(valid_hours, key=lambda x: x["winRate"]) if valid_hours else None
    worst = min(valid_hours, key=lambda x: x["winRate"]) if valid_hours else None

    return {
        "hours": hours, "bestWindow": best, "worstWindow": worst,
        "totalSignals": len(rows), "days": days,
    }


# ══════════════════════════════════════════════════════════════════════════
# 4. PATTERN ANALYSIS — Win/loss condition patterns
# ══════════════════════════════════════════════════════════════════════════

def get_pattern_analysis(days=30):
    """Analyze what conditions lead to wins vs losses."""
    conn = _bt_conn()
    if not conn:
        return {"error": "No backtest data"}

    cutoff = (ist_now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM backtest_log WHERE timestamp > ? AND checked = 1",
        (cutoff,)
    ).fetchall()
    conn.close()

    if not rows:
        return {"error": "No checked data", "patterns": []}

    high_prob = [r for r in rows if (r["verdict_probability"] or 0) >= 80]
    med_prob = [r for r in rows if 70 <= (r["verdict_probability"] or 0) < 80]
    low_prob = [r for r in rows if (r["verdict_probability"] or 0) < 70]

    def calc_stats(subset, label):
        valid = [r for r in subset if r["outcome_30min"] in ("WIN", "LOSS")]
        if not valid:
            return {"label": label, "count": 0, "winRate": 0}
        wins = sum(1 for r in valid if r["outcome_30min"] == "WIN")
        return {"label": label, "count": len(valid),
                "winRate": round(wins / len(valid) * 100, 1),
                "wins": wins, "losses": len(valid) - wins}

    low_vol = [r for r in rows if r["spot_30min"] and abs(r["spot_30min"] - r["spot_at_verdict"]) / max(r["spot_at_verdict"], 1) < 0.001]
    high_vol = [r for r in rows if r["spot_30min"] and abs(r["spot_30min"] - r["spot_at_verdict"]) / max(r["spot_at_verdict"], 1) > 0.003]

    morning = [r for r in rows if 9 <= datetime.fromisoformat(r["timestamp"]).hour < 11]
    midday = [r for r in rows if 11 <= datetime.fromisoformat(r["timestamp"]).hour < 14]
    afternoon = [r for r in rows if 14 <= datetime.fromisoformat(r["timestamp"]).hour < 16]

    # Engine-specific patterns (NEW — using per-engine scores)
    scored_rows = [r for r in rows if _has_engine_scores(r)]
    engine_patterns = []
    if scored_rows:
        for col, engine_name in COL_TO_ENGINE.items():
            active = [r for r in scored_rows if r[col] and r[col] > 0]
            inactive = [r for r in scored_rows if not r[col] or r[col] == 0]
            active_stats = calc_stats(active, f"{engine_name.replace('_', ' ').title()} active")
            inactive_stats = calc_stats(inactive, f"{engine_name.replace('_', ' ').title()} silent")
            if active_stats["count"] >= 3:
                engine_patterns.append(active_stats)
            if inactive_stats["count"] >= 3:
                engine_patterns.append(inactive_stats)

    winning_patterns = []
    losing_patterns = []

    all_patterns = [
        ("High probability (80%+)", high_prob),
        ("Medium probability (70-80%)", med_prob),
        ("Low probability (60-70%)", low_prob),
        ("Morning session (9-11 AM)", morning),
        ("Midday (11 AM - 2 PM)", midday),
        ("Afternoon (2-3:30 PM)", afternoon),
        ("Low volatility moves", low_vol),
        ("High volatility moves", high_vol),
        ("CE (Bullish) trades", [r for r in rows if "CE" in (r["verdict_action"] or "")]),
        ("PE (Bearish) trades", [r for r in rows if "PE" in (r["verdict_action"] or "")]),
    ]

    for label, subset in all_patterns:
        stats = calc_stats(subset, label)
        if stats["count"] >= 3:
            if stats["winRate"] >= 60:
                winning_patterns.append(stats)
            elif stats["winRate"] < 45:
                losing_patterns.append(stats)

    # Add engine patterns
    for ep in engine_patterns:
        if ep["count"] >= 3:
            if ep["winRate"] >= 60:
                winning_patterns.append(ep)
            elif ep["winRate"] < 45:
                losing_patterns.append(ep)

    winning_patterns.sort(key=lambda x: x["winRate"], reverse=True)
    losing_patterns.sort(key=lambda x: x["winRate"])

    return {
        "probabilityBands": {
            "high": calc_stats(high_prob, "80%+ Probability"),
            "medium": calc_stats(med_prob, "70-80% Probability"),
            "low": calc_stats(low_prob, "60-70% Probability"),
        },
        "sessions": {
            "morning": calc_stats(morning, "Morning (9-11 AM)"),
            "midday": calc_stats(midday, "Midday (11 AM-2 PM)"),
            "afternoon": calc_stats(afternoon, "Afternoon (2-3:30 PM)"),
        },
        "winningPatterns": winning_patterns[:7],
        "losingPatterns": losing_patterns[:7],
        "enginePatterns": engine_patterns,
        "days": days,
    }


# ══════════════════════════════════════════════════════════════════════════
# 5. WEEKLY TRAINING REPORT
# ══════════════════════════════════════════════════════════════════════════

def get_weekly_report():
    """Generate comprehensive weekly training report."""
    now = ist_now()
    week_start = now - timedelta(days=7)
    prev_week_start = now - timedelta(days=14)

    this_week = get_engine_accuracy(days=7)

    conn = _bt_conn()
    prev_week_data = {"total": 0, "rate": 0}
    if conn:
        prev_rows = conn.execute(
            "SELECT * FROM backtest_log WHERE timestamp BETWEEN ? AND ? AND checked = 1",
            (prev_week_start.isoformat(), week_start.isoformat())
        ).fetchall()
        conn.close()
        if prev_rows:
            valid = [r for r in prev_rows if r["outcome_30min"] in ("WIN", "LOSS")]
            wins = sum(1 for r in valid if r["outcome_30min"] == "WIN")
            prev_week_data = {"total": len(prev_rows),
                              "rate": round(wins / max(len(valid), 1) * 100, 1)}

    trades_conn = _trades_conn()
    trade_stats = {"total": 0, "pnl": 0, "wins": 0, "losses": 0}
    if trades_conn:
        trades = trades_conn.execute(
            "SELECT * FROM trades WHERE status != 'OPEN' AND exit_time > ?",
            (week_start.isoformat(),)
        ).fetchall()
        trades_conn.close()
        trade_stats["total"] = len(trades)
        trade_stats["pnl"] = sum(t["pnl_rupees"] or 0 for t in trades)
        trade_stats["wins"] = sum(1 for t in trades if (t["pnl_rupees"] or 0) > 0)
        trade_stats["losses"] = sum(1 for t in trades if (t["pnl_rupees"] or 0) <= 0)

    hourly = get_hourly_analysis(days=7)
    weights = get_optimal_weights()

    insights = []
    current_rate = this_week.get("overall", {}).get("30min", {}).get("rate", 0)
    prev_rate = prev_week_data["rate"]

    if current_rate > prev_rate + 5:
        insights.append(f"Win rate improved: {prev_rate}% -> {current_rate}% (+{current_rate - prev_rate:.1f}%)")
    elif current_rate < prev_rate - 5:
        insights.append(f"Win rate declined: {prev_rate}% -> {current_rate}% ({current_rate - prev_rate:.1f}%)")
    else:
        insights.append(f"Win rate stable at {current_rate}%")

    ce_rate = this_week.get("byDirection", {}).get("CE", {}).get("rate", 0)
    pe_rate = this_week.get("byDirection", {}).get("PE", {}).get("rate", 0)
    if ce_rate > pe_rate + 10:
        insights.append(f"CE trades outperforming PE: {ce_rate}% vs {pe_rate}%")
    elif pe_rate > ce_rate + 10:
        insights.append(f"PE trades outperforming CE: {pe_rate}% vs {ce_rate}%")

    best = hourly.get("bestWindow")
    worst = hourly.get("worstWindow")
    if best:
        insights.append(f"Best trading hour: {best['label']} ({best['winRate']}% win rate)")
    if worst and worst["winRate"] < 45:
        insights.append(f"Avoid {worst['label']} — only {worst['winRate']}% win rate")

    if trade_stats["pnl"] > 0:
        insights.append(f"P&L this week: +Rs.{trade_stats['pnl']:,.0f}")
    elif trade_stats["pnl"] < 0:
        insights.append(f"P&L this week: Rs.{trade_stats['pnl']:,.0f} — tighten risk")

    # Engine-specific insights
    for eng in this_week.get("engines", []):
        if eng.get("hasRealData") and eng["dataPoints"] >= 5:
            if eng["accuracy"] >= 70:
                insights.append(f"{eng['name'].replace('_',' ').title()} is strong: {eng['accuracy']}% accuracy")
            elif eng["accuracy"] < 40:
                insights.append(f"{eng['name'].replace('_',' ').title()} is weak: {eng['accuracy']}% — reduce weight")

    return {
        "period": {"from": week_start.strftime("%Y-%m-%d"), "to": now.strftime("%Y-%m-%d")},
        "summary": {
            "totalVerdicts": this_week.get("total", 0),
            "winRate30m": current_rate,
            "prevWeekRate": prev_rate,
            "improvement": round(current_rate - prev_rate, 1),
            "improving": current_rate > prev_rate,
        },
        "trades": trade_stats,
        "accuracy": this_week.get("overall", {}),
        "byDirection": this_week.get("byDirection", {}),
        "byProbability": this_week.get("byProbability", {}),
        "hourly": hourly,
        "weights": weights,
        "insights": insights,
        "generatedAt": now.strftime("%Y-%m-%d %H:%M IST"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 6. WEIGHT MANAGEMENT — Load, save, apply
# ══════════════════════════════════════════════════════════════════════════

def load_weights():
    try:
        if WEIGHTS_FILE.exists():
            data = json.loads(WEIGHTS_FILE.read_text())
            return {k: v for k, v in data.items() if k in DEFAULT_WEIGHTS}
    except Exception:
        pass
    return {k: v["max"] for k, v in DEFAULT_WEIGHTS.items()}


def save_weights(weights: dict, auto=False):
    data = {
        **weights,
        "last_updated": ist_now().strftime("%Y-%m-%d %H:%M IST"),
        "auto_adjusted": auto,
    }
    WEIGHTS_FILE.write_text(json.dumps(data, indent=2))
    return data


def apply_recommended_weights():
    optimal = get_optimal_weights()
    if "error" in optimal and not optimal.get("recommended"):
        return {"error": optimal["error"]}
    recommended = optimal["recommended"]
    saved = save_weights(recommended, auto=False)
    return {"applied": recommended, "totalWeight": sum(recommended.values()),
            "savedAt": saved.get("last_updated")}


def reset_weights():
    defaults = {k: v["max"] for k, v in DEFAULT_WEIGHTS.items()}
    saved = save_weights(defaults, auto=False)
    return {"reset": defaults, "totalWeight": TOTAL_MAX,
            "savedAt": saved.get("last_updated")}


def get_weights_info():
    current = load_weights()
    optimal = get_optimal_weights()
    acc = get_engine_accuracy(days=30)
    engine_acc_map = {e["name"]: e for e in acc.get("engines", [])}

    engines = []
    for name, info in DEFAULT_WEIGHTS.items():
        curr = current.get(name, info["max"])
        rec = optimal.get("recommended", {}).get(name, info["max"])
        eng_data = engine_acc_map.get(name, {})
        engines.append({
            "name": name,
            "description": info["description"],
            "default": info["max"],
            "current": curr,
            "recommended": rec,
            "diff": rec - curr,
            "accuracy": eng_data.get("accuracy", 0),
            "dataPoints": eng_data.get("dataPoints", 0),
            "hasRealData": eng_data.get("hasRealData", False),
        })

    # Load last updated info from weights file
    last_updated = None
    auto_adjusted = False
    try:
        if WEIGHTS_FILE.exists():
            wdata = json.loads(WEIGHTS_FILE.read_text())
            last_updated = wdata.get("last_updated")
            auto_adjusted = wdata.get("auto_adjusted", False)
    except Exception:
        pass

    return {
        "engines": engines,
        "totalDefault": TOTAL_MAX,
        "totalCurrent": sum(current.get(k, v["max"]) for k, v in DEFAULT_WEIGHTS.items()),
        "totalRecommended": sum(optimal.get("recommended", {}).values()) if optimal.get("recommended") else TOTAL_MAX,
        "lastUpdated": last_updated,
        "autoAdjusted": auto_adjusted,
        "hasRealData": optimal.get("hasRealData", False),
    }


# ══════════════════════════════════════════════════════════════════════════
# 7. TRADING WINDOWS
# ══════════════════════════════════════════════════════════════════════════

def get_trading_windows(days=30):
    hourly = get_hourly_analysis(days)
    hours = hourly.get("hours", [])

    blast_hours = [h for h in hours if h["marketType"] == "BLAST"]
    trending_hours = [h for h in hours if h["marketType"] == "TRENDING"]
    sideways_hours = [h for h in hours if h["marketType"] == "SIDEWAYS"]

    profitable = [h for h in hours if h["winRate"] >= 55 and h["trades"] >= 3]
    avoid = [h for h in hours if h["winRate"] < 45 and h["trades"] >= 3]

    recs = []
    for h in hours:
        if h["trades"] < 3:
            continue
        if h["winRate"] >= 70:
            recs.append(f"AGGRESSIVE at {h['label']} — {h['winRate']}% win rate, {h['marketType']}")
        elif h["winRate"] >= 55:
            recs.append(f"NORMAL at {h['label']} — {h['winRate']}% win rate")
        elif h["winRate"] < 40:
            recs.append(f"AVOID {h['label']} — only {h['winRate']}% win rate")

    return {
        "windows": hours,
        "bestWindow": hourly.get("bestWindow"),
        "worstWindow": hourly.get("worstWindow"),
        "profitable": profitable, "avoid": avoid,
        "regimes": {
            "blast": [h["label"] for h in blast_hours],
            "trending": [h["label"] for h in trending_hours],
            "sideways": [h["label"] for h in sideways_hours],
        },
        "recommendation": recs, "days": days,
    }


# ══════════════════════════════════════════════════════════════════════════
# 8. AUTO-TRAIN — Self-learning weight adjustment
# ══════════════════════════════════════════════════════════════════════════

def run_auto_train():
    """Execute one training cycle:
    1. Calculate per-engine accuracy from last 30 days
    2. Compute optimal weights via Bayesian update
    3. Apply weights to engine_weights.json
    4. Log training run to training_log table
    """
    now = ist_now()

    # Get current accuracy before adjustment
    accuracy_before = get_engine_accuracy(days=30)
    overall_before = accuracy_before.get("overall", {}).get("30min", {}).get("rate", 0)
    data_points = accuracy_before.get("total", 0)

    if data_points < 10:
        return {
            "status": "skipped",
            "notes": f"Not enough data ({data_points} points, need 10+)",
            "timestamp": now.isoformat(),
        }

    # Get old weights
    old_weights = load_weights()

    # Calculate optimal weights
    optimal = get_optimal_weights()
    if not optimal.get("hasRealData"):
        return {
            "status": "skipped",
            "notes": "No per-engine score data yet. Need more verdicts with engine breakdowns.",
            "timestamp": now.isoformat(),
        }

    new_weights = optimal.get("recommended", {})
    if not new_weights:
        return {"status": "skipped", "notes": "No recommendations available",
                "timestamp": now.isoformat()}

    # Apply new weights
    save_weights(new_weights, auto=True)

    # Log training run
    notes = f"Auto-train: {data_points} data points, accuracy {overall_before}%"
    changes = optimal.get("changes", [])
    if changes:
        top_change = max(changes, key=lambda c: abs(c["change"]))
        notes += f", biggest change: {top_change['engine']} {top_change['from']}->{top_change['to']}"

    _log_training(now, overall_before, overall_before, old_weights, new_weights, data_points, notes)

    return {
        "status": "completed",
        "timestamp": now.isoformat(),
        "accuracyBefore": overall_before,
        "dataPoints": data_points,
        "oldWeights": old_weights,
        "newWeights": new_weights,
        "changes": changes,
        "notes": notes,
    }


def _log_training(timestamp, accuracy_before, accuracy_after, old_weights, new_weights, data_points, notes):
    """Log a training run to the training_log table."""
    conn = _bt_conn()
    if not conn:
        return
    try:
        conn.execute("""
            INSERT INTO training_log (timestamp, accuracy_before, accuracy_after,
                                      old_weights, new_weights, data_points, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp.isoformat(), accuracy_before, accuracy_after,
            json.dumps(old_weights), json.dumps(new_weights), data_points, notes
        ))
        conn.commit()
    except Exception as e:
        print(f"[ML] Failed to log training: {e}")
    finally:
        conn.close()


def get_training_history(limit=20):
    """Get past training runs from training_log."""
    conn = _bt_conn()
    if not conn:
        return {"runs": [], "total": 0}

    try:
        rows = conn.execute(
            "SELECT * FROM training_log ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()

        total = conn.execute("SELECT COUNT(*) FROM training_log").fetchone()[0]
        conn.close()

        runs = []
        for r in rows:
            runs.append({
                "id": r["id"],
                "timestamp": r["timestamp"],
                "accuracyBefore": r["accuracy_before"],
                "accuracyAfter": r["accuracy_after"],
                "oldWeights": json.loads(r["old_weights"]) if r["old_weights"] else {},
                "newWeights": json.loads(r["new_weights"]) if r["new_weights"] else {},
                "dataPoints": r["data_points"],
                "notes": r["notes"],
            })

        return {"runs": runs, "total": total}
    except Exception as e:
        conn.close()
        return {"runs": [], "total": 0, "error": str(e)}


def get_last_training_time():
    """Get timestamp of last training run."""
    conn = _bt_conn()
    if not conn:
        return None
    try:
        row = conn.execute(
            "SELECT timestamp FROM training_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return datetime.fromisoformat(row["timestamp"]).replace(tzinfo=IST)
        return None
    except Exception:
        conn.close()
        return None


def get_auto_train_status():
    """Get auto-train status for the frontend."""
    last_train = get_last_training_time()
    now = ist_now()

    # Next scheduled: Sunday 8 PM IST
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 20:
        days_until_sunday = 7
    next_train = (now + timedelta(days=days_until_sunday)).replace(hour=20, minute=0, second=0)

    history = get_training_history(limit=5)

    return {
        "lastTrain": last_train.isoformat() if last_train else None,
        "lastTrainAgo": f"{(now - last_train).days}d ago" if last_train else "Never",
        "nextTrain": next_train.strftime("%Y-%m-%d %H:%M IST"),
        "nextTrainIn": f"{(next_train - now).days}d {(next_train - now).seconds // 3600}h",
        "schedule": "Every Sunday 8:00 PM IST",
        "recentRuns": history["runs"][:5],
        "totalRuns": history["total"],
    }
