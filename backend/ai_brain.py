"""
AI Brain — Claude Haiku integration for live dashboard analysis.

User can ask: "what is data saying" / "what to buy" / "is this trap"
AI fetches ALL dashboard data + analyzes + responds with trade recommendation.

Also: 3:20 PM EOD daily forecast with:
  - Today's story
  - Tomorrow gap prediction
  - Trap zones
  - Reversal levels
  - Best strikes
  - Time windows
"""

import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = _data_dir / "ai_brain.db"

# Claude Haiku — fastest, cheapest, smart enough for trading analysis
CLAUDE_MODEL = "claude-haiku-4-5"


def ist_now():
    return datetime.now(IST)


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eod_forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            generated_at TEXT,
            today_summary TEXT,
            tomorrow_gap TEXT,
            tomorrow_bias TEXT,
            trap_zones TEXT,
            reversal_levels TEXT,
            best_strikes TEXT,
            time_windows TEXT,
            confidence INTEGER,
            full_analysis TEXT,
            raw_data TEXT
        )
    """)
    conn.commit()
    conn.close()


def _get_client():
    import anthropic
    key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)


def fetch_all_dashboard_data(engine):
    """Aggregate ALL dashboard data sources into single context dict."""
    data = {
        "timestamp": ist_now().isoformat(),
        "day": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][ist_now().weekday()],
    }

    # 1. Live spot, OI, PCR
    try:
        live = engine.get_live_data()
        data["live"] = {
            "nifty": live.get("nifty", {}),
            "banknifty": live.get("banknifty", {}),
        }
    except Exception:
        data["live"] = {}

    # 2. Verdict
    try:
        verdict = engine.get_trap_verdict()
        data["verdict"] = {
            "nifty": {
                "action": verdict.get("nifty", {}).get("action"),
                "winProbability": verdict.get("nifty", {}).get("winProbability"),
                "bullPct": verdict.get("nifty", {}).get("bullPct"),
                "bearPct": verdict.get("nifty", {}).get("bearPct"),
                "reasons": verdict.get("nifty", {}).get("reasons", [])[:5],
            },
            "banknifty": {
                "action": verdict.get("banknifty", {}).get("action"),
                "winProbability": verdict.get("banknifty", {}).get("winProbability"),
                "bullPct": verdict.get("banknifty", {}).get("bullPct"),
                "bearPct": verdict.get("banknifty", {}).get("bearPct"),
            },
        }
    except Exception:
        data["verdict"] = {}

    # 3. OI Summary
    try:
        oi = engine.get_oi_change_summary()
        # Trim to top 5 strikes per index by OI change
        for idx in ["nifty", "banknifty"]:
            d = oi.get(idx, {})
            strikes = d.get("strikes", [])
            top_change = sorted(strikes, key=lambda s: abs((s.get("ceOIChange") or 0) + (s.get("peOIChange") or 0)), reverse=True)[:5]
            d["strikes"] = top_change
            oi[idx] = d
        data["oi_summary"] = oi
    except Exception:
        data["oi_summary"] = {}

    # 4. Trinity snapshot
    try:
        from trinity import orchestrator as _to
        data["trinity"] = _to.get_snapshot()
    except Exception:
        data["trinity"] = {}

    # 5. Volatility regime
    try:
        from volatility_detector import classify_regime
        data["volatility"] = classify_regime(engine)
    except Exception:
        data["volatility"] = {}

    # 6. Risk tier
    try:
        from risk_tier_manager import get_summary
        data["risk_tier"] = get_summary()
    except Exception:
        data["risk_tier"] = {}

    # 7. Recent OI shifts
    try:
        from oi_shift_detector import get_recent_shifts
        data["oi_shifts"] = get_recent_shifts(hours=4)[:10]
    except Exception:
        data["oi_shifts"] = []

    # 8. Hidden events
    try:
        from rejection_engine import get_recent_hidden_events
        data["hidden_events"] = get_recent_hidden_events(hours=4)[:10]
    except Exception:
        data["hidden_events"] = []

    # 9. Today's story
    try:
        from times_tab_engine import get_today_story
        story = get_today_story("NIFTY")
        data["today_story"] = {
            "bias": story["bias"],
            "wall_shifts": story["wall_shifts_count"],
            "trades": story["trades_today"],
            "wins": story["wins"],
            "losses": story["losses"],
            "net_pnl": story["net_pnl"],
        }
    except Exception:
        data["today_story"] = {}

    # 10. Past similar days
    try:
        from daily_training import find_similar_past_days
        data["similar_past_days"] = find_similar_past_days(engine, days_back=5)
    except Exception:
        data["similar_past_days"] = []

    # 11. FII / Global cues
    try:
        data["fii_dii"] = engine.get_fii_dii() if hasattr(engine, "get_fii_dii") else {}
        data["global_cues"] = engine.get_global_cues() if hasattr(engine, "get_global_cues") else {}
    except Exception:
        data["fii_dii"] = {}
        data["global_cues"] = {}

    # 12. Smart money / whales
    try:
        from smart_money import get_smart_money_state
        data["smart_money"] = get_smart_money_state(engine)
    except Exception:
        data["smart_money"] = {}

    # 13. Rejection zones
    try:
        from rejection_engine import get_zones_analysis
        data["zones"] = get_zones_analysis(engine, "NIFTY")
    except Exception:
        data["zones"] = {}

    return data


def ask(question, engine, session_id="default"):
    """User asks a question, AI analyzes all dashboard data and responds."""
    client = _get_client()
    if not client:
        return {"error": "No CLAUDE_API_KEY in environment"}

    # Fetch full dashboard context
    context = fetch_all_dashboard_data(engine)

    # Build system prompt
    system_prompt = """You are an expert NIFTY options analyst for an Indian options BUYER (CE/PE buyer, never seller).

The user is a trader who wants:
- WHAT is happening in the market RIGHT NOW
- WHY (smart money logic, OI behavior, sellers vs buyers)
- WHAT to BUY (exact strike, premium target, SL, why)
- WHERE retail is being trapped

Style:
- Use Hinglish casually
- Be DIRECT, no fluff
- Quote SPECIFIC numbers from the data
- Always end with actionable recommendation
- Keep response under 400 words unless user asks for deep analysis

You have FULL access to live dashboard data including:
- Spot prices, OI changes, PCR, walls
- Trinity engine regime (Spot/Future/Synthetic)
- Volatility regime + recommendations
- Risk tier state
- Recent OI wall shifts
- Hidden activity events (mass buys/writes/covers)
- Today's events story + bias
- Similar past days for pattern matching
- FII/DII flows
- Smart money signals
- Rejection zones

Use this data to answer with REAL ANALYSIS, not generic advice.
"""

    # Build user message with context
    context_json = json.dumps(context, default=str, indent=2)[:8000]  # cap context to avoid token explosion
    user_message = f"""Live Dashboard Data:
```json
{context_json}
```

User question: {question}

Analyze this data deeply and respond with:
1. What's happening (direct observation)
2. Why (smart money logic)
3. What to do (specific strike + entry + SL + target)
4. Risks/warnings
"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        answer = response.content[0].text if response.content else ""
        input_t = response.usage.input_tokens if hasattr(response, "usage") else 0
        output_t = response.usage.output_tokens if hasattr(response, "usage") else 0

        # Save chat
        init_db()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT INTO ai_chats (ts, session_id, role, content, input_tokens, output_tokens)
            VALUES (?,?,?,?,?,?)
        """, (ist_now().isoformat(), session_id, "user", question, 0, 0))
        conn.execute("""
            INSERT INTO ai_chats (ts, session_id, role, content, input_tokens, output_tokens)
            VALUES (?,?,?,?,?,?)
        """, (ist_now().isoformat(), session_id, "assistant", answer, input_t, output_t))
        conn.commit()
        conn.close()

        return {
            "answer": answer,
            "model": CLAUDE_MODEL,
            "input_tokens": input_t,
            "output_tokens": output_t,
            "ts": ist_now().isoformat(),
        }
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}


def generate_eod_forecast(engine):
    """Daily 3:20 PM EOD analysis + tomorrow forecast."""
    client = _get_client()
    if not client:
        return {"error": "No CLAUDE_API_KEY in environment"}

    context = fetch_all_dashboard_data(engine)

    system_prompt = """You are an expert NIFTY options analyst generating end-of-day forecast.

Generate a structured forecast for TOMORROW based on today's data.

Output MUST be valid JSON in this exact format:
{
  "today_summary": "1-2 sentence story of today",
  "today_key_events": ["event 1", "event 2", "event 3"],
  "today_bias": "BULLISH / BEARISH / MIXED / SIDEWAYS",
  "tomorrow_gap_prediction": "GAP_UP_0.3% / GAP_DOWN_0.5% / FLAT",
  "tomorrow_gap_reasoning": "why this gap expected",
  "tomorrow_bias": "BULLISH / BEARISH / MIXED",
  "trap_zones": {
    "bull_trap_above": 24650,
    "bear_trap_below": 24350,
    "logic": "explanation"
  },
  "reversal_levels": {
    "strong_support": 24300,
    "strong_resistance": 24700
  },
  "best_strikes_to_watch": [
    {"strike": 24500, "type": "CE", "reason": "why"},
    {"strike": 24300, "type": "PE", "reason": "why"}
  ],
  "time_windows": {
    "best": ["9:30-10:30", "1:30-2:30"],
    "avoid": ["11:30-12:30 lunch chop"]
  },
  "key_warnings": ["expiry day", "high VIX", etc.],
  "confidence_pct": 75,
  "narrative": "Full 200-word analysis explaining today's story and tomorrow setup"
}

Be specific, use ACTUAL numbers from data. No fluff.
"""

    context_json = json.dumps(context, default=str, indent=2)[:10000]
    user_message = f"""Today's complete dashboard data:
```json
{context_json}
```

Generate EOD forecast JSON for {ist_now().strftime('%A, %d %B %Y')}.
"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        answer = response.content[0].text if response.content else ""

        # Try to extract JSON from response
        forecast = None
        try:
            # Find first { and last }
            start = answer.find("{")
            end = answer.rfind("}")
            if start >= 0 and end > start:
                forecast = json.loads(answer[start:end+1])
        except Exception:
            forecast = {"raw_text": answer}

        # Save to DB
        init_db()
        today = ist_now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT OR REPLACE INTO eod_forecasts
            (date, generated_at, today_summary, tomorrow_gap, tomorrow_bias,
             trap_zones, reversal_levels, best_strikes, time_windows,
             confidence, full_analysis, raw_data)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            today, ist_now().isoformat(),
            forecast.get("today_summary", "") if forecast else "",
            forecast.get("tomorrow_gap_prediction", "") if forecast else "",
            forecast.get("tomorrow_bias", "") if forecast else "",
            json.dumps(forecast.get("trap_zones", {})) if forecast else "{}",
            json.dumps(forecast.get("reversal_levels", {})) if forecast else "{}",
            json.dumps(forecast.get("best_strikes_to_watch", [])) if forecast else "[]",
            json.dumps(forecast.get("time_windows", {})) if forecast else "{}",
            forecast.get("confidence_pct", 0) if forecast else 0,
            answer,
            json.dumps({"input_tokens": getattr(response, "usage", {}).input_tokens if hasattr(response, "usage") else 0}),
        ))
        conn.commit()
        conn.close()

        return {
            "ok": True,
            "forecast": forecast,
            "raw": answer,
            "ts": ist_now().isoformat(),
        }
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}


def get_latest_eod_forecast():
    """Get most recent EOD forecast (today's or yesterday's)."""
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT * FROM eod_forecasts ORDER BY date DESC LIMIT 1
    """).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    for k in ("trap_zones", "reversal_levels", "best_strikes", "time_windows"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except Exception:
            pass
    return d


def get_chat_history(session_id="default", limit=30):
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM ai_chats WHERE session_id=? ORDER BY ts DESC LIMIT ?
    """, (session_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows][::-1]  # oldest first
