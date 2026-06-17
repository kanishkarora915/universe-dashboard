"""
Scalper Mode — Aggressive quick-trade engine.

Different from default swing mode:
  - 15 trades/day cap (vs 6)
  - Tighter SL: -8% (vs -15%)
  - Smaller targets: T1 +12%, T2 +25% (vs +30%/+60%)
  - 15s confirmation (vs 60s)
  - Lower threshold: 45% (vs 50%)
  - Smaller position: 1.5% risk (vs 3%)
  - Quick exits — max 30 min hold
  - Separate trades table for paper tracking

Philosophy: Many small wins, accept many small losses.
Win rate target: 50-55%, R:R 1:1.5
"""

import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
SCALPER_DB = _data_dir / "scalper_trades.db"

# ── KILL SWITCH ────────────────────────────────────────────────────
# Scalper auto-trading flag. User chose 2026-05-18 to keep scalper
# active while Phase 2 smart improvements are built in parallel.
#
# Audit context retained (4-session loss reference):
#   May 12: +₹14,660  · May 13: -₹20,354
#   May 14: -₹81,078  · May 18: -₹32,274  → net -₹119k
#   Root: 82% PE bias, theta-decay killed 10/45 trades.
#
# Default ON — set SCALPER_AUTO_TRADE=off to pause without code change.
SCALPER_AUTO_TRADE_ENABLED = os.environ.get("SCALPER_AUTO_TRADE", "on").lower() != "off"

# SCALPER CONFIG (tuned after 2026-05-04 -₹1.37L bleed)
SCALPER_THRESHOLD = 50         # Lowered 55→50 (2026-06-05, user: "fast scalper, small profits")
                               # Backtest: prob 50-54% bucket = 8 trades, 57% WR, +₹85k untapped
                               # Damage control (EARLY_CUT + BREAKEVEN) handles the small losses
SCALPER_DAILY_CAP = 30         # Raised 20→30 (user req 2026-06-15)
SCALPER_SL_PCT = 0.08          # 8% SL (lowered 12→8 per user safety rule, B1.1)
SCALPER_T1_PCT = 0.10          # 10% T1 (lowered 15→10 per profit-mgmt #1 — more reachable)
SCALPER_T2_PCT = 0.20          # 20% T2 (lowered 30→20 — better R:R 1:2.5 with -8% SL)
SCALPER_RISK_PCT = 1.0         # 1.0% risk (smaller size — 4 hard losses = -4% max)
SCALPER_CONFIRM_SEC = 30       # 30s confirmation (was 15 — avoid noise)
SCALPER_MAX_HOLD_MIN = 30      # 30 min max hold

# WHIPSAW GUARDS
COOLDOWN_SAME_STRIKE_MIN = 10  # No re-entry same strike for 10 min after exit
COOLDOWN_FLIP_DIRECTION_MIN = 15  # No CE→PE or PE→CE same strike for 15 min
MAX_SL_HITS_SAME_STRIKE = 2    # After 2 SL hits on same strike, pause that strike for day

# B3.10 CAPITAL CONCENTRATION
MAX_CAPITAL_PCT_PER_TRADE = 0.30   # Single trade ≤ 30% of capital
MAX_CAPITAL_PCT_PER_DIRECTION = 0.60  # All open same-direction trades ≤ 60% of capital


def ist_now():
    return datetime.now(IST)


def init_scalper_db():
    """Init scalper trades table — separate from main trades."""
    conn = sqlite3.connect(str(SCALPER_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scalper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            idx TEXT NOT NULL,
            action TEXT NOT NULL,
            strike INTEGER NOT NULL,
            expiry TEXT,
            entry_price REAL NOT NULL,
            sl_price REAL,
            t1_price REAL,
            t2_price REAL,
            current_ltp REAL,
            peak_ltp REAL,
            exit_price REAL DEFAULT 0,
            lots INTEGER,
            lot_size INTEGER,
            qty INTEGER,
            pnl_pts REAL DEFAULT 0,
            pnl_rupees REAL DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            exit_reason TEXT,
            probability INTEGER,
            hold_seconds INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'SCALPER',
            entry_reasoning TEXT,
            entry_bull_pct REAL,
            entry_bear_pct REAL,
            entry_spot REAL,
            capital_used REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scalper_status ON scalper_trades(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scalper_date ON scalper_trades(entry_time)")

    # Migrate older DB: add new columns if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(scalper_trades)").fetchall()]
    for col, sql in [
        ("entry_reasoning", "ALTER TABLE scalper_trades ADD COLUMN entry_reasoning TEXT"),
        ("entry_bull_pct", "ALTER TABLE scalper_trades ADD COLUMN entry_bull_pct REAL"),
        ("entry_bear_pct", "ALTER TABLE scalper_trades ADD COLUMN entry_bear_pct REAL"),
        ("entry_spot", "ALTER TABLE scalper_trades ADD COLUMN entry_spot REAL"),
        ("capital_used", "ALTER TABLE scalper_trades ADD COLUMN capital_used REAL"),
        # Smart SL state per trade
        ("smart_sl_stage", "ALTER TABLE scalper_trades ADD COLUMN smart_sl_stage INTEGER DEFAULT 0"),
        ("smart_sl_value", "ALTER TABLE scalper_trades ADD COLUMN smart_sl_value REAL"),
        ("sl_hit_time", "ALTER TABLE scalper_trades ADD COLUMN sl_hit_time TEXT"),
        ("sl_reason", "ALTER TABLE scalper_trades ADD COLUMN sl_reason TEXT"),
        # 2026-06-15: Regime capture at entry — for chop/structure analysis
        ("regime_at_entry", "ALTER TABLE scalper_trades ADD COLUMN regime_at_entry TEXT DEFAULT ''"),
        ("range_pct_at_entry", "ALTER TABLE scalper_trades ADD COLUMN range_pct_at_entry REAL DEFAULT 0"),
        ("candle_pct_at_entry", "ALTER TABLE scalper_trades ADD COLUMN candle_pct_at_entry REAL DEFAULT 0"),
        ("structure_5m", "ALTER TABLE scalper_trades ADD COLUMN structure_5m TEXT DEFAULT ''"),
        ("structure_15m", "ALTER TABLE scalper_trades ADD COLUMN structure_15m TEXT DEFAULT ''"),
        ("structure_1h", "ALTER TABLE scalper_trades ADD COLUMN structure_1h TEXT DEFAULT ''"),
        # 2026-06-16: Per-engine attribution at entry (JSON blobs)
        ("engine_scores_json", "ALTER TABLE scalper_trades ADD COLUMN engine_scores_json TEXT DEFAULT ''"),
        ("signals_triggered", "ALTER TABLE scalper_trades ADD COLUMN signals_triggered TEXT DEFAULT ''"),
        ("gates_passed", "ALTER TABLE scalper_trades ADD COLUMN gates_passed TEXT DEFAULT ''"),
        # 2026-06-16: Level context at entry (PDH/PDL/PDC/gap/day_high/low)
        ("level_context_json", "ALTER TABLE scalper_trades ADD COLUMN level_context_json TEXT DEFAULT ''"),
        ("level_zone_at_entry", "ALTER TABLE scalper_trades ADD COLUMN level_zone_at_entry TEXT DEFAULT ''"),
        # 2026-06-17 (auditor NOTE #11): watcher trigger attribution
        ("watcher_trigger", "ALTER TABLE scalper_trades ADD COLUMN watcher_trigger TEXT DEFAULT ''"),
    ]:
        if col not in cols:
            try: conn.execute(sql)
            except Exception: pass

    # Smart SL config row (separate from main scalper_config to avoid conflicts)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS smart_sl_config (
            id INTEGER PRIMARY KEY CHECK (id=1),
            enabled INTEGER DEFAULT 0,
            spot_anchor_pct REAL DEFAULT 0.4,
            updated_at TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO smart_sl_config (id, enabled, spot_anchor_pct, updated_at) VALUES (1, 0, 0.4, ?)",
        (ist_now().isoformat(),)
    )

    # Tick history (live LTP samples per trade)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scalper_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            ts INTEGER NOT NULL,
            ltp REAL,
            spot REAL,
            pnl_rupees REAL,
            pnl_pct REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scalp_ticks ON scalper_ticks(trade_id, ts DESC)")

    # User-configurable scalper settings
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scalper_config (
            id INTEGER PRIMARY KEY CHECK (id=1),
            capital REAL DEFAULT 1000000,
            nifty_qty INTEGER DEFAULT 0,
            banknifty_qty INTEGER DEFAULT 0,
            sl_pct REAL DEFAULT 0.08,
            t1_pct REAL DEFAULT 0.10,
            t2_pct REAL DEFAULT 0.20,
            threshold INTEGER DEFAULT 55,
            daily_cap INTEGER DEFAULT 30,
            updated_at TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO scalper_config (id, capital, nifty_qty, banknifty_qty, sl_pct, t1_pct, t2_pct, updated_at) "
        "VALUES (1, 1000000, 0, 0, 0.08, 0.10, 0.20, ?)",
        (ist_now().isoformat(),)
    )
    # FORCE migration to current targets:
    #   sl_pct ≥ 0.10  → 0.08 (B1.1 safety floor)
    #   t1_pct ≥ 0.13  → 0.10 (profit-mgmt #1 — T1 more reachable)
    #   t2_pct ≥ 0.25  → 0.20 (R:R 1:2.5)
    #   daily_cap raised to 30 (user req 2026-06-15) — clamp old DB rows up to 30 too
    try:
        conn.execute("""
            UPDATE scalper_config
               SET sl_pct = 0.08,
                   t1_pct = 0.10,
                   t2_pct = 0.20,
                   daily_cap = MAX(daily_cap, 30),
                   updated_at = ?
             WHERE id = 1 AND (sl_pct >= 0.10 OR t1_pct >= 0.13 OR t2_pct >= 0.25
                               OR daily_cap < 30)
        """, (ist_now().isoformat(),))
    except Exception:
        pass
    conn.commit()
    conn.close()


def get_scalper_config():
    """Return user-configurable scalper settings."""
    init_scalper_db()
    conn = _conn()
    row = conn.execute("SELECT * FROM scalper_config WHERE id=1").fetchone()
    conn.close()
    if not row:
        return {
            "capital": 1000000, "nifty_qty": 0, "banknifty_qty": 0,
            "sl_pct": SCALPER_SL_PCT, "t1_pct": SCALPER_T1_PCT, "t2_pct": SCALPER_T2_PCT,
            "threshold": SCALPER_THRESHOLD, "daily_cap": SCALPER_DAILY_CAP,
        }
    return dict(row)


# Expiry-day fast-scalp multipliers (applied ONLY when is_expiry_day() == True).
# Tighter SL + smaller targets + half size + shorter hold to combat 5x theta crush.
EXPIRY_SL_MULT      = 0.7    # 8% × 0.7 = 5.6% SL
EXPIRY_TARGET_MULT  = 0.7    # T1 10% × 0.7 = 7%  ·  T2 20% × 0.7 = 14%
EXPIRY_QTY_MULT     = 0.5    # half size on expiry
EXPIRY_MAX_HOLD_MIN = 15     # half hold time (was 30)


def get_active_scalp_config():
    """Returns scalper config WITH expiry-day multipliers baked in.

    On non-expiry days: returns raw user config.
    On expiry days: applies tighter SL/T1/T2/qty/hold multipliers
                    while preserving all safety floors.

    Returns dict: {sl_pct, t1_pct, t2_pct, qty_mult, max_hold_min,
                   threshold, daily_cap, capital, ..., is_expiry}
    """
    cfg = get_scalper_config()
    is_expiry = False
    try:
        from volatility_detector import is_expiry_day
        is_expiry = is_expiry_day()
    except Exception:
        pass

    # ── CONFIG DRIFT FIX (Week 1, 2026-06-17) ──
    # buyer_mode is the single source of truth for SL/T1/T2/max_hold.
    # Previously scalper used its own SCALPER_SL/T1/T2_PCT constants
    # which drifted from buyer_mode (live 8/10/20 vs buyer_mode 5/5/12).
    # Now: BUYER mode → use buyer_mode values; HEDGER → keep cfg/defaults.
    # Env kill: SCALPER_BUYER_MODE_SYNC_DISABLED=1
    base_sl  = cfg.get("sl_pct",  SCALPER_SL_PCT)
    base_t1  = cfg.get("t1_pct",  SCALPER_T1_PCT)
    base_t2  = cfg.get("t2_pct",  SCALPER_T2_PCT)
    base_hold = SCALPER_MAX_HOLD_MIN
    try:
        import os as _os_sync
        if _os_sync.environ.get("SCALPER_BUYER_MODE_SYNC_DISABLED", "").strip() not in ("1","true","on"):
            from buyer_mode import get_thresholds as _bm_get
            _bm = _bm_get() or {}
            if (_bm.get("mode") or "").upper() == "BUYER":
                _bm_sl  = _bm.get("scalper_sl_pct")
                _bm_t1  = _bm.get("scalper_t1_pct")
                _bm_t2  = _bm.get("scalper_t2_pct")
                _bm_hold = _bm.get("scalper_max_hold_min")
                if _bm_sl  is not None: base_sl  = float(_bm_sl)
                if _bm_t1  is not None: base_t1  = float(_bm_t1)
                if _bm_t2  is not None: base_t2  = float(_bm_t2)
                if _bm_hold is not None: base_hold = int(_bm_hold)
                print(f"[SCALPER_CFG] buyer_mode sync: sl={base_sl} t1={base_t1} t2={base_t2} hold={base_hold}m")
    except Exception as _sync_e:
        print(f"[SCALPER_CFG] buyer_mode sync error (using cfg): {_sync_e}")

    if is_expiry:
        return {
            **cfg,
            "sl_pct":  round(base_sl * EXPIRY_SL_MULT, 4),
            "t1_pct":  round(base_t1 * EXPIRY_TARGET_MULT, 4),
            "t2_pct":  round(base_t2 * EXPIRY_TARGET_MULT, 4),
            "qty_mult": EXPIRY_QTY_MULT,
            "max_hold_min": EXPIRY_MAX_HOLD_MIN,
            "is_expiry": True,
        }
    return {
        **cfg,
        "sl_pct": base_sl,
        "t1_pct": base_t1,
        "t2_pct": base_t2,
        "qty_mult": 1.0,
        "max_hold_min": base_hold,
        "is_expiry": False,
    }


def set_scalper_config(capital=None, nifty_qty=None, banknifty_qty=None,
                      sl_pct=None, t1_pct=None, t2_pct=None,
                      threshold=None, daily_cap=None):
    """Update user-configurable scalper settings (only provided fields)."""
    init_scalper_db()
    cur = get_scalper_config()
    updated = {
        "capital": capital if capital is not None else cur.get("capital", 1000000),
        "nifty_qty": nifty_qty if nifty_qty is not None else cur.get("nifty_qty", 0),
        "banknifty_qty": banknifty_qty if banknifty_qty is not None else cur.get("banknifty_qty", 0),
        "sl_pct": sl_pct if sl_pct is not None else cur.get("sl_pct", SCALPER_SL_PCT),
        "t1_pct": t1_pct if t1_pct is not None else cur.get("t1_pct", SCALPER_T1_PCT),
        "t2_pct": t2_pct if t2_pct is not None else cur.get("t2_pct", SCALPER_T2_PCT),
        "threshold": threshold if threshold is not None else cur.get("threshold", SCALPER_THRESHOLD),
        "daily_cap": daily_cap if daily_cap is not None else cur.get("daily_cap", SCALPER_DAILY_CAP),
    }
    conn = _conn()
    conn.execute("""
        UPDATE scalper_config
        SET capital=?, nifty_qty=?, banknifty_qty=?, sl_pct=?, t1_pct=?, t2_pct=?,
            threshold=?, daily_cap=?, updated_at=?
        WHERE id=1
    """, (
        updated["capital"], updated["nifty_qty"], updated["banknifty_qty"],
        updated["sl_pct"], updated["t1_pct"], updated["t2_pct"],
        updated["threshold"], updated["daily_cap"], ist_now().isoformat(),
    ))
    conn.commit()
    conn.close()
    return updated


def _last_n_outcomes(n: int = 2) -> list:
    """Return the last N closed scalper trade outcomes (today only).
    Returns list of 'WIN' / 'LOSS' / 'BE'. Empty list if fewer than N.

    Used by streak-overconfidence handler (Fix 2 from 445-trade audit).
    Today-only because cross-day streak isn't behavioral — yesterday's
    wins don't cause today's first trade to over-fire.
    """
    try:
        from datetime import datetime
        import pytz as _pytz
        _IST = _pytz.timezone("Asia/Kolkata")
        today_iso = datetime.now(_IST).strftime("%Y-%m-%d")
        conn = sqlite3.connect(str(SCALPER_DB), timeout=2.0)
        cur = conn.execute(
            "SELECT pnl_rupees FROM scalper_trades "
            "WHERE substr(entry_time,1,10) = ? "
            "AND COALESCE(status,'') NOT IN ('OPEN','') "
            "AND exit_time IS NOT NULL "
            "ORDER BY exit_time DESC LIMIT ?",
            (today_iso, n)
        )
        rows = cur.fetchall()
        conn.close()
        out = []
        for r in rows:
            p = r[0] or 0
            out.append('WIN' if p > 0 else 'LOSS' if p < 0 else 'BE')
        return out
    except Exception:
        return []


_scalper_pragma_done = False
def _conn():
    global _scalper_pragma_done
    init_scalper_db()
    conn = sqlite3.connect(str(SCALPER_DB), timeout=10.0)
    conn.row_factory = sqlite3.Row
    if not _scalper_pragma_done:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-128000")  # 128MB (2GB RAM)
            conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
            _scalper_pragma_done = True
        except Exception:
            pass
    return conn


def should_enter_scalp(idx, verdict_data, scalper_enabled=True, atm_strike=None,
                       engine=None):
    """Scalper entry rules with FULL guard stack (B1+B2+B3 hardening).

    Pipeline (rejects on first failure):
      1. Scalper enabled toggle
      2. Market hours (9:20–15:15)
      3. Probability >= threshold (cfg)
      4. B1.3 HARD daily-cap (ATOMIC, IST-aware date filter)
      5. Capital sanity (committed + this trade ≤ capital)
      6. B3.10 capital concentration (≤30% per-trade, ≤60% per-direction)
      7. No duplicate same idx+action open
      8. Cooldown after exit (same strike 10m, flip 15m, 2+ SL hits = day-pause)
      9. B1.4 DIRECTION SANITY (OI delta + spot must agree with verdict)
     10. B2.6 CAPITULATION GATE (block entries against reversal direction)
     11. ENTRY FILTERS (5-min trend + greeks gate + market regime check)
         - CHOP regime → block (unless capit-confirmed elsewhere)
         - 5-min trend bearish → block CE (vice versa for PE)
         - Delta out of 0.30-0.70 → block (deep OTM/ITM)
         - BREAKOUT regime → fast-track (caller may skip 30s confirmation)
    """
    if not scalper_enabled:
        return False

    if not verdict_data or verdict_data.get("action") == "NO TRADE":
        return False

    now = ist_now()

    # Market hours
    if now.weekday() > 4:
        return False
    market_open = (now.hour == 9 and now.minute >= 20) or (10 <= now.hour <= 14) or (now.hour == 15 and now.minute <= 15)
    if not market_open:
        return False

    # ── G0: EXPIRY DAY GUARD (Tuesday NIFTY weekly expiry) ──
    # 60-day audit: Tuesday cost combined -₹193,805 (24% of all losses).
    # Theta crusher + max-pain pin = BUYER strategy gets murdered.
    try:
        from expiry_day_guard import should_skip
        if should_skip(source="scalper.should_enter_scalp", now=now):
            print(f"[SCALPER] REJECT entry (G0): Tuesday NIFTY weekly expiry — buyer strategy paused")
            return False
    except Exception as _e:
        print(f"[SCALPER] expiry_day_guard error (allow): {_e}")

    # ── G0b: CIRCUIT BREAKER (daily loss cap + consecutive loss pause) ──
    # 60-day audit: May 14 disaster -₹81k single session, 4-session collapse -₹119k.
    # Hard cap prevents bleed-out days; consec pause prevents tilt-firing.
    try:
        from circuit_breaker import should_block
        if should_block(tab="SCALPER", source="scalper.should_enter_scalp"):
            print(f"[SCALPER] REJECT entry (G0b): circuit breaker triggered")
            return False
    except Exception as _e:
        print(f"[SCALPER] circuit_breaker error (allow): {_e}")

    # ── G0d: PROFIT TARGET (book win + walk away mode) ──
    # User vision 2026-05-21: "paisa banakr nikle" — when target hit,
    # stop trading and lock the day's profit. Prevents greed reversal.
    try:
        from profit_target import should_block as _pt_should_block, assess as _pt_assess
        if _pt_should_block(tab="SCALPER", source="scalper.should_enter_scalp"):
            print(f"[SCALPER] REJECT entry (G0d): profit target hit — booking the win")
            # Throttled telegram alert (first hit of the day)
            try:
                import telegram_alerts as _tg
                if _tg.is_enabled():
                    d = _pt_assess("SCALPER")
                    _tg.send(
                        f"🎯 SCALPER target hit ₹{d['today_pnl']:,.0f} / "
                        f"₹{d['target']:,.0f} — booking the win, no new entries today",
                        key="scalper_profit_target_hit",  # 1/day throttle
                    )
            except Exception:
                pass
            return False
    except Exception as _e:
        print(f"[SCALPER] profit_target error (allow): {_e}")

    # Threshold (user-configurable) — moved up so calibration gate can use win_pct/action_str
    cfg = get_scalper_config()
    threshold = cfg.get("threshold") or SCALPER_THRESHOLD
    daily_cap = cfg.get("daily_cap") or SCALPER_DAILY_CAP

    # ── ADAPTIVE MARKET-HEALTH (2026-05-22) ──
    # The scalper reads market health and tunes its own aggression:
    # looser gates when conditions favour scalping, tighter when they
    # don't. Replaces a static aggressive flag. Shadow mode (default)
    # computes + logs the level but does NOT apply it; only 'live' mode
    # applies the tuning. Any failure → BALANCED (no change).
    _cooldown_mult = 1.0
    _allow_chop_health = False
    try:
        from scalper_health import assess as _sh_assess
        _health = _sh_assess(engine, idx)
        if _health.get("mode") == "live":
            _ht = _health.get("tuning", {})
            threshold = max(40, threshold + _ht.get("threshold_delta", 0))
            if _ht.get("daily_cap"):
                daily_cap = _ht["daily_cap"]
            _cooldown_mult = _ht.get("cooldown_mult", 1.0)
            _allow_chop_health = bool(_ht.get("allow_chop", False))
    except Exception as _e:
        print(f"[SCALPER] scalper_health error (balanced fallback): {_e}")

    # Use RUNNING capital from tracker (compounds with P&L)
    try:
        from capital_tracker import get_running_capital
        capital = get_running_capital("SCALPER") or cfg.get("capital") or 1000000
    except Exception:
        capital = cfg.get("capital") or 1000000

    win_pct = verdict_data.get("winProbability", 0)
    action_str = verdict_data.get("action", "")
    is_ce = "CE" in action_str.upper()

    # ── G0c: CALIBRATION GATE (Fix 6) — uses win_pct + action_str ──
    # 60-day audit: probability is INVERSE — high raw_prob = low actual WR.
    # When CALIBRATION_GATE_ENABLED=on, skip entries where calibrated_wr
    # (historical WR at that raw_prob bucket) is below threshold.
    # Default off until 250+ trades accumulate for higher confidence buckets.
    #
    # 2026-05-21 bug fix: gate was placed BEFORE win_pct was defined, so
    # NameError was being swallowed and gate never worked. Moved here.
    try:
        import os as _os
        if _os.environ.get("CALIBRATION_GATE_ENABLED", "off").lower() == "on":
            from calibration import calibrated_wr, is_inverted
            cal_min = float(_os.environ.get("CALIBRATION_MIN_WR", "55"))
            cal_wr = calibrated_wr(int(win_pct), engine_type="scalper", action=action_str)
            inverted = is_inverted(int(win_pct), engine_type="scalper")
            # Shadow log every check
            print(f"[CALIB_SHADOW] scalper {action_str} raw_prob={win_pct:.0f}% "
                  f"cal_wr={cal_wr} inverted={inverted} threshold={cal_min}")
            # Block only if data exists AND below threshold
            if cal_wr is not None and cal_wr < cal_min:
                print(f"[SCALPER] REJECT entry (G0c): calibration gate — "
                      f"raw {win_pct:.0f}% but historical WR {cal_wr}% < {cal_min}%")
                return False
    except Exception as _e:
        print(f"[SCALPER] calibration_gate error (allow): {_e}")

    # ── Capitulation-confirmed lowering of threshold ──
    # If capit engine sees reversal in same direction as our action,
    # accept entries 5pp below the configured threshold.
    # Today's miss: 23950 CE V-bottom — capit bull was ~3-4 (just under
    # ALERT) but the trade would have made +600%. Worth taking with
    # capit confirmation even at 50% probability.
    effective_threshold = threshold
    try:
        from capitulation_engine import get_live_state
        cap_state = get_live_state() or {}
        cap_idx = (cap_state.get("results") or {}).get(idx, {})
        cap_bull = (cap_idx.get("bullish") or {}).get("score", 0)
        cap_bear = (cap_idx.get("bearish") or {}).get("score", 0)
        if is_ce and cap_bull >= 4:
            effective_threshold = max(50, threshold - 5)
        elif not is_ce and cap_bear >= 4:
            effective_threshold = max(50, threshold - 5)
    except Exception:
        pass

    # ── STEP 2 SURGICAL FIXES (2026-06-03 — 445-trade audit) ──
    # Data-driven threshold tuning per context. All env-overridable.
    # No new module — just bias the existing effective_threshold.
    try:
        # FIX A: BANKNIFTY scalper threshold bias (RELAXED 2026-06-05)
        # Audit said SCALPER BANKNIFTY = -₹50,586 baseline. But user
        # principle: "smart not strict". A blanket +5 threshold hurts
        # legitimate BNF setups. Now default 0 (no penalty) — let market
        # context bonus + damage control handle it. Set env to 5 to
        # restore previous behavior.
        if idx == "BANKNIFTY":
            effective_threshold += int(os.environ.get("SCALPER_BNF_THRESHOLD_BIAS", "0"))

        # FIX B was: Scalper CE side bias (+3 pts for CE entries)
        # DROPPED 2026-06-03 — backtest showed it BLOCKED MORE WINS THAN
        # LOSSES (₹73k wins blocked vs only ₹22k losses = net -₹51k).
        # The CE-loses-money insight is real, but the losses come from
        # SPECIFIC conditions (BANKNIFTY + late entries + counter-trend),
        # not generic "low conviction CE". The BNF threshold + bleed-zone
        # + overconviction cap already catch those. A blanket CE penalty
        # here would over-block.
        # Override available: set SCALPER_CE_THRESHOLD_BIAS=N to enable.
        _ce_bias = int(os.environ.get("SCALPER_CE_THRESHOLD_BIAS", "0"))
        if is_ce and _ce_bias:
            effective_threshold += _ce_bias

        # FIX C2: Bleed-zone — RELAXED to OFF by default (2026-06-05)
        # User: "smart not strict, strict only for loss-cap"
        # Original intent: 13:30-14:00 had 26% WR + ₹257k losses.
        # But damage control (EARLY_CUT + BREAKEVEN 3%) now handles the
        # bad-decision losses at exit side. Don't double-block entries.
        # Set BLEED_HOURS_MODE=smart or strict if you want to re-enable.
        # FIX C: 13:30-14:00 IST bleed-zone — SMART CONDITIONAL block
        # User insight: "worst window nahi, worst decision making" — big
        # moves DO happen in this window; small pullbacks within bigger
        # trends look like reversals to the system → bad entries.
        # Audit deep-dive of 40 trades in this window:
        #   WINNERS (10, ₹+138k): 60% NIFTY, avg conviction 34/66 (clear),
        #     Multi-TF MOSTLY_* (partial alignment = early move)
        #   LOSERS (29, ₹-395k): 69% BANKNIFTY, avg conviction 47/53 (mixed),
        #     Multi-TF ALL_BEARISH/ALL_BULLISH (saturated = top/bottom)
        # → Block only loss-prone setups, allow conviction-clear ones.
        # Backtest: universal block +₹257k vs smart block +₹207k.
        # ₹50k cost saves 17 winning trades from being blocked.
        # BLEED_HOURS_MODE: smart (default) | strict | off
        hour_min = now.hour * 60 + now.minute
        _bleed_mode = os.environ.get("BLEED_HOURS_MODE", "off").lower()
        if _bleed_mode != "off" and 13 * 60 + 30 <= hour_min < 14 * 60:
            if _bleed_mode == "strict":
                print(f"[SCALPER] REJECT (bleed-strict): 13:30-14:00 universal block")
                return False
            # SMART MODE: block only the loss-prone setups
            # Rule 1: BANKNIFTY needs higher conviction (75%+) in this window
            # Rule 2: Multi-TF ALL_* alignment = saturated move = reversal risk
            reasoning_list = verdict_data.get('reasons') or []
            reasoning_text = ' '.join(str(r) for r in reasoning_list).lower() if isinstance(reasoning_list, list) else str(reasoning_list).lower()
            is_saturated_tf = ('all_bullish' in reasoning_text or 'all_bearish' in reasoning_text)
            if idx == 'BANKNIFTY' and win_pct < 75:
                print(f"[SCALPER] REJECT (bleed-smart): BANKNIFTY in 13:30 zone needs ≥75% (got {win_pct}%)")
                return False
            if is_saturated_tf:
                print(f"[SCALPER] REJECT (bleed-smart): Multi-TF ALL_* in 13:30 zone = saturated move risk")
                return False
            # Else: allow — other gates (overconviction, BNF threshold, G16) still apply

        # FIX F was: BANKNIFTY CE bad-hour filter (REMOVED 2026-06-03)
        # User principle: "bad hours nahi hoti, bad decision/detection hoti
        # hai". Hour-blocking is the lazy fix — same hour can produce
        # massive winners or losers depending on the SETUP, not the clock.
        # Reverted in favor of damage-control approach (EARLY_CUT + lower
        # breakeven trigger) which:
        #   • Saves ₹829k by limiting loss size (vs ₹305k by blocking)
        #   • Doesn't block any winning trades
        #   • Applies to ALL setups in ALL hours
        # Env still available for emergency: BNF_CE_BAD_HOURS=11,13,15 to
        # re-enable hour filter; default empty = off.
        if (idx == "BANKNIFTY" and is_ce
                and os.environ.get("BNF_CE_BAD_HOURS", "").strip()):
            try:
                bad_hrs = [int(h.strip()) for h in os.environ.get("BNF_CE_BAD_HOURS", "").split(",") if h.strip()]
                if now.hour in bad_hrs:
                    print(f"[SCALPER] REJECT (BNF-CE bad-hour override): "
                          f"hour {now.hour} blocked by BNF_CE_BAD_HOURS env")
                    return False
            except Exception:
                pass

        # FIX E: Streak overconfidence handler (scalper-specific)
        # Audit: SCALPER after 2 consecutive wins:
        #   • 48 trades, 35% WR, -₹104,070 net (worse than baseline 47% WR)
        # Cooldown timing DOESN'T help (35 of 48 within 10min were neutral).
        # The REAL pattern: relaxed conviction + BANKNIFTY chasing.
        # Fix: after 2W streak, raise threshold +5 AND block BANKNIFTY.
        # Backtest: blocks 30 trades, saves ₹368k losses vs ₹182k wins
        # blocked = net +₹186k. Best per-fix ROI of the bunch.
        # Override:
        #   STREAK_HANDLER_DISABLED=1     kill switch
        #   STREAK_THRESHOLD_BIAS=5       extra threshold after 2W (default 5)
        #   STREAK_BLOCK_BNF=1            block BANKNIFTY after 2W (default 1)
        # 2026-06-08 v2: DEFAULT OFF. After 2 wins blocking BNF caused
        # follow-on big winners to be missed. Damage control catches losses.
        # Set STREAK_HANDLER_ENABLED=on to re-enable.
        if os.environ.get("STREAK_HANDLER_ENABLED", "").strip() in ("1","true","on"):
            try:
                last_two = _last_n_outcomes(2)
                if len(last_two) == 2 and last_two[0] == 'WIN' and last_two[1] == 'WIN':
                    _streak_bias = int(os.environ.get("STREAK_THRESHOLD_BIAS", "5"))
                    _streak_block_bnf = os.environ.get("STREAK_BLOCK_BNF", "1").strip() in ("1","true","on")
                    if _streak_block_bnf and idx == "BANKNIFTY":
                        print(f"[SCALPER] REJECT (streak): BANKNIFTY after 2 wins — "
                              f"audit shows 35% WR + over-confidence chase")
                        return False
                    effective_threshold += _streak_bias
                    print(f"[SCALPER] STREAK guard active (2W today): threshold "
                          f"+{_streak_bias} (now {effective_threshold}%)")
            except Exception as _e:
                # NEVER block legit entry on streak handler error
                print(f"[SCALPER] streak handler error (allow): {_e}")

        # FIX D: Over-conviction cap (both modes)
        # Audit: prob 80+% → MAIN 42% WR -₹111k, SCALPER 32% WR -₹42k
        # Extreme conviction = market exhaustion = reversal imminent.
        # Block fresh entries above 85% unless capit confirms reversal.
        # 2026-06-08 v2: raised 85 → 90. Was blocking legitimate high-conv
        # entries. Only extreme 90%+ now considered "top/bottom risk".
        overconv_cap = float(os.environ.get("OVERCONVICTION_BLOCK", "90"))
        if win_pct >= overconv_cap:
            # capitulation reversal-confirmation can bypass
            try:
                from capitulation_engine import get_live_state as _gls
                cs = _gls() or {}
                _cidx = (cs.get("results") or {}).get(idx, {})
                _cb = (_cidx.get("bullish") or {}).get("score", 0)
                _cz = (_cidx.get("bearish") or {}).get("score", 0)
                if is_ce and _cb >= 5:
                    pass  # capit strongly confirms bull → allow
                elif (not is_ce) and _cz >= 5:
                    pass
                else:
                    print(f"[SCALPER] REJECT entry (Step 2 overconv-cap): "
                          f"win_pct {win_pct}% ≥ {overconv_cap}% — "
                          f"extreme conviction = top/bottom risk (audit -₹150k)")
                    return False
            except Exception:
                pass
    except Exception as _e:
        # NEVER let surgical-fix logic block a legit trade on its own error
        print(f"[SCALPER] Step 2 fix error (allow): {_e}")

    if win_pct < effective_threshold:
        return False
    today_iso = now.strftime("%Y-%m-%d")
    conn = _conn()

    # ── B1.3: HARD daily cap (date prefix match, IST-aware) ──
    # Old query used `entry_time > today_start_iso` which is fragile against
    # tz-aware/naive ISO mixing and timezone drift. Use date prefix match —
    # entry_time is always stored as IST ISO so prefix is YYYY-MM-DD.
    today_count = conn.execute(
        "SELECT COUNT(*) FROM scalper_trades WHERE substr(entry_time,1,10) = ?",
        (today_iso,)
    ).fetchone()[0]
    if today_count >= daily_cap:
        conn.close()
        print(f"[SCALPER] REJECT entry: daily cap {daily_cap} hit ({today_count} trades today)")
        return False

    # ── CAPITAL CHECK — total committed across all OPEN trades must not exceed capital ──
    committed_row = conn.execute("""
        SELECT COALESCE(SUM(COALESCE(capital_used, entry_price * qty)), 0) as committed
        FROM scalper_trades WHERE status='OPEN'
    """).fetchone()
    committed = committed_row["committed"] or 0
    available = capital - committed

    if available <= 0:
        conn.close()
        print(f"[SCALPER] REJECT entry: capital ₹{capital:,.0f} fully committed across {today_count} open trades (₹{committed:,.0f}). Available: ₹{available:,.0f}")
        return False

    # Estimate new trade cost from user qty config or 10% of capital fallback
    user_qty_field = "nifty_qty" if idx == "NIFTY" else "banknifty_qty"
    user_qty = cfg.get(user_qty_field, 0) or 0
    # Use a conservative premium estimate — most scalper entries are ₹50-300
    est_premium = 200
    if user_qty > 0:
        est_cost = user_qty * est_premium
    else:
        # Auto-sizing: 1% risk × capital, with 8% SL → max qty cost ≈ ~12% of capital
        est_cost = capital * 0.12

    if est_cost > available:
        conn.close()
        print(f"[SCALPER] REJECT entry: estimated cost ₹{est_cost:,.0f} > available ₹{available:,.0f} (capital ₹{capital:,.0f}, committed ₹{committed:,.0f})")
        return False

    # ── B3.10: CAPITAL CONCENTRATION (per-trade + per-direction) ──
    if est_cost > capital * MAX_CAPITAL_PCT_PER_TRADE:
        conn.close()
        print(f"[SCALPER] REJECT entry: trade size ₹{est_cost:,.0f} exceeds "
              f"{MAX_CAPITAL_PCT_PER_TRADE*100:.0f}% concentration cap (₹{capital * MAX_CAPITAL_PCT_PER_TRADE:,.0f})")
        return False

    # Per-direction cap: sum capital_used of open trades on same side (CE/PE)
    side_filter = "BUY CE" if is_ce else "BUY PE"
    same_side_committed_row = conn.execute("""
        SELECT COALESCE(SUM(COALESCE(capital_used, entry_price * qty)), 0)
        FROM scalper_trades
        WHERE status='OPEN' AND action LIKE ?
    """, (f"%{'CE' if is_ce else 'PE'}%",)).fetchone()
    same_side_committed = same_side_committed_row[0] or 0
    if (same_side_committed + est_cost) > capital * MAX_CAPITAL_PCT_PER_DIRECTION:
        conn.close()
        print(f"[SCALPER] REJECT entry: same-direction concentration "
              f"₹{same_side_committed + est_cost:,.0f} would exceed "
              f"{MAX_CAPITAL_PCT_PER_DIRECTION*100:.0f}% cap "
              f"(₹{capital * MAX_CAPITAL_PCT_PER_DIRECTION:,.0f})")
        return False

    # No duplicate open
    dup_open = conn.execute(
        "SELECT COUNT(*) FROM scalper_trades WHERE status='OPEN' AND idx=? AND action=?",
        (idx, action_str)
    ).fetchone()[0]
    if dup_open > 0:
        conn.close()
        return False

    # ── WHIPSAW GUARDS ──
    if atm_strike is None:
        conn.close()
        return False

    # Guard 1: Cooldown after ANY exit on same strike
    # Scalper INDEPENDENT — uses its own config, not buyer_mode (which is for main trades)
    cooldown_cutoff = (now - timedelta(minutes=COOLDOWN_SAME_STRIKE_MIN * _cooldown_mult)).isoformat()
    recent_same_strike = conn.execute(
        """SELECT COUNT(*) FROM scalper_trades
           WHERE idx=? AND strike=? AND status!='OPEN' AND exit_time > ?""",
        (idx, int(atm_strike), cooldown_cutoff)
    ).fetchone()[0]
    if recent_same_strike > 0:
        conn.close()
        return False

    # Guard 2: Flip direction block (CE→PE or PE→CE on same strike)
    flip_cutoff = (now - timedelta(minutes=COOLDOWN_FLIP_DIRECTION_MIN * _cooldown_mult)).isoformat()
    opposite = "BUY PE" if "CE" in action_str else "BUY CE"
    recent_opposite = conn.execute(
        """SELECT COUNT(*) FROM scalper_trades
           WHERE idx=? AND strike=? AND action=? AND entry_time > ?""",
        (idx, int(atm_strike), opposite, flip_cutoff)
    ).fetchone()[0]
    if recent_opposite > 0:
        conn.close()
        return False

    # Guard 3: 2+ SL hits on same strike today → pause that strike
    sl_hits_today = conn.execute(
        """SELECT COUNT(*) FROM scalper_trades
           WHERE idx=? AND strike=? AND status='SL_HIT'
             AND substr(entry_time,1,10) = ?""",
        (idx, int(atm_strike), today_iso)
    ).fetchone()[0]
    if sl_hits_today >= MAX_SL_HITS_SAME_STRIKE:
        conn.close()
        return False

    # Guard 4: WATCHER_EXIT cooldown (Fix C, 2026-06-15)
    # Targets WATCHER_EXIT ₹3.58L bleed (16 trades, avg peak 0.83%,
    # hold 1.6 min — wrong-direction immediate kills).
    # If ANY scalper trade in same index just got watcher-killed in
    # last N min, the market is hostile to that idx — skip new entries.
    # 2026-06-15 tweak: window 5 → 15 min after observing 3 WATCHER_EXIT
    # trades spaced hours apart that all escaped 5-min cooldown.
    # Env: SCALPER_WATCHER_COOLDOWN_DISABLED=1, SCALPER_WATCHER_COOLDOWN_MIN=15
    try:
        import os as _os_wc
        if _os_wc.environ.get("SCALPER_WATCHER_COOLDOWN_DISABLED", "").strip() not in ("1","true","on"):
            _wc_min = float(_os_wc.environ.get("SCALPER_WATCHER_COOLDOWN_MIN", "15"))
            _wc_cutoff = (now - timedelta(minutes=_wc_min)).isoformat()
            recent_watcher = conn.execute(
                """SELECT COUNT(*) FROM scalper_trades
                   WHERE idx=? AND status IN ('WATCHER_EXIT','EARLY_CUT','INSTANT_REJECT')
                     AND exit_time > ?""",
                (idx, _wc_cutoff)
            ).fetchone()[0]
            if recent_watcher > 0:
                conn.close()
                print(f"[SCALPER] REJECT entry: WATCHER_COOLDOWN — {recent_watcher} hostile "
                      f"exit(s) on {idx} in last {_wc_min:.0f} min, market unfriendly")
                return False
    except Exception as _wc_e:
        print(f"[SCALPER] WATCHER_COOLDOWN error (allow): {_wc_e}")

    conn.close()

    # ── B1.4: DIRECTION SANITY (OI delta + spot must AGREE with action) ──
    # Today (2026-05-04) cost ₹91k: "PE dominant 80%" reasoning kept saying
    # BUY CE while spot was falling and OI was rotating bearish.
    # New rule: query oi_delta_tracker — for BUY CE we want CE writers
    # COVERING (bullish reversal) OR PE writers ADDING (bullish floor).
    # If OI signals contradict, BLOCK.
    try:
        from oi_delta_tracker import assess as _oi_assess
        oi = _oi_assess(idx)
        sigs = oi.get("signals", {}) if oi else {}
        if is_ce:
            # Hostile to BUY CE: CE writers ADDING (ceiling) or PE writers COVERING (bearish reversal)
            if sigs.get("ce_writer_adding") or sigs.get("pe_writer_covering"):
                ce15 = oi.get("ce_oi_delta_15m_pct")
                pe15 = oi.get("pe_oi_delta_15m_pct")
                print(f"[SCALPER] REJECT entry (B1.4): {idx} BUY CE blocked — "
                      f"OI hostile (CE15m={ce15}%, PE15m={pe15}%, "
                      f"ce_adding={sigs.get('ce_writer_adding')}, "
                      f"pe_covering={sigs.get('pe_writer_covering')})")
                return False
        else:
            # Hostile to BUY PE: PE writers ADDING (floor) or CE writers COVERING (bullish reversal)
            if sigs.get("pe_writer_adding") or sigs.get("ce_writer_covering"):
                ce15 = oi.get("ce_oi_delta_15m_pct")
                pe15 = oi.get("pe_oi_delta_15m_pct")
                print(f"[SCALPER] REJECT entry (B1.4): {idx} BUY PE blocked — "
                      f"OI hostile (CE15m={ce15}%, PE15m={pe15}%, "
                      f"pe_adding={sigs.get('pe_writer_adding')}, "
                      f"ce_covering={sigs.get('ce_writer_covering')})")
                return False
    except Exception as _e:
        # Non-fatal — if tracker isn't ready, fall through (don't block valid trades)
        pass

    # ── B2.6: CAPITULATION REVERSAL GATE ──
    # 2026-06-08 v2: STRICTER bypass — only block when capit score >= 7
    # (was 5). Score 5-6 is borderline and was blocking legitimate
    # high-conviction entries during today's reversal. Damage control
    # catches loss if direction was wrong.
    # Env: CAPIT_REVERSAL_THRESHOLD=7 (default), set =5 to restore old strict.
    try:
        from capitulation_engine import get_live_state
        cap_state = get_live_state() or {}
        idx_state = (cap_state.get("results") or {}).get(idx, {})
        bull = idx_state.get("bullish") or {}
        bear = idx_state.get("bearish") or {}
        bull_score = float(bull.get("score") or 0)
        bear_score = float(bear.get("score") or 0)
        capit_thr = float(os.environ.get("CAPIT_REVERSAL_THRESHOLD", "7"))
        if is_ce and bear_score >= capit_thr and bear_score > bull_score:
            print(f"[SCALPER] REJECT entry (B2.6): {idx} BUY CE blocked — "
                  f"BEARISH capit {bear_score} (thr {capit_thr})")
            return False
        if not is_ce and bull_score >= capit_thr and bull_score > bear_score:
            print(f"[SCALPER] REJECT entry (B2.6): {idx} BUY PE blocked — "
                  f"BULLISH capit {bull_score} (thr {capit_thr})")
            return False
    except Exception:
        pass

    # ── GATE 11: ENTRY FILTERS (5-min trend + greeks + regime) ──
    # PERMISSIVE MODE (2026-06-05) — user concern: "system jaise pehle
    # trades lera tha waise hi le, miss na kare. SL smart hai."
    #
    # Default: entry_filter logs WARNING but doesn't block. Damage
    # control (EARLY_CUT + BREAKEVEN) handles bad-setup losses at exit
    # side. This matches the May behavior when system was firing 10-25
    # trades/day instead of 0.
    # Override: ENTRY_FILTER_MODE=strict to restore old hard-block.
    if engine is not None and atm_strike is not None:
        _ef_mode = os.environ.get("ENTRY_FILTER_MODE", "permissive").lower()
        try:
            from entry_filters import check_all_filters
            filters_ok, filter_reason, regime_info = check_all_filters(
                engine, idx, int(atm_strike), action_str
            )
            if not filters_ok:
                if _ef_mode == "strict":
                    # Old strict behaviour — hard block
                    if regime_info.get("regime") == "CHOP":
                        capit_confirms_direction = (
                            (is_ce and cap_bull >= 4) or
                            (not is_ce and cap_bear >= 4)
                        )
                        if not capit_confirms_direction and not _allow_chop_health:
                            print(f"[SCALPER] REJECT entry (G11 strict): {filter_reason}")
                            return False
                        else:
                            _chop_why = "capit-confirmed" if capit_confirms_direction else "health AGGRESSIVE"
                            print(f"[SCALPER] CHOP override ({_chop_why}): {filter_reason}")
                    else:
                        print(f"[SCALPER] REJECT entry (G11 strict): {filter_reason}")
                        return False
                else:
                    # PERMISSIVE: log warning, let trade fire, trust damage control
                    print(f"[SCALPER] G11 WARN (allow, damage-control will catch): {filter_reason}")
            else:
                if regime_info.get("regime") == "BREAKOUT":
                    print(f"[SCALPER] BREAKOUT detected — {regime_info.get('reason')}")
        except Exception as _e:
            print(f"[SCALPER] entry_filters error (allow): {_e}")

    # ── G12: THETA GATE (2026-05-19) ──
    # 19 VELOCITY_EXIT scalper trades, 0 wins, -₹212,386 over 60d.
    # All were "bought option in flat market, theta ate premium before
    # spot moved". theta_gate uses REAL Black-Scholes (from engine.py
    # bs_greeks) + realized 30-min spot range to gate entries when
    # expected premium gain < 2× theta loss over hold window.
    # Shadow-logs always; only blocks when THETA_GATE_ENABLED=on.
    if engine is not None and atm_strike is not None:
        try:
            from theta_gate import gate_or_pass, is_theta_gate_enabled
            side = "CE" if is_ce else "PE"
            atm_data = engine.chains.get(idx, {}).get(int(atm_strike), {})
            entry_premium = atm_data.get(f"{side.lower()}_ltp", 0)
            expiry_str = str(engine.nearest_expiry.get(idx, "")) if hasattr(engine, "nearest_expiry") else ""
            hold_min = cfg.get("max_hold_min", 15)

            if entry_premium > 0:
                decision = gate_or_pass(
                    engine=engine,
                    idx=idx,
                    strike=int(atm_strike),
                    side=side,
                    option_premium=entry_premium,
                    expiry_date=expiry_str,
                    action=action_str,
                    hold_minutes=hold_min,
                    source="scalper.should_enter_scalp",
                )
                if is_theta_gate_enabled() and not decision.get("passes", True):
                    print(f"[SCALPER] REJECT entry (G12 theta): {decision.get('reason', '')}")
                    return False
        except Exception as _e:
            # NEVER let theta_gate exception block legit entries
            print(f"[SCALPER] theta_gate error (allow): {_e}")

    # ── G15: TIME-OF-DAY BLEED GATE (2026-05-27) ──
    # 60-day audit revealed 13:30-14:00 IST has 19% WR and lost ₹1.87L
    # alone (worst single 30-min window). Lunch-end chop traps traders
    # with false moves. Skip this hour entirely.
    # Env: SCALPER_SKIP_BLEED_HOURS (default off — explicit opt-in).
    if os.environ.get("SCALPER_SKIP_BLEED_HOURS", "off").lower() == "on":
        hour_min = now.hour * 60 + now.minute
        if 13 * 60 + 30 <= hour_min < 14 * 60:
            print(f"[SCALPER] REJECT entry (G15): 13:30-14:00 IST is bleed zone "
                  f"(historical WR 19%, -₹1.87L damage)")
            return False

    # ── G16: ANTI COUNTER-TREND GATE (2026-06-03 — always on) ──
    # Today (Jun 3): scalper took 3 PE entries (13:31/13:58 #241 #242 lost
    # ₹26k) during a 1,267-pt BANKNIFTY rally because 5-min OI flips
    # showed brief CE-fall/PE-rise during minor pullbacks. The 1hr trend
    # was clearly UP. Conviction was only 55% (right at threshold).
    #
    # Rule: if spot moved ≥ 0.4% in last 30 min in one direction AND the
    # proposed trade is OPPOSITE direction AND probability < 65, REJECT.
    # This blocks weak-conviction counter-trend chases without touching
    # high-conviction reversal calls (≥65% can still buy reversals).
    #
    # Env overrides:
    #   ANTI_COUNTER_DISABLED=1        kill switch
    #   ANTI_COUNTER_MOVE_PCT=0.4      threshold % move (default 0.4)
    #   ANTI_COUNTER_PROB_BYPASS=65    prob ≥ this skips the gate
    # 2026-06-08 v2: DEFAULT NOW OFF. User feedback: "trades hi ruk gaye hai".
    # G16 was blocking legitimate reversal entries during rallies — exactly
    # the setups that catch 100+ pt swings. Damage control handles bad ones.
    # Set ANTI_COUNTER_ENABLED=on to re-enable.
    #
    # 2026-06-15 (Fix F): DEFAULT FLIPPED BACK ON with smarter tuning:
    #   - move_thresh raised to 0.6 (was 0.4) — only big moves count
    #     as "in-trend" to be counter to
    #   - prob_bypass kept at 60 — high-conviction trades still pass
    # Combined with WATCHER cooldown (Fix C) and aggressive peak floor
    # (Fix A), legitimate reversals are safer than they were June 8.
    # Set ANTI_COUNTER_ENABLED=off to revert to fully off.
    _ac_env = os.environ.get("ANTI_COUNTER_ENABLED", "on").strip().lower()
    if engine is not None and _ac_env in ("1", "true", "on"):
        try:
            move_thresh = float(os.environ.get("ANTI_COUNTER_MOVE_PCT", "0.6"))
            # Default lowered 65→60 (2026-06-05): faster fires when conviction
            # is decent. Damage control catches the few bad ones at exit side.
            prob_bypass = float(os.environ.get("ANTI_COUNTER_PROB_BYPASS", "60"))
            hist = getattr(engine, "_spot_history", {}).get(idx, []) or []
            if hist and win_pct < prob_bypass:
                # Spot 30 min ago vs now
                import time as _t
                from datetime import datetime as _dt, timedelta as _td
                # Find the tick closest to 30 min ago
                cutoff = now - _td(minutes=30)
                cutoff_iso = cutoff.isoformat()
                ref = None
                for h in hist:
                    if h.get("t", "") <= cutoff_iso:
                        ref = h
                    else:
                        break
                # Fallback: oldest tick if hist < 30 min
                if ref is None and hist:
                    ref = hist[0]
                cur_ltp = hist[-1].get("ltp", 0) if hist else 0
                ref_ltp = ref.get("ltp", 0) if ref else 0
                if ref_ltp > 0 and cur_ltp > 0:
                    move_pct = (cur_ltp - ref_ltp) / ref_ltp * 100
                    # Rally up & wants PE = counter-trend bearish
                    if move_pct >= move_thresh and "PE" in action_str:
                        print(
                            f"[SCALPER] REJECT entry (G16 anti-counter): "
                            f"spot +{move_pct:.2f}% last 30m, BUY PE counter-trend "
                            f"@ {win_pct}% (need ≥{prob_bypass}% to override)"
                        )
                        return False
                    # Selloff down & wants CE = counter-trend bullish
                    if move_pct <= -move_thresh and "CE" in action_str:
                        print(
                            f"[SCALPER] REJECT entry (G16 anti-counter): "
                            f"spot {move_pct:.2f}% last 30m, BUY CE counter-trend "
                            f"@ {win_pct}% (need ≥{prob_bypass}% to override)"
                        )
                        return False
        except Exception as _e:
            # NEVER let G16 block a legit trade on its own error
            print(f"[SCALPER] G16 anti-counter error (allow): {_e}")

    # ── G14: STRUCTURE GATE (Phase 2 — 2026-05-27) ──
    # Multi-timeframe HH/HL/LH/LL check via price_structure module.
    # Decides MODE A (aligned trend) / MODE B (counter-trend scalp) / SKIP.
    # Default master flag STRUCTURE_MODE=off → no behavior change. Failures
    # fail-safe (allow trade). Tuning (size/SL/T1/hold) returned for the
    # caller to apply downstream — log_scalp_trade reads it via the same
    # structure_gate cache.
    if engine is not None:
        try:
            import structure_gate as sg
            if sg.master_mode() != "off" and sg.scalper_enabled():
                sg_decision = sg.evaluate_entry(
                    engine=engine, idx=idx,
                    proposed_action=action_str,
                    source="scalper.should_enter_scalp",
                )
                if not sg_decision.get("allow", True):
                    print(
                        f"[SCALPER] REJECT entry (G14 structure): "
                        f"{sg_decision.get('reason', '')}"
                    )
                    return False
                # If LIVE mode with a real mode-A/B decision, stash tuning
                # for log_scalp_trade() to apply downstream. Only stash on
                # 'live' — shadow doesn't change behavior.
                if (sg.master_mode() == "live"
                        and sg_decision.get("mode") in ("aligned", "counter_trend")
                        and sg_decision.get("tuning")):
                    _pending_structure_tuning[(idx, action_str)] = {
                        "mode": sg_decision["mode"],
                        "tuning": sg_decision["tuning"],
                        "alignment": sg_decision.get("alignment"),
                    }
        except Exception as _e:
            # NEVER let structure gate exception block a legit trade
            print(f"[SCALPER] structure_gate error (allow): {_e}")

    # ── G13: EARLY-MOVE ENTRY GATE (2026-05-22) ──
    # Aggregator of 5 leading detectors. In 'veto'/'full' mode it can
    # BLOCK a scalper entry if the leading-indicator panel says BLOCKED
    # (IV crush / fakeout / exhaustion) or FIRE the OPPOSITE direction.
    # Default mode 'off' = pure shadow log, never affects trades.
    # This is the FIX for scalper's late-entry / chop-reversal problem.
    if engine is not None:
        try:
            from early_move.entry_gate import evaluate_entry
            em = evaluate_entry(
                engine=engine,
                idx=idx,
                proposed_action=action_str,
                source="scalper.should_enter_scalp",
            )
            if not em.get("allow", True):
                print(f"[SCALPER] REJECT entry (G13 early-move): {em.get('reason', '')}")
                return False
        except Exception as _e:
            # NEVER let early_move exception block legit entries
            print(f"[SCALPER] early_move entry_gate error (allow): {_e}")

    return True


# ═══════════════════════════════════════════════════════════════
# SMART SL SYSTEM — 7-stage Profit Ladder + Spot Anchor
# ═══════════════════════════════════════════════════════════════

# Profit-management #2 RETUNE (2026-05-05): More aggressive locks — at +5%
# trigger, lock +2% (not breakeven). Previous ladder gave back winners —
# user complained about peaked +12% exiting at +2%. New ladder locks more
# at every stage to capture more of the move.
SMART_SL_LADDER = [
    (0,   -8,  "Initial"),       # safety floor
    (3,   -5,  "Trail-1"),       # early micro-trail
    (5,   +2,  "Lock +2%"),      # was breakeven — now lock +2% at +5% trigger
    (8,   +5,  "Lock +5%"),      # was +3 — tighter
    (12,  +8,  "Lock +8%"),      # was +6
    (18,  +12, "Lock +12%"),     # was +10
    (28,  +20, "Lock +20%"),     # was +18
    (40,  +30, "Lock +30%"),     # was +28
]


def get_smart_sl_config():
    """Returns {enabled: bool, spot_anchor_pct: float}."""
    init_scalper_db()
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM smart_sl_config WHERE id=1").fetchone()
        if not row:
            return {"enabled": False, "spot_anchor_pct": 0.4}
        return {
            "enabled": bool(row["enabled"]),
            "spot_anchor_pct": row["spot_anchor_pct"] or 0.4,
        }
    finally:
        conn.close()


def set_smart_sl_config(enabled=None, spot_anchor_pct=None):
    """Update smart SL config (only provided fields)."""
    init_scalper_db()
    cur = get_smart_sl_config()
    new_enabled = 1 if (enabled if enabled is not None else cur["enabled"]) else 0
    new_pct = float(spot_anchor_pct) if spot_anchor_pct is not None else cur["spot_anchor_pct"]
    conn = _conn()
    try:
        conn.execute("""
            UPDATE smart_sl_config SET enabled=?, spot_anchor_pct=?, updated_at=? WHERE id=1
        """, (new_enabled, new_pct, ist_now().isoformat()))
        conn.commit()
    finally:
        conn.close()
    return get_smart_sl_config()


def compute_smart_sl(entry_price, current_premium, current_stage_saved=0, saved_sl=None):
    """Compute smart SL based on profit ladder.
    Returns (active_sl, stage, stage_label).
    Ratchet rule: SL never goes DOWN — stays at highest achieved stage."""
    if entry_price <= 0:
        return entry_price * 0.85, 0, "Initial"

    profit_pct = (current_premium - entry_price) / entry_price * 100

    # Find highest stage achieved by current profit
    new_stage = 0
    new_offset = -15
    new_label = "Initial"
    for trigger_pct, offset_pct, label in SMART_SL_LADDER:
        if profit_pct >= trigger_pct:
            new_stage = SMART_SL_LADDER.index((trigger_pct, offset_pct, label))
            new_offset = offset_pct
            new_label = label

    new_sl = round(entry_price * (1 + new_offset / 100), 2)

    # Ratchet rule: if saved_sl is higher, use that (SL never decreases)
    if saved_sl is not None and saved_sl > new_sl:
        return saved_sl, current_stage_saved, SMART_SL_LADDER[current_stage_saved][2]

    # Stage upgraded
    return new_sl, new_stage, new_label


def check_spot_anchor(action, entry_spot, current_spot, threshold_pct=0.4):
    """Returns (should_exit, reason).
    BUY CE: exit if spot drops > threshold_pct
    BUY PE: exit if spot rises > threshold_pct"""
    if not entry_spot or entry_spot <= 0 or not current_spot or current_spot <= 0:
        return False, None
    spot_change_pct = (current_spot - entry_spot) / entry_spot * 100
    is_ce = "CE" in (action or "")
    if is_ce and spot_change_pct < -threshold_pct:
        return True, f"Spot anchor: NIFTY dropped {spot_change_pct:.2f}% from entry {entry_spot} (now {current_spot:.1f})"
    if not is_ce and spot_change_pct > threshold_pct:
        return True, f"Spot anchor: NIFTY rose {spot_change_pct:.2f}% from entry {entry_spot} (now {current_spot:.1f})"
    return False, None


def get_ladder_progress(entry_price, current_premium, current_stage_saved=0):
    """For UI: full ladder state with current/done/pending stages."""
    if entry_price <= 0:
        return []
    profit_pct = (current_premium - entry_price) / entry_price * 100
    out = []
    for i, (trigger, offset, label) in enumerate(SMART_SL_LADDER):
        sl_at = round(entry_price * (1 + offset / 100), 2)
        if profit_pct >= trigger:
            status = "DONE" if i < len([s for s in SMART_SL_LADDER if profit_pct >= s[0]]) - 1 else "ACTIVE"
        else:
            status = "PENDING"
        out.append({
            "stage": i,
            "trigger_pct": trigger,
            "sl_offset_pct": offset,
            "sl_at": sl_at,
            "label": label,
            "status": status,
        })
    return out


def calc_scalper_size(entry_price, sl_price, running_capital=1000000):
    """Smaller position for scalper — 1.5% risk per trade."""
    if entry_price <= 0:
        return 1, 25, 25

    max_risk = running_capital * SCALPER_RISK_PCT / 100
    risk_per_unit = max(entry_price - sl_price, 1) if sl_price > 0 else entry_price * 0.08
    risk_per_unit = max(risk_per_unit, 1)

    max_qty = int(max_risk / risk_per_unit)
    return max_qty


def log_scalp_trade(idx, action, strike, entry_price, probability, expiry="",
                    entry_reasoning=None, entry_bull_pct=None, entry_bear_pct=None,
                    entry_spot=None, verdict_data=None):
    """Create new scalper trade with RUNNING capital (auto-adjusts after profit/loss).

    Returns trade_id on success, None if skipped/rejected.
    """
    # ── KILL SWITCH: auto-trading disabled? ──
    # When SCALPER_AUTO_TRADE_ENABLED is False, all entry-firing is
    # suppressed but signal generation + analytics keep working.
    # See module-level comment for rationale (2026-05-18 pause).
    if not SCALPER_AUTO_TRADE_ENABLED:
        try:
            now_iso = ist_now().isoformat() if 'ist_now' in globals() else datetime.now().isoformat()
        except Exception:
            now_iso = ""
        print(
            f"[SCALPER] SKIPPED entry — auto-trade DISABLED (kill switch on). "
            f"Would have fired: {idx} {action} {strike} @ ₹{entry_price} prob={probability}%. "
            f"Set SCALPER_AUTO_TRADE=on env var to re-enable."
        )
        # Best-effort: log the suppressed entry to council.db audit so
        # we can later compare "what would have fired" vs "what we saved".
        try:
            from council import storage as _council_storage
            _council_storage.log_autologin_attempt(
                trigger_source="scalper_suppressed",
                status="skipped",
                error=f"scalper auto-trade disabled (would have fired {idx} {action} {strike})",
                duration_ms=0,
                extra={
                    "idx": idx, "action": action, "strike": strike,
                    "entry_price": entry_price, "probability": probability,
                    "entry_bull_pct": entry_bull_pct, "entry_bear_pct": entry_bear_pct,
                },
            )
        except Exception:
            pass
        # Telegram alert (throttled, so we don't spam) — informational
        try:
            import telegram_alerts as _tg
            if _tg.is_enabled():
                _tg.send(
                    f"⏸️ Scalper suppressed: {idx} {action} {strike} @ ₹{entry_price} "
                    f"prob={probability}%",
                    key="scalper_suppressed",  # throttled to 1/min
                )
        except Exception:
            pass
        return None

    if entry_price <= 0:
        return None

    # NEW: use active config with expiry multipliers applied
    cfg = get_active_scalp_config()
    sl_pct = cfg.get("sl_pct") or SCALPER_SL_PCT
    t1_pct = cfg.get("t1_pct") or SCALPER_T1_PCT
    t2_pct = cfg.get("t2_pct") or SCALPER_T2_PCT
    qty_mult = cfg.get("qty_mult", 1.0)
    is_expiry = cfg.get("is_expiry", False)
    if is_expiry:
        print(f"[SCALPER] EXPIRY config: SL={sl_pct*100:.1f}% T1={t1_pct*100:.1f}% "
              f"T2={t2_pct*100:.1f}% qty×{qty_mult} hold≤{cfg.get('max_hold_min')}m")

    # ── STRUCTURE-MODE TUNING (Phase 4 — 2026-05-27) ──
    # If G14 (structure_gate) chose Mode A (aligned) or Mode B (counter-
    # trend), apply that mode's size/SL/T1/T2/hold params. Stored by
    # should_enter_scalp() keyed by (idx, action). Default tuning unchanged
    # when not present (e.g. STRUCTURE_MODE=off or 'shadow').
    structure_mode_for_trade = None
    try:
        _st = _pending_structure_tuning.pop((idx, action), None)
        if _st and _st.get("tuning"):
            _t = _st["tuning"]
            structure_mode_for_trade = _st["mode"]
            old_sl, old_t1, old_t2 = sl_pct, t1_pct, t2_pct
            sl_pct = _t.get("sl_pct", sl_pct) or sl_pct
            t1_pct = _t.get("t1_pct", t1_pct) or t1_pct
            if _t.get("t2_pct") is not None:
                t2_pct = _t.get("t2_pct") or t2_pct
            # qty_mult is applied later (multiplied with health qty_mult)
            qty_mult = qty_mult * (_t.get("size_mult", 1.0) or 1.0)
            print(f"[SCALPER] STRUCTURE-{structure_mode_for_trade.upper()} tuning — "
                  f"SL {old_sl*100:.1f}%→{sl_pct*100:.1f}% "
                  f"T1 {old_t1*100:.1f}%→{t1_pct*100:.1f}% "
                  f"size×{_t.get('size_mult', 1.0)}")
    except Exception as _e:
        print(f"[SCALPER] structure tuning apply error (default): {_e}")

    # ── ADAPTIVE MARKET-HEALTH — exit-side tuning (2026-05-22) ──
    # Bigger targets when the market is AGGRESSIVE, smaller size when
    # DEFENSIVE. Reads the cached health level set by should_enter_scalp
    # earlier in the same cycle (assess() returns the cached result, so
    # no engine ref is needed here). Applied only in 'live' mode.
    try:
        import scalper_health
        _h = scalper_health.assess(None, idx)
        if _h.get("mode") == "live":
            _ht = _h.get("tuning", {})
            _tm = _ht.get("target_mult", 1.0)
            _sm = _ht.get("size_mult", 1.0)
            if _tm != 1.0 or _sm != 1.0:
                t1_pct = t1_pct * _tm
                t2_pct = t2_pct * _tm
                qty_mult = qty_mult * _sm
                print(f"[SCALPER] health {_h.get('level')} exit-tune — "
                      f"targets ×{_tm} size ×{_sm}")
    except Exception as _e:
        print(f"[SCALPER] scalper_health exit-tuning error (default): {_e}")

    # Use RUNNING capital (capital tracker) — base falls back to user config
    try:
        from capital_tracker import get_running_capital
        capital = get_running_capital("SCALPER") or cfg.get("capital") or 1000000
    except Exception:
        capital = cfg.get("capital") or 1000000

    sl_price = round(entry_price * (1 - sl_pct))
    t1_price = round(entry_price * (1 + t1_pct))
    t2_price = round(entry_price * (1 + t2_pct))

    # ── ADAPTIVE SL DISTANCE CAP BY PREMIUM (Fix 3, 2026-06-15) ──
    # When sl_pct config is wide (8%+) AND premium is fat (₹1000+ BNF ATM),
    # the absolute rupee risk per lot becomes ₹80k+. Cap SL distance by
    # premium size. Audit: S-106 ₹1233 entry × 12% wide = ₹88k loss.
    # Env kill: SCALPER_ADAPTIVE_SL_DISABLED=1
    try:
        import os as _os_asl
        if _os_asl.environ.get("SCALPER_ADAPTIVE_SL_DISABLED", "").strip() not in ("1","true","on"):
            _asl_fat = float(_os_asl.environ.get("SCALPER_ADAPTIVE_SL_FAT_PREMIUM", "1000"))
            _asl_mid = float(_os_asl.environ.get("SCALPER_ADAPTIVE_SL_MID_PREMIUM", "500"))
            _asl_fat_cap = float(_os_asl.environ.get("SCALPER_ADAPTIVE_SL_FAT_PCT", "7.0"))
            _asl_mid_cap = float(_os_asl.environ.get("SCALPER_ADAPTIVE_SL_MID_PCT", "9.0"))
            _asl_def_cap = float(_os_asl.environ.get("SCALPER_ADAPTIVE_SL_DEFAULT_PCT", "12.0"))
            current_sl_dist_pct = (entry_price - sl_price) / entry_price * 100 if entry_price > 0 else 0
            if entry_price >= _asl_fat:
                cap_pct = _asl_fat_cap
            elif entry_price >= _asl_mid:
                cap_pct = _asl_mid_cap
            else:
                cap_pct = _asl_def_cap
            if current_sl_dist_pct > cap_pct:
                new_sl_price = round(entry_price * (1 - cap_pct/100))
                print(f"[SCALPER] ADAPTIVE_SL: premium ₹{entry_price:.1f} → "
                      f"capping SL distance from {current_sl_dist_pct:.1f}% to {cap_pct:.1f}% "
                      f"(SL ₹{sl_price} → ₹{new_sl_price})")
                sl_price = new_sl_price
    except Exception as _asl_e:
        print(f"[SCALPER] ADAPTIVE_SL error (keeping original): {_asl_e}")

    # Anti-stop-hunt SL wrapping (2026-05-19).
    # Scalper had 69 SL_HIT trades / -₹656k over 60d — many at obvious
    # round-number SLs that institutions sweep. smart_sl applies tick-
    # precision rounding + offset from round levels. Always shadow-logs.
    # Behaviour change only when SMART_SL_ENABLED=on.
    try:
        from smart_sl import smart_sl_or_legacy
        sl_price = smart_sl_or_legacy(
            entry_price=entry_price,
            legacy_sl=sl_price,
            atr_pct=None,  # scalper uses % config; pass None for non-round adj only
            direction=action,
            source="scalper.log_scalp_trade",
        )
    except Exception as _e:
        print(f"[SCALPER] smart_sl wrap failed (keeping legacy): {_e}")

    # Lot size — VERIFIED 2026-06-10 against Kite live NFO instruments.
    # NSE revised: NIFTY 75→65, BANKNIFTY 35→30 (current as of June 2026).
    # OLD hardcoded 75/35 caused #267 to deploy with wrong qty calc.
    lot_sizes = {"NIFTY": 65, "BANKNIFTY": 30}
    lot_size = lot_sizes.get(idx, 65)

    user_qty = cfg.get("nifty_qty") if idx == "NIFTY" else cfg.get("banknifty_qty")
    if user_qty and user_qty > 0:
        qty = int(user_qty)
        lots = max(1, qty // lot_size)
    else:
        max_qty = calc_scalper_size(entry_price, sl_price, running_capital=capital)
        lots = max(1, max_qty // lot_size)
        qty = lots * lot_size

    # Apply expiry qty multiplier (round to lot multiple, min 1 lot)
    if qty_mult != 1.0:
        scaled_qty = int(qty * qty_mult)
        scaled_lots = max(1, scaled_qty // lot_size)
        qty = scaled_lots * lot_size
        lots = scaled_lots

    # ── TIERED CONVICTION SIZING (2026-06-11 v2 — data-driven) ──
    # 2026-06-13: DEFAULT DISABLED after forensic.
    # Reason: tiered killed 65-69 sweet spot AND 80+ buckets where Main
    # tab was profitable (Main 70-79: +₹146k, 80+%: +₹79k).
    # Plus: scaling by raw_prob is unreliable when threshold itself
    # is being raised to 65%. Below 65% trades don't exist anyway.
    # Flat sizing = May 19 era full power on every entry.
    # Override: SCALPER_TIERED_SIZING_DISABLED=0 to re-enable.
    try:
        import os as _os_oc
        if _os_oc.environ.get("SCALPER_TIERED_SIZING_DISABLED", "1").strip() not in ("1","true","on"):
            if probability and probability > 0:
                p = probability
                # Tiered multiplier from audit data
                if 65 <= p <= 69:
                    conv_mult = 1.5     # SWEET SPOT — boost
                    tier_label = "SWEET_SPOT"
                elif 60 <= p <= 64:
                    conv_mult = 1.0     # decent
                    tier_label = "DECENT"
                elif 70 <= p <= 74:
                    conv_mult = 1.0     # mild over-trap
                    tier_label = "MILD_OVER"
                elif 75 <= p <= 79:
                    conv_mult = 0.7     # over-trap zone
                    tier_label = "OVER_TRAP"
                elif p >= 80:
                    conv_mult = 0.5     # heavy over-trap (was OVERCONF)
                    tier_label = "HEAVY_OVER"
                elif 55 <= p <= 59:
                    conv_mult = 0.7     # under-trap
                    tier_label = "UNDER_TRAP"
                else:
                    conv_mult = 0.5     # very low, cautious
                    tier_label = "VERY_LOW"

                if conv_mult != 1.0:
                    new_qty = int(qty * conv_mult)
                    new_lots = max(1, new_qty // lot_size)
                    old_qty = qty
                    qty = new_lots * lot_size
                    lots = new_lots
                    print(f"[SCALPER] CONV_TIER · prob={p}% [{tier_label}] "
                          f"× {conv_mult} → qty {old_qty} → {qty}")
    except Exception as _oc_e:
        print(f"[SCALPER] tiered sizing error (allow): {_oc_e}")

    # ── HARD CAPITAL ENFORCEMENT ──
    # Total committed (open trades) + this trade MUST NOT exceed user's capital.
    # If it would, shrink THIS trade's qty to fit. If even 1 lot won't fit, REJECT.
    init_scalper_db()
    _conn_check = _conn()
    committed_row = _conn_check.execute("""
        SELECT COALESCE(SUM(COALESCE(capital_used, entry_price * qty)), 0) as committed
        FROM scalper_trades WHERE status='OPEN'
    """).fetchone()
    _conn_check.close()
    committed = committed_row["committed"] or 0
    available = capital - committed
    needed = entry_price * qty

    if needed > available:
        # Try to shrink qty to fit available capital (round down to lot multiple)
        max_affordable_qty = int(available // entry_price)
        max_affordable_qty = (max_affordable_qty // lot_size) * lot_size  # round to lot
        if max_affordable_qty < lot_size:
            # Can't even fit 1 lot — reject
            print(f"[SCALPER] REJECT log: needed ₹{needed:,.0f} > available ₹{available:,.0f} (capital ₹{capital:,.0f}, committed ₹{committed:,.0f}). Cannot fit even 1 lot.")
            return None
        # Shrink to fit
        old_qty = qty
        qty = max_affordable_qty
        lots = qty // lot_size
        print(f"[SCALPER] SHRUNK qty {old_qty}→{qty} ({lots} lots) to fit available ₹{available:,.0f} (was needing ₹{needed:,.0f})")

    # ── ASYMMETRIC SIZING by direction × structure (Fix H + I, 2026-06-15) ──
    # Data (60d backfill): PE aligned 5m+15m DN = 100% WR;
    #                      CE aligned 5m+15m UP = ₹-29k loss bucket.
    # Boost PE-golden, cut CE-aligned. Other buckets unchanged.
    # Env kill: ASYM_SIZE_DISABLED=1
    try:
        import os as _os_as
        if _os_as.environ.get("ASYM_SIZE_DISABLED", "").strip() not in ("1","true","on"):
            _pe_boost = float(_os_as.environ.get("ASYM_SIZE_PE_ALIGNED_MULT", "1.5"))
            _ce_cut = float(_os_as.environ.get("ASYM_SIZE_CE_ALIGNED_MULT", "0.5"))
            from structure_gate import get_cached_structure as _gcs
            _cache = _gcs(idx) or {}
            _structs = _cache.get("structures", {})
            _s5m = (_structs.get("5m") or {}).get("verdict", "")
            _s15m = (_structs.get("15m") or {}).get("verdict", "")
            _is_ce = "CE" in action
            _asym_mult = 1.0
            if (not _is_ce) and _s5m == "DOWNTREND" and _s15m == "DOWNTREND":
                _asym_mult = _pe_boost
                print(f"[SCALPER] ASYM_SIZE: PE aligned 5m+15m DN → boost {_asym_mult}x")
            elif _is_ce and _s5m == "UPTREND" and _s15m == "UPTREND":
                _asym_mult = _ce_cut
                print(f"[SCALPER] ASYM_SIZE: CE aligned 5m+15m UP → cut {_asym_mult}x")
            if _asym_mult != 1.0:
                new_qty = max(lot_size, int(qty * _asym_mult))
                # If boosting, re-check capital; else just apply
                if _asym_mult > 1.0:
                    available_after = capital - committed
                    if entry_price * new_qty <= available_after:
                        qty = new_qty
                        lots = qty // lot_size
                    else:
                        # not enough capital for boost — fall back to base
                        print(f"[SCALPER] ASYM_SIZE: PE boost skipped (capital ₹{available_after:,.0f} insufficient for ₹{entry_price * new_qty:,.0f})")
                else:
                    qty = new_qty
                    lots = qty // lot_size
    except Exception as _as_e:
        print(f"[SCALPER] ASYM_SIZE error (keep base): {_as_e}")

    capital_used = entry_price * qty

    # ── REGIME CAPTURE AT ENTRY (2026-06-15) ──
    # Snapshot of market regime + multi-TF structure for post-hoc chop audit.
    # Wrapped in try — never blocks trade if computation fails.
    _regime, _range_pct, _candle_pct = "", 0.0, 0.0
    _s5m, _s15m, _s1h = "", "", ""
    # ── ENGINE ATTRIBUTION CAPTURE (2026-06-16) ──
    _engine_scores_json = ""
    _signals_triggered = ""
    _gates_passed = ""
    try:
        import json as _json_es
        _es_blob = {"probability": probability, "action": action,
                    "entry_bull_pct": entry_bull_pct, "entry_bear_pct": entry_bear_pct,
                    "entry_reasoning": (entry_reasoning or "")[:500]}
        if verdict_data:
            for _k in ("winProbability", "bull", "bear", "reasons",
                       "engineScores", "engine_scores",
                       "voters", "council_votes", "modules", "callout"):
                _v = verdict_data.get(_k)
                if _v is not None:
                    _es_blob[_k] = _v
            _signals_triggered = " | ".join(
                str(r) for r in (verdict_data.get("reasons") or [])
            )[:1000]
        try:
            from capitulation_engine import get_live_state as _gls
            _cap = (_gls() or {}).get("results", {}).get(idx, {})
            _es_blob["capit_bull"] = (_cap.get("bullish") or {}).get("score", 0)
            _es_blob["capit_bear"] = (_cap.get("bearish") or {}).get("score", 0)
        except Exception:
            pass
        _engine_scores_json = _json_es.dumps(_es_blob, default=str)[:4000]
    except Exception as _es_e:
        print(f"[SCALPER] engine_attribution capture failed: {_es_e}")

    # ── LEVEL CONTEXT CAPTURE (2026-06-16) ──
    _level_context_json = ""
    _level_zone = ""
    try:
        from levels_context import get_levels_context as _glc
        from main import session as _msess
        _eng_lc = (_msess or {}).get("engine") if _msess else None
        _spot_lc = entry_spot if (entry_spot and entry_spot > 0) else None
        if _eng_lc is None:
            _eng_lc = type('E', (), {'spot_tokens': {}, 'prices': {}, '_spot_history': {}})()
        _ctx_lc = _glc(_eng_lc, idx, _spot_lc)
        import json as _json_lc
        _level_context_json = _json_lc.dumps(_ctx_lc, default=str)[:2000]
        _level_zone = (_ctx_lc.get("zone") or "")[:32]
    except Exception as _lc_e:
        print(f"[SCALPER] level_context capture failed: {_lc_e}")
    try:
        # engine accessed via main.session (set by app at startup)
        from main import session as _msession
        _eng = (_msession or {}).get("engine")
        if _eng is not None:
            try:
                from entry_filters import detect_market_regime as _dmr
                spot_hist = getattr(_eng, "_spot_history", {}).get(idx, [])
                if spot_hist:
                    _r = _dmr(spot_hist)
                    _regime = _r.get("regime", "")
                    _range_pct = float(_r.get("range_pct") or 0)
                    _candle_pct = float(_r.get("candle_pct") or 0)
            except Exception as _re:
                print(f"[SCALPER] regime capture failed: {_re}")
        try:
            from structure_gate import get_cached_structure as _gcs
            _cache = _gcs(idx) or {}
            _structs = _cache.get("structures", {})
            _s5m = (_structs.get("5m") or {}).get("verdict", "")
            _s15m = (_structs.get("15m") or {}).get("verdict", "")
            _s1h = (_structs.get("1h") or {}).get("verdict", "")
        except Exception as _se:
            print(f"[SCALPER] structure capture failed: {_se}")
    except Exception as _ce:
        print(f"[SCALPER] regime/structure capture skipped: {_ce}")

    now = ist_now()
    conn = _conn()
    cursor = conn.execute("""
        INSERT INTO scalper_trades (entry_time, idx, action, strike, expiry,
            entry_price, sl_price, t1_price, t2_price,
            current_ltp, peak_ltp, lots, lot_size, qty,
            status, probability,
            entry_reasoning, entry_bull_pct, entry_bear_pct, entry_spot, capital_used,
            regime_at_entry, range_pct_at_entry, candle_pct_at_entry,
            structure_5m, structure_15m, structure_1h,
            engine_scores_json, signals_triggered, gates_passed,
            level_context_json, level_zone_at_entry)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (now.isoformat(), idx, action, strike, expiry,
          entry_price, sl_price, t1_price, t2_price,
          entry_price, entry_price, lots, lot_size, qty,
          probability, entry_reasoning, entry_bull_pct, entry_bear_pct,
          entry_spot, capital_used,
          _regime, _range_pct, _candle_pct,
          _s5m, _s15m, _s1h,
          _engine_scores_json, _signals_triggered, _gates_passed,
          _level_context_json, _level_zone))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # ── Track structure mode for this trade (Phase 4 — 2026-05-27) ──
    # Used by check_scalper_exits to run structural-trail break check on
    # Mode A trades. Stored in-memory keyed by trade_id; cleaned up on close.
    if structure_mode_for_trade is not None:
        _trade_structure_mode[trade_id] = {
            "mode": structure_mode_for_trade,
            "entry_spot": entry_spot or 0,
            "entry_time": ist_now().isoformat(),
            "direction": "BULL" if "CE" in action else "BEAR",
        }
        print(f"[SCALPER] STRUCTURE-MODE tracked for #{trade_id}: "
              f"{structure_mode_for_trade} @ spot {entry_spot}")

    print(f"[SCALPER] OPENED #{trade_id}: {action} {idx} {strike} @ ₹{entry_price} | qty {qty} | capital used ₹{capital_used:,.0f} (of ₹{capital:,.0f}) | SL ₹{sl_price} T1 ₹{t1_price}")

    # ── Journal: log entry with full context ──
    try:
        from trade_journal import log_entry
        log_entry(
            trade_id=trade_id,
            tab="SCALPER",
            idx=idx,
            action=action,
            strike=int(strike),
            entry_price=entry_price,
            qty=qty,
            probability=probability,
            sl_price=sl_price,
            t1_price=t1_price,
            t2_price=t2_price,
            source="verdict_momentum",
            reasoning=entry_reasoning or "",
        )
    except Exception as _je:
        print(f"[JOURNAL] scalper entry log failed: {_je}")

    return trade_id


def record_tick(trade_id, ltp, spot=None):
    """Sample tick for an open scalper trade. Call from check_scalper_exits loop."""
    init_scalper_db()
    import time
    conn = _conn()
    try:
        # Get entry context for pnl calc
        row = conn.execute("SELECT entry_price, qty FROM scalper_trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            return
        entry = row["entry_price"]
        qty = row["qty"]
        pnl_rupees = round((ltp - entry) * qty, 2)
        pnl_pct = round((ltp - entry) / entry * 100, 2) if entry > 0 else 0
        conn.execute("""
            INSERT INTO scalper_ticks (trade_id, ts, ltp, spot, pnl_rupees, pnl_pct)
            VALUES (?,?,?,?,?,?)
        """, (trade_id, int(time.time() * 1000), ltp, spot, pnl_rupees, pnl_pct))
        conn.commit()
    except Exception as e:
        print(f"[SCALPER] tick record error: {e}")
    finally:
        conn.close()


def get_trade_ticks(trade_id, limit=500):
    """Return tick history for one trade (chart data)."""
    init_scalper_db()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT ts, ltp, spot, pnl_rupees, pnl_pct
            FROM scalper_ticks WHERE trade_id=?
            ORDER BY ts ASC LIMIT ?
        """, (trade_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def manual_exit(trade_id, current_ltp, reason="MANUAL_EXIT", defer_capital_track=False):
    """User-triggered manual exit of an open scalper trade.

    N1: When called from an endpoint with BackgroundTasks, pass
    `defer_capital_track=True` and queue `record_capital_after_exit`
    as the bg task. Saves ~15-30ms perceived latency by returning the
    critical UPDATE result first, doing capital ledger write after.
    """
    init_scalper_db()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM scalper_trades WHERE id=? AND status='OPEN'", (trade_id,)
        ).fetchone()
        if not row:
            return {"error": "Trade not found or already closed"}

        entry_price = row["entry_price"]
        qty = row["qty"]
        exit_price = current_ltp if current_ltp > 0 else (row["current_ltp"] or entry_price)
        pnl_rupees = round((exit_price - entry_price) * qty, 2)
        pnl_pts = round(exit_price - entry_price, 2)

        try:
            entry_dt = datetime.fromisoformat(row["entry_time"])
            hold_sec = int((ist_now() - entry_dt).total_seconds())
        except Exception:
            hold_sec = 0

        result_status = "MANUAL_EXIT"
        exit_reason_str = f"Manual exit by user @ ₹{exit_price:.2f} (PnL ₹{pnl_rupees:+,.0f}, +{round((exit_price/entry_price-1)*100,1)}%, held {hold_sec//60}m{hold_sec%60}s)"

        conn.execute("""
            UPDATE scalper_trades SET
                status=?, exit_time=?, exit_price=?, pnl_rupees=?, pnl_pts=?,
                hold_seconds=?, exit_reason=?
            WHERE id=?
        """, (result_status, ist_now().isoformat(), exit_price, pnl_rupees, pnl_pts,
              hold_sec, exit_reason_str, trade_id))
        conn.commit()

        print(f"[SCALPER] MANUAL EXIT #{trade_id}: ₹{exit_price} | PnL ₹{pnl_rupees:+,.0f}")
        # Capital tracker — sync (default) or deferred (N1 BackgroundTasks)
        if not defer_capital_track:
            try:
                from capital_tracker import record_trade_pnl
                record_trade_pnl("SCALPER", pnl_rupees, trade_id=trade_id,
                                 description=f"Manual exit @ ₹{exit_price:.2f}")
            except Exception as e:
                print(f"[CAPITAL] manual exit record error: {e}")
        return {
            "ok": True, "trade_id": trade_id, "exit_price": exit_price,
            "pnl_rupees": pnl_rupees, "pnl_pts": pnl_pts, "hold_seconds": hold_sec,
            "reason": exit_reason_str,
        }
    finally:
        conn.close()


def record_capital_after_exit(source: str, pnl_rupees: float, trade_id: int,
                              description: str = ""):
    """N1 BackgroundTask helper — runs after `manual_exit` has returned.
    Catches exceptions silently — the trade is already closed in DB, this
    is just ledger maintenance."""
    try:
        from capital_tracker import record_trade_pnl
        record_trade_pnl(source, pnl_rupees, trade_id=trade_id, description=description)
    except Exception as e:
        print(f"[CAPITAL] deferred record err (#{trade_id}): {e}")


def get_capital_usage():
    """Return current capital usage breakdown for scalper."""
    init_scalper_db()
    cfg = get_scalper_config()
    capital = cfg.get("capital", 1000000)
    conn = _conn()
    try:
        # Currently committed (open trades)
        open_rows = conn.execute("""
            SELECT id, entry_price, current_ltp, qty, capital_used, idx, strike, action
            FROM scalper_trades WHERE status='OPEN'
        """).fetchall()
        committed = 0.0
        live_value = 0.0
        unrealized = 0.0
        open_list = []
        for r in open_rows:
            row = dict(r)
            cap_used = row.get("capital_used") or (row["entry_price"] * row["qty"])
            committed += cap_used
            cur_ltp = row.get("current_ltp") or row["entry_price"]
            live_val = cur_ltp * row["qty"]
            live_value += live_val
            unrealized += (cur_ltp - row["entry_price"]) * row["qty"]
            open_list.append({
                "id": row["id"], "idx": row["idx"], "strike": row["strike"],
                "action": row["action"],
                "entry": row["entry_price"], "current": cur_ltp, "qty": row["qty"],
                "capital_used": round(cap_used, 2),
                "live_value": round(live_val, 2),
                "unrealized": round((cur_ltp - row["entry_price"]) * row["qty"], 2),
            })

        # Today's realized P&L
        today = ist_now().strftime("%Y-%m-%d")
        realized = conn.execute("""
            SELECT COALESCE(SUM(pnl_rupees), 0) FROM scalper_trades
            WHERE status!='OPEN' AND date(entry_time)=?
        """, (today,)).fetchone()[0]

        available = capital - committed
        return {
            "capital": capital,
            "committed": round(committed, 2),
            "available": round(available, 2),
            "committed_pct": round((committed / capital * 100), 2) if capital > 0 else 0,
            "live_value": round(live_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_today": round(realized, 2),
            "total_today_pnl": round(realized + unrealized, 2),
            "open_count": len(open_list),
            "open_trades": open_list,
        }
    finally:
        conn.close()


def _eod_close_all_scalper(open_trades, chains, now):
    """Close every open scalper trade at last-known LTP. Status=EOD_CLOSE."""
    if not open_trades:
        return
    conn = _conn()
    closed = 0
    for t in open_trades:
        t = dict(t)
        idx = t["idx"]
        strike = t["strike"]
        action = t["action"]
        chain = chains.get(idx, {}) if chains else {}
        sd = chain.get(strike, {}) if isinstance(chain, dict) else {}
        opt_key = "ce_ltp" if "CE" in action else "pe_ltp"
        # last-known live LTP, fallback to current_ltp / entry
        exit_price = sd.get(opt_key, 0) or t.get("current_ltp") or t.get("entry_price") or 0
        if exit_price <= 0:
            continue
        entry = t.get("entry_price", 0) or 0
        qty = t.get("qty", 0) or 0
        pnl_pts = round(exit_price - entry, 2)
        pnl_rupees = round(pnl_pts * qty, 2)
        try:
            entry_dt = datetime.fromisoformat(t["entry_time"])
            hold_sec = int((now - entry_dt).total_seconds())
        except Exception:
            hold_sec = 0
        try:
            conn.execute("""
                UPDATE scalper_trades SET
                    status='EOD_CLOSE', exit_price=?, exit_time=?, exit_reason=?,
                    pnl_pts=?, pnl_rupees=?, hold_seconds=?, peak_ltp=?
                WHERE id=? AND status='OPEN'
            """, (exit_price, now.isoformat(),
                  "Market closing 3:25 PM — scalper EOD auto-close (options intraday only).",
                  pnl_pts, pnl_rupees, hold_sec, max(t.get("peak_ltp") or entry, exit_price),
                  t["id"]))
            closed += 1
            print(f"[SCALPER-EOD] Closed #{t['id']} {idx} {action} {strike} @ ₹{exit_price} → ₹{pnl_rupees:+,.0f}")
            try:
                from capital_tracker import record_trade_pnl
                record_trade_pnl("SCALPER", pnl_rupees, trade_id=t["id"],
                                 description=f"EOD auto-close: {idx} {action} {strike}")
            except Exception:
                pass
        except Exception as e:
            print(f"[SCALPER-EOD] failed to close #{t.get('id')}: {e}")
    conn.commit()
    conn.close()
    if closed:
        print(f"[SCALPER-EOD] Closed {closed} open scalper trade(s) at 3:25 PM IST.")


def get_market_close_status():
    """Return market-close countdown info for the UI banner.
    States: NORMAL → CLOSING_SOON (3:20-3:25) → CLOSING_NOW (3:25-3:30) → CLOSED
    """
    now = ist_now()
    h, m = now.hour, now.minute
    # Pre-3:20 = normal, 3:20-3:24 = warning, 3:25-3:30 = auto-closing, post = closed
    state = "NORMAL"
    seconds_to_close = None
    if h == 15 and 20 <= m < 25:
        state = "CLOSING_SOON"
        # seconds until 3:25
        target = now.replace(hour=15, minute=25, second=0, microsecond=0)
        seconds_to_close = int((target - now).total_seconds())
    elif h == 15 and 25 <= m < 30:
        state = "AUTO_CLOSING"
        seconds_to_close = 0
    elif h == 15 and m >= 30:
        state = "CLOSED"
    elif h >= 16:
        state = "CLOSED"
    elif h < 9 or (h == 9 and m < 15):
        state = "PRE_OPEN"

    return {
        "state": state,
        "seconds_to_close": seconds_to_close,
        "now_ist": now.strftime("%H:%M:%S"),
        "warning_active": state in ("CLOSING_SOON", "AUTO_CLOSING"),
        "auto_close_active": state == "AUTO_CLOSING",
    }


# Wick-hunt protection: consecutive ticks each open trade has spent
# at/below its SL. A single-tick poke (classic "SL hunt") is filtered;
# the SL only fires once the breach holds for SCALPER_SL_CONFIRM_TICKS.
_sl_breach_count: dict = {}


# Structure-mode handoff (Phase 4 — 2026-05-27)
# should_enter_scalp() populates this when G14 (structure_gate) returns a
# Mode A or Mode B decision. log_scalp_trade() consumes + clears it to
# apply mode-specific size/SL/T1/T2/hold tuning. Keyed by (idx, action).
_pending_structure_tuning: dict = {}

# Per-trade mode + entry_spot — populated by log_scalp_trade, read by
# check_scalper_exits for structural-trail break detection (Mode A only).
_trade_structure_mode: dict = {}   # trade_id → {"mode": "aligned"/"counter_trend", "entry_spot": float}


def check_scalper_exits(chains):
    """Monitor open scalper trades — quick exit logic.
    Smart SL applied if enabled in smart_sl_config (else static SL)."""
    conn = _conn()
    open_trades = conn.execute(
        "SELECT * FROM scalper_trades WHERE status='OPEN'"
    ).fetchall()
    conn.close()

    smart_cfg = get_smart_sl_config()
    smart_enabled = smart_cfg.get("enabled", False)
    spot_anchor_pct = smart_cfg.get("spot_anchor_pct", 0.4)

    # NEW: max-hold from active config (drops to 15min on expiry day)
    _active_cfg = get_active_scalp_config()
    active_max_hold_min = _active_cfg.get("max_hold_min", SCALPER_MAX_HOLD_MIN)

    now = ist_now()

    # ── EOD AUTO-CLOSE: close all scalper trades at 3:25 PM IST ──
    # Options are intraday only. Holding past 3:25 risks settlement at
    # closing-auction prices and loss of any meaningful exit liquidity.
    if now.hour == 15 and now.minute >= 25:
        _eod_close_all_scalper(open_trades, chains, now)
        return

    for t in open_trades:
        t = dict(t)
        idx = t["idx"]
        strike = t["strike"]
        action = t["action"]
        chain = chains.get(idx, {})
        sd = chain.get(strike, {})

        opt_key = "ce_ltp" if "CE" in action else "pe_ltp"
        current_ltp = sd.get(opt_key, 0)
        if current_ltp <= 0:
            continue

        entry = t["entry_price"]
        sl = t["sl_price"]
        t1 = t["t1_price"]
        t2 = t["t2_price"]
        peak = max(t.get("peak_ltp", entry), current_ltp)

        # Hold time check
        try:
            entry_time = datetime.fromisoformat(t["entry_time"])
            hold_sec = (now - entry_time).total_seconds()
        except Exception:
            hold_sec = 0

        new_status = "OPEN"
        exit_reason = None
        exit_price = 0
        sl_reason_text = None

        # ─── PEAK-AWARE FLOOR (Rule 1: peaked ≥+5% → exit at -5%) ───
        # Tick-level enforcement on scalper (watcher fires every 10s,
        # this runs every tick — much faster reaction).
        # Uses peak_ltp from DB row (already tracked) — entry × 1.05 threshold.
        peak_profit_pct_local = ((peak - entry) / entry * 100) if entry > 0 else 0
        cur_profit_pct = ((current_ltp - entry) / entry * 100) if entry > 0 else 0
        if peak_profit_pct_local >= 5.0 and cur_profit_pct <= -5.0:
            new_status = "PEAK_FLOOR_EXIT"
            exit_price = current_ltp
            exit_reason = (f"PEAK_FLOOR: peaked {peak_profit_pct_local:+.2f}% then fell to "
                           f"{cur_profit_pct:+.2f}% — capped at -5%")
            sl_reason_text = exit_reason
            print(f"[SCALPER] PEAK_FLOOR exit · #{t['id']} {idx} {action} {strike} "
                  f"· peak {peak_profit_pct_local:+.1f}% → now {cur_profit_pct:+.1f}%")

        # ─── PROFIT-LOCK TRAILING SL (runs FIRST, raises sl_price in DB) ───
        # 8-stage ladder auto-trails SL up as profit grows.
        try:
            from profit_trailing_sl import update_scalper_trail
            trail_result = update_scalper_trail(t, current_ltp)
            if trail_result:
                new_sl_from_trail = trail_result["new_sl"]
                if new_sl_from_trail > sl:
                    sl = new_sl_from_trail
                    t["sl_price"] = sl
        except Exception as _e:
            pass

        # ─── PEAK-TRAIL SL (#3 + #4 — runs after Profit-Trail, before Time-Decay) ───
        # Trails based on peak premium (not entry). Activates only when
        # peak ≥ +10%. Also detects stuck profit and tightens further.
        try:
            from peak_trail_sl import compute_peak_trail_sl
            sid_pt = f"SCALPER:{t['id']}"
            peak_result = compute_peak_trail_sl(sid_pt, entry, current_ltp)
            if peak_result:
                new_sl_from_peak = peak_result["new_sl"]
                if new_sl_from_peak > sl:
                    sl = new_sl_from_peak
                    t["sl_price"] = sl
                    print(f"[SCALPER] PEAK-TRAIL #{t['id']} {peak_result['source']}: "
                          f"{peak_result['reason']}")
        except Exception as _e:
            pass

        # ─── TIME-DECAY SL (caps further loss based on hold time) ───
        # Tighter ladder for scalper (30-min max hold):
        # 0-3min -8%, 3-8min -6%, 8-15min -4%, 15+min -2%.
        try:
            from time_decay_sl import update_scalper_decay
            decay_result = update_scalper_decay(t, current_ltp)
            if decay_result:
                new_sl_from_decay = decay_result["new_sl"]
                if new_sl_from_decay > sl:
                    sl = new_sl_from_decay
                    t["sl_price"] = sl
        except Exception as _e:
            pass

        # ─── BREAKEVEN LOCK (2026-05-22 — user rule) ───
        # "Trade profit mein gaya toh SL entry pe aa jaye." Once a trade is
        # meaningfully in profit, raise SL to the entry price so it can
        # NEVER turn into a loss — worst case becomes breakeven.
        # Works in BOTH static and smart-SL paths: the smart ladder below
        # does max(smart_active_sl, sl), so the raised sl is picked up;
        # the static path uses sl directly. Stateless — recomputed each
        # tick from cur_profit_pct, so no DB persistence needed.
        # Trigger threshold avoids noise-locking on a +0.5% wiggle that
        # would then stop the trade at breakeven on normal chop.
        # Env: SCALPER_BREAKEVEN_LOCK (default on),
        #      SCALPER_BREAKEVEN_TRIGGER (default 4 = +4% profit).
        try:
            import os as _os_be
            if _os_be.environ.get("SCALPER_BREAKEVEN_LOCK", "on").lower() == "on":
                # DEFAULT CHANGED 2026-06-03: 4% → 3% (data-driven)
                # 445-trade audit: 34 losing trades peaked between +3-5%
                # then died at -5% SL (₹395k loss). Lowering trigger to
                # +3% catches them at breakeven instead. The remaining
                # +5%+ peaked trades still hit the PEAK_TRAIL SL.
                # User principle: "bad trade ko bade loss me convert na
                # hone dena = smart".
                be_trigger = float(_os_be.environ.get("SCALPER_BREAKEVEN_TRIGGER", "3"))
                if cur_profit_pct >= be_trigger and sl < entry:
                    sl = entry
                    t["sl_price"] = sl
                    print(f"[SCALPER] BREAKEVEN #{t['id']}: profit {cur_profit_pct:+.1f}% "
                          f"≥ {be_trigger}% — SL locked to entry ₹{entry} (risk-free)")
        except Exception:
            pass

        # ─── EARLY-WARNING ADAPTIVE SL (2026-06-03 — damage control) ───
        # User principle: "bad detection ko bade loss me convert na hone
        # dena = smart". Audit found 19 loss trades that NEVER went
        # positive (avg -₹13k loss = ₹246k total). These are the trades
        # where setup was wrong from tick 1 — letting them ride to -5%
        # default SL is irrational. Cut them at -2.5%.
        # Logic: if no positive peak in first 10 min AND already at -2%,
        # exit immediately at -2.5% (small loss). Saves ~₹434k estimated.
        #
        # 2026-06-11 v2 — WINDOW EXTENDED 3 → 10 min based on data audit.
        # 60d exit audit found 14 NEVER_POSITIVE trades held 22-26 min
        # before hitting SL (-₹22k avg = ₹3.08L total leak). They slipped
        # past 3-min window because they bled slowly.
        # New window 10 min catches them. Plus added INSTANT_REJECT rule
        # below for the < 60s crash pattern.
        # Env:
        #   EARLY_CUT_DISABLED=1            kill switch
        #   EARLY_CUT_WINDOW_MIN=10         observation window (default 10)
        #   EARLY_CUT_TRIGGER=-2.5          loss % to trigger early cut
        try:
            import os as _os_ec
            if _os_ec.environ.get("EARLY_CUT_DISABLED", "").strip() not in ("1","true","on"):
                ec_window = float(_os_ec.environ.get("EARLY_CUT_WINDOW_MIN", "10")) * 60
                ec_trigger = float(_os_ec.environ.get("EARLY_CUT_TRIGGER", "-2.5"))
                hold_min = hold_sec / 60
                peak_pct = peak_profit_pct_local
                if (hold_sec >= 60 and hold_sec <= ec_window
                        and peak_pct <= 0.5  # never reached even +0.5%
                        and cur_profit_pct <= ec_trigger
                        and new_status == "OPEN"):
                    new_status = "EARLY_CUT"
                    exit_price = current_ltp
                    exit_reason = (f"EARLY_CUT: setup wrong from start "
                                   f"(peak {peak_pct:+.1f}%, now {cur_profit_pct:+.1f}%, "
                                   f"hold {hold_min:.1f}m) — small loss not big loss")
                    sl_reason_text = exit_reason
                    print(f"[SCALPER] EARLY_CUT · #{t['id']} {idx} {action} {strike} · "
                          f"never positive, cut at {cur_profit_pct:+.1f}%")
        except Exception:
            pass

        # ─── STALE_TRADE_KILL (Fix 4, 2026-06-15) ───
        # Catches GAP between EARLY_CUT (10min, -2.5%) and ZOMBIE_KILL
        # (30min, profit band -1..+1). Bleed-trades that drift below -1%
        # before 30min escape both. Audit: 14 such trades held 180m avg
        # lost ₹3.08L scalper-side. Kill at 15-30 min if peak <1% and loss
        # -2% to -5%. Direction wrong → theta will keep widening loss.
        # Env kill: SCALPER_STALE_KILL_DISABLED=1
        try:
            import os as _os_sk
            if (_os_sk.environ.get("SCALPER_STALE_KILL_DISABLED", "").strip() not in ("1","true","on")
                    and new_status == "OPEN"):
                sk_min_hold = float(_os_sk.environ.get("SCALPER_STALE_KILL_MIN_HOLD_MIN", "15")) * 60
                sk_max_hold = float(_os_sk.environ.get("SCALPER_STALE_KILL_MAX_HOLD_MIN", "30")) * 60
                sk_max_peak = float(_os_sk.environ.get("SCALPER_STALE_KILL_MAX_PEAK_PCT", "1.0"))
                sk_loss_low = float(_os_sk.environ.get("SCALPER_STALE_KILL_LOSS_LOW", "-5.0"))
                sk_loss_high = float(_os_sk.environ.get("SCALPER_STALE_KILL_LOSS_HIGH", "-2.0"))
                if (sk_min_hold <= hold_sec <= sk_max_hold
                        and peak_profit_pct_local <= sk_max_peak
                        and sk_loss_low <= cur_profit_pct <= sk_loss_high):
                    new_status = "STALE_TRADE_KILL"
                    exit_price = current_ltp
                    exit_reason = (f"STALE_TRADE_KILL: hold {hold_sec/60:.0f}m, "
                                   f"peak {peak_profit_pct_local:+.1f}% (never broke {sk_max_peak}%), "
                                   f"loss {cur_profit_pct:+.1f}% — direction wrong, "
                                   f"prevent slow-bleed to -5% SL")
                    sl_reason_text = exit_reason
                    print(f"[SCALPER] STALE_KILL · #{t['id']} {idx} {action} {strike} · "
                          f"hold {hold_sec/60:.0f}m, peak {peak_profit_pct_local:+.1f}%, "
                          f"now {cur_profit_pct:+.1f}% — bleeding stopped")
        except Exception:
            pass

        # ─── INSTANT_REJECT — < 60s crash exit (2026-06-11) ───
        # 2026-06-11 v2: DEFAULT DISABLED per user feedback.
        # Was firing 5-6 times today, sometimes on legitimate dips
        # that would have recovered. User: "trade leke chut mut price change toh
        # hota rehta hai".
        # Env: SCALPER_INSTANT_REJECT_DISABLED=0 to re-enable.
        try:
            import os as _os_ir
            if (_os_ir.environ.get("SCALPER_INSTANT_REJECT_DISABLED", "1").strip() not in ("1","true","on")
                    and new_status == "OPEN"):
                ir_max_hold = float(_os_ir.environ.get("SCALPER_INSTANT_REJECT_HOLD_SEC", "90"))
                ir_trigger = float(_os_ir.environ.get("SCALPER_INSTANT_REJECT_TRIGGER", "-1.0"))
                if (hold_sec <= ir_max_hold
                        and cur_profit_pct <= ir_trigger
                        and peak_profit_pct_local <= 0.5):
                    new_status = "INSTANT_REJECT"
                    exit_price = current_ltp
                    exit_reason = (f"INSTANT_REJECT: hold {hold_sec:.0f}s, peak {peak_profit_pct_local:+.2f}%, "
                                   f"now {cur_profit_pct:+.2f}% — market rejected entry, cut quick")
                    sl_reason_text = exit_reason
                    print(f"[SCALPER] INSTANT_REJECT · #{t['id']} {idx} {action} {strike} · "
                          f"crashed at {cur_profit_pct:+.1f}% in {hold_sec:.0f}s")
        except Exception:
            pass

        # ─── ZOMBIE_TRADE_KILL (2026-06-11 — bear-trader fix) ───
        # PEAK_GIVEBACK was capped to peak ≤+1.5% to avoid overlap with
        # profit_floor. But profit_floor only RAISES SL — doesn't EXIT.
        # Trades stuck at +0.3% above entry bleed theta until SL hits.
        # Rule: hold > 30 min, peak < +2%, profit in (-1%, +1%) → exit.
        # Disabled by env: SCALPER_ZOMBIE_KILL_DISABLED=1
        try:
            import os as _os_zk
            if (_os_zk.environ.get("SCALPER_ZOMBIE_KILL_DISABLED", "").strip() not in ("1","true","on")
                    and new_status == "OPEN"):
                zk_min_hold_min = float(_os_zk.environ.get("SCALPER_ZOMBIE_MIN_HOLD_MIN", "30"))
                zk_max_peak_pct = float(_os_zk.environ.get("SCALPER_ZOMBIE_MAX_PEAK_PCT", "2.0"))
                zk_band_low = float(_os_zk.environ.get("SCALPER_ZOMBIE_BAND_LOW", "-1.0"))
                zk_band_high = float(_os_zk.environ.get("SCALPER_ZOMBIE_BAND_HIGH", "1.0"))
                hold_min = hold_sec / 60
                peak_pct = peak_profit_pct_local
                if (hold_min >= zk_min_hold_min
                        and peak_pct < zk_max_peak_pct
                        and zk_band_low <= cur_profit_pct <= zk_band_high):
                    new_status = "ZOMBIE_KILL"
                    exit_price = current_ltp
                    exit_reason = (f"ZOMBIE_KILL: hold {hold_min:.0f}m, no momentum "
                                   f"(peak {peak_pct:+.1f}%, now {cur_profit_pct:+.1f}%) — "
                                   f"free capital, theta bleeding")
                    sl_reason_text = exit_reason
                    print(f"[SCALPER] ZOMBIE_KILL · #{t['id']} {idx} {action} {strike} · "
                          f"hold {hold_min:.0f}m, stuck at {cur_profit_pct:+.1f}%")
        except Exception:
            pass

        # ─── SMART SL LADDER (if enabled) ───
        smart_active_sl = sl  # fallback to static
        smart_stage = 0
        smart_label = "Static"
        if smart_enabled:
            saved_sl = t.get("smart_sl_value") or sl
            saved_stage = t.get("smart_sl_stage") or 0
            smart_active_sl, smart_stage, smart_label = compute_smart_sl(
                entry, current_ltp,
                current_stage_saved=saved_stage,
                saved_sl=saved_sl,
            )
            # Profit trail wins if it's higher
            smart_active_sl = max(smart_active_sl, sl)

        # ─── SPOT ANCHOR check (if enabled) ───
        spot_exit = False
        spot_reason = None
        if smart_enabled:
            spot_token_idx = idx
            entry_spot = t.get("entry_spot")
            # Get current spot from chains (use any strike's underlying — chain doesn't store spot directly)
            # Better: spot from engine is accessible via global; fallback to skip
            try:
                # We don't have engine ref here; use approximate: spot ≈ strike + (CE_ltp - PE_ltp) at ATM.
                # But simpler: rely on entry_spot stored, current_spot from engine.spot_tokens
                # For now, check_scalper_exits is called with chains only — skip spot anchor if entry_spot missing
                if entry_spot:
                    # Pull from chain's "underlying_value" if available
                    # Fallback: use peak_ltp_strike as proxy (not ideal). Skip if can't determine.
                    pass
            except Exception:
                pass

        # ─── STRUCTURE-TRAIL break (Phase 4 — 2026-05-27) ───
        # Only for Mode A (aligned) trades. If post-entry candles formed
        # a new LL (in BULL trade) or new HH (in BEAR trade), trend is
        # broken — exit at MARKET (not at premium SL). Captures the move
        # break before the % SL fires, locking better exit prices.
        structure_break_exit = False
        structure_break_reason = None
        _struct_info = _trade_structure_mode.get(t["id"])
        if _struct_info and _struct_info.get("mode") == "aligned":
            try:
                import structure_gate as sg
                import structure_trail as st_trail
                if sg.master_mode() == "live":
                    cached = sg.get_cached_structure(idx)
                    if cached:
                        # Build candle list from historical loader (cached too)
                        import historical_loader as hl
                        # Reuse the structure_gate's source — fetch 5m candles
                        # via historical_loader (which caches 30 min).
                        kite = None
                        try:
                            from main import session as _s
                            kite = _s.get("kite") if _s else None
                        except Exception:
                            pass
                        if kite is not None:
                            candles_5m = hl.load_index_history(kite, idx, "5minute", days=2)
                            decision = st_trail.should_exit_on_break(
                                candles_5m=candles_5m,
                                trade_direction=_struct_info.get("direction", "BULL"),
                                entry_spot=_struct_info.get("entry_spot"),
                                entry_ts=_struct_info.get("entry_time"),
                            )
                            if decision.get("should_exit"):
                                structure_break_exit = True
                                structure_break_reason = (
                                    f"STRUCTURE_BREAK: {decision.get('reason', '')}"
                                )
            except Exception as _e:
                # Never block on trail error
                print(f"[SCALPER] structure_trail check error #{t['id']}: {_e}")

        if structure_break_exit and new_status == "OPEN":
            new_status = "STRUCTURE_BREAK"
            exit_price = current_ltp
            exit_reason = structure_break_reason
            sl_reason_text = structure_break_reason
            print(f"[SCALPER] {structure_break_reason} · trade #{t['id']} "
                  f"{idx} {action} {strike} @ ₹{exit_price}")

        # ─── B2.5 + B3.8: REVERSAL & VELOCITY pre-emptive exits ───
        # Run BEFORE SL/T1/T2 — if market context says exit, do it at LIVE
        # price (not at SL price), saves 2-5% slippage on big moves.
        reversal_exit = False
        reversal_reason = None

        # ── MIN-HOLD GUARD (2026-05-27) ──
        # 60-day audit: 38 PREMATURE_REVERSAL trades held only 3.3 min avg
        # then got OI-reversal-exited at -0.7%. Trade didn't get chance to
        # develop. ₹3.1L lost in this category.
        # Fix: skip reversal/velocity exits if trade hasn't held minimum
        # time. Min SL/T1/T2 logic still runs normally — just OI-based
        # premature exit suppressed.
        # Env: SCALPER_REVERSAL_MIN_HOLD_MIN
        # DEFAULT CHANGED 2026-06-03: 0 → 5 (data-driven)
        # 445-trade audit: 107 fast exits (<3min) total, 58 were REVERSAL
        # exits with -₹71k net loss. These are premature OI-flip panic
        # exits — most would have recovered if held the original setup's
        # natural exit (SL/T1/trail).
        # Override via env to test other values: 0=off, 3-10 reasonable.
        try:
            _rev_min_hold = int(os.environ.get("SCALPER_REVERSAL_MIN_HOLD_MIN", "5") or 5)
        except Exception:
            _rev_min_hold = 5
        _reversal_blocked_by_min_hold = (_rev_min_hold > 0 and (hold_sec / 60) < _rev_min_hold)

        # B2.5: OI delta reversal trigger
        try:
            from oi_delta_tracker import assess as _oi_assess
            oi = _oi_assess(idx)
            sigs = oi.get("signals", {}) if oi else {}
            ce15 = oi.get("ce_oi_delta_15m_pct")
            pe15 = oi.get("pe_oi_delta_15m_pct")
            if "CE" in action:
                # We're long CE → bearish reversal forming if:
                #   - CE writers ADDING (new ceiling)
                #   - OR PE writers COVERING (PE shorts buying back = bearish)
                if sigs.get("ce_writer_adding"):
                    reversal_exit = True
                    reversal_reason = (f"REVERSAL_EXIT: CE writers adding {ce15:+.1f}% in 15m "
                                       f"— ceiling forming, exit long CE")
                elif sigs.get("pe_writer_covering"):
                    reversal_exit = True
                    reversal_reason = (f"REVERSAL_EXIT: PE writers covering {pe15:+.1f}% — "
                                       f"bearish reversal, exit long CE")
            else:
                # Long PE → bullish reversal if:
                #   - PE writers ADDING (new floor)
                #   - OR CE writers COVERING
                if sigs.get("pe_writer_adding"):
                    reversal_exit = True
                    reversal_reason = (f"REVERSAL_EXIT: PE writers adding {pe15:+.1f}% in 15m "
                                       f"— floor forming, exit long PE")
                elif sigs.get("ce_writer_covering"):
                    reversal_exit = True
                    reversal_reason = (f"REVERSAL_EXIT: CE writers covering {ce15:+.1f}% — "
                                       f"bullish reversal, exit long PE")
        except Exception:
            pass

        # B3.8: Premium velocity collapse (DISABLED 2026-06-03 by data audit)
        # 445-trade audit revealed VELOCITY_EXIT:
        #   • 34 trades, 3% win rate, -₹344,936 net loss
        #   • Average loss per fire: -₹10,145
        #   • Worst exit category after WATCHER_EXIT
        # Theta decay alone is NOT a reason to exit a recoverable trade.
        # The math: a -3% premium drop in 10 min ≠ trade is dead. Most of
        # these recovered if held to TRAIL_EXIT (99% WR, +₹13k avg) or
        # T1_HIT (100% WR, +₹30k avg). Velocity exit was cutting flowers,
        # watering weeds.
        #
        # 2026-06-12 v2: DEFAULT FLIPPED off → on after deep audit.
        # Original audit was MISCOUNTED — VELOCITY_EXIT was exiting LOSERS,
        # looked like "losses" when it was actually loss-cutting.
        # Disabling it caused SL_HIT avg hold to balloon 30min → 203min (3.5hr!)
        # Same losers now compound to -8% SL instead of being cut at -3%.
        # Scalper avg/trade collapsed ₹1,224 → ₹275 (-78%) since disabling.
        # Re-enable as default. Override: VELOCITY_EXIT_ENABLED=off to disable.
        if not reversal_exit and os.environ.get("VELOCITY_EXIT_ENABLED", "on").lower() == "on":
            try:
                from premium_velocity import register as _pv_reg, push as _pv_push, assess as _pv_assess
                sid = f"SCALPER:{t['id']}"
                pv = _pv_assess(sid, action)
                # If first time seeing this trade, register
                if pv and pv.get("samples", 0) == 0:
                    _pv_reg(sid, entry, t.get("entry_spot", 0) or 0, action)
                if pv and pv.get("severity") == "HIGH":
                    profit_now_pct = ((current_ltp - entry) / entry * 100) if entry > 0 else 0
                    if -5 <= profit_now_pct <= 12:
                        reversal_exit = True
                        reversal_reason = (f"VELOCITY_EXIT: {pv.get('warning', 'velocity collapse')} "
                                           f"(profit {profit_now_pct:+.1f}%)")
            except Exception:
                pass

        # Honour min-hold guard: suppress reversal-exit if trade is too young.
        # This filters the 38 PREMATURE_REVERSAL trades (₹3.1L damage in 60d).
        if reversal_exit and _reversal_blocked_by_min_hold:
            print(f"[SCALPER] REVERSAL suppressed by min-hold #{t['id']} "
                  f"(hold {hold_sec/60:.1f}m < {_rev_min_hold}m threshold) — "
                  f"would have been: {reversal_reason}")
            reversal_exit = False
            reversal_reason = None

        # ─── 3-TIER PEAK-BASED SUPPRESSION (2026-06-11 — let runners run) ───
        # User feedback: "200 wins × ₹1.25k = ₹2.5L vs 200 losses × ₹1k = ₹2L.
        # Net ₹50k = death by thousand wins. Need bigger wins per trade."
        #
        # Today's data: REVERSAL_EXIT 12 trades, avg peak +2.77%, avg exit +2%.
        # System exits at +2% when trades could have run to +5-10%.
        #
        # Logic (env-overridable):
        #   Peak <+2%:        Exit normal (don't ride scratches)
        #   Peak +2% to +5%:  Only exit if giveback > 1.5pp from peak
        #   Peak >=+5%:       NEVER exit via REVERSAL (trust profit_floor)
        #
        # Estimated impact: 3x improvement on REVERSAL_EXIT category
        # (₹90k today → ₹270k if rides held to +5-8% peak avg)
        if reversal_exit:
            try:
                import os as _os_rs
                if _os_rs.environ.get("SCALPER_REVERSAL_SMART_DISABLED", "").strip() not in ("1","true","on"):
                    # Compute peak/profit context
                    peak_local = max(t.get("peak_ltp", entry) or entry, current_ltp)
                    peak_pct_local = ((peak_local - entry) / entry * 100) if entry > 0 else 0
                    curr_profit_local = ((current_ltp - entry) / entry * 100) if entry > 0 else 0
                    giveback_pp = peak_pct_local - curr_profit_local

                    runner_threshold = float(_os_rs.environ.get("SCALPER_REVERSAL_RUNNER_PEAK", "5.0"))
                    mid_peak_min = float(_os_rs.environ.get("SCALPER_REVERSAL_MID_PEAK_MIN", "2.0"))
                    mid_max_giveback = float(_os_rs.environ.get("SCALPER_REVERSAL_MID_MAX_GIVEBACK", "1.5"))

                    if peak_pct_local >= runner_threshold:
                        # RUNNER ZONE — never kill via reversal
                        print(f"[SCALPER] REVERSAL_SUPPRESS · #{t['id']} {idx} {action}: "
                              f"RUNNER (peak +{peak_pct_local:.1f}% ≥ {runner_threshold}%) — "
                              f"trust profit_floor + trail. Would have: {reversal_reason}")
                        reversal_exit = False
                        reversal_reason = None
                    elif peak_pct_local >= mid_peak_min and giveback_pp < mid_max_giveback:
                        # MID ZONE — small giveback, hold
                        print(f"[SCALPER] REVERSAL_SUPPRESS · #{t['id']} {idx} {action}: "
                              f"HEALTHY (peak +{peak_pct_local:.1f}%, giveback {giveback_pp:.1f}pp < {mid_max_giveback}pp) — "
                              f"let it ride. Would have: {reversal_reason}")
                        reversal_exit = False
                        reversal_reason = None
                    # else: peak < +2% OR giveback >= 1.5pp → exit normal (small win/scratch)
            except Exception as _rs_e:
                print(f"[SCALPER] reversal smart suppress error (allow exit): {_rs_e}")

        if reversal_exit:
            new_status = "REVERSAL_EXIT"
            exit_price = current_ltp  # exit at market — no SL slippage
            exit_reason = reversal_reason
            sl_reason_text = reversal_reason
            print(f"[SCALPER] {reversal_reason} · trade #{t['id']} {idx} {action} {strike}")

        # ─── Exit logic (priority order) ───
        active_sl_used = smart_active_sl if smart_enabled else sl

        # ─── AGGRESSIVE PEAK FLOOR (Fix A, 2026-06-15) ──────────────────
        # Belt-and-suspenders: independent of profit_floor / smart_sl /
        # profit_trailing_sl chain. Guarantees peak-based floor is honored
        # even if upstream systems missed it. Targets scalper SL_HIT bleed
        # (₹6.29L over 60d, avg peak 5.46% — moves came but weren't locked).
        # Tiers chosen to be MORE aggressive than profit_floor (starts at +3%).
        # Env kill: SCALPER_AGG_FLOOR_DISABLED=1
        try:
            import os as _os_af
            if _os_af.environ.get("SCALPER_AGG_FLOOR_DISABLED", "").strip() not in ("1","true","on"):
                _af_peak_pct = peak_profit_pct_local
                if _af_peak_pct >= 3.0:
                    if _af_peak_pct >= 12.0:
                        _af_floor_pct = 6.0
                    elif _af_peak_pct >= 8.0:
                        _af_floor_pct = 4.0
                    elif _af_peak_pct >= 5.0:
                        _af_floor_pct = 2.5
                    elif _af_peak_pct >= 3.5:
                        _af_floor_pct = 1.5
                    else:  # 3.0 - 3.5
                        _af_floor_pct = 0.5
                    _af_floor_price = round(entry * (1 + _af_floor_pct/100), 2)
                    if _af_floor_price > active_sl_used:
                        old_sl = active_sl_used
                        active_sl_used = _af_floor_price
                        sl = active_sl_used
                        t["sl_price"] = sl
                        print(f"[SCALPER] AGG_FLOOR #{t['id']}: peak {_af_peak_pct:.1f}% → "
                              f"floor +{_af_floor_pct}% (SL ₹{old_sl:.2f} → ₹{active_sl_used:.2f})")
        except Exception:
            pass

        # Wick-hunt protection: reset the consecutive-breach counter the
        # moment premium climbs back clear of SL.
        if current_ltp > active_sl_used:
            _sl_breach_count.pop(t["id"], None)

        if new_status == "PEAK_FLOOR_EXIT":
            pass  # already set — skip everything else
        elif reversal_exit:
            pass  # already set above — skip SL/T1/T2 logic
        elif current_ltp <= active_sl_used:
            # ── WICK-HUNT PROTECTION (2026-05-22 — user rule) ──
            # A single tick poking below SL is often a wick that reverts
            # instantly — exiting on it is the classic "SL hunt" loss.
            # Require N consecutive ticks at/below SL before honouring
            # the exit; the trade stays OPEN until the breach is confirmed.
            # Env: SCALPER_SL_CONFIRM_TICKS (default 2; set 1 = old behaviour).
            import os as _os_wk
            _confirm_ticks = max(1, int(_os_wk.environ.get("SCALPER_SL_CONFIRM_TICKS", "2") or 2))
            _sl_breach_count[t["id"]] = _sl_breach_count.get(t["id"], 0) + 1
            if _sl_breach_count[t["id"]] >= _confirm_ticks:
                # ── PEAK-AWARE FLOOR (Fix 2, 2026-06-15) ──
                # If trade peaked +5%+ during lifetime, don't dump full SL.
                # Lock at peak × 0.4 (or current ltp if past floor).
                # Catches trades like #51 (peak +17% → SL hit) — would lock +6.8%.
                # Env kill: SCALPER_PEAK_FLOOR_DISABLED=1
                _pf_disabled = _os_wk.environ.get("SCALPER_PEAK_FLOOR_DISABLED", "").strip() in ("1","true","on")
                _pf_peak_thresh = float(_os_wk.environ.get("SCALPER_PEAK_FLOOR_PEAK_PCT", "5.0"))
                _pf_factor = float(_os_wk.environ.get("SCALPER_PEAK_FLOOR_FACTOR", "0.4"))
                _peak_pct_now = peak_profit_pct_local
                if (not _pf_disabled) and _peak_pct_now >= _pf_peak_thresh:
                    floor_pct = _peak_pct_now * _pf_factor
                    floor_price = round(entry * (1 + floor_pct/100), 2)
                    # 2026-06-17 (auditor NOTE #17): respect AGG_FLOOR's
                    # already-raised SL — never lock below it.
                    if active_sl_used > floor_price:
                        floor_price = active_sl_used
                        floor_pct = round((floor_price / entry - 1) * 100, 2)
                    if current_ltp >= floor_price:
                        new_status = "PEAK_FLOOR_EXIT"
                        exit_price = floor_price
                        exit_reason = (f"PEAK_FLOOR_LOCK at ₹{floor_price} (+{floor_pct:.1f}%) — "
                                       f"trade peaked +{_peak_pct_now:.1f}% then SL approach. "
                                       f"Floor saved profit (was SL at ₹{active_sl_used:.2f}).")
                        sl_reason_text = exit_reason
                        print(f"[SCALPER] PEAK_FLOOR · #{t['id']} {idx} {action} {strike} · "
                              f"peak +{_peak_pct_now:.1f}% → exit +{floor_pct:.1f}% (was SL exit)")
                    else:
                        new_status = "SL_HIT"
                        exit_price = active_sl_used
                        exit_reason = (f"SL hit at ₹{active_sl_used:.2f} — peak was "
                                       f"+{_peak_pct_now:.1f}% but premium crashed past "
                                       f"floor ₹{floor_price}.")
                        sl_reason_text = exit_reason
                elif (not _pf_disabled) and _peak_pct_now >= 2.0:
                    # ── MINI_PEAK_FLOOR (2026-06-17 — mirror main mode) ──
                    # Peak 2-5% trades had no protection — exited at SL price.
                    # Tiered: peak 2%→+0.3%, peak 3%→+0.8%, peak 4%→+1.3%
                    _mini_floor_pct = round(0.3 + (_peak_pct_now - 2.0) * 0.5, 1)
                    _mini_floor_price = round(entry * (1 + _mini_floor_pct/100), 2)
                    if current_ltp >= _mini_floor_price:
                        new_status = "PEAK_FLOOR_EXIT"
                        exit_price = _mini_floor_price
                        exit_reason = (f"MINI_PEAK_FLOOR at ₹{_mini_floor_price} (+{_mini_floor_pct}%) — "
                                       f"peak +{_peak_pct_now:.1f}% saved from SL.")
                        sl_reason_text = exit_reason
                        print(f"[SCALPER] MINI_PEAK_FLOOR · #{t['id']} peak +{_peak_pct_now:.1f}% "
                              f"→ +{_mini_floor_pct}% (was SL exit)")
                    else:
                        new_status = "SL_HIT"
                        exit_price = active_sl_used
                        exit_reason = (f"SL hit at ₹{active_sl_used:.2f} — peak +{_peak_pct_now:.1f}% "
                                       f"but premium below mini-floor ₹{_mini_floor_price}.")
                        sl_reason_text = exit_reason
                else:
                    new_status = "SL_HIT"
                    exit_price = active_sl_used
                    if smart_enabled:
                        exit_reason = f"Smart SL hit (Stage {smart_stage} - {smart_label}) at ₹{active_sl_used:.2f}"
                        sl_reason_text = f"Profit ladder triggered: Stage {smart_stage} ({smart_label}). Premium ₹{current_ltp:.2f} hit SL ₹{active_sl_used:.2f}"
                    else:
                        exit_reason = f"SL hit at ₹{sl:.2f}"
                        sl_reason_text = f"Static SL hit. Premium ₹{current_ltp:.2f} ≤ SL ₹{sl:.2f}"
            else:
                print(f"[SCALPER] SL wick #{t['id']}: premium ₹{current_ltp:.2f} ≤ SL "
                      f"₹{active_sl_used:.2f} but only {_sl_breach_count[t['id']]}/{_confirm_ticks} "
                      f"ticks — waiting for confirmation (anti-hunt)")
        elif spot_exit:
            new_status = "SPOT_ANCHOR_EXIT"
            exit_price = current_ltp
            exit_reason = spot_reason
            sl_reason_text = spot_reason
        elif current_ltp >= t2:
            new_status = "T2_HIT"
            exit_price = t2
            exit_reason = f"T2 hit at ₹{t2}"
        elif current_ltp >= t1:
            # ── T1 FLOOR LOCK (Fix 5, 2026-05-19) ──
            # Audit: 30 T1_HIT scalper trades, all wins, +₹921k.
            # Full exit at T1 misses runners that go to T2 or beyond.
            #
            # NEW: when T1 hits AND lock enabled AND current SL < T1,
            # promote SL to T1 (lock in the T1 profit as floor). Trade
            # stays OPEN. Subsequent ticks:
            #   • If price → T2: exit at T2 (full runner captured)
            #   • If price drops back to T1: SL_HIT but at T1 = +T1% profit
            #   • If timeout: exit at current_ltp ≥ T1
            #
            # Strictly better than current behavior:
            #   Worst case  = T1 profit (same as old)
            #   Best case   = T2 profit (12% better avg)
            #   Side effect = SL_HIT will increment instead of T1_HIT,
            #                 but exit_reason makes the distinction clear.
            #
            # DEFAULT CHANGED 2026-06-03: off → on (data-driven Fix 3)
            # 445-trade audit revealed:
            #   • 31 T1_HIT scalper trades (avg +5%, +₹921k total) — capped
            #   • Only 2 T2_HIT (T2 is 12% — too far without floor lock)
            #   • 31 trades peaked between T1 and T2 (avg peak 19.5%)
            # Those 31 mid-runners would have ridden to avg 19.5% if T1
            # floor lock were on — locking in T1 as floor while letting
            # price run to T2 or higher. Worst case = T1 profit (same as
            # before). Best case = full runner captured.
            import os as _os
            t1_lock_enabled = _os.environ.get("T1_FLOOR_LOCK_ENABLED", "on").lower() != "off"
            if t1_lock_enabled and active_sl_used < t1:
                # Lock SL to T1 — apply small buffer to avoid same-tick re-fire
                new_floor_sl = round(t1 * 0.995, 2)  # T1 - 0.5% buffer
                if new_floor_sl > active_sl_used:
                    # Update DB so next tick sees the new SL
                    try:
                        conn_lock = _conn()
                        conn_lock.execute(
                            "UPDATE scalper_trades SET sl_price=? WHERE id=? AND status='OPEN'",
                            (new_floor_sl, t["id"]),
                        )
                        conn_lock.commit()
                        conn_lock.close()
                        print(f"[SCALPER] T1_FLOOR_LOCK #{t['id']}: T1 ₹{t1} reached, "
                              f"SL locked to ₹{new_floor_sl} (was ₹{active_sl_used:.2f}). "
                              f"Letting runner continue to T2 ₹{t2}.")
                    except Exception as _e:
                        print(f"[SCALPER] T1_FLOOR_LOCK update failed (falling through to exit): {_e}")
                        new_status = "T1_HIT"
                        exit_price = t1
                        exit_reason = f"T1 hit at ₹{t1}"
                # Don't set new_status — trade stays OPEN
            else:
                new_status = "T1_HIT"
                exit_price = t1
                exit_reason = f"T1 hit at ₹{t1}"
        elif hold_sec >= active_max_hold_min * 60:
            # ── PROFIT-ANCHOR TIMEOUT EXTENSION (Fix 2, 2026-05-19) ──
            # Audit: TIMEOUT_EXIT bucket = 18W (+₹357k) vs 16L (-₹223k).
            # When trade is profitable at timeout, it's "still working" —
            # let it run another 50% time to capture more (or hit T1/T2).
            # When trade is losing at timeout, cut as before (don't waste).
            #
            # State derived from hold_sec — no DB column needed:
            #   hold < active_max_hold_min    →   normal monitoring
            #   active_max <= hold < extended →   if profitable, hold; else exit
            #   hold >= extended_max_min      →   hard exit
            #
            # Env-gated: TIMEOUT_EXTENSION_ENABLED=on
            import os as _os
            extension_enabled = _os.environ.get("TIMEOUT_EXTENSION_ENABLED", "off").lower() == "on"
            extension_factor = 1.5  # extend by 50%
            min_profit_pct = 0.01   # must be >+1% to qualify

            extended_max_min = active_max_hold_min * extension_factor
            current_pnl_pct = (current_ltp - entry) / entry if entry > 0 else 0

            past_hard_limit = hold_sec >= extended_max_min * 60
            in_extension_window = (
                hold_sec >= active_max_hold_min * 60
                and hold_sec < extended_max_min * 60
            )

            if not extension_enabled or past_hard_limit:
                # Either feature off or we've crossed the hard ceiling → exit
                new_status = "TIMEOUT_EXIT"
                exit_price = current_ltp
                tag = "EXTENDED" if extension_enabled and past_hard_limit else "STANDARD"
                exit_reason = (
                    f"Max hold {(extended_max_min if extension_enabled else active_max_hold_min):.0f}min "
                    f"reached ({tag}), exit @ ₹{current_ltp}"
                )
            elif in_extension_window and current_pnl_pct > min_profit_pct:
                # Profitable + in extension window → log once, stay open
                # (the next monitoring tick will re-evaluate)
                if hold_sec < (active_max_hold_min * 60) + 30:
                    # Only log within 30s of crossing the threshold
                    print(f"[SCALPER] TIMEOUT_EXTENDED #{t['id']}: profitable at "
                          f"{current_pnl_pct*100:+.1f}% at {hold_sec/60:.0f}m, "
                          f"extending to {extended_max_min:.0f}m total")
                # Don't set new_status — trade stays OPEN
            elif in_extension_window:
                # In extension window but NOT profitable → exit now (cut loss)
                new_status = "TIMEOUT_EXIT"
                exit_price = current_ltp
                exit_reason = (
                    f"Max hold {active_max_hold_min}min reached "
                    f"(not profitable, no extension), exit @ ₹{current_ltp}"
                )

        # Update or close
        pnl_pts = round(current_ltp - entry, 2)
        pnl_rupees = round(pnl_pts * t["qty"], 2)

        conn2 = _conn()
        if new_status != "OPEN":
            _sl_breach_count.pop(t["id"], None)  # clean wick counter on close
            _trade_structure_mode.pop(t["id"], None)  # clean structure-mode tracking
            final_pnl = round((exit_price - entry) * t["qty"], 2)
            conn2.execute("""
                UPDATE scalper_trades SET
                    status=?, exit_price=?, exit_time=?, exit_reason=?,
                    pnl_pts=?, pnl_rupees=?, peak_ltp=?, hold_seconds=?,
                    sl_hit_time=?, sl_reason=?, smart_sl_stage=?, smart_sl_value=?
                WHERE id=?
            """, (new_status, exit_price, now.isoformat(), exit_reason,
                  round(exit_price - entry, 2), final_pnl, peak, int(hold_sec),
                  now.isoformat() if "SL" in new_status else None,
                  sl_reason_text,
                  smart_stage if smart_enabled else None,
                  smart_active_sl if smart_enabled else None,
                  t["id"]))
            print(f"[SCALPER] CLOSED #{t['id']} {idx} {action} {strike}: ₹{final_pnl:+,.0f} ({new_status})")

            # ── Journal: log exit with full context ──
            try:
                from trade_journal import log_exit as _je_log_exit
                pnl_pct = ((exit_price - entry) / entry * 100) if entry > 0 else 0
                _je_log_exit(
                    trade_id=t["id"],
                    tab="SCALPER",
                    exit_price=exit_price,
                    exit_reason=exit_reason or new_status,
                    status=new_status,
                    pnl_rupees=final_pnl,
                    pnl_pct=pnl_pct,
                    peak_price=peak,
                )
            except Exception as _je:
                print(f"[JOURNAL] scalper exit log failed: {_je}")
            # ── Record P&L in capital tracker (auto-adjust running capital + profit bank) ──
            try:
                from capital_tracker import record_trade_pnl
                desc = f"{idx} {action} {strike} @ ₹{exit_price} ({new_status})"
                record_trade_pnl("SCALPER", final_pnl, trade_id=t["id"], description=desc)
            except Exception as e:
                print(f"[CAPITAL] scalper record error: {e}")
        else:
            conn2.execute("""
                UPDATE scalper_trades SET
                    current_ltp=?, peak_ltp=?, pnl_pts=?, pnl_rupees=?, hold_seconds=?,
                    smart_sl_stage=?, smart_sl_value=?
                WHERE id=?
            """, (current_ltp, peak, pnl_pts, pnl_rupees, int(hold_sec),
                  smart_stage if smart_enabled else None,
                  smart_active_sl if smart_enabled else None,
                  t["id"]))
            # Record tick sample (live LTP history per trade)
            try:
                import time as _time
                conn2.execute("""
                    INSERT INTO scalper_ticks (trade_id, ts, ltp, spot, pnl_rupees, pnl_pct)
                    VALUES (?,?,?,?,?,?)
                """, (t["id"], int(_time.time() * 1000), current_ltp, None, pnl_rupees,
                      round((current_ltp - entry) / entry * 100, 2) if entry > 0 else 0))
            except Exception:
                pass

        conn2.commit()
        conn2.close()


def get_scalper_open_trades():
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM scalper_trades WHERE status='OPEN' ORDER BY entry_time DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_scalper_closed_trades(days=7):
    cutoff = (ist_now() - timedelta(days=days)).isoformat()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM scalper_trades WHERE status!='OPEN' AND entry_time > ? ORDER BY exit_time DESC",
        (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_scalper_stats():
    """Aggregate scalper performance stats."""
    today_start = ist_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    conn = _conn()

    total = conn.execute("SELECT COUNT(*) FROM scalper_trades").fetchone()[0]
    today_count = conn.execute("SELECT COUNT(*) FROM scalper_trades WHERE entry_time > ?", (today_start,)).fetchone()[0]
    open_count = conn.execute("SELECT COUNT(*) FROM scalper_trades WHERE status='OPEN'").fetchone()[0]

    closed = conn.execute("SELECT pnl_rupees FROM scalper_trades WHERE status!='OPEN'").fetchall()
    closed = [r[0] or 0 for r in closed]
    wins = [p for p in closed if p > 0]
    losses = [p for p in closed if p < 0]
    breakevens = [p for p in closed if p == 0]

    conn.close()

    # Win rate = wins / (wins + losses). Breakeven (pnl=0) trades are
    # scratches, not losses — excluded from the denominator so the
    # breakeven-lock feature does not artificially deflate win rate.
    decided = len(wins) + len(losses)
    win_rate = round(len(wins) / decided * 100, 1) if decided else 0
    total_pnl = sum(closed)
    avg_win = round(sum(wins) / max(len(wins), 1), 0) if wins else 0
    avg_loss = round(sum(losses) / max(len(losses), 1), 0) if losses else 0

    return {
        "total": total,
        "todayCount": today_count,
        "open": open_count,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "winRate": win_rate,
        "totalPnl": total_pnl,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "dailyCap": SCALPER_DAILY_CAP,
        "remaining": max(0, SCALPER_DAILY_CAP - today_count),
    }


# Module-level toggle (in-memory, can be controlled via API)
_scalper_enabled = True  # ALWAYS ON — user wants live tick accuracy preserved


def is_scalper_enabled():
    return _scalper_enabled


def enable_scalper():
    global _scalper_enabled
    _scalper_enabled = True
    print("[SCALPER] Mode ENABLED")


def disable_scalper():
    global _scalper_enabled
    _scalper_enabled = False
    print("[SCALPER] Mode DISABLED")
