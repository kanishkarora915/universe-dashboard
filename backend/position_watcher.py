"""
Position Watcher
────────────────
30-second orchestrator that monitors all OPEN positions in BOTH:
  • Main P&L (trades.db)
  • Scalper (scalper_trades.db)

For each open trade:
  1. Pull live spot/VIX/premium from engine
  2. Push samples into velocity trackers
  3. Build 5-min candles from spot tick history
  4. Compute composite health score (0-10)
  5. Fire one of 6 triggers if applicable:
     - REVERSAL_PATTERN  → exit @ market
     - VIX_CRUSH         → tighten SL to entry +1%
     - THETA_WINS        → exit @ market
     - DAY_HIGH_TRAP     → exit @ market
     - POST_LUNCH_STALL  → exit before timeout
     - PATTERN_LOSER     → block entries + exit current
  6. Log every action with full reason chain

Auto-exit is OFF by default, controlled per mode via settings.
Health scores are computed regardless and exposed via API.
"""

import time
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict, deque
from typing import Dict, List, Optional, Any
import os
from pathlib import Path

from candle_pattern_engine import build_candles_from_ticks, detect_patterns
from vix_velocity import push_vix, get_tracker as get_vix_tracker
from premium_velocity import register as prem_register, push as prem_push, assess as prem_assess, get_tracker as get_prem_tracker
from health_score import compute_health


# ──────────────────────────────────────────────────────────────
# Config & state
# ──────────────────────────────────────────────────────────────

# Match the same pattern used by scalper_mode.py / trade_logger.py:
#   /data/  on Render (persistent disk),  backend/  locally.
_DATA_DIR_PATH = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DATA_DIR = str(_DATA_DIR_PATH)
WATCHER_DB = str(_DATA_DIR_PATH / "position_watcher.db")

DEFAULT_CONFIG = {
    "auto_exit_main": False,      # Auto-exit on PnL trades
    "auto_exit_scalper": False,   # Auto-exit on Scalper trades
    "tight_sl_main": True,        # Auto-tighten SL on warning
    "tight_sl_scalper": True,
    "min_score_for_exit": 3,      # Below this = auto-exit if enabled
    "min_score_for_tight_sl": 5,
}


def init_watcher_db():
    conn = sqlite3.connect(WATCHER_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spot_ticks (
            idx TEXT,
            ts REAL,
            spot REAL,
            volume REAL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spot_ts ON spot_ticks(idx, ts)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_log (
            ts REAL,
            source TEXT,         -- 'MAIN' or 'SCALPER'
            trade_id INTEGER,
            idx TEXT,
            action TEXT,
            score REAL,
            verdict TEXT,
            reasons TEXT,        -- JSON
            triggered TEXT,      -- trigger name if any
            action_taken TEXT    -- 'EXIT'|'TIGHT_SL'|'NONE'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_health_ts ON health_log(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_health_trade ON health_log(source, trade_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_ticks (
            source TEXT,
            trade_id INTEGER,
            ts REAL,
            premium REAL,
            spot REAL,
            score REAL,
            pnl_pct REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pt ON position_ticks(source, trade_id, ts)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS exit_log (
            ts REAL,
            source TEXT,
            trade_id INTEGER,
            idx TEXT,
            action TEXT,
            strike INTEGER,
            entry_price REAL,
            exit_price REAL,
            pnl_rupees REAL,
            trigger TEXT,
            reasons TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exit_ts ON exit_log(ts)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS watcher_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_config() -> Dict:
    init_watcher_db()
    conn = sqlite3.connect(WATCHER_DB)
    rows = conn.execute("SELECT key, value FROM watcher_config").fetchall()
    conn.close()
    cfg = dict(DEFAULT_CONFIG)
    for k, v in rows:
        if k in cfg:
            try:
                if isinstance(cfg[k], bool):
                    cfg[k] = v in ("1", "true", "True", True)
                elif isinstance(cfg[k], (int, float)):
                    cfg[k] = float(v) if "." in str(v) else int(v)
                else:
                    cfg[k] = v
            except Exception:
                pass
    return cfg


def set_config(**kwargs):
    init_watcher_db()
    conn = sqlite3.connect(WATCHER_DB)
    for k, v in kwargs.items():
        if k in DEFAULT_CONFIG:
            conn.execute(
                "INSERT OR REPLACE INTO watcher_config(key, value) VALUES(?,?)",
                (k, str(v))
            )
    conn.commit()
    conn.close()
    return get_config()


# ──────────────────────────────────────────────────────────────
# Spot tick recording (for candle building)
# ──────────────────────────────────────────────────────────────

def record_spot_tick(idx: str, spot: float, volume: float = 0):
    """Record a spot tick. Engine should call this every cycle."""
    if spot <= 0:
        return
    init_watcher_db()
    try:
        conn = sqlite3.connect(WATCHER_DB)
        conn.execute(
            "INSERT INTO spot_ticks(idx, ts, spot, volume) VALUES(?,?,?,?)",
            (idx.upper(), time.time(), float(spot), float(volume or 0))
        )
        # Cleanup ticks older than 6 hours (intraday only)
        cutoff = time.time() - 6 * 3600
        conn.execute("DELETE FROM spot_ticks WHERE ts < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WATCHER] tick record error: {e}")


def get_recent_spot_ticks(idx: str, minutes: int = 60) -> List[Dict]:
    init_watcher_db()
    try:
        conn = sqlite3.connect(WATCHER_DB)
        cutoff = time.time() - minutes * 60
        rows = conn.execute(
            "SELECT ts, spot, volume FROM spot_ticks WHERE idx=? AND ts >= ? ORDER BY ts ASC",
            (idx.upper(), cutoff)
        ).fetchall()
        conn.close()
        return [{"ts": r[0] * 1000, "price": r[1], "volume": r[2]} for r in rows]
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────

def _trades_db_path():
    return os.path.join(DATA_DIR, "trades.db")


def _scalper_db_path():
    return os.path.join(DATA_DIR, "scalper_trades.db")


def _get_open_main_trades() -> List[Dict]:
    try:
        conn = sqlite3.connect(_trades_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='OPEN'"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[WATCHER] main trades read error: {e}")
        return []


def _get_open_scalper_trades() -> List[Dict]:
    try:
        conn = sqlite3.connect(_scalper_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM scalper_trades WHERE status='OPEN'"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[WATCHER] scalper trades read error: {e}")
        return []


def _count_today_similar_losses(source: str, action: str, idx: str) -> int:
    """Count today's losses on same idx + same action (CE/PE)."""
    db = _trades_db_path() if source == "MAIN" else _scalper_db_path()
    table = "trades" if source == "MAIN" else "scalper_trades"
    today = datetime.now().strftime("%Y-%m-%d")
    is_ce = "CE" in action.upper()
    action_filter = "CE" if is_ce else "PE"
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute(f"""
            SELECT COUNT(*) FROM {table}
            WHERE entry_time LIKE ? AND idx=? AND action LIKE ?
              AND status IN ('SL_HIT','TIMEOUT_EXIT','SPOT_ANCHOR_EXIT','STOP_HUNTED','EOD_CLOSE')
              AND pnl_rupees < 0
        """, (f"{today}%", idx, f"%{action_filter}%")).fetchone()
        conn.close()
        return rows[0] if rows else 0
    except Exception:
        return 0


def _get_day_extremes(idx: str) -> tuple:
    """Get day high/low for an index from spot_ticks."""
    init_watcher_db()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        conn = sqlite3.connect(WATCHER_DB)
        row = conn.execute(
            "SELECT MAX(spot), MIN(spot) FROM spot_ticks WHERE idx=? AND ts >= ?",
            (idx.upper(), today_start)
        ).fetchone()
        conn.close()
        return (row[0] or 0, row[1] or 0)
    except Exception:
        return (0, 0)


def _log_health(source: str, trade_id: int, idx: str, action: str,
                health: Dict, triggered: Optional[str], action_taken: str):
    init_watcher_db()
    try:
        conn = sqlite3.connect(WATCHER_DB)
        conn.execute("""
            INSERT INTO health_log(ts, source, trade_id, idx, action,
                score, verdict, reasons, triggered, action_taken)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (
            time.time(), source, trade_id, idx, action,
            health.get("score"), health.get("verdict"),
            json.dumps(health.get("reasons", [])),
            triggered, action_taken
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WATCHER] log_health error: {e}")


def _log_exit(source: str, trade: Dict, exit_price: float, trigger: str, reasons: List[str]):
    init_watcher_db()
    try:
        pnl = (exit_price - trade.get("entry_price", 0)) * trade.get("qty", 0)
        conn = sqlite3.connect(WATCHER_DB)
        conn.execute("""
            INSERT INTO exit_log(ts, source, trade_id, idx, action, strike,
                entry_price, exit_price, pnl_rupees, trigger, reasons)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (
            time.time(), source, trade["id"], trade.get("idx"),
            trade.get("action"), trade.get("strike"),
            trade.get("entry_price"), exit_price, pnl, trigger,
            json.dumps(reasons)
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WATCHER] log_exit error: {e}")


# ──────────────────────────────────────────────────────────────
# Trigger evaluation
# ──────────────────────────────────────────────────────────────

def _evaluate_triggers(trade: Dict, health: Dict, action: str) -> Optional[str]:
    """
    Determine which trigger (if any) is firing.
    Priority order: REVERSAL > THETA > VIX_CRUSH > DAY_HIGH_TRAP > POST_LUNCH > PATTERN_LOSER
    """
    comps = health.get("components", {})
    profit_pct = health.get("profit_pct", 0)
    hold_min = health.get("hold_minutes", 0)

    candle = comps.get("candle", {})
    if candle.get("penalty", 0) >= 1.5 and profit_pct < 5:
        # Strong reversal pattern post-entry, not in profit
        return "REVERSAL_PATTERN"

    prem = comps.get("premium", {})
    if prem.get("theta_winning") and profit_pct < 0:
        return "THETA_WINS"

    vix = comps.get("vix", {})
    if vix.get("severity") == "HIGH" and profit_pct < 5:
        return "VIX_CRUSH"

    prox = comps.get("proximity", {})
    if prox.get("penalty", 0) >= 1.0:
        return "DAY_HIGH_TRAP"

    timec = comps.get("time", {})
    entry_iso = trade.get("entry_time", "")
    try:
        entry_dt = datetime.fromisoformat(entry_iso) if entry_iso else None
        if entry_dt and entry_dt.tzinfo is not None:
            entry_dt = entry_dt.replace(tzinfo=None)
    except Exception:
        entry_dt = None
    is_post_lunch = entry_dt and entry_dt.hour >= 13
    if is_post_lunch and hold_min >= 45 and profit_pct < 2:
        return "POST_LUNCH_STALL"

    pat = comps.get("pattern", {})
    if pat.get("penalty", 0) >= 1.0:
        return "PATTERN_LOSER"

    return None


# ──────────────────────────────────────────────────────────────
# SL tightening helpers
# ──────────────────────────────────────────────────────────────

def _tighten_main_sl(trade_id: int, new_sl: float, reason: str):
    try:
        conn = sqlite3.connect(_trades_db_path())
        conn.execute(
            "UPDATE trades SET sl_price=?, alerts=COALESCE(alerts,'') || ? WHERE id=? AND status='OPEN'",
            (new_sl, f" | watcher: {reason}", trade_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[WATCHER] tighten main SL error: {e}")
        return False


def _tighten_scalper_sl(trade_id: int, new_sl: float, reason: str):
    try:
        conn = sqlite3.connect(_scalper_db_path())
        # scalper has both static sl_price + smart_sl_value
        conn.execute(
            "UPDATE scalper_trades SET sl_price=?, smart_sl_value=? WHERE id=? AND status='OPEN'",
            (new_sl, new_sl, trade_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[WATCHER] tighten scalper SL error: {e}")
        return False


def _force_close_main(trade_id: int, exit_price: float, reason: str):
    try:
        conn = sqlite3.connect(_trades_db_path())
        now_iso = datetime.now().isoformat()
        row = conn.execute("SELECT entry_price, qty FROM trades WHERE id=? AND status='OPEN'",
                           (trade_id,)).fetchone()
        if not row:
            conn.close()
            return False
        entry_price, qty = row
        pnl_pts = round(exit_price - entry_price, 2)
        pnl_rupees = round(pnl_pts * qty, 2)
        conn.execute("""
            UPDATE trades SET status='WATCHER_EXIT', exit_price=?, exit_time=?,
                pnl_pts=?, pnl_rupees=?, exit_reason=?
            WHERE id=? AND status='OPEN'
        """, (exit_price, now_iso, pnl_pts, pnl_rupees, reason, trade_id))
        conn.commit()
        conn.close()
        # Capital tracker hook
        try:
            from capital_tracker import record_trade_pnl
            record_trade_pnl("MAIN", pnl_rupees, trade_id=trade_id,
                             description=f"Watcher exit: {reason}")
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[WATCHER] force close main error: {e}")
        return False


def _force_close_scalper(trade_id: int, exit_price: float, reason: str):
    try:
        conn = sqlite3.connect(_scalper_db_path())
        now_iso = datetime.now().isoformat()
        row = conn.execute(
            "SELECT entry_price, qty, entry_time FROM scalper_trades WHERE id=? AND status='OPEN'",
            (trade_id,)
        ).fetchone()
        if not row:
            conn.close()
            return False
        entry_price, qty, entry_time = row
        pnl_pts = round(exit_price - entry_price, 2)
        pnl_rupees = round(pnl_pts * qty, 2)
        try:
            entry_dt = datetime.fromisoformat(entry_time)
            hold_sec = int((datetime.now() - entry_dt).total_seconds())
        except Exception:
            hold_sec = 0
        conn.execute("""
            UPDATE scalper_trades SET status='WATCHER_EXIT', exit_price=?, exit_time=?,
                exit_reason=?, pnl_pts=?, pnl_rupees=?, hold_seconds=?
            WHERE id=? AND status='OPEN'
        """, (exit_price, now_iso, reason, pnl_pts, pnl_rupees, hold_sec, trade_id))
        conn.commit()
        conn.close()
        try:
            from capital_tracker import record_trade_pnl
            record_trade_pnl("SCALPER", pnl_rupees, trade_id=trade_id,
                             description=f"Watcher exit: {reason}")
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[WATCHER] force close scalper error: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# Main watcher pulse — call every 30s from engine loop
# ──────────────────────────────────────────────────────────────

# Cache of last computed health per (source, trade_id)
_last_health_cache: Dict[str, Dict] = {}


def get_last_health() -> List[Dict]:
    """Return all cached health snapshots for API consumption."""
    return list(_last_health_cache.values())


def get_health_for_trade(source: str, trade_id: int) -> Optional[Dict]:
    return _last_health_cache.get(f"{source}:{trade_id}")


def watcher_pulse(engine) -> Dict:
    """
    Main pulse — called every 30s from engine async loop.
    engine: the AppEngine instance with .prices, .spot_tokens, .chains
    """
    init_watcher_db()
    cfg = get_config()
    snapshot = {
        "ts": time.time(),
        "main_count": 0,
        "scalper_count": 0,
        "actions": [],
        "errors": [],
    }
    print(f"[WATCHER] pulse start · DB={WATCHER_DB} · "
          f"trades={_trades_db_path()} · scalper={_scalper_db_path()}")

    # ── 1. Push VIX sample ──
    try:
        vix_tok = engine.spot_tokens.get("VIX")
        if vix_tok:
            vix_val = engine.prices.get(vix_tok, {}).get("ltp", 0)
            if vix_val > 0:
                push_vix(vix_val)
    except Exception as e:
        snapshot["errors"].append(f"vix push: {e}")

    # ── 2. Record spot ticks ──
    try:
        for idx in ("NIFTY", "BANKNIFTY"):
            tok = engine.spot_tokens.get(idx)
            if tok:
                spot = engine.prices.get(tok, {}).get("ltp", 0)
                if spot > 0:
                    record_spot_tick(idx, spot)
    except Exception as e:
        snapshot["errors"].append(f"spot tick: {e}")

    # ── 3. Process main trades ──
    try:
        main_trades = _get_open_main_trades()
        snapshot["main_count"] = len(main_trades)
        for t in main_trades:
            try:
                _process_trade(t, "MAIN", engine, cfg, snapshot)
            except Exception as e:
                snapshot["errors"].append(f"main #{t.get('id')}: {e}")
    except Exception as e:
        snapshot["errors"].append(f"main loop: {e}")

    # ── 4. Process scalper trades ──
    try:
        scalper_trades = _get_open_scalper_trades()
        snapshot["scalper_count"] = len(scalper_trades)
        for t in scalper_trades:
            try:
                _process_trade(t, "SCALPER", engine, cfg, snapshot)
            except Exception as e:
                snapshot["errors"].append(f"scalper #{t.get('id')}: {e}")
                import traceback; traceback.print_exc()
    except Exception as e:
        snapshot["errors"].append(f"scalper loop: {e}")
        import traceback; traceback.print_exc()

    # ── Cleanup stale cache entries (closed trades) ──
    # Cache should only hold entries for currently-OPEN trades. Anything
    # else is residue from previously-closed trades that the user keeps
    # seeing as a phantom "MISMATCH".
    try:
        live_keys = set()
        try:
            for t in _get_open_main_trades():
                live_keys.add(f"MAIN:{t.get('id')}")
        except Exception:
            pass
        try:
            for t in _get_open_scalper_trades():
                live_keys.add(f"SCALPER:{t.get('id')}")
        except Exception:
            pass
        stale = [k for k in list(_last_health_cache.keys()) if k not in live_keys]
        for k in stale:
            _last_health_cache.pop(k, None)
        if stale:
            print(f"[WATCHER] cleaned {len(stale)} stale cache entries")
    except Exception as e:
        print(f"[WATCHER] cache cleanup err: {e}")

    print(f"[WATCHER] pulse done · main={snapshot['main_count']} "
          f"scalper={snapshot['scalper_count']} actions={len(snapshot['actions'])} "
          f"errors={len(snapshot['errors'])} cached={len(_last_health_cache)}")
    if snapshot["errors"]:
        for err in snapshot["errors"][:5]:
            print(f"[WATCHER]   err: {err}")
    return snapshot


def _stub_health(trade: Dict, source: str, note: str) -> Dict:
    """Minimal health entry shown when full computation is impossible
    (cold-start, missing live data). Frontend treats this as 'initialising'."""
    return {
        "score": 7.0,
        "verdict": "HEALTHY",
        "exit_recommended": False,
        "tighten_sl": False,
        "suggested_action": "HOLD",
        "reasons": [f"Watcher initialising — {note}"],
        "profit_pct": 0,
        "hold_minutes": 0,
        "components": {},
        "trade_id": trade.get("id"),
        "source": source,
        "idx": trade.get("idx"),
        "action": trade.get("action"),
        "strike": trade.get("strike"),
        "entry_price": trade.get("entry_price"),
        "current_premium": trade.get("current_ltp") or trade.get("entry_price"),
        "spot": trade.get("entry_spot"),
        "day_high": 0,
        "day_low": 0,
        "qty": trade.get("qty"),
        "ts": time.time(),
        "stub": True,
    }


def _process_trade(trade: Dict, source: str, engine, cfg: Dict, snapshot: Dict):
    """Compute health + apply action for a single trade.

    GUARANTEE: writes at least a stub entry to _last_health_cache for
    every call, even if every internal step blows up. The frontend
    must never see "pending data" while a trade is open in the DB.
    """
    trade_id = trade.get("id")
    sid = f"{source}:{trade_id}"
    # Pre-emptive stub so any later exception still leaves an entry behind
    _last_health_cache[sid] = _stub_health(trade, source, "computing…")

    try:
        _process_trade_inner(trade, source, engine, cfg, snapshot)
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Replace with stub that surfaces the error
        _last_health_cache[sid] = _stub_health(trade, source, f"engine error: {e}")
        snapshot["errors"].append(f"{source} #{trade_id}: {e}")


def _process_trade_inner(trade: Dict, source: str, engine, cfg: Dict, snapshot: Dict):
    """Real logic — wrapped by _process_trade for exception safety."""
    idx = trade.get("idx", "")
    action = trade.get("action", "BUY_CE")
    strike = trade.get("strike", 0)
    entry_price = trade.get("entry_price", 0) or 0
    qty = trade.get("qty", 0) or 0
    trade_id = trade.get("id")
    sid = f"{source}:{trade_id}"

    if not idx or not strike or entry_price <= 0:
        # Even bad rows get a stub so frontend stops spinning
        _last_health_cache[sid] = _stub_health(trade, source, "missing core fields")
        return

    # Live data with multiple fallbacks
    spot_tok = engine.spot_tokens.get(idx.upper()) if hasattr(engine, "spot_tokens") else None
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
    if spot <= 0:
        # Fallback: trade's stored entry_spot (better than 0)
        spot = trade.get("entry_spot", 0) or 0

    chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
    if not isinstance(chain, dict):
        chain = {}
    # Strike key may be int or str depending on source — try both
    sd = chain.get(strike, {}) or chain.get(str(strike), {}) or {}
    opt_key = "ce_ltp" if "CE" in action.upper() else "pe_ltp"
    current_premium = (
        sd.get(opt_key, 0)
        or trade.get("current_ltp", 0)
        or trade.get("peak_ltp", 0)
        or entry_price  # last resort: assume flat
    )

    # If we still have nothing useful for spot, write stub and bail.
    if spot <= 0:
        _last_health_cache[sid] = _stub_health(trade, source,
            f"no spot for {idx} (token={spot_tok})")
        return

    # Register trade in premium tracker if first time
    entry_spot = trade.get("entry_spot", spot)
    sid = f"{source}:{trade_id}"
    pt = get_prem_tracker()
    if sid not in pt.entry_data:
        pt.register_entry(sid, entry_price, entry_spot or spot, action)
    pt.push(sid, current_premium, spot)

    # Build candles
    ticks = get_recent_spot_ticks(idx, minutes=120)
    candles = build_candles_from_ticks(ticks, interval_min=5)

    # Day extremes
    day_high, day_low = _get_day_extremes(idx)
    if day_high <= 0:
        day_high = spot
    if day_low <= 0:
        day_low = spot

    # Entry time — strip timezone so it's comparable to datetime.now() (naive)
    entry_iso = trade.get("entry_time", "")
    try:
        entry_dt = datetime.fromisoformat(entry_iso) if entry_iso else None
        if entry_dt and entry_dt.tzinfo is not None:
            entry_dt = entry_dt.replace(tzinfo=None)
    except Exception:
        entry_dt = None

    # Today's similar losses
    similar_losses = _count_today_similar_losses(source, action, idx)

    # Compute health
    health = compute_health(
        trade_id=sid,
        action=action,
        entry_price=entry_price,
        current_premium=current_premium,
        entry_spot=entry_spot or spot,
        current_spot=spot,
        day_high=day_high,
        day_low=day_low,
        candles_5min=candles,
        entry_time=entry_dt,
        today_similar_losses=similar_losses,
    )
    health["trade_id"] = trade_id
    health["source"] = source
    health["idx"] = idx
    health["action"] = action
    health["strike"] = strike
    health["entry_price"] = entry_price
    health["current_premium"] = current_premium
    health["spot"] = spot
    health["day_high"] = day_high
    health["day_low"] = day_low
    health["qty"] = qty

    # Cache for API
    _last_health_cache[sid] = health

    # Record tick for live chart
    try:
        pnl_pct = ((current_premium - entry_price) / entry_price * 100) if entry_price > 0 else 0
        conn = sqlite3.connect(WATCHER_DB)
        conn.execute("""
            INSERT INTO position_ticks(source, trade_id, ts, premium, spot, score, pnl_pct)
            VALUES (?,?,?,?,?,?,?)
        """, (source, trade_id, time.time(), float(current_premium),
              float(spot or 0), float(health.get("score", 0)),
              round(pnl_pct, 2)))
        # Cleanup ticks older than 8 hours
        conn.execute("DELETE FROM position_ticks WHERE ts < ?", (time.time() - 8*3600,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WATCHER] tick record err: {e}")

    # Evaluate trigger
    trigger = _evaluate_triggers(trade, health, action)
    action_taken = "NONE"

    if trigger:
        # Determine if auto-action enabled
        auto_exit = (cfg["auto_exit_main"] if source == "MAIN" else cfg["auto_exit_scalper"])
        tight_sl = (cfg["tight_sl_main"] if source == "MAIN" else cfg["tight_sl_scalper"])

        # CRITICAL exits get hard close (if auto-exit on)
        if health["score"] < cfg["min_score_for_exit"] and auto_exit:
            ok = (_force_close_main(trade_id, current_premium, f"{trigger}: {health['reasons'][0] if health['reasons'] else trigger}")
                  if source == "MAIN"
                  else _force_close_scalper(trade_id, current_premium, f"{trigger}: {health['reasons'][0] if health['reasons'] else trigger}"))
            if ok:
                action_taken = "EXIT"
                _log_exit(source, trade, current_premium, trigger, health.get("reasons", []))
                snapshot["actions"].append({
                    "source": source, "trade_id": trade_id, "trigger": trigger,
                    "action": "EXIT", "exit_price": current_premium,
                })
        # WARNING zone gets SL tighten
        elif health["score"] < cfg["min_score_for_tight_sl"] and tight_sl:
            new_sl = round(entry_price * 1.005, 2)  # entry + 0.5% (lock small win)
            if new_sl > trade.get("sl_price", 0):
                ok = (_tighten_main_sl(trade_id, new_sl, f"{trigger}: tight SL")
                      if source == "MAIN"
                      else _tighten_scalper_sl(trade_id, new_sl, f"{trigger}: tight SL"))
                if ok:
                    action_taken = "TIGHT_SL"
                    snapshot["actions"].append({
                        "source": source, "trade_id": trade_id, "trigger": trigger,
                        "action": "TIGHT_SL", "new_sl": new_sl,
                    })

    # Log every health computation (sampled — every 30s naturally)
    _log_health(source, trade_id, idx, action, health, trigger, action_taken)


# ──────────────────────────────────────────────────────────────
# Reading helpers for API
# ──────────────────────────────────────────────────────────────

def get_recent_exits(limit: int = 50) -> List[Dict]:
    init_watcher_db()
    try:
        conn = sqlite3.connect(WATCHER_DB)
        rows = conn.execute("""
            SELECT ts, source, trade_id, idx, action, strike, entry_price,
                   exit_price, pnl_rupees, trigger, reasons
            FROM exit_log ORDER BY ts DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        out = []
        for r in rows:
            out.append({
                "ts": r[0],
                "source": r[1],
                "trade_id": r[2],
                "idx": r[3],
                "action": r[4],
                "strike": r[5],
                "entry_price": r[6],
                "exit_price": r[7],
                "pnl_rupees": r[8],
                "trigger": r[9],
                "reasons": json.loads(r[10] or "[]"),
            })
        return out
    except Exception:
        return []


def get_position_ticks(source: str, trade_id: int, limit: int = 500) -> List[Dict]:
    """Per-trade premium tick stream — used for live LTP chart."""
    init_watcher_db()
    try:
        conn = sqlite3.connect(WATCHER_DB)
        rows = conn.execute("""
            SELECT ts, premium, spot, score, pnl_pct FROM position_ticks
            WHERE source=? AND trade_id=? ORDER BY ts ASC LIMIT ?
        """, (source, trade_id, limit)).fetchall()
        conn.close()
        return [{
            "ts": int(r[0] * 1000),
            "premium": r[1],
            "spot": r[2],
            "score": r[3],
            "pnl_pct": r[4],
        } for r in rows]
    except Exception as e:
        print(f"[WATCHER] get_position_ticks err: {e}")
        return []


def get_health_history(source: str, trade_id: int, limit: int = 100) -> List[Dict]:
    init_watcher_db()
    try:
        conn = sqlite3.connect(WATCHER_DB)
        rows = conn.execute("""
            SELECT ts, score, verdict, reasons, triggered, action_taken
            FROM health_log WHERE source=? AND trade_id=?
            ORDER BY ts DESC LIMIT ?
        """, (source, trade_id, limit)).fetchall()
        conn.close()
        return [{
            "ts": r[0],
            "score": r[1],
            "verdict": r[2],
            "reasons": json.loads(r[3] or "[]"),
            "triggered": r[4],
            "action_taken": r[5],
        } for r in rows]
    except Exception:
        return []
