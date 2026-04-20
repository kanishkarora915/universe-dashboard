"""
Smart Money Detector — Tracks institutional footprint in options chain.

Big players (FIIs, prop desks, HNI blocks) leave signatures:
1. SLOW COOKING OI — consistent 5-15k OI additions every 5 min at same strike
2. ICEBERG — round-number OI changes (5k, 10k, 15k) repeated = algo writing
3. BLOCK TRADES — >50k OI change in single 5-min window
4. WRITER CLASSIFICATION — price vs OI direction tells who's writing/covering

Strategy: Trade WITH smart money, not against it.
- If sellers (whales) writing PE = they support current price = BULLISH (buy CE)
- If sellers writing CE = they resist current price = BEARISH (buy PE)
- If short covering (unwinding) = trap exit = trade the REVERSAL
"""

import time
from collections import deque, defaultdict
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist_now():
    return datetime.now(IST)


class SmartMoneyState:
    """Per-index per-strike rolling OI snapshots for whale tracking.
    Engine holds one instance; calls record_chain_snapshot every 60s."""

    SNAPSHOT_INTERVAL_SEC = 60      # Record every 60s
    WINDOW_COOK = 15 * 60           # 15-min window for slow-cook detection
    MIN_COOK_THRESHOLD = 8000       # 8k+ OI add per snapshot = whale activity
    MIN_BLOCK_THRESHOLD = 50000     # 50k+ in single window = block trade
    MIN_CONSISTENCY = 3             # 3 consecutive snapshots = confirmed

    def __init__(self):
        # {(index, strike, side): deque of (ts, oi, price, volume)}
        self.snapshots = defaultdict(lambda: deque(maxlen=60))  # 60 snaps = 1 hour
        self.last_snapshot_ts = 0

    def record_chain_snapshot(self, engine):
        """Called every 60s from engine tick loop. Records ATM±6 CE+PE OI."""
        from engine import INDEX_CONFIG
        now = time.time()
        if now - self.last_snapshot_ts < self.SNAPSHOT_INTERVAL_SEC:
            return
        self.last_snapshot_ts = now

        for idx in ["NIFTY", "BANKNIFTY"]:
            cfg = INDEX_CONFIG.get(idx, {})
            spot_token = engine.spot_tokens.get(idx)
            spot = engine.prices.get(spot_token, {}).get("ltp", 0) if spot_token else 0
            if spot <= 0:
                continue
            atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"]
            chain = engine.chains.get(idx, {})
            for offset in range(-6, 7):
                strike = atm + offset * cfg["strike_gap"]
                sd = chain.get(strike, {})
                if not sd:
                    continue
                for side in ["CE", "PE"]:
                    oi = sd.get(f"{side.lower()}_oi", 0)
                    ltp = sd.get(f"{side.lower()}_ltp", 0)
                    vol = sd.get(f"{side.lower()}_volume", 0)
                    if oi > 0:
                        self.snapshots[(idx, strike, side)].append((now, oi, ltp, vol))

    def _get_price_direction(self, engine, idx, window_sec=900):
        """Spot price direction over last N seconds. Returns +1/0/-1."""
        # Use predictive state if available
        ps = getattr(engine, "predictive_state", None)
        if ps:
            pct, _ = ps.spot_velocity(idx, window_sec)
            if pct > 0.1:
                return 1
            if pct < -0.1:
                return -1
        return 0

    def detect_slow_cooking(self, engine, idx, price_direction):
        """Find strikes where whales are slowly building OI.

        Returns list of signals:
          [{strike, side, oi_delta, consecutive, interpretation, bullish/bearish pts}]

        Interpretation based on price_direction:
        - CE writing during UP move = resistance cooking (BEARISH)
        - PE writing during UP move = support building (BULLISH)
        - CE writing during DOWN move = sellers confident in downside (BEARISH)
        - PE writing during DOWN move = fresh puts = can flip bullish (trap/reversal)
        """
        signals = []
        for (i, strike, side), buf in self.snapshots.items():
            if i != idx or len(buf) < self.MIN_CONSISTENCY + 1:
                continue

            # Last 3 snapshots OI deltas
            recent = list(buf)[-4:]  # 4 entries = 3 deltas
            deltas = []
            for j in range(1, len(recent)):
                delta = recent[j][1] - recent[j-1][1]
                deltas.append(delta)

            # Slow cooking: all 3 deltas positive AND avg >= threshold
            all_positive = all(d > 0 for d in deltas)
            all_negative = all(d < 0 for d in deltas)
            avg_delta = sum(deltas) / len(deltas) if deltas else 0

            if all_positive and avg_delta >= self.MIN_COOK_THRESHOLD:
                total_added = sum(deltas)
                signal = {
                    "strike": strike,
                    "side": side,
                    "direction": "WRITING",
                    "oi_delta": total_added,
                    "avg_per_snapshot": int(avg_delta),
                    "consecutive_snapshots": len(deltas),
                    "confidence": "HIGH" if avg_delta >= 15000 else "MEDIUM",
                }
                signal["interpretation"], signal["bullish_pts"], signal["bearish_pts"] = \
                    self._interpret_writing(side, price_direction, avg_delta)
                signals.append(signal)

            elif all_negative and abs(avg_delta) >= self.MIN_COOK_THRESHOLD:
                total_removed = abs(sum(deltas))
                signal = {
                    "strike": strike,
                    "side": side,
                    "direction": "UNWINDING",
                    "oi_delta": -total_removed,
                    "avg_per_snapshot": int(avg_delta),
                    "consecutive_snapshots": len(deltas),
                    "confidence": "HIGH" if abs(avg_delta) >= 15000 else "MEDIUM",
                }
                signal["interpretation"], signal["bullish_pts"], signal["bearish_pts"] = \
                    self._interpret_unwinding(side, price_direction, abs(avg_delta))
                signals.append(signal)

        return signals

    def _interpret_writing(self, side, price_dir, avg_delta):
        """What does FRESH WRITING mean given price direction?"""
        pts = 5 if avg_delta >= 15000 else 3

        if side == "CE":
            if price_dir >= 0:
                # CE writing while price flat/up = BEARISH (resistance forming)
                return (f"CE writers building resistance — cap above current price", 0, pts + 2)
            else:
                # CE writing during down = sellers confident
                return (f"CE writers during decline — confirms bearish", 0, pts)
        else:  # PE
            if price_dir >= 0:
                # PE writing during up move = BULLISH (support building)
                return (f"PE writers supporting price — floor established", pts + 2, 0)
            else:
                # PE writing during down = fresh shorts OR trap
                return (f"PE writers during decline — fresh shorts, watch for reversal", pts // 2, pts)

    def _interpret_unwinding(self, side, price_dir, avg_delta):
        """What does UNWINDING (OI removal) mean?"""
        pts = 5 if avg_delta >= 15000 else 3

        if side == "CE":
            if price_dir > 0:
                # CE short covering during rise = BULLISH (resistance breaking)
                return (f"CE short covering — resistance breaking, breakout ahead", pts + 3, 0)
            else:
                return (f"CE unwinding — mixed signal", pts // 2, pts // 2)
        else:  # PE
            if price_dir < 0:
                # PE short covering during fall = BEARISH (support broken)
                return (f"PE short covering — support broken, more downside", 0, pts + 3)
            else:
                return (f"PE unwinding — mixed signal", pts // 2, pts // 2)

    def detect_block_trades(self, engine, idx):
        """Detect single-window OI jumps >50k at specific strikes."""
        signals = []
        for (i, strike, side), buf in self.snapshots.items():
            if i != idx or len(buf) < 2:
                continue
            last_2 = list(buf)[-2:]
            delta = last_2[1][1] - last_2[0][1]
            if abs(delta) >= self.MIN_BLOCK_THRESHOLD:
                signals.append({
                    "strike": strike,
                    "side": side,
                    "oi_delta": delta,
                    "direction": "WRITING" if delta > 0 else "UNWINDING",
                    "type": "BLOCK_TRADE",
                    "confidence": "VERY_HIGH",
                })
        return signals

    def detect_iceberg(self, engine, idx):
        """Detect consistent round-number OI additions = algo writing.
        Retail doesn't write in exact 5k/10k multiples repeatedly."""
        signals = []
        for (i, strike, side), buf in self.snapshots.items():
            if i != idx or len(buf) < 5:
                continue
            last_5 = list(buf)[-5:]
            deltas = [last_5[j][1] - last_5[j-1][1] for j in range(1, len(last_5))]

            # Check if deltas are in "round" multiples of 1000
            # (allowing ±10% tolerance for partial fills)
            round_count = 0
            for d in deltas:
                if d > 0 and abs(d - round(d / 1000) * 1000) / max(abs(d), 1) < 0.1 and d >= 3000:
                    round_count += 1
            if round_count >= 3:
                signals.append({
                    "strike": strike,
                    "side": side,
                    "deltas": deltas,
                    "type": "ICEBERG",
                    "confidence": "HIGH",
                    "interpretation": f"Algo {side} writing at {strike} — consistent round lots",
                })
        return signals


def score_smart_money(state: SmartMoneyState, engine, index: str):
    """Score smart money signals for verdict integration.

    Returns bull/bear pts (max 25 each side) + reason list.
    This is ADDITIVE to existing engines.
    """
    if not state.snapshots:
        return {"bullScore": 0, "bearScore": 0, "reasons": [], "signals": {}}

    price_dir = state._get_price_direction(engine, index)

    cooking = state.detect_slow_cooking(engine, index, price_dir)
    blocks = state.detect_block_trades(engine, index)
    icebergs = state.detect_iceberg(engine, index)

    bull_total = 0
    bear_total = 0
    reasons = []

    # Aggregate slow cooking signals (cap at 15 pts)
    for sig in cooking:
        bull_total += sig.get("bullish_pts", 0)
        bear_total += sig.get("bearish_pts", 0)
        side = sig["side"]
        strike = sig["strike"]
        direction = sig["direction"]
        conf = sig["confidence"]
        if sig.get("bullish_pts", 0) > 0:
            reasons.append(f"WHALE {side} {direction} at {strike} ({conf}): {sig['interpretation']} [+{sig['bullish_pts']}pts bull]")
        elif sig.get("bearish_pts", 0) > 0:
            reasons.append(f"WHALE {side} {direction} at {strike} ({conf}): {sig['interpretation']} [+{sig['bearish_pts']}pts bear]")

    bull_total = min(bull_total, 15)
    bear_total = min(bear_total, 15)

    # Block trades (high-conviction, 5 pts each, cap 10)
    block_pts = 0
    for b in blocks[:2]:
        side = b["side"]
        direction = b["direction"]
        # Block writing CE = bearish, PE = bullish; unwinding opposite
        if (side == "CE" and direction == "WRITING") or (side == "PE" and direction == "UNWINDING"):
            bear_total = min(bear_total + 5, 25)
            reasons.append(f"BLOCK TRADE: {side} {direction} {b['oi_delta']:+,} at {b['strike']} [5pts bear]")
        elif (side == "PE" and direction == "WRITING") or (side == "CE" and direction == "UNWINDING"):
            bull_total = min(bull_total + 5, 25)
            reasons.append(f"BLOCK TRADE: {side} {direction} {b['oi_delta']:+,} at {b['strike']} [5pts bull]")

    # Iceberg adds conviction (3 pts)
    for ice in icebergs[:2]:
        if ice["side"] == "CE":
            bear_total = min(bear_total + 3, 25)
        else:
            bull_total = min(bull_total + 3, 25)
        reasons.append(f"ICEBERG: {ice['interpretation']} [3pts]")

    return {
        "bullScore": bull_total,
        "bearScore": bear_total,
        "reasons": reasons[:5],
        "signals": {
            "cooking": cooking[:5],
            "blocks": blocks[:3],
            "icebergs": icebergs[:3],
        },
        "priceDir": price_dir,
    }


def is_smart_money_aligned(smart_money_result: dict, action: str) -> bool:
    """Does smart money agree with our intended trade direction?
    Used for position sizing — more size when whales agree."""
    if not smart_money_result:
        return False
    bull = smart_money_result.get("bullScore", 0)
    bear = smart_money_result.get("bearScore", 0)
    if "CE" in action and bull >= 10 and bull > bear * 1.5:
        return True
    if "PE" in action and bear >= 10 and bear > bull * 1.5:
        return True
    return False
