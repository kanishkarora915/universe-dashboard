"""
ATR-Based Realistic Targets — Replace fantasy +30%/+60% with actual market data.

Problem: Fixed +30%/+60% targets NEVER hit (0/65 trades reached T2).
Solution: Compute targets from option's actual recent volatility (ATR).

Formula:
  ATR = avg true range of option premium over last N ticks
  T1  = entry × (1 + 1.5 × ATR_pct)  # realistic first target
  T2  = entry × (1 + 3.0 × ATR_pct)  # stretch target
  SL  = entry × (1 - 1.0 × ATR_pct)  # 1 ATR risk
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


def get_option_atr(engine, idx, strike, side, periods=14):
    """Compute ATR for an option from recent tick history.

    Returns ATR as % of current LTP (e.g., 0.05 = 5% volatility).
    Falls back to safe defaults if no history available.
    """
    try:
        # Try scalper_ticks table for recent option ticks
        from pathlib import Path
        scalper_db = Path("/data/scalper_trades.db") if Path("/data").is_dir() \
                     else Path(__file__).parent / "scalper_trades.db"

        # Get recent ticks for similar strike (any trade on this strike)
        if scalper_db.exists():
            conn = sqlite3.connect(str(scalper_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT t.ltp, t.ts FROM scalper_ticks t
                JOIN scalper_trades tr ON t.trade_id = tr.id
                WHERE tr.idx=? AND tr.strike=? AND tr.action LIKE ?
                AND t.ts > ?
                ORDER BY t.ts DESC LIMIT 100
            """, (
                idx, strike, f"%{side}%",
                int((ist_now() - timedelta(hours=4)).timestamp() * 1000),
            )).fetchall()
            conn.close()

            ltps = [r["ltp"] for r in rows if r["ltp"] and r["ltp"] > 0]
            if len(ltps) >= 5:
                # Compute true ranges from consecutive ticks
                ranges = []
                for i in range(1, len(ltps)):
                    tr = abs(ltps[i] - ltps[i-1])
                    ranges.append(tr)
                if ranges:
                    avg_range = sum(ranges) / len(ranges)
                    avg_ltp = sum(ltps) / len(ltps)
                    if avg_ltp > 0:
                        # ATR as % of LTP
                        atr_pct = avg_range / avg_ltp
                        # Sanity: 1-30% range
                        return max(0.01, min(0.30, atr_pct))
    except Exception as e:
        print(f"[ATR] tick history error: {e}")

    # Fallback: based on premium price (cheap options more volatile)
    return get_default_atr_by_premium_band(engine, idx, strike, side)


def get_default_atr_by_premium_band(engine, idx, strike, side):
    """Fallback ATR estimate based on option premium price band."""
    try:
        chain = engine.chains.get(idx, {})
        d = chain.get(strike, {})
        ltp = d.get(f"{side.lower()}_ltp", 0)

        if ltp <= 0:
            return 0.10  # 10% default

        # Cheap options (<₹50): high relative volatility
        if ltp < 50:
            return 0.15  # 15% ATR
        # Mid options (₹50-150): medium
        elif ltp < 150:
            return 0.10  # 10% ATR
        # ATM-ish options (₹150-300): standard
        elif ltp < 300:
            return 0.07  # 7% ATR
        # Expensive options (>₹300): low relative volatility
        else:
            return 0.05  # 5% ATR
    except Exception:
        return 0.08  # safe default


def calculate_targets(entry_price, atr_pct, vol_multiplier=1.0):
    """Calculate SL/T1/T2 from entry + ATR.

    Args:
        entry_price: option premium
        atr_pct: ATR as decimal (0.05 = 5%)
        vol_multiplier: from VolatilityDetector (1.5 on HIGH-VOL, 0.7 on EXPIRY)

    Returns dict with sl, t1, t2 prices.
    """
    if entry_price <= 0 or atr_pct <= 0:
        return {
            "sl": entry_price * 0.85,
            "t1": entry_price * 1.30,
            "t2": entry_price * 1.60,
            "atr_pct": 0,
            "method": "fallback",
        }

    # Apply vol multiplier
    sl_atr = atr_pct * 1.0 * vol_multiplier
    t1_atr = atr_pct * 1.5 * vol_multiplier
    t2_atr = atr_pct * 3.0 * vol_multiplier

    # Sanity caps (no SL >25%, no T1 >50%, no T2 >100%)
    sl_atr = min(sl_atr, 0.25)
    t1_atr = min(t1_atr, 0.50)
    t2_atr = min(t2_atr, 1.00)

    sl = round(entry_price * (1 - sl_atr), 1)
    t1 = round(entry_price * (1 + t1_atr), 1)
    t2 = round(entry_price * (1 + t2_atr), 1)

    return {
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "atr_pct": round(atr_pct, 4),
        "sl_pct": round(sl_atr * 100, 1),
        "t1_pct": round(t1_atr * 100, 1),
        "t2_pct": round(t2_atr * 100, 1),
        "vol_multiplier": vol_multiplier,
        "method": "atr",
    }


def get_realistic_targets(engine, idx, strike, side, entry_price):
    """Main entry point — combines ATR + volatility regime to give targets."""
    # Get option ATR
    atr = get_option_atr(engine, idx, strike, side)

    # Get volatility regime multipliers
    vol_mult = 1.0
    try:
        from volatility_detector import classify_regime
        regime = classify_regime(engine)
        rec = regime.get("recommend", {})
        # Combine SL + target multipliers (use target_multiplier for general scaling)
        vol_mult = rec.get("target_multiplier", 1.0)
    except Exception:
        pass

    return calculate_targets(entry_price, atr, vol_mult)
