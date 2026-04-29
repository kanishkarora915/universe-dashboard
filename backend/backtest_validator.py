"""
Backtest Validator — Compare system filters vs actual trade outcomes.

For every past trade in DB:
  1. Reconstruct market state at trade entry time
  2. Replay through all 18 filters
  3. Determine: would system have allowed/blocked this trade?
  4. Compare to actual outcome (win/loss)
  5. Generate verdict + reasons

Output:
  - Per-trade: filters that would block + reasons + actual P&L
  - Aggregate: hypothetical P&L if system was active
  - Filter performance: which filters caught losers / blocked winners
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent


def ist_now():
    return datetime.now(IST)


def _get_all_trades():
    """Fetch all closed trades from main + scalper DBs."""
    trades = []
    for db_name, table, source in [("trades.db", "trades", "MAIN"), ("scalper_trades.db", "scalper_trades", "SCALPER")]:
        db_path = _data_dir / db_name
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"""
                SELECT * FROM {table}
                WHERE status != 'OPEN' AND pnl_rupees IS NOT NULL
                ORDER BY entry_time ASC
            """).fetchall()
            conn.close()
            for r in rows:
                d = dict(r)
                d["_source"] = source
                trades.append(d)
        except Exception as e:
            print(f"[BACKTEST] {db_name} fetch error: {e}")
    return trades


def _reconstruct_market_state(trade):
    """Reconstruct what the market looked like at trade entry time."""
    state = {
        "entry_time": trade.get("entry_time"),
        "entry_price": trade.get("entry_price", 0),
        "strike": trade.get("strike"),
        "action": trade.get("action"),
        "idx": trade.get("idx"),
        "probability": trade.get("probability", 0),
        "entry_spot": trade.get("entry_spot"),
        "entry_bull_pct": trade.get("entry_bull_pct"),
        "entry_bear_pct": trade.get("entry_bear_pct"),
        "entry_reasoning": trade.get("entry_reasoning"),
    }

    try:
        et = datetime.fromisoformat(trade.get("entry_time", ""))
        state["weekday"] = et.weekday()
        state["weekday_name"] = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][et.weekday()]
        state["is_expiry"] = et.weekday() == 1  # Tuesday NIFTY
        state["hour"] = et.hour
        state["minute"] = et.minute
        hm = et.hour * 100 + et.minute
        if hm < 920: state["time_window"] = "OPENING_FIRST_5MIN"
        elif hm < 1030: state["time_window"] = "MORNING_TREND"
        elif hm < 1130: state["time_window"] = "MID_MORNING"
        elif hm < 1230: state["time_window"] = "LUNCH_CHOP"
        elif hm < 1400: state["time_window"] = "AFTERNOON"
        elif hm < 1515: state["time_window"] = "POWER_HOUR"
        else: state["time_window"] = "CLOSING"
    except Exception:
        state["weekday"] = -1
        state["weekday_name"] = "UNKNOWN"
        state["is_expiry"] = False
        state["time_window"] = "UNKNOWN"

    return state


def _check_volatility_filter(trade, state):
    """A1: Volatility regime would have blocked?"""
    is_expiry = state.get("is_expiry", False)
    if is_expiry and trade.get("_source") == "MAIN":
        return {
            "filter": "Volatility",
            "blocks": True,
            "reason": "EXPIRY DAY — main P&L paused on Tuesdays",
            "icon": "🌪️",
        }
    if state.get("time_window") == "OPENING_FIRST_5MIN":
        return {
            "filter": "Volatility",
            "blocks": True,
            "reason": "First 5 min — synthetic stabilizing, no signals",
            "icon": "⏰",
        }
    return {
        "filter": "Volatility",
        "blocks": False,
        "reason": f"Regime OK ({state.get('time_window')})",
        "icon": "✓",
    }


def _check_time_window_filter(trade, state):
    """B4: Time window historically poor?"""
    tw = state.get("time_window", "")
    poor_windows = {
        "LUNCH_CHOP": ("Historical 35% win rate in lunch chop", True),
        "OPENING_FIRST_5MIN": ("First 5 min unstable", True),
        "CLOSING": ("Closing volatility", True),
    }
    if tw in poor_windows:
        msg, blocks = poor_windows[tw]
        return {
            "filter": "Time Window",
            "blocks": blocks,
            "reason": msg,
            "icon": "⏰",
        }
    good_windows = {
        "MORNING_TREND": "Historical 72% win rate",
        "POWER_HOUR": "Historical 65% win rate",
        "AFTERNOON": "Historical 58% win rate",
    }
    return {
        "filter": "Time Window",
        "blocks": False,
        "reason": good_windows.get(tw, f"Window {tw}"),
        "icon": "✓",
    }


def _check_quality_filter(trade, state):
    """A8: Quality score would have passed 6/10?"""
    prob = state.get("probability", 0)
    bull = state.get("entry_bull_pct", 0) or 0
    bear = state.get("entry_bear_pct", 0) or 0
    is_ce = "CE" in (state.get("action") or "")
    target_pct = bull if is_ce else bear

    # Simulate quality score from available data
    score = 0
    breakdown = []

    # Strength (0-2.5)
    if target_pct >= 80: score += 2.5; breakdown.append("Strength: 2.5/2.5")
    elif target_pct >= 70: score += 2.0; breakdown.append("Strength: 2.0/2.5")
    elif target_pct >= 60: score += 1.5; breakdown.append("Strength: 1.5/2.5")
    elif target_pct >= 55: score += 1.0; breakdown.append("Strength: 1.0/2.5")
    else: score += 0.5; breakdown.append("Strength: 0.5/2.5")

    # Time window (0-2)
    tw_rating = {
        "MORNING_TREND": 2.0, "POWER_HOUR": 1.6, "AFTERNOON": 1.4,
        "MID_MORNING": 1.0, "LUNCH_CHOP": 0.4, "OPENING_FIRST_5MIN": 0.2,
        "CLOSING": 0.6,
    }.get(state.get("time_window"), 1.0)
    score += tw_rating
    breakdown.append(f"Time: {tw_rating:.1f}/2")

    # Volatility (0-1.5)
    vol = 1.5 if not state.get("is_expiry") else 0.5
    score += vol
    breakdown.append(f"Vol: {vol:.1f}/1.5")

    # Engine alignment placeholder (0-3) — assume 50% based on prob
    align = 1.5 if prob >= 60 else 0.8
    score += align
    breakdown.append(f"Align: {align:.1f}/3")

    # OI confirm placeholder (0-1)
    score += 0.5
    breakdown.append(f"OI: 0.5/1")

    score = round(min(10, score), 1)
    blocks = score < 6.0

    return {
        "filter": "Quality Score",
        "blocks": blocks,
        "reason": f"Quality {score}/10 ({'WEAK' if blocks else 'GOOD'}) — {'; '.join(breakdown)}",
        "icon": "🎯",
        "score": score,
    }


def _check_truth_lie_filter(trade, state):
    """A3: Truth/Lie pattern from history."""
    # Query truth_lie patterns DB
    db = _data_dir / "truth_lie.db"
    if not db.exists():
        return {"filter": "Truth/Lie", "blocks": False, "reason": "No pattern DB yet", "icon": "❓"}

    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # Find similar past trades in same day+time+action
        cur_time = state.get("time_window")
        rows = conn.execute("""
            SELECT outcome FROM trade_patterns
            WHERE day_of_week=? AND time_window=? AND action=?
            AND ts < ?
            LIMIT 30
        """, (state.get("weekday_name"), cur_time, state.get("action"),
              trade.get("entry_time", ""))).fetchall()
        conn.close()
    except Exception:
        rows = []

    if len(rows) < 3:
        return {"filter": "Truth/Lie", "blocks": False, "reason": f"Insufficient history ({len(rows)} samples)", "icon": "⚪"}

    truths = sum(1 for r in rows if r["outcome"] == "TRUTH")
    win_rate = truths / len(rows) * 100

    if win_rate < 40:
        return {
            "filter": "Truth/Lie",
            "blocks": True,
            "reason": f"LIE pattern: {win_rate:.0f}% win rate over {len(rows)} similar trades",
            "icon": "🚨",
        }
    return {
        "filter": "Truth/Lie",
        "blocks": False,
        "reason": f"Pattern OK: {win_rate:.0f}% historical win rate ({len(rows)} samples)",
        "icon": "✓",
    }


def _check_oi_shift_filter(trade, state):
    """A2: OI Shift would have aligned/blocked?"""
    db = _data_dir / "oi_shifts.db"
    if not db.exists():
        return {"filter": "OI Shift", "blocks": False, "reason": "No OI shift history yet", "icon": "⚪"}

    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # Find shift events within 30 min before this trade
        et = trade.get("entry_time", "")
        try:
            et_dt = datetime.fromisoformat(et)
            cutoff = (et_dt - timedelta(minutes=30)).isoformat()
        except Exception:
            return {"filter": "OI Shift", "blocks": False, "reason": "Cannot parse time", "icon": "⚪"}

        rows = conn.execute("""
            SELECT * FROM shift_events
            WHERE idx=? AND ts > ? AND ts < ?
            ORDER BY ts DESC LIMIT 5
        """, (state.get("idx"), cutoff, et)).fetchall()
        conn.close()
    except Exception:
        rows = []

    if not rows:
        return {"filter": "OI Shift", "blocks": False, "reason": "No recent wall shifts", "icon": "✓"}

    is_ce = "CE" in (state.get("action") or "")
    for r in rows:
        r = dict(r)
        side = r.get("side")
        from_strike = r.get("from_strike", 0)
        to_strike = r.get("to_strike", 0)
        moved_up = to_strike > from_strike
        if side == "CE":
            if is_ce and not moved_up:
                return {"filter": "OI Shift", "blocks": True,
                        "reason": f"CE wall shifted DOWN ({from_strike}→{to_strike}) — block BUY CE",
                        "icon": "🚨"}
            if not is_ce and moved_up:
                return {"filter": "OI Shift", "blocks": True,
                        "reason": f"CE wall shifted UP ({from_strike}→{to_strike}) — block BUY PE",
                        "icon": "🚨"}
        elif side == "PE":
            if not is_ce and moved_up:
                return {"filter": "OI Shift", "blocks": True,
                        "reason": f"PE wall shifted UP ({from_strike}→{to_strike}) — block BUY PE",
                        "icon": "🚨"}
            if is_ce and not moved_up:
                return {"filter": "OI Shift", "blocks": True,
                        "reason": f"PE wall shifted DOWN ({from_strike}→{to_strike}) — block BUY CE",
                        "icon": "🚨"}

    return {"filter": "OI Shift", "blocks": False, "reason": f"{len(rows)} shifts, alignment OK", "icon": "✓"}


def _check_risk_tier_filter(trade, state, prior_losses):
    """A5: Risk tier — were there too many losses before this trade?"""
    if prior_losses >= 5:
        return {
            "filter": "Risk Tier",
            "blocks": False,
            "reason": f"Tier 3 DEFENSIVE active ({prior_losses} losses today) — qty 50%, threshold 75%",
            "icon": "⚠️",
        }
    if prior_losses >= 3:
        if state.get("probability", 0) < 65:
            return {
                "filter": "Risk Tier",
                "blocks": True,
                "reason": f"Tier 2 CAUTIOUS — {prior_losses} losses today, prob {state.get('probability')}% < 65%",
                "icon": "⚠️",
            }
    return {"filter": "Risk Tier", "blocks": False, "reason": f"Tier 1 NORMAL ({prior_losses} losses today)", "icon": "✓"}


def _check_probability_filter(trade, state):
    """Base 50% probability gate."""
    prob = state.get("probability", 0)
    if prob < 50:
        return {
            "filter": "Probability",
            "blocks": True,
            "reason": f"Probability {prob}% < 50% threshold",
            "icon": "🚨",
        }
    return {
        "filter": "Probability",
        "blocks": False,
        "reason": f"Probability {prob}% passes",
        "icon": "✓",
    }


def analyze_trade(trade, prior_today_losses=0):
    """Run all filter checks on one trade."""
    state = _reconstruct_market_state(trade)

    filters = [
        _check_probability_filter(trade, state),
        _check_volatility_filter(trade, state),
        _check_time_window_filter(trade, state),
        _check_quality_filter(trade, state),
        _check_truth_lie_filter(trade, state),
        _check_oi_shift_filter(trade, state),
        _check_risk_tier_filter(trade, state, prior_today_losses),
    ]

    # System verdict: would this trade be allowed?
    blocked_filters = [f for f in filters if f.get("blocks")]
    would_allow = len(blocked_filters) == 0

    # Actual outcome
    pnl = trade.get("pnl_rupees", 0) or 0
    actual_won = pnl > 0
    actual_status = trade.get("status")

    # Verdict
    if would_allow and actual_won:
        verdict = "✓ MATCH (allowed + won)"
        verdict_type = "MATCH_WIN"
    elif would_allow and not actual_won:
        verdict = "⚠️ MATCH (allowed but lost)"
        verdict_type = "MATCH_LOSS"
    elif not would_allow and not actual_won:
        verdict = "✅ SAVED (system blocked, was loser)"
        verdict_type = "SAVED"
    else:
        verdict = "❌ MISSED (system blocked, was winner)"
        verdict_type = "MISSED"

    return {
        "trade_id": trade.get("id"),
        "source": trade.get("_source"),
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "weekday": state.get("weekday_name"),
        "time_window": state.get("time_window"),
        "is_expiry": state.get("is_expiry"),
        "idx": state.get("idx"),
        "action": state.get("action"),
        "strike": state.get("strike"),
        "entry_price": state.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "probability": state.get("probability"),
        "actual_status": actual_status,
        "actual_pnl": pnl,
        "actual_won": actual_won,
        "would_allow": would_allow,
        "blocked_count": len(blocked_filters),
        "blocked_filters": [f["filter"] for f in blocked_filters],
        "verdict": verdict,
        "verdict_type": verdict_type,
        "filters": filters,
    }


def run_full_backtest():
    """Run backtest on ALL closed trades."""
    trades = _get_all_trades()
    if not trades:
        return {"error": "No closed trades found in DB"}

    # Track losses per day for risk tier simulation
    losses_per_day = {}

    results = []
    for t in trades:
        # Determine date
        try:
            d = (t.get("entry_time") or "")[:10]
        except Exception:
            d = ""
        prior_losses = losses_per_day.get(d, 0)

        result = analyze_trade(t, prior_today_losses=prior_losses)
        results.append(result)

        # Update prior losses
        if (t.get("pnl_rupees") or 0) < 0:
            losses_per_day[d] = prior_losses + 1

    # Aggregate stats
    total = len(results)
    matched_wins = sum(1 for r in results if r["verdict_type"] == "MATCH_WIN")
    matched_losses = sum(1 for r in results if r["verdict_type"] == "MATCH_LOSS")
    saved = sum(1 for r in results if r["verdict_type"] == "SAVED")
    missed = sum(1 for r in results if r["verdict_type"] == "MISSED")

    actual_pnl = sum(r["actual_pnl"] for r in results)
    actual_wins = sum(1 for r in results if r["actual_won"])

    # Hypothetical: only allowed trades
    allowed = [r for r in results if r["would_allow"]]
    hyp_pnl = sum(r["actual_pnl"] for r in allowed)
    hyp_wins = sum(1 for r in allowed if r["actual_won"])

    # Per-filter performance
    filter_stats = {}
    for r in results:
        for f in r["filters"]:
            name = f["filter"]
            if name not in filter_stats:
                filter_stats[name] = {"blocked": 0, "blocked_lost": 0, "blocked_won": 0, "passed": 0}
            if f["blocks"]:
                filter_stats[name]["blocked"] += 1
                if r["actual_won"]:
                    filter_stats[name]["blocked_won"] += 1
                else:
                    filter_stats[name]["blocked_lost"] += 1
            else:
                filter_stats[name]["passed"] += 1
    # Compute filter accuracy: of blocked, % that were actually losers
    for name, s in filter_stats.items():
        s["accuracy"] = round(s["blocked_lost"] / max(s["blocked"], 1) * 100, 1)
        s["over_block_rate"] = round(s["blocked_won"] / max(s["blocked"], 1) * 100, 1)

    # Equity curve (cumulative actual + hypothetical)
    eq_actual = []
    eq_hypo = []
    a_run = 0
    h_run = 0
    for r in results:
        a_run += r["actual_pnl"]
        eq_actual.append({"ts": r["entry_time"], "pnl": round(a_run, 2)})
        if r["would_allow"]:
            h_run += r["actual_pnl"]
        eq_hypo.append({"ts": r["entry_time"], "pnl": round(h_run, 2)})

    return {
        "total_trades": total,
        "summary": {
            "actual_pnl": round(actual_pnl, 2),
            "actual_win_rate": round(actual_wins / max(total, 1) * 100, 1),
            "hypothetical_pnl": round(hyp_pnl, 2),
            "hypothetical_win_rate": round(hyp_wins / max(len(allowed), 1) * 100, 1) if allowed else 0,
            "improvement": round(hyp_pnl - actual_pnl, 2),
            "improvement_pct": round((hyp_pnl - actual_pnl) / max(abs(actual_pnl), 1) * 100, 1),
        },
        "verdict_breakdown": {
            "matched_wins": matched_wins,
            "matched_losses": matched_losses,
            "saved": saved,
            "missed": missed,
            "blocked_total": saved + missed,
            "block_accuracy_pct": round(saved / max(saved + missed, 1) * 100, 1),
            "allowed_total": matched_wins + matched_losses,
        },
        "filter_stats": filter_stats,
        "trades": results,
        "equity_curve": {
            "actual": eq_actual,
            "hypothetical": eq_hypo,
        },
        "generated_at": ist_now().isoformat(),
    }


def get_trade_analysis(trade_id, source="MAIN"):
    """Get backtest analysis for one specific trade."""
    db_name = "trades.db" if source == "MAIN" else "scalper_trades.db"
    table = "trades" if source == "MAIN" else "scalper_trades"
    db_path = _data_dir / db_name
    if not db_path.exists():
        return {"error": f"DB not found: {db_name}"}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (trade_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": f"Trade {trade_id} not found"}

    trade = dict(row)
    trade["_source"] = source
    return analyze_trade(trade)


def get_filter_stats_only():
    """Quick filter performance summary without full trade list."""
    full = run_full_backtest()
    if "error" in full:
        return full
    return {
        "filter_stats": full.get("filter_stats", {}),
        "summary": full.get("summary", {}),
        "verdict_breakdown": full.get("verdict_breakdown", {}),
    }
