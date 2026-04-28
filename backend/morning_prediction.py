"""
Morning Prediction Dashboard (B10) — Pre-market forecast.

At 9:00 AM (or first call of day), generates:
  - Today's expected pattern based on most similar past same-weekday
  - Best/worst time windows for trades
  - Recommended engine weights
  - Warnings (volatility, expiry, news flags)
  - Trade recommendations
"""

from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


def predict_today(engine):
    """Generate full morning prediction for today's trading."""
    now = ist_now()
    day_idx = now.weekday()

    if day_idx >= 5:  # Saturday/Sunday
        return {"error": "Weekend — no market today"}

    result = {
        "date": now.strftime("%Y-%m-%d"),
        "day_of_week": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][day_idx],
        "is_expiry": day_idx == 1,  # Tuesday NIFTY
        "ts": now.isoformat(),
    }

    # 1. Find similar past days
    try:
        from daily_training import find_similar_past_days, get_day_weights
        matches = find_similar_past_days(engine, days_back=5)
        result["similar_days"] = matches[:5]

        if matches:
            top = matches[0]
            result["top_match"] = top
            result["confidence_pct"] = top["similarity_pct"]
            result["expected_pattern"] = {
                "morning_trend": top.get("morning_trend"),
                "afternoon_trend": top.get("afternoon_trend"),
                "expected_pnl": top.get("net_pnl"),
            }

        # Day-specific weights
        weights = get_day_weights(day_idx)
        result["day_specific_weights"] = weights or {"error": "Not enough samples yet"}
    except Exception as e:
        result["pattern_error"] = str(e)
        result["similar_days"] = []

    # 2. Volatility regime
    try:
        from volatility_detector import classify_regime
        regime = classify_regime(engine)
        result["volatility"] = {
            "regime": regime["regime"],
            "vix": regime.get("vix"),
            "is_expiry": regime.get("is_expiry"),
            "warnings": regime.get("notes", [])[:5],
            "recommend": regime.get("recommend", {}),
        }
    except Exception as e:
        result["volatility_error"] = str(e)

    # 3. Best time windows (from past similar days)
    try:
        from daily_training import get_profile_for_day, DAY_NAMES
        profiles = get_profile_for_day(DAY_NAMES[day_idx])
        if profiles:
            tw_aggregate = {}
            for p in profiles[:8]:  # last 8 same-weekdays
                tw = p.get("time_window_performance") or {}
                if not isinstance(tw, dict):
                    continue
                for window, perf in tw.items():
                    if window not in tw_aggregate:
                        tw_aggregate[window] = {"trades": 0, "wins": 0, "losses": 0}
                    tw_aggregate[window]["trades"] += perf.get("trades", 0)
                    tw_aggregate[window]["wins"] += perf.get("wins", 0)
                    tw_aggregate[window]["losses"] += perf.get("losses", 0)

            best_windows = []
            avoid_windows = []
            for w, perf in tw_aggregate.items():
                t = perf["trades"]
                if t < 3:
                    continue
                wr = perf["wins"] / max(t, 1) * 100
                perf["win_rate"] = round(wr, 1)
                if wr >= 60:
                    best_windows.append({"window": w, **perf})
                elif wr <= 40:
                    avoid_windows.append({"window": w, **perf})
            result["best_time_windows"] = sorted(best_windows, key=lambda x: -x["win_rate"])
            result["avoid_time_windows"] = sorted(avoid_windows, key=lambda x: x["win_rate"])
    except Exception as e:
        result["time_window_error"] = str(e)

    # 4. Tier state
    try:
        from risk_tier_manager import get_summary as tier_summary
        result["risk_tier"] = tier_summary()
    except Exception:
        pass

    # 5. Recommendation summary
    notes = []
    if result.get("is_expiry"):
        notes.append("⚠️ EXPIRY DAY — main P&L paused, scalper only with tight SL")
    if result.get("volatility", {}).get("regime") == "EXTREME":
        notes.append("🛑 EXTREME volatility — all trading paused")
    if result.get("similar_days"):
        avg_pnl = sum(d.get("net_pnl", 0) or 0 for d in result["similar_days"]) / len(result["similar_days"])
        notes.append(f"Past {len(result['similar_days'])} similar days avg P&L: ₹{avg_pnl:+,.0f}")
    if result.get("best_time_windows"):
        names = [w["window"] for w in result["best_time_windows"][:3]]
        notes.append(f"Best windows: {', '.join(names)}")
    if result.get("avoid_time_windows"):
        names = [w["window"] for w in result["avoid_time_windows"][:3]]
        notes.append(f"Avoid windows: {', '.join(names)}")
    result["recommendations"] = notes

    return result
