"""
Reversal Zone Tracker — premium-only double-bottom detector.

Watches option premium for re-tests of recent local lows. Fires entry
when premium bounces from a previous support level with confirmation.

Used for PnL-tab (main) trades only. NOT for scalper.

Pattern (double-bottom on premium):
  1. Premium hits L1 (local low in last 90 min)
  2. Premium bounces ≥15% from L1 (confirms L1 was real support)
  3. Premium drops back near L1 (within 6%)
  4. Confirmation candle prints (bullish + wick rejection)
  → FIRE ENTRY: BUY the option

Trade tagging:
  - Logged with source="reversal_zone"
  - trade_logger.py applies special exit rules:
      * SL: entry × 0.95 (-5% hard cap, no overrides)
      * No fixed T1/T2 exits
      * Trail SL activates at +25% profit
      * Trail SL = peak × 0.95 (5% give-back from peak)

Cooldown: same (idx, strike, side) won't re-fire for 30 min.
"""

import time
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


# ── Pattern config (tuned for typical NIFTY/BANKNIFTY option premium behavior) ──

LOOKBACK_MINUTES        = 90    # Hunt L1 within last 90 min
EXCLUDE_RECENT_MINUTES  = 15    # L1 must be older than 15 min (real "memory")
MIN_BOUNCE_PCT          = 15    # First bounce from L1 must be ≥15%
RETEST_TOLERANCE_PCT    = 6     # Re-test must be within 6% of L1
MIN_DATA_POINTS         = 30    # Need ≥30 ticks for valid analysis

# Confirmation thresholds
CONFIRM_BULLISH_CANDLE_PCT  = 2.0   # Last tick > previous by 2%
CONFIRM_WICK_PCT            = 3.0   # Recent low → current ≥ 3% (rejection)

# Premium range filter (avoid ultra-cheap and ultra-expensive)
MIN_PREMIUM = 30
MAX_PREMIUM = 500

# Per-key cooldown
COOLDOWN_MINUTES = 30


class ReversalZoneTracker:
    """Detects double-bottom reversal patterns in option premium."""

    def __init__(self, engine):
        self.engine = engine
        self._fired_signals = {}  # {(idx, strike, side): last_fire_unix_ts}

    # ── Core detection ─────────────────────────────────────────────

    def scan_strike(self, idx, strike, side):
        """Scan one strike for double-bottom setup.

        Returns:
            None  if no setup
            dict  with entry signal if setup detected
        """
        key = (idx, strike, side.upper())

        # Cooldown check — don't re-fire same setup
        last_fire = self._fired_signals.get(key)
        if last_fire and (time.time() - last_fire) < COOLDOWN_MINUTES * 60:
            return None

        # Pull premium history
        history = self.engine.ltp_history.get(key, [])
        if len(history) < MIN_DATA_POINTS:
            return None

        # Filter to last LOOKBACK_MINUTES
        cutoff = ist_now() - timedelta(minutes=LOOKBACK_MINUTES)
        recent = []
        for h in history:
            try:
                t = datetime.fromisoformat(h["t"])
                if t.tzinfo is None:
                    t = IST.localize(t)
                if t >= cutoff and h.get("ltp", 0) > 0:
                    recent.append(h)
            except Exception:
                continue

        if len(recent) < MIN_DATA_POINTS:
            return None

        ltps = [h["ltp"] for h in recent]
        current_ltp = ltps[-1]

        # Premium-range filter
        if current_ltp < MIN_PREMIUM or current_ltp > MAX_PREMIUM:
            return None

        # ── Find L1: lowest point in older portion (>15 min ago) ──
        cutoff_recent = ist_now() - timedelta(minutes=EXCLUDE_RECENT_MINUTES)
        older = []
        for h in recent:
            try:
                t = datetime.fromisoformat(h["t"])
                if t.tzinfo is None:
                    t = IST.localize(t)
                if t < cutoff_recent:
                    older.append(h)
            except Exception:
                continue

        if len(older) < 10:
            return None

        l1_ltp = min(h["ltp"] for h in older)
        # Find L1 index in `recent` (by matching ltp)
        l1_idx = None
        for i, h in enumerate(recent):
            if abs(h["ltp"] - l1_ltp) < 0.01:
                l1_idx = i
                break

        if l1_idx is None or l1_ltp <= 0:
            return None

        # ── Validate bounce: peak between L1 and now must be ≥15% above L1 ──
        between_l1_now = ltps[l1_idx:]
        if not between_l1_now:
            return None

        peak_after_l1 = max(between_l1_now)
        bounce_pct = ((peak_after_l1 - l1_ltp) / l1_ltp) * 100
        if bounce_pct < MIN_BOUNCE_PCT:
            return None  # No real bounce — L1 wasn't true support

        # ── Re-test check: current must be near L1 (within tolerance) ──
        distance_pct = ((current_ltp - l1_ltp) / l1_ltp) * 100
        if distance_pct < 0 or distance_pct > RETEST_TOLERANCE_PCT:
            return None  # Not in the re-test zone

        # ── Confirmation checks ──
        confirmations = []

        # 1. Bullish candle: last tick clearly above previous tick
        if len(ltps) >= 3:
            recent_change_pct = ((ltps[-1] - ltps[-2]) / max(ltps[-2], 1)) * 100
            if recent_change_pct >= CONFIRM_BULLISH_CANDLE_PCT:
                confirmations.append(f"bullish_candle({recent_change_pct:+.1f}%)")

        # 2. Lower-wick rejection: recent low → current is ≥3% bounce
        last_window = ltps[-min(10, len(ltps)):]
        recent_low = min(last_window)
        if recent_low > 0:
            wick_pct = ((current_ltp - recent_low) / recent_low) * 100
            if wick_pct >= CONFIRM_WICK_PCT:
                confirmations.append(f"wick_reject({wick_pct:+.1f}%)")

        # Need at least 1 confirmation
        if not confirmations:
            return None

        # ── FIRE: signal valid ──
        self._fired_signals[key] = time.time()

        return {
            "idx": idx,
            "strike": strike,
            "side": side.upper(),
            "action": f"BUY {side.upper()}",
            "entry_price": round(current_ltp, 2),
            "l1_low": round(l1_ltp, 2),
            "peak_after_l1": round(peak_after_l1, 2),
            "bounce_pct": round(bounce_pct, 1),
            "distance_from_l1_pct": round(distance_pct, 1),
            "confirmations": confirmations,
            "source": "reversal_zone",
            "reasoning": (
                f"Reversal zone @ ₹{l1_ltp:.0f} (peaked ₹{peak_after_l1:.0f}, "
                f"+{bounce_pct:.0f}%). Re-test {distance_pct:+.1f}% from L1. "
                f"Confirms: {' + '.join(confirmations)}."
            ),
        }

    def scan_atm_strikes(self, idx):
        """Scan ATM ± 1 strike CE+PE. Return first valid signal (or None).

        Strategy: scan ATM, ATM+gap, ATM-gap (so 6 combos for CE+PE).
        Returns first valid signal by precedence: ATM > nearby strikes.
        """
        try:
            spot_token = self.engine.spot_tokens.get(idx)
            if not spot_token:
                return None
            spot_ltp = self.engine.prices.get(spot_token, {}).get("ltp", 0)
            if spot_ltp <= 0:
                return None

            # Strike gap from engine's INDEX_CONFIG
            try:
                from engine import INDEX_CONFIG
                cfg = INDEX_CONFIG.get(idx, {})
                strike_gap = cfg.get("strike_gap", 50)
            except Exception:
                strike_gap = 50 if idx == "NIFTY" else 100

            atm = round(spot_ltp / strike_gap) * strike_gap

            # Scan offsets in priority order: ATM first, then ±1
            for offset in [0, 1, -1]:
                strike = int(atm + offset * strike_gap)
                for side in ["CE", "PE"]:
                    signal = self.scan_strike(idx, strike, side)
                    if signal:
                        return signal

            return None
        except Exception as e:
            print(f"[REVERSAL] {idx} scan error: {e}")
            return None
