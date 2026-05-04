"""
Spread + Liquidity Filter
─────────────────────────
Pre-entry quality check on bid-ask spread % and book depth.
Buyer pays ASK and sells BID — wide spread = guaranteed bleed.

GATES:
  spread > 2%         → HARD BLOCK (illiquid)
  spread 1.5-2%       → WARN, suggest reduce qty 50%
  spread 0.5-1.5%     → OK
  spread < 0.5%       → bonus quality (tight book)

  depth_5_lots < 500  → WARN (thin book, exit risk)

WORKS WITH:
  - Read-only check, returns (allow, reason, qty_multiplier)
  - Plugged into should_enter_trade as quality gate
  - Doesn't change existing entry logic, just adds a check

DATA SOURCE:
  - Kite chain provides bid, ask, bid_qty, ask_qty per strike
  - Compute spread + depth live (no DB needed for analysis)
"""

import sqlite3
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple, Optional, List


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
LOG_DB_PATH = str(_DATA_DIR / "spread_filter.db")


# ── Thresholds ────────────────────────────────────────────────────────

SPREAD_HARD_BLOCK = 2.0        # > 2% spread → block
SPREAD_WARN = 1.5              # 1.5-2% spread → reduce qty
SPREAD_OK_UPPER = 1.5
SPREAD_TIGHT = 0.5             # < 0.5% → bonus

DEPTH_THIN_THRESHOLD = 500     # top-5 bid+ask total qty < this = thin book


# ── DB init ───────────────────────────────────────────────────────────

def _init_db():
    conn = sqlite3.connect(LOG_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spread_block_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            idx TEXT,
            strike INTEGER,
            side TEXT,
            spread_pct REAL,
            depth_5 INTEGER,
            verdict TEXT,        -- BLOCK / WARN / OK / TIGHT
            mid REAL,
            bid REAL,
            ask REAL,
            qty_multiplier REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sb_ts ON spread_block_log(ts)")
    conn.commit()
    conn.close()


# ── Core compute ──────────────────────────────────────────────────────

def compute_spread_metrics(strike_data: Dict, side: str) -> Optional[Dict]:
    """
    Pull spread + depth from chain row.

    side: 'CE' or 'PE'

    Returns:
      None if data missing
      dict with mid, bid, ask, spread_pct, depth_5, verdict, qty_multiplier
    """
    if not isinstance(strike_data, dict):
        return None

    # Try multiple key formats Kite might use
    bid_key = f"{side.lower()}_bid"
    ask_key = f"{side.lower()}_ask"
    bid_qty_key = f"{side.lower()}_bid_qty"
    ask_qty_key = f"{side.lower()}_ask_qty"
    ltp_key = f"{side.lower()}_ltp"

    bid = strike_data.get(bid_key, 0) or 0
    ask = strike_data.get(ask_key, 0) or 0
    bid_qty = strike_data.get(bid_qty_key, 0) or 0
    ask_qty = strike_data.get(ask_qty_key, 0) or 0
    ltp = strike_data.get(ltp_key, 0) or 0

    # Fallback: depth array
    if (bid <= 0 or ask <= 0) and "depth" in strike_data:
        depth = strike_data.get("depth", {}).get(side.lower(), {})
        if isinstance(depth, dict):
            buy = depth.get("buy", [])
            sell = depth.get("sell", [])
            if buy and sell:
                bid = buy[0].get("price", 0) if buy else 0
                ask = sell[0].get("price", 0) if sell else 0
                bid_qty = buy[0].get("quantity", 0) if buy else 0
                ask_qty = sell[0].get("quantity", 0) if sell else 0

    # If still missing, fall back to LTP-based estimate (50bps assumption)
    if bid <= 0 or ask <= 0:
        if ltp > 0:
            # Conservative estimate: assume 1% spread when bid/ask unavailable
            bid = round(ltp * 0.995, 2)
            ask = round(ltp * 1.005, 2)
            spread_pct = 1.0
            return {
                "mid": ltp, "bid": bid, "ask": ask,
                "spread_pct": spread_pct, "depth_5": 0,
                "verdict": "OK", "qty_multiplier": 1.0,
                "data_source": "estimated_from_ltp",
            }
        return None

    if ask <= bid:
        return None

    mid = (bid + ask) / 2
    if mid <= 0:
        return None

    spread_pct = (ask - bid) / mid * 100
    depth_5 = bid_qty + ask_qty

    # Verdict + qty multiplier
    verdict = "OK"
    qty_multiplier = 1.0

    if spread_pct > SPREAD_HARD_BLOCK:
        verdict = "BLOCK"
        qty_multiplier = 0.0  # block
    elif spread_pct > SPREAD_WARN:
        verdict = "WARN"
        qty_multiplier = 0.5  # half size
    elif spread_pct < SPREAD_TIGHT:
        verdict = "TIGHT"
        qty_multiplier = 1.0  # full size with quality boost

    # Thin-book overlay
    if depth_5 > 0 and depth_5 < DEPTH_THIN_THRESHOLD:
        if verdict == "OK" or verdict == "TIGHT":
            verdict = "WARN"
            qty_multiplier = min(qty_multiplier, 0.5)

    return {
        "mid": round(mid, 2),
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "bid_qty": int(bid_qty),
        "ask_qty": int(ask_qty),
        "spread_pct": round(spread_pct, 2),
        "depth_5": depth_5,
        "verdict": verdict,
        "qty_multiplier": qty_multiplier,
        "data_source": "live",
    }


# ── Entry gate ────────────────────────────────────────────────────────

def check_spread_gate(engine, idx: str, action: str, strike: int) -> Tuple[bool, str, float]:
    """Pre-entry spread/liquidity check.

    Returns:
      (allowed, reason, qty_multiplier)
      allowed=False     → block entry
      qty_multiplier<1  → soft warn (caller can apply or ignore)
    """
    try:
        chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
        if not chain:
            return True, "no chain data — allow", 1.0

        sd = chain.get(strike) or chain.get(str(strike)) or {}
        if not sd:
            return True, "strike not in chain — allow", 1.0

        side = "CE" if "CE" in action.upper() else "PE"
        m = compute_spread_metrics(sd, side)
        if not m:
            return True, "no bid/ask data — allow", 1.0

        # Log if interesting
        if m["verdict"] in ("BLOCK", "WARN"):
            try:
                _init_db()
                conn = sqlite3.connect(LOG_DB_PATH)
                conn.execute("""
                    INSERT INTO spread_block_log
                    (ts, idx, strike, side, spread_pct, depth_5, verdict, mid, bid, ask, qty_multiplier)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (time.time(), idx, int(strike), side,
                      m["spread_pct"], m["depth_5"], m["verdict"],
                      m["mid"], m["bid"], m["ask"], m["qty_multiplier"]))
                conn.commit()
                conn.close()
            except Exception:
                pass

        if m["verdict"] == "BLOCK":
            return False, (
                f"WIDE_SPREAD: {idx} {strike} {side} spread {m['spread_pct']:.2f}% "
                f"(bid ₹{m['bid']} / ask ₹{m['ask']}) — illiquid, slippage death"
            ), 0.0

        if m["verdict"] == "WARN":
            return True, (
                f"WIDE_SPREAD: spread {m['spread_pct']:.2f}% — reduce qty 50% recommended"
            ), m["qty_multiplier"]

        if m["verdict"] == "TIGHT":
            return True, f"TIGHT_BOOK: spread {m['spread_pct']:.2f}% — quality entry", 1.0

        return True, "spread OK", 1.0
    except Exception as e:
        # Never block on filter error
        return True, f"spread filter err: {e}", 1.0


# ── API helpers ───────────────────────────────────────────────────────

def get_strike_liquidity(engine, idx: str, strike: int) -> Dict:
    """Live liquidity assessment of one strike — both CE and PE."""
    chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
    sd = chain.get(strike) or chain.get(str(strike)) or {}
    if not sd:
        return {"error": "strike not found"}
    return {
        "idx": idx, "strike": strike,
        "ce": compute_spread_metrics(sd, "CE"),
        "pe": compute_spread_metrics(sd, "PE"),
    }


def get_chain_liquidity(engine, idx: str) -> Dict:
    """Liquidity scan of all NTM strikes for an index."""
    chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
    spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0

    gap = 50 if idx == "NIFTY" else 100
    atm = round(spot / gap) * gap if spot > 0 else 0

    strikes_data = []
    for offset in range(-10, 11):
        strike = atm + offset * gap
        sd = chain.get(strike) or chain.get(str(strike)) or {}
        if not sd:
            continue
        ce = compute_spread_metrics(sd, "CE")
        pe = compute_spread_metrics(sd, "PE")
        strikes_data.append({
            "strike": strike, "is_atm": (strike == atm),
            "ce": ce, "pe": pe,
        })
    return {"idx": idx, "atm": atm, "spot": spot, "strikes": strikes_data}


def get_blocks_today(idx: Optional[str] = None, limit: int = 50) -> List[Dict]:
    _init_db()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    conn = sqlite3.connect(LOG_DB_PATH)
    if idx:
        rows = conn.execute("""
            SELECT ts, idx, strike, side, spread_pct, depth_5, verdict, mid, bid, ask
            FROM spread_block_log WHERE idx=? AND ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (idx.upper(), today_start, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT ts, idx, strike, side, spread_pct, depth_5, verdict, mid, bid, ask
            FROM spread_block_log WHERE ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (today_start, limit)).fetchall()
    conn.close()
    return [{
        "ts": r[0], "idx": r[1], "strike": r[2], "side": r[3],
        "spread_pct": r[4], "depth_5": r[5], "verdict": r[6],
        "mid": r[7], "bid": r[8], "ask": r[9],
    } for r in rows]
