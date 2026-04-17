"""
AI Analysis Engine — Uses Claude API to analyze ALL dashboard data
and generate comprehensive trading verdicts for option BUYERS.
"""

import os
import json
import anthropic
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")

SYSTEM_PROMPT = """You are UNIVERSE AI — an elite NSE options trading intelligence engine.
You ONLY advise for option BUYING (CE or PE). Never recommend selling/writing options.

You receive REAL-TIME data from multiple engines:
- Live market data (LTP, change, PCR, VIX, max pain, OI walls)
- Trading signals with 9-point scoring
- Seller activity (writing/short covering/buying/unwinding per strike)
- Unusual activity alerts (OI changes > 1L with classification)
- Hidden Shift patterns (institutional OI cooking detection)
- Trap Fingerprint detection (OTM institutional positioning)
- Intraday technicals (EMA, RSI, MACD, SuperTrend, VWAP)

Your job: Analyze ALL this data together and give the trader ONE clear actionable verdict.

RESPONSE FORMAT (use this exact JSON structure):
{
  "marketPulse": "2-3 sentence summary of what's happening RIGHT NOW",
  "nifty": {
    "verdict": "BUY CE" or "BUY PE" or "NO TRADE",
    "confidence": "HIGH" or "MEDIUM" or "LOW",
    "strike": 22900,
    "expiry": "current" or "next",
    "entry": "150-160",
    "target1": "195",
    "target2": "230",
    "stoploss": "110",
    "riskReward": "1:2.5",
    "holdTime": "30 min to 2 hours",
    "reasons": ["reason 1", "reason 2", "reason 3", "reason 4"],
    "risks": ["risk 1", "risk 2"],
    "keyLevels": {"resistance": [23000, 23100], "support": [22800, 22700]},
    "prediction": {
      "intraday": "Expected to test 23000 resistance, if breaks can go to 23100",
      "nextDay": "Gap up/down likely based on global cues, watch 22800 support",
      "weekly": "Broader trend bullish/bearish, range 22500-23200"
    }
  },
  "banknifty": {
    "verdict": "BUY CE" or "BUY PE" or "NO TRADE",
    "confidence": "HIGH" or "MEDIUM" or "LOW",
    "strike": 52200,
    "expiry": "current",
    "entry": "200-220",
    "target1": "280",
    "target2": "350",
    "stoploss": "140",
    "riskReward": "1:2",
    "holdTime": "1-3 hours",
    "reasons": ["reason 1", "reason 2"],
    "risks": ["risk 1"],
    "keyLevels": {"resistance": [52500], "support": [52000]},
    "prediction": {
      "intraday": "...",
      "nextDay": "...",
      "weekly": "..."
    }
  },
  "hedgeStrategy": "If taking NIFTY BUY CE, hedge with small PE at resistance strike",
  "avoidList": ["Avoid BankNifty if IVR > 70", "No trades in last 30 min"],
  "institutionalRead": "What institutions are doing based on seller + trap data"
}

RULES:
- Always give EXACT strike, entry range, targets, stoploss in numbers
- If no clear trade, say "NO TRADE" with confidence "LOW" and explain why
- Be honest about risks — don't oversell
- Use the actual data provided, don't hallucinate numbers
- IVR > 60 = premiums expensive, warn the trader
- VIX > 20 = high volatility, adjust SL wider
- Consider expiry decay if Thursday/Friday
- If PCR extreme (<0.7 or >1.3), highlight it
- Only respond with valid JSON, no markdown or extra text"""


def run_ai_analysis(all_data: dict) -> dict:
    """Send all dashboard data to Claude API and get trading analysis."""
    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

        # Build data summary for Claude
        data_prompt = _build_data_prompt(all_data)

        now = datetime.now(IST)
        time_str = now.strftime("%I:%M %p IST")
        day_str = now.strftime("%A, %d %B %Y")

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Current time: {time_str}, {day_str}\n\nHere is ALL the real-time dashboard data. Analyze everything and give me your trading verdict:\n\n{data_prompt}"
            }]
        )

        # Parse response
        response_text = message.content[0].text.strip()

        # Try to parse JSON (handle potential markdown wrapping)
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()

        result = json.loads(response_text)
        result["_meta"] = {
            "generatedAt": time_str,
            "model": "claude-sonnet-4",
            "tokensUsed": message.usage.input_tokens + message.usage.output_tokens,
        }
        return result

    except json.JSONDecodeError:
        # If Claude didn't return valid JSON, wrap the text
        return {
            "marketPulse": response_text[:500] if 'response_text' in dir() else "Analysis failed",
            "nifty": {"verdict": "NO TRADE", "confidence": "LOW", "reasons": ["AI response parsing failed"]},
            "banknifty": {"verdict": "NO TRADE", "confidence": "LOW", "reasons": ["AI response parsing failed"]},
            "_meta": {"error": "JSON parse failed", "rawResponse": response_text[:1000] if 'response_text' in dir() else ""}
        }
    except Exception as e:
        print(f"[AI] Claude API error: {e}")
        return {
            "marketPulse": f"AI analysis temporarily unavailable: {str(e)[:100]}",
            "nifty": {"verdict": "NO TRADE", "confidence": "LOW", "reasons": [str(e)[:200]]},
            "banknifty": {"verdict": "NO TRADE", "confidence": "LOW", "reasons": [str(e)[:200]]},
            "_meta": {"error": str(e)}
        }


def _build_data_prompt(data: dict) -> str:
    """Build a compact data summary from all dashboard engines."""
    parts = []

    # 1. Live Market Data
    live = data.get("live")
    # Only skip if it's a dict with an error key; otherwise include it
    if live and (not isinstance(live, dict) or not live.get("error")):
        parts.append(f"=== LIVE MARKET DATA ===\n{json.dumps(live, default=str, indent=None)[:2000]}")

    # 2. Signals
    signals = data.get("signals", [])
    if signals and isinstance(signals, list) and len(signals) > 0:
        parts.append(f"=== TRADING SIGNALS ({len(signals)} active) ===\n{json.dumps(signals[:5], default=str, indent=None)[:1500]}")

    # 3. OI Summary
    oi = data.get("oiSummary")
    if oi and isinstance(oi, dict) and not oi.get("error"):
        # Trim strikes to top 10 for each
        oi_compact = {}
        for key in ["nifty", "banknifty"]:
            d = oi.get(key, {})
            if d:
                oi_compact[key] = {k: v for k, v in d.items() if k != "strikes"}
                strikes = d.get("strikes", [])
                # Keep only strikes with significant OI change
                sig_strikes = [s for s in strikes if abs(s.get("ceOIChange", 0)) > 50000 or abs(s.get("peOIChange", 0)) > 50000]
                oi_compact[key]["topStrikes"] = sig_strikes[:10]
        parts.append(f"=== OI CHANGE SUMMARY ===\n{json.dumps(oi_compact, default=str, indent=None)[:2000]}")

    # 4. Seller Data
    seller = data.get("sellerData")
    if seller and isinstance(seller, dict) and not seller.get("error"):
        seller_compact = {}
        for key in ["nifty", "banknifty"]:
            d = seller.get(key, {})
            if d:
                seller_compact[key] = {k: v for k, v in d.items() if k not in ("strikes",)}
                # Only major changes
                majors = [s for s in d.get("strikes", []) if s.get("ceMagnitude") == "MAJOR" or s.get("peMagnitude") == "MAJOR"]
                seller_compact[key]["majorStrikes"] = majors[:8]
                seller_compact[key]["shifts"] = d.get("shifts", [])
        parts.append(f"=== SELLER ACTIVITY ===\n{json.dumps(seller_compact, default=str, indent=None)[:2000]}")

    # 5. Unusual Activity
    unusual = data.get("unusual", [])
    if unusual and isinstance(unusual, list) and len(unusual) > 0:
        parts.append(f"=== UNUSUAL ACTIVITY ({len(unusual)} alerts) ===\n{json.dumps(unusual[:10], default=str, indent=None)[:1500]}")

    # 6. Trade AI
    trade = data.get("tradeAnalysis")
    if trade and isinstance(trade, dict) and not trade.get("error"):
        parts.append(f"=== TRADE AI ANALYSIS ===\n{json.dumps(trade, default=str, indent=None)[:2000]}")

    # 7. Hidden Shift
    hidden = data.get("hiddenShift")
    if hidden and isinstance(hidden, dict) and not hidden.get("error"):
        hidden_compact = {}
        for key in ["nifty", "banknifty"]:
            d = hidden.get(key, {})
            if d:
                hidden_compact[key] = {k: v for k, v in d.items() if k != "strikes"}
        parts.append(f"=== HIDDEN SHIFT (Institutional Patterns) ===\n{json.dumps(hidden_compact, default=str, indent=None)[:1500]}")

    # 8. Trap Fingerprints
    trap = data.get("trapScan")
    if trap and isinstance(trap, dict):
        trap_compact = {}
        for key in ["nifty", "banknifty"]:
            d = trap.get(key, {})
            if d and not d.get("error"):
                trap_compact[key] = {k: v for k, v in d.items() if k != "strikes"}
                fingerprints = [s for s in d.get("strikes", []) if s.get("trapScore", 0) >= 4]
                trap_compact[key]["fingerprints"] = fingerprints[:8]
        if trap_compact:
            parts.append(f"=== TRAP FINGERPRINTS ===\n{json.dumps(trap_compact, default=str, indent=None)[:1500]}")

    # 9. Intraday
    intraday = data.get("intraday")
    if intraday and isinstance(intraday, dict) and not intraday.get("error"):
        parts.append(f"=== INTRADAY TECHNICALS ===\n{json.dumps(intraday, default=str, indent=None)[:1500]}")

    return "\n\n".join(parts)
