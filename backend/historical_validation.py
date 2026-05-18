"""
Historical Validation — Replay past verdicts to check if system would have been profitable.
Uses backtest_log data (verdict + outcome) to simulate trades with current rules.
Answers: "If this system traded last 30 days, would it have made money?"
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import pytz

IST = pytz.timezone("Asia/Kolkata")

_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
BACKTEST_DB = _data_dir / "backtest.db"
TRADES_DB = _data_dir / "trades.db"


def ist_now():
    return datetime.now(IST)


def run_validation(days=30, capital=1000000):
    """Simulate trading on historical verdict data.

    Replays every logged verdict through current entry rules and simulates:
    - Would we have entered this trade?
    - What would the SL/T1/T2 be?
    - Based on actual spot movement (15min/30min/1hr), did we win or lose?
    - Running P&L calculation

    Returns full simulation report.
    """
    if not BACKTEST_DB.exists():
        return {"error": "No backtest data available", "trades": []}

    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.row_factory = sqlite3.Row
    cutoff = (ist_now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM backtest_log WHERE timestamp > ? AND checked = 1 ORDER BY timestamp ASC",
        (cutoff,)
    ).fetchall()
    conn.close()

    if not rows:
        return {"error": f"No checked verdicts in last {days} days", "trades": []}

    # Simulation state
    running_capital = capital
    total_trades = 0
    wins = 0
    losses = 0
    total_pnl = 0
    max_drawdown = 0
    peak_capital = capital
    sim_trades = []
    daily_pnl = defaultdict(float)
    hourly_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0})
    consecutive_losses = 0
    max_consecutive_losses = 0
    last_loss_time = None

    for row in rows:
        r = dict(row)
        prob = r["verdict_probability"]
        action = r["verdict_action"]
        spot_entry = r["spot_at_verdict"]
        outcome_30m = r.get("outcome_30min", "PENDING")
        spot_30m = r.get("spot_30min", 0)

        if outcome_30m == "PENDING" or spot_entry <= 0:
            continue

        ts = datetime.fromisoformat(r["timestamp"])
        hour = ts.hour
        date_str = ts.strftime("%Y-%m-%d")

        # Skip weekends
        if ts.weekday() >= 5:
            continue

        # ── SIMULATE ENTRY DECISION ──
        spread = abs(prob - (100 - prob))  # Approximate spread

        # Apply current entry rules (practical, not over-strict)
        if prob < 62:
            continue
        if spread < 20:
            continue

        # Smart re-entry: if last trade was loss within 5 min, skip unless stronger
        if last_loss_time:
            time_since = (ts - last_loss_time).total_seconds()
            if time_since < 600:  # 10 min cooldown for same-strength signals
                continue

        # After 5% daily loss, need 70%+
        today_loss = sum(t["pnl"] for t in sim_trades if t["date"] == date_str and t["pnl"] < 0)
        if today_loss < -(running_capital * 0.05) and prob < 70:
            continue

        # ── SIMULATE TRADE ──
        # Approximate option entry = ~2% of spot (rough ATM premium)
        option_entry = round(spot_entry * 0.02)
        if option_entry < 10:
            option_entry = max(spot_entry * 0.015, 20)

        # SL: 15-20%
        sl_pct = 0.20 if option_entry < 100 else 0.15
        sl_price = round(option_entry * (1 - sl_pct))
        risk_per_unit = option_entry - sl_price

        # Position sizing: 1.5% risk
        max_risk = running_capital * 0.015
        lot_size = 65 if "NIFTY" in (r.get("idx", "NIFTY")) and "BANK" not in (r.get("idx", "")) else 30
        max_qty = int(max_risk / max(risk_per_unit, 1))
        lots = max(1, min(max_qty // lot_size, 20))
        qty = lots * lot_size

        # T1: 20% profit, T2: 40% profit
        t1_price = round(option_entry * 1.20)
        t2_price = round(option_entry * 1.40)

        # ── SIMULATE OUTCOME ──
        # Use actual spot movement to estimate option P&L
        if spot_30m > 0:
            spot_move = spot_30m - spot_entry
            spot_move_pct = spot_move / spot_entry * 100

            # Rough delta: ATM option moves ~50% of spot move (in ₹ terms)
            # For a ₹200 option on ₹23000 spot, 100pt spot move ≈ ₹50-80 premium move
            option_delta = 0.5  # ATM delta
            spot_move_abs = abs(spot_move)

            if "CE" in action and spot_move > 0:
                # CE bought, spot went UP = WIN
                est_premium_move = spot_move_abs * option_delta * (option_entry / spot_entry) * 50
            elif "PE" in action and spot_move < 0:
                # PE bought, spot went DOWN = WIN
                est_premium_move = spot_move_abs * option_delta * (option_entry / spot_entry) * 50
            else:
                # Wrong direction = LOSS
                est_premium_move = -(spot_move_abs * option_delta * (option_entry / spot_entry) * 50)
        else:
            continue

        # Determine outcome
        if outcome_30m == "WIN":
            # Check if T1 would have been hit
            if est_premium_move >= (t1_price - option_entry):
                # T1 hit — partial book 50%, rest at avg
                pnl_pts = (t1_price - option_entry) * 0.5 + min(est_premium_move, t2_price - option_entry) * 0.5
            else:
                # Win but below T1 — approximate exit at breakeven or small profit
                pnl_pts = max(est_premium_move * 0.3, 0)  # Partial capture
            trade_result = "WIN"
            wins += 1
            consecutive_losses = 0
        else:
            # LOSS — SL hit
            pnl_pts = -(option_entry - sl_price)
            trade_result = "LOSS"
            losses += 1
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            last_loss_time = ts

        pnl_rupees = round(pnl_pts * qty)
        total_pnl += pnl_rupees
        running_capital += pnl_rupees
        peak_capital = max(peak_capital, running_capital)
        drawdown = peak_capital - running_capital
        max_drawdown = max(max_drawdown, drawdown)
        daily_pnl[date_str] += pnl_rupees
        hourly_stats[hour]["trades"] += 1
        hourly_stats[hour]["pnl"] += pnl_rupees
        if trade_result == "WIN":
            hourly_stats[hour]["wins"] += 1
        total_trades += 1

        sim_trades.append({
            "date": date_str,
            "time": ts.strftime("%H:%M"),
            "idx": r.get("idx", "NIFTY"),
            "action": action,
            "probability": prob,
            "spotEntry": spot_entry,
            "spot30m": spot_30m,
            "optionEntry": round(option_entry, 1),
            "sl": sl_price,
            "t1": t1_price,
            "t2": t2_price,
            "lots": lots,
            "qty": qty,
            "result": trade_result,
            "pnl": pnl_rupees,
            "runningCapital": round(running_capital),
        })

    # ── BUILD REPORT ──
    win_rate = round(wins / max(total_trades, 1) * 100, 1)
    avg_win = round(sum(t["pnl"] for t in sim_trades if t["result"] == "WIN") / max(wins, 1))
    avg_loss = round(sum(t["pnl"] for t in sim_trades if t["result"] == "LOSS") / max(losses, 1))

    # Best/worst days
    best_day = max(daily_pnl.items(), key=lambda x: x[1]) if daily_pnl else ("N/A", 0)
    worst_day = min(daily_pnl.items(), key=lambda x: x[1]) if daily_pnl else ("N/A", 0)

    # Best/worst hours
    best_hour = max(hourly_stats.items(), key=lambda x: x[1]["pnl"]) if hourly_stats else (0, {"pnl": 0})
    worst_hour = min(hourly_stats.items(), key=lambda x: x[1]["pnl"]) if hourly_stats else (0, {"pnl": 0})

    # Profit factor
    total_profit = sum(t["pnl"] for t in sim_trades if t["result"] == "WIN")
    total_loss_abs = abs(sum(t["pnl"] for t in sim_trades if t["result"] == "LOSS"))
    profit_factor = round(total_profit / max(total_loss_abs, 1), 2)

    return {
        "period": f"Last {days} days",
        "startCapital": capital,
        "endCapital": round(running_capital),
        "totalPnl": round(total_pnl),
        "totalPnlPct": round(total_pnl / capital * 100, 1),
        "totalTrades": total_trades,
        "wins": wins,
        "losses": losses,
        "winRate": win_rate,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "profitFactor": profit_factor,
        "maxDrawdown": round(max_drawdown),
        "maxDrawdownPct": round(max_drawdown / capital * 100, 1),
        "maxConsecutiveLosses": max_consecutive_losses,
        "bestDay": {"date": best_day[0], "pnl": round(best_day[1])},
        "worstDay": {"date": worst_day[0], "pnl": round(worst_day[1])},
        "bestHour": {"hour": f"{best_hour[0]}:00", "pnl": round(best_hour[1]["pnl"])},
        "worstHour": {"hour": f"{worst_hour[0]}:00", "pnl": round(worst_hour[1]["pnl"])},
        "dailyPnl": [{"date": d, "pnl": round(p)} for d, p in sorted(daily_pnl.items())],
        "hourlyStats": [{"hour": f"{h}:00", **s} for h, s in sorted(hourly_stats.items())],
        "trades": sim_trades[-50:],  # Last 50 trades
        "verdict": "PROFITABLE" if total_pnl > 0 else "NOT PROFITABLE",
        "recommendation": _build_recommendation(win_rate, profit_factor, max_drawdown, capital, total_trades),
    }


def _build_recommendation(win_rate, profit_factor, max_drawdown, capital, total_trades):
    """Build honest recommendation based on backtest results."""
    recs = []

    if total_trades < 10:
        recs.append("NOT ENOUGH DATA: Need at least 10 simulated trades for reliable results")
        return recs

    if win_rate >= 55:
        recs.append(f"WIN RATE OK: {win_rate}% is above breakeven threshold")
    else:
        recs.append(f"WIN RATE LOW: {win_rate}% — need better entry timing or wider SL")

    if profit_factor >= 1.5:
        recs.append(f"PROFIT FACTOR STRONG: {profit_factor}x — wins are significantly larger than losses")
    elif profit_factor >= 1.0:
        recs.append(f"PROFIT FACTOR MARGINAL: {profit_factor}x — profitable but thin edge")
    else:
        recs.append(f"PROFIT FACTOR NEGATIVE: {profit_factor}x — losses larger than wins, system needs tuning")

    dd_pct = max_drawdown / capital * 100
    if dd_pct < 10:
        recs.append(f"DRAWDOWN SAFE: {dd_pct:.1f}% max drawdown — acceptable risk")
    elif dd_pct < 20:
        recs.append(f"DRAWDOWN MODERATE: {dd_pct:.1f}% — reduce position size or tighten SL")
    else:
        recs.append(f"DRAWDOWN HIGH: {dd_pct:.1f}% — system too risky, reduce exposure significantly")

    if win_rate >= 55 and profit_factor >= 1.2 and dd_pct < 15:
        recs.append("VERDICT: System shows positive edge. Paper trade for 1 week, then go live with 50% capital.")
    elif win_rate >= 50 and profit_factor >= 1.0:
        recs.append("VERDICT: Marginal edge. Needs more data or tuning before live trading.")
    else:
        recs.append("VERDICT: Not ready for live trading. Tune entry criteria and SL levels.")

    return recs


def get_real_trade_analysis():
    """Analyze ACTUAL trades taken by the system (not simulated)."""
    if not TRADES_DB.exists():
        return {"error": "No trades database"}

    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    try:
        trades = conn.execute(
            "SELECT * FROM trades WHERE status != 'OPEN' ORDER BY entry_time ASC"
        ).fetchall()
    except Exception:
        conn.close()
        return {"error": "No trades table"}
    conn.close()

    if not trades:
        return {"error": "No closed trades yet", "trades": []}

    wins = 0
    losses = 0
    total_pnl = 0
    pnl_list = []
    by_hour = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0})
    by_day = defaultdict(float)

    for t in trades:
        t = dict(t)
        pnl = t.get("pnl_rupees", 0) or 0
        total_pnl += pnl
        pnl_list.append(pnl)

        if pnl > 0:
            wins += 1
        else:
            losses += 1

        try:
            dt = datetime.fromisoformat(t["entry_time"])
            by_hour[dt.hour]["trades"] += 1
            by_hour[dt.hour]["pnl"] += pnl
            if pnl > 0:
                by_hour[dt.hour]["wins"] += 1
            by_day[dt.strftime("%Y-%m-%d")] += pnl
        except Exception:
            pass

    total = wins + losses
    win_rate = round(wins / max(total, 1) * 100, 1)
    avg_win = round(sum(p for p in pnl_list if p > 0) / max(wins, 1))
    avg_loss = round(sum(p for p in pnl_list if p < 0) / max(losses, 1))

    return {
        "totalTrades": total,
        "wins": wins,
        "losses": losses,
        "winRate": win_rate,
        "totalPnl": round(total_pnl),
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "profitFactor": round(sum(p for p in pnl_list if p > 0) / max(abs(sum(p for p in pnl_list if p < 0)), 1), 2),
        "byHour": [{"hour": f"{h}:00", **s} for h, s in sorted(by_hour.items())],
        "byDay": [{"date": d, "pnl": round(p)} for d, p in sorted(by_day.items())],
    }
