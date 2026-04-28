"""
Divergence Filter (A4) — Spot vs Premium nightmare detector.

Buyer's worst nightmare:
  Spot: 24500 → 24550 (+0.2%) ✓ moving up (BUY CE looks good)
  YOUR CE premium: ₹100 → ₹95 (-5%) ✗ FALLING despite spot up

Causes:
  - Theta crush (expiry day)
  - IV crush (volatility collapse)
  - Low liquidity strike
  - Manipulation (low volume, big sell)

This filter checks BEFORE entry:
  - Did spot move favorably in last 5 min?
  - Did our option premium ALSO move favorably?
  - If divergence > 2% → BLOCK trade

Saves you from theta-crushed trades that look fundamentally good.
"""

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


def check_divergence(engine, idx, action, strike, current_premium, lookback_minutes=5):
    """Check if option premium is diverging from spot direction.

    Returns (should_block, reason, divergence_pct).
    """
    try:
        # Get current spot
        spot_token = engine.spot_tokens.get(idx)
        if not spot_token:
            return False, None, 0
        current_spot = engine.prices.get(spot_token, {}).get("ltp", 0)
        if current_spot <= 0:
            return False, None, 0

        # Get historical spot price (~5 min ago) — use Trinity bar buffer if available
        old_spot = None
        old_premium = None
        try:
            from trinity import tick_processor as tp
            state = tp.get_state()
            bars = state.bar_buffer.last_n(lookback_minutes * 60 + 60)  # ~5 min worth
            if bars and len(bars) >= 5:
                # Take ~5 min ago bar
                idx_bar = max(0, len(bars) - lookback_minutes * 60)
                old_bar = bars[idx_bar]
                old_spot = old_bar.get("spot")
        except Exception:
            pass

        if old_spot is None or old_spot <= 0:
            return False, None, 0  # not enough data

        # Get historical premium from scalper_ticks if available
        try:
            from pathlib import Path
            import sqlite3
            scalper_db = Path("/data/scalper_trades.db") if Path("/data").is_dir() \
                         else Path(__file__).parent / "scalper_trades.db"
            if scalper_db.exists():
                cutoff_ms = int((ist_now() - timedelta(minutes=lookback_minutes)).timestamp() * 1000)
                conn = sqlite3.connect(str(scalper_db))
                row = conn.execute("""
                    SELECT t.ltp FROM scalper_ticks t
                    JOIN scalper_trades tr ON t.trade_id = tr.id
                    WHERE tr.idx=? AND tr.strike=? AND tr.action LIKE ?
                    AND t.ts >= ?
                    ORDER BY t.ts ASC LIMIT 1
                """, (idx, strike, f"%{action[-2:]}%", cutoff_ms)).fetchone()
                conn.close()
                if row:
                    old_premium = row[0]
        except Exception:
            pass

        if old_premium is None:
            # Can't verify — don't block (default allow)
            return False, None, 0

        # Calculate moves
        spot_move_pct = ((current_spot - old_spot) / old_spot) * 100
        premium_move_pct = ((current_premium - old_premium) / old_premium) * 100

        is_ce = "CE" in (action or "")

        # Expected: CE → spot up = premium up; PE → spot down = premium up
        # Divergence: spot moved favorably but premium moved opposite
        if is_ce:
            # Spot UP, premium DOWN = bad divergence
            if spot_move_pct > 0.05 and premium_move_pct < -2.0:
                divergence = abs(premium_move_pct - spot_move_pct)
                return (True,
                        f"DIVERGENCE: Spot +{spot_move_pct:.2f}% but CE premium {premium_move_pct:.1f}% (theta/IV crush?)",
                        round(divergence, 2))
        else:
            # Spot DOWN, premium DOWN = bad divergence
            if spot_move_pct < -0.05 and premium_move_pct < -2.0:
                divergence = abs(premium_move_pct - abs(spot_move_pct))
                return (True,
                        f"DIVERGENCE: Spot {spot_move_pct:.2f}% but PE premium {premium_move_pct:.1f}% (theta/IV crush?)",
                        round(divergence, 2))

        return False, None, 0

    except Exception as e:
        print(f"[DIVERGENCE] check error: {e}")
        return False, None, 0
