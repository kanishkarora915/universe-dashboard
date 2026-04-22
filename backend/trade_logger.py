"""
Smart Trade Logger — Auto-logs trades from verdict engine.
Tracks SL/target hits. Detects institutional stop hunts.
SQLite-backed persistence.

Lot sizes: NIFTY = 65 qty, BANKNIFTY = 30 qty, ALWAYS 20 lots
Max SL = 15% of entry premium
"""

import sqlite3
import time
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
DB_PATH = None

INITIAL_CAPITAL = 1000000  # ₹10 lakh starting capital
MAX_RISK_PER_TRADE_PCT = 3.0  # Risk 3% of capital per trade (₹30k on ₹10L) — was 1.5%
MAX_RISK_WHALE_ALIGNED_PCT = 5.0  # 5% on whale-aligned trades (₹50k risk) — was 2.5%
MAX_DAILY_LOSS_PCT = 8  # Stop trading after 8% daily loss (was 5% — let trades breathe)
MAX_SIMULTANEOUS_TRADES = 10  # No practical limit
MAX_DAILY_TRADES = 6  # Real trades per day — prevents overtrading

# NSE Holidays — Auto-fetched from Kite API, fallback to hardcoded
_NSE_HOLIDAYS_CACHE = None
_NSE_HOLIDAYS_FALLBACK = {
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
    "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
}


def _fetch_nse_holidays():
    """Fetch NSE trading holidays from Kite Connect API. Cached for entire session."""
    global _NSE_HOLIDAYS_CACHE
    if _NSE_HOLIDAYS_CACHE is not None:
        return _NSE_HOLIDAYS_CACHE
    try:
        # Try fetching from Kite API (kite.trading_holidays())
        # This requires an authenticated kite instance
        # Fallback: try NSE website or use hardcoded
        import json
        from pathlib import Path
        cache_file = Path(__file__).parent / "nse_holidays_cache.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            year = ist_now().year
            if data.get("year") == year:
                _NSE_HOLIDAYS_CACHE = set(data.get("dates", []))
                return _NSE_HOLIDAYS_CACHE
    except Exception:
        pass
    _NSE_HOLIDAYS_CACHE = _NSE_HOLIDAYS_FALLBACK
    return _NSE_HOLIDAYS_CACHE


def save_nse_holidays_from_kite(kite):
    """Call this after Kite login to fetch and cache real holidays."""
    global _NSE_HOLIDAYS_CACHE
    try:
        holidays = kite.trading_holidays()
        dates = set()
        for h in holidays:
            # Kite returns list of dicts with 'date' and 'description'
            if isinstance(h, dict):
                d = h.get("date", "")
                if isinstance(d, str) and len(d) >= 10:
                    dates.add(d[:10])
                elif hasattr(d, "strftime"):
                    dates.add(d.strftime("%Y-%m-%d"))
            # Also handle list-of-lists format
            elif isinstance(h, (list, tuple)) and len(h) >= 1:
                d = str(h[0])[:10]
                dates.add(d)
        if dates:
            _NSE_HOLIDAYS_CACHE = dates
            # Save to cache file
            import json
            from pathlib import Path
            cache_file = Path(__file__).parent / "nse_holidays_cache.json"
            cache_file.write_text(json.dumps({
                "year": ist_now().year,
                "dates": sorted(dates),
                "fetched": ist_now().isoformat(),
            }))
            print(f"[TRADES] Fetched {len(dates)} NSE holidays from Kite API")
            return dates
    except Exception as e:
        print(f"[TRADES] Could not fetch holidays from Kite: {e}")
    return _NSE_HOLIDAYS_FALLBACK

LOT_CONFIG = {
    "NIFTY": {"lot_size": 65},
    "BANKNIFTY": {"lot_size": 30},
}


def _is_trading_day():
    """Check if today is a valid trading day (not weekend/holiday)."""
    now = ist_now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    holidays = _fetch_nse_holidays()
    if now.strftime("%Y-%m-%d") in holidays:
        return False
    return True


def _get_running_capital():
    """Get current running capital = initial + cumulative realized P&L."""
    if not DB_PATH:
        return INITIAL_CAPITAL
    try:
        conn = sqlite3.connect(DB_PATH)
        total_pnl = conn.execute(
            "SELECT COALESCE(SUM(pnl_rupees), 0) FROM trades WHERE status != 'OPEN'"
        ).fetchone()[0] or 0
        conn.close()
        running = INITIAL_CAPITAL + total_pnl
        return max(running, INITIAL_CAPITAL * 0.2)  # Never go below 20% of initial (₹2L floor)
    except Exception:
        return INITIAL_CAPITAL


def calc_position_size(idx, entry_price, sl_price=0, conviction=70, whale_aligned=False):
    """Risk-based position sizing SCALED by conviction + whale alignment.

    Conviction scaling:
      90%+ → full size (100% of max_risk)
      80-89% → 75% of max_risk
      70-79% → 50% of max_risk
      55-69% → 25% of max_risk

    Whale alignment bonus (smart money agrees with direction):
      Uses MAX_RISK_WHALE_ALIGNED_PCT (2.5%) instead of 1.5% base → +67% size

    Result: 90%+ conviction + whale aligned = 2.5% risk (beast mode)
    Result: 55% conviction + no whale = 0.375% risk (protect capital)
    """
    cfg = LOT_CONFIG.get(idx, LOT_CONFIG["NIFTY"])
    lot_size = cfg["lot_size"]

    if entry_price <= 0:
        return 1, lot_size, lot_size

    # Conviction multiplier — AGGRESSIVE for ₹10L capital
    # Goal: deploy 30-60% of capital per trade on high conviction
    if conviction >= 90:
        conv_mult = 2.0    # MAX — beast mode (₹60k risk = 6%)
    elif conviction >= 80:
        conv_mult = 1.5    # Aggressive (₹45k risk = 4.5%)
    elif conviction >= 70:
        conv_mult = 1.2    # Full (₹36k risk = 3.6%)
    elif conviction >= 60:
        conv_mult = 1.0    # Standard (₹30k risk = 3%)
    else:
        conv_mult = 0.7    # Smaller (₹21k risk = 2.1%)

    running_capital = _get_running_capital()
    base_risk_pct = MAX_RISK_WHALE_ALIGNED_PCT if whale_aligned else MAX_RISK_PER_TRADE_PCT
    max_risk = running_capital * base_risk_pct / 100 * conv_mult

    # Risk per unit = entry - SL
    if sl_price > 0 and sl_price < entry_price:
        risk_per_unit = entry_price - sl_price
    else:
        risk_per_unit = entry_price * 0.15

    risk_per_unit = max(risk_per_unit, 1)

    max_qty_by_risk = int(max_risk / risk_per_unit)
    # Capital deployment cap: 80% (was 50%) — allows bigger positions
    max_qty_by_capital = int(running_capital * 0.8 / max(entry_price, 1))
    max_qty = min(max_qty_by_risk, max_qty_by_capital)

    # Max lots cap: 30 (was 20) — for high conviction trades
    lots = max(1, min(max_qty // lot_size, 30))
    qty = lots * lot_size

    return lots, lot_size, qty


def ist_now():
    return datetime.now(IST)


def _cleanup_invalid_trades():
    """Remove trades from weekends/holidays — they should never have existed."""
    if not DB_PATH:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        all_trades = conn.execute("SELECT id, entry_time FROM trades").fetchall()
        invalid_ids = []
        for t in all_trades:
            try:
                dt = datetime.fromisoformat(t["entry_time"])
                trade_date = dt.strftime("%Y-%m-%d")
                holidays = _fetch_nse_holidays()
                if dt.weekday() >= 5 or trade_date in holidays:
                    invalid_ids.append(t["id"])
            except Exception:
                pass
        if invalid_ids:
            placeholders = ",".join(str(i) for i in invalid_ids)
            conn.execute(f"DELETE FROM trades WHERE id IN ({placeholders})")
            conn.commit()
            print(f"[TRADES] Cleaned {len(invalid_ids)} invalid trades (weekends/holidays)")
        conn.close()
    except Exception as e:
        print(f"[TRADES] Cleanup error: {e}")


def init_trades_db(db_path):
    global DB_PATH
    DB_PATH = db_path
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            idx TEXT NOT NULL,
            action TEXT NOT NULL,
            strike INTEGER NOT NULL,
            expiry TEXT,
            entry_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            original_sl REAL NOT NULL,
            t1_price REAL NOT NULL,
            t2_price REAL NOT NULL,
            current_ltp REAL DEFAULT 0,
            peak_ltp REAL DEFAULT 0,
            exit_price REAL DEFAULT 0,
            lots INTEGER DEFAULT 20,
            lot_size INTEGER,
            qty INTEGER,
            pnl_pts REAL DEFAULT 0,
            pnl_rupees REAL DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            exit_reason TEXT,
            probability INTEGER DEFAULT 0,
            source TEXT,
            breakeven_active INTEGER DEFAULT 0,
            trailing_active INTEGER DEFAULT 0,
            trail_level TEXT DEFAULT '',
            alerts TEXT DEFAULT '',
            sl_hit_time TEXT,
            reversal_price REAL DEFAULT 0,
            reversal_detected INTEGER DEFAULT 0,
            oi_at_sl_hit INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON trades(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON trades(entry_time)")

    # Migrate: add new columns if missing (for old DBs)
    existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()]
    migrations = {
        "original_sl": "REAL DEFAULT 0",
        "peak_ltp": "REAL DEFAULT 0",
        "breakeven_active": "INTEGER DEFAULT 0",
        "trailing_active": "INTEGER DEFAULT 0",
        "trail_level": "TEXT DEFAULT ''",
        "alerts": "TEXT DEFAULT ''",
    }
    for col, col_type in migrations.items():
        if col not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
                print(f"[TRADES] Migrated: added column {col}")
            except Exception:
                pass

    conn.commit()
    conn.close()
    # Purge very old trades (>90 days)
    cutoff = (ist_now() - timedelta(days=90)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM trades WHERE entry_time < ? AND status != 'OPEN'", (cutoff,))
    conn.commit()
    conn.close()
    print(f"[TRADES] Database initialized at {db_path}")
    _cleanup_invalid_trades()


def _conn():
    return sqlite3.connect(DB_PATH)


class TradeManager:
    def __init__(self):
        self._last_verdict_check = 0
        self._last_sl_check = 0
        self._cached_verdict = {}
        self._sl_override_count = {}
        self._trade_alerts = []
        # Per-index pending entries (was single slot — caused cross-contamination bug)
        self._pending_entry = {}          # {idx: {action, strike, entry_price, probability}}
        self._pending_entry_time = {}     # {idx: timestamp}
        self._engine_ref = None  # Set by engine for autopsy snapshots
        self._closed_trade_ids = set()  # Track recently closed to trigger exit snapshot

    def update_verdict_cache(self, verdict):
        """Called by engine every 30s with latest verdict data."""
        self._cached_verdict = verdict or {}

    def _engines_favor_hold(self, trade, idx, action):
        """Check if engines still support the trade direction.
        Returns True if engines say HOLD (don't exit at SL)."""
        v = self._cached_verdict.get(idx.lower(), {})
        if not v:
            return False  # No data = don't override, let SL hit

        # Max 3 SL overrides per trade (safety)
        trade_id = trade.get("id", 0)
        override_count = self._sl_override_count.get(trade_id, 0)
        if override_count >= 3:
            return False  # Already overridden 3 times, let SL hit now

        v_action = v.get("action", "")
        win_pct = v.get("winProbability", 0)

        # Engines must strongly agree with our trade direction
        # AND probability must be >55% (not just barely above 50%)
        if v_action == action and win_pct >= 55:
            self._sl_override_count[trade_id] = override_count + 1
            return True

        return False

    def _detect_trade_context(self, trade, idx, action, chain, current_ltp):
        """Detect if trade is in 'reversal zone' (OI against us) or 'stop hunt' (price dipped+bouncing).
        Returns: 'REVERSAL' | 'STOP_HUNT' | 'NORMAL'

        REVERSAL → tighten SL to 10% (OI says we're wrong, exit fast)
        STOP_HUNT → widen SL to 20% (temporary dip, give room to recover)
        NORMAL → default 15% SL
        """
        strike = trade["strike"]
        entry = trade["entry_price"]
        sd = chain.get(strike, {})

        # ── STOP HUNT: price dipped 10%+ then recovered 5%+ ──
        peak = max(trade.get("peak_ltp", entry), current_ltp)
        price_dip_pct = (entry - current_ltp) / entry * 100 if entry > 0 else 0

        # Track min price in memory
        if not hasattr(self, '_trade_min_ltp'):
            self._trade_min_ltp = {}
        tid = trade["id"]
        prev_min = self._trade_min_ltp.get(tid, current_ltp)
        if current_ltp < prev_min:
            self._trade_min_ltp[tid] = current_ltp
            prev_min = current_ltp

        # Stop hunt: went down 10%+ from entry, then recovered 5%+ from bottom
        if prev_min < entry * 0.90 and current_ltp > prev_min * 1.05:
            return "STOP_HUNT"

        # ── REVERSAL: OI building against our direction ──
        if "CE" in action:
            # CE trade → watch for PE premium rising, CE falling, PE OI surging
            ce_oi = sd.get("ce_oi", 0)
            pe_oi = sd.get("pe_oi", 0)
            pe_ltp = sd.get("pe_ltp", 0)
            # PE premium > CE premium (bears winning at this strike)
            if pe_ltp > current_ltp * 1.15 and pe_oi > ce_oi * 1.2:
                return "REVERSAL"
        elif "PE" in action:
            # PE trade → watch for CE premium rising, PE falling
            ce_oi = sd.get("ce_oi", 0)
            pe_oi = sd.get("pe_oi", 0)
            ce_ltp = sd.get("ce_ltp", 0)
            if ce_ltp > current_ltp * 1.15 and ce_oi > pe_oi * 1.2:
                return "REVERSAL"

        return "NORMAL"

    def log_trade(self, idx, action, strike, entry_price, probability, source="verdict", expiry="",
                  straddle=0, big_wall=0, whale_aligned=False):
        """Log a new trade entry with smart SL/targets.
        whale_aligned=True → uses larger position (smart money agrees with direction)."""
        if entry_price <= 0:
            return None

        # Smart SL: 20% of entry for cheap premiums, 15% for expensive, minimum ₹5 drop
        if entry_price < 100:
            sl_price = round(entry_price * 0.80)  # 20% SL for cheap options
        else:
            sl_price = round(entry_price * 0.85)  # 15% SL for expensive options
        sl_price = max(sl_price, round(entry_price - max(straddle * 0.15, 5)))
        sl_price = min(sl_price, round(entry_price * 0.85))  # Never tighter than 15%

        # Position size SCALED by conviction (probability) + whale alignment bonus
        lots, lot_size, qty = calc_position_size(
            idx, entry_price, sl_price,
            conviction=probability,
            whale_aligned=whale_aligned,
        )

        # Smart T1: based on straddle or 20% of entry
        if straddle > 0:
            t1_price = round(entry_price + straddle * 0.20)  # 20% of straddle move
        else:
            t1_price = round(entry_price * 1.20)

        # Smart T2: based on straddle or 40%
        if straddle > 0:
            t2_price = round(entry_price + straddle * 0.40)
        else:
            t2_price = round(entry_price * 1.40)

        now = ist_now()
        conn = _conn()
        cursor = conn.execute("""
            INSERT INTO trades (entry_time, idx, action, strike, expiry,
                entry_price, sl_price, original_sl, t1_price, t2_price,
                current_ltp, peak_ltp,
                lots, lot_size, qty, status, probability, source,
                breakeven_active, trailing_active, trail_level, alerts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, 0, 0, '', '')
        """, (
            now.isoformat(), idx, action, strike, expiry,
            entry_price, sl_price, sl_price, t1_price, t2_price,
            entry_price, entry_price,
            lots, lot_size, qty,
            probability, source,
        ))
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()

        capital_used = round(entry_price * qty)
        risk_amount = round((entry_price - sl_price) * qty)
        print(f"[TRADE] NEW: {action} {idx} {strike} @ {entry_price} | SL: {sl_price} | T1: {t1_price} | T2: {t2_price} | {lots}L x {lot_size} = {qty} qty | Capital: ₹{capital_used:,} | Risk: ₹{risk_amount:,} | Prob: {probability}%")

        # Store alert for frontend notification
        alert = {
            "type": "TRADE_ENTRY",
            "time": now.strftime("%I:%M:%S %p"),
            "message": f"🚀 NEW TRADE: {action} {idx} {strike} @ ₹{entry_price}",
            "details": f"SL: ₹{sl_price} | T1: ₹{t1_price} | T2: ₹{t2_price} | {lots} lots ({qty} qty) | Risk: ₹{risk_amount:,}",
            "tradeId": trade_id,
            "idx": idx,
            "action": action,
            "strike": strike,
            "entry": entry_price,
            "probability": probability,
        }
        self._trade_alerts.append(alert)
        self._trade_alerts = self._trade_alerts[-50:]  # Keep last 50

        return trade_id

    def check_and_update(self, chains, prices, spot_tokens, token_to_info):
        """Smart trade management: breakeven, trailing SL, alerts, early exit."""
        now = ist_now()

        # ── EOD AUTO-CLOSE: Close ALL open trades at 3:25 PM ──
        if now.hour == 15 and now.minute >= 25:
            self._close_all_open("EOD_CLOSE", "Market closing — auto-closed at 3:25 PM. Options are intraday only.")
            return

        # ── STALE TRADE CLEANUP: Close trades from previous days ──
        conn = _conn()
        conn.row_factory = sqlite3.Row
        open_trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        conn.close()

        today = now.strftime("%Y-%m-%d")
        for trade in open_trades:
            t = dict(trade)
            entry_date = t["entry_time"][:10] if t.get("entry_time") else ""
            if entry_date and entry_date != today:
                # Trade from previous day — should not be open
                conn2 = _conn()
                current_ltp = 0
                chain = chains.get(t["idx"], {})
                strike_data = chain.get(t["strike"], {})
                opt = "ce" if "CE" in t["action"] else "pe"
                current_ltp = strike_data.get(f"{opt}_ltp", 0)

                exit_price = current_ltp if current_ltp > 0 else t["entry_price"]
                pnl_pts = round(exit_price - t["entry_price"], 2)
                pnl_rupees = round(pnl_pts * t["qty"], 2) + (t.get("pnl_rupees", 0) or 0)

                conn2.execute("""
                    UPDATE trades SET status='STALE_CLOSE', exit_price=?, exit_time=?,
                        pnl_pts=?, pnl_rupees=?, exit_reason=?
                    WHERE id=? AND status='OPEN'
                """, (exit_price, now.isoformat(), pnl_pts, pnl_rupees,
                      f"Stale trade from {entry_date} — auto-closed. Options must close same day.",
                      t["id"]))
                conn2.commit()
                conn2.close()
                print(f"[TRADE] STALE CLOSE: {t['action']} {t['idx']} {t['strike']} from {entry_date} — PnL: ₹{pnl_rupees:+,.0f}")
                continue

        # Reload after cleanup
        conn = _conn()
        conn.row_factory = sqlite3.Row
        open_trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        conn.close()

        for trade in open_trades:
            t = dict(trade)

            # Idempotent: re-check status before processing
            conn_check = _conn()
            current_status = conn_check.execute("SELECT status FROM trades WHERE id=?", (t["id"],)).fetchone()
            conn_check.close()
            if not current_status or current_status[0] != "OPEN":
                continue  # Already closed by another thread
            idx = t["idx"]
            strike = t["strike"]
            action = t["action"]

            chain = chains.get(idx, {})
            strike_data = chain.get(strike, {})
            opt = "ce" if "CE" in action else "pe"
            current_ltp = strike_data.get(f"{opt}_ltp", 0)

            if current_ltp <= 0:
                continue

            entry = t["entry_price"]
            sl = t["sl_price"]
            t1 = t["t1_price"]
            t2 = t["t2_price"]
            peak = max(t.get("peak_ltp", entry), current_ltp)
            breakeven_active = t.get("breakeven_active", 0)
            trailing_active = t.get("trailing_active", 0)

            pnl_pts = round(current_ltp - entry, 2)
            pnl_rupees = round(pnl_pts * t["qty"], 2)
            profit_pct = round((current_ltp - entry) / entry * 100, 1) if entry > 0 else 0

            new_status = "OPEN"
            exit_reason = None
            exit_price = 0
            new_sl = sl
            alerts_list = []
            trail_level = t.get("trail_level", "")

            # ══════════════════════════════════════════════
            # ADAPTIVE SL: tighten on reversal, widen on stop-hunt
            # Only adjusts BEFORE breakeven (once in profit, trailing takes over)
            # ══════════════════════════════════════════════
            if not breakeven_active:
                context = self._detect_trade_context(t, idx, action, chain, current_ltp)
                original_sl = t.get("original_sl", sl) or sl
                if context == "REVERSAL":
                    # OI flipped against us → 10% SL (tighter, exit fast)
                    tight_sl = round(entry * 0.90)
                    if tight_sl > new_sl:  # Only tighten, never loosen
                        new_sl = tight_sl
                        trail_level = "OI_REVERSAL_TIGHT"
                        alerts_list.append(f"OI REVERSAL detected — SL tightened to 10% (₹{new_sl}). Exit fast if triggered.")
                elif context == "STOP_HUNT":
                    # Price was hunted → 20% SL (wider, give room)
                    wide_sl = round(entry * 0.80)
                    if wide_sl < new_sl and current_ltp > wide_sl:
                        new_sl = wide_sl
                        trail_level = "STOP_HUNT_WIDE"
                        alerts_list.append(f"STOP HUNT pattern detected — SL widened to 20% (₹{new_sl}) to avoid hunt. Recovery expected.")

            # ══════════════════════════════════════════════
            # SMART SL MANAGEMENT
            # ══════════════════════════════════════════════

            # STAGE 0: CONVICTION DROP + PROFIT → Early breakeven
            # If engines no longer support AND we're profitable → protect profit NOW
            cached_verdict = self._cached_verdict.get(idx.lower(), {})
            our_side = "bullPct" if "CE" in action else "bearPct"
            current_conviction = cached_verdict.get(our_side, 50)

            if not breakeven_active and profit_pct > 5 and current_conviction < 50:
                # Conviction dropped below 50% but we have profit → move SL to entry
                breakeven_active = 1
                new_sl = entry
                trail_level = "CONVICTION_BE"
                alerts_list.append(f"EARLY BREAKEVEN: Conviction dropped to {current_conviction}% but profit +{profit_pct:.0f}%. SL moved to entry ₹{entry}. Zero loss protected.")
                print(f"[TRADE] EARLY BREAKEVEN (conviction): {action} {idx} {strike} — conviction {current_conviction}%, profit +{profit_pct:.0f}%")

            # STAGE 1: BREAKEVEN — activate at +2% profit (quick lock, no greed)
            if not breakeven_active and profit_pct >= 2:
                breakeven_active = 1
                new_sl = entry
                trail_level = "BREAKEVEN"
                alerts_list.append(f"BREAKEVEN activated at +{profit_pct:.1f}% — SL moved to entry ₹{entry}")
                print(f"[TRADE] BREAKEVEN: {action} {idx} {strike} — SL moved to entry ₹{entry} (was ₹{sl})")

            # STAGE 1.5: REVERSAL EXIT — if -3% after 10 min hold, accept small loss
            # Prevents 15% SL catastrophe when trade clearly going wrong
            try:
                entry_time = datetime.fromisoformat(t["entry_time"])
                hold_sec = (ist_now() - entry_time).total_seconds()
            except Exception:
                hold_sec = 0

            if not breakeven_active and hold_sec >= 600 and profit_pct <= -3:
                new_status = "REVERSAL_EXIT"
                exit_price = current_ltp
                exit_reason = f"Reversal exit at ₹{current_ltp:.1f} ({profit_pct:.1f}%) — held {int(hold_sec/60)}min, no recovery. Small loss accepted, 15% SL avoided."
                print(f"[TRADE] REVERSAL EXIT: {action} {idx} {strike} @ ₹{current_ltp} ({profit_pct:.1f}% after {int(hold_sec/60)}min)")

            # STAGE 2: TRAILING SL — after breakeven, lock 50% of peak gain (user preference)
            if breakeven_active and new_status == "OPEN":
                # Lock 50% of peak profit (more aggressive protection)
                trail_from_peak = round(peak - (peak - entry) * 0.50)
                if trail_from_peak > new_sl and trail_from_peak > entry:
                    new_sl = trail_from_peak
                    trailing_active = 1

                # Tighter trail levels
                if profit_pct >= 35:
                    tight_trail = round(peak - (peak - entry) * 0.25)  # Lock 75% at 35%+ profit
                    if tight_trail > new_sl:
                        new_sl = tight_trail
                        trail_level = "TRAIL_75"
                        alerts_list.append(f"Tight trail: locking 75% profit, SL at ₹{new_sl}")
                elif profit_pct >= 25:
                    trail_level = "TRAIL_60"

            # ══════════════════════════════════════════════
            # EXIT CONDITIONS (with engine override)
            # ══════════════════════════════════════════════

            # Check SL zone (within 3% of SL)
            sl_zone = current_ltp <= new_sl * 1.03
            sl_breached = current_ltp <= new_sl

            if sl_breached:
                if breakeven_active and new_sl >= entry:
                    exit_price = new_sl
                    final_pnl = round((exit_price - entry) * t["qty"], 2)
                    if exit_price > entry:
                        new_status = "TRAIL_EXIT"
                        exit_reason = f"Trailing SL hit at ₹{new_sl} — locked profit ₹{final_pnl:+,.0f} ({round((new_sl/entry-1)*100)}% gain). Peak was ₹{peak:.1f}"
                    else:
                        new_status = "BREAKEVEN_EXIT"
                        exit_reason = f"Breakeven exit at ₹{entry} — no loss. Price reached ₹{peak:.1f} (+{round((peak/entry-1)*100)}%) then reversed"
                elif self._engines_favor_hold(t, idx, action):
                    # ENGINES SAY HOLD — keep SL same (don't widen), just don't exit yet
                    # SL stays at 15% — it will NOT change
                    # But we give it one more check cycle before closing
                    alerts_list.append(f"SL ZONE: Price ₹{current_ltp:.1f} at SL ₹{new_sl} but engines favor HOLD. Giving 1 more cycle. Max loss stays 15%.")
                    # Hard stop = same 15% SL, absolutely no more
                    hard_stop = round(entry * 0.85)
                    if current_ltp <= hard_stop:
                        new_status = "SL_HIT"
                        exit_price = hard_stop
                        exit_reason = f"Stoploss hit at ₹{hard_stop} (-15% max). Engines tried to hold but hard limit reached. Entry: ₹{entry}"
                else:
                    new_status = "SL_HIT"
                    exit_price = new_sl
                    exit_reason = f"Stoploss hit at ₹{new_sl} (entry ₹{entry}, loss {round((1-new_sl/entry)*100)}%). Original SL was ₹{t.get('original_sl', sl)}"

            elif sl_zone and not breakeven_active:
                # Near SL zone — check if engines say hold
                if self._engines_favor_hold(t, idx, action):
                    alerts_list.append(f"NEAR SL (₹{current_ltp:.1f} vs SL ₹{new_sl}) but engines still favor {action}. HOLDING. Will exit at entry if reversal fails.")
                else:
                    alerts_list.append(f"WARNING: Price ₹{current_ltp:.1f} approaching SL ₹{new_sl}. Engines NOT supporting — prepare for SL exit.")

            # Check T1 — PARTIAL PROFIT BOOKING (50% qty booked, rest trails to T2)
            elif current_ltp >= t1 and not breakeven_active:
                breakeven_active = 1
                new_sl = entry  # Move SL to entry (zero loss on remaining)

                # Calculate partial profit (50% of qty)
                booked_qty = t["qty"] // 2
                booked_pnl = round((t1 - entry) * booked_qty, 2)
                remaining_qty = t["qty"] - booked_qty

                trail_level = "T1_PARTIAL"
                alerts_list.append(f"T1 HIT ₹{t1} — BOOKED 50% ({booked_qty} qty) profit ₹{booked_pnl:+,.0f}. Trailing {remaining_qty} qty to T2 ₹{t2}. SL at entry ₹{entry}.")
                print(f"[TRADE] T1 PARTIAL: {action} {idx} {strike} — booked {booked_qty} qty @ ₹{t1}, profit ₹{booked_pnl:+,.0f}, trailing {remaining_qty}")

                # Update qty in DB to remaining amount, add partial P&L
                conn2 = _conn()
                conn2.execute("UPDATE trades SET qty=?, pnl_rupees=pnl_rupees+? WHERE id=?",
                              (remaining_qty, booked_pnl, t["id"]))
                conn2.commit()
                conn2.close()

                # Alert
                self._trade_alerts.append({
                    "type": "PARTIAL_BOOK",
                    "time": ist_now().strftime("%I:%M:%S %p"),
                    "message": f"💰 T1 HIT: Booked 50% profit on {action} {idx} {strike}",
                    "details": f"Booked ₹{booked_pnl:+,.0f} ({booked_qty} qty @ ₹{t1}). Trailing {remaining_qty} qty to T2 ₹{t2}",
                })
                self._trade_alerts = self._trade_alerts[-50:]

            # Check T2 (full target on remaining qty)
            elif current_ltp >= t2:
                new_status = "T2_HIT"
                exit_price = t2
                exit_reason = f"TARGET 2 HIT at ₹{t2} — full profit +{round((t2/entry-1)*100)}% from entry ₹{entry}. PnL: ₹{round((t2-entry)*t['qty']):+,}"

            # ══════════════════════════════════════════════
            # UPDATE DATABASE
            # ══════════════════════════════════════════════
            conn = _conn()
            alerts_str = " | ".join(alerts_list) if alerts_list else t.get("alerts", "")

            if new_status != "OPEN":
                # Idempotent close: re-check status with lock
                conn_recheck = _conn()
                still_open = conn_recheck.execute("SELECT status, pnl_rupees, qty FROM trades WHERE id=?", (t["id"],)).fetchone()
                conn_recheck.close()
                if not still_open or still_open[0] != "OPEN":
                    continue  # Already closed

                # Detect if T1 partial booking happened (qty < original lots*lot_size)
                current_qty = still_open[2] or t["qty"]
                original_qty = t.get("lots", 1) * t.get("lot_size", current_qty)
                t1_price = t.get("t1_price", 0) or 0
                partial_booked = current_qty < original_qty

                final_pnl_pts = round(exit_price - entry, 2)

                if partial_booked and t1_price > 0:
                    # T1 partial hit earlier — recompute actual booked amount
                    booked_qty = original_qty - current_qty
                    booked_pnl_realized = round((t1_price - entry) * booked_qty, 2)
                    remaining_pnl = round(final_pnl_pts * current_qty, 2)
                    total_pnl = round(booked_pnl_realized + remaining_pnl, 2)
                    already_booked_pnl = booked_pnl_realized  # For log
                    remaining_pnl_for_log = remaining_pnl
                else:
                    # No partial — simple close: full qty × (exit - entry)
                    total_pnl = round(final_pnl_pts * current_qty, 2)
                    already_booked_pnl = 0
                    remaining_pnl_for_log = total_pnl

                conn.execute("""
                    UPDATE trades SET current_ltp=?, peak_ltp=?, pnl_pts=?, pnl_rupees=?,
                        sl_price=?, breakeven_active=?, trailing_active=?, trail_level=?,
                        status=?, exit_price=?, exit_time=?, exit_reason=?, alerts=?
                    WHERE id=? AND status='OPEN'
                """, (current_ltp, peak, final_pnl_pts, total_pnl,
                      new_sl, breakeven_active, trailing_active, trail_level,
                      new_status, exit_price, ist_now().isoformat(), exit_reason, alerts_str, t["id"]))
                print(f"[TRADE] CLOSED: {action} {idx} {strike} — {new_status} — PnL: ₹{total_pnl:+,.0f} (partial: ₹{already_booked_pnl:+,.0f} + exit: ₹{remaining_pnl_for_log:+,.0f})")

                # Autopsy: capture exit snapshot
                if self._engine_ref:
                    try:
                        from trade_autopsy import capture_trade_snapshot
                        capture_trade_snapshot(self._engine_ref, t["id"], idx, "EXIT")
                    except Exception as e:
                        print(f"[AUTOPSY] EXIT capture failed for trade #{t['id']}: {e}")

                emoji = "✅" if total_pnl > 0 else "❌"
                self._trade_alerts.append({
                    "type": "TRADE_EXIT",
                    "time": ist_now().strftime("%I:%M:%S %p"),
                    "message": f"{emoji} CLOSED: {action} {idx} {strike} — {new_status}",
                    "details": f"Exit ₹{exit_price} | PnL: ₹{total_pnl:+,.0f} | {exit_reason[:100]}",
                })
                self._trade_alerts = self._trade_alerts[-50:]

                if new_status == "SL_HIT":
                    oi_at_sl = strike_data.get(f"{opt}_oi", 0)
                    conn.execute("UPDATE trades SET sl_hit_time=?, oi_at_sl_hit=? WHERE id=?",
                                 (ist_now().isoformat(), oi_at_sl, t["id"]))
            else:
                conn.execute("""
                    UPDATE trades SET current_ltp=?, peak_ltp=?, pnl_pts=?, pnl_rupees=?,
                        sl_price=?, breakeven_active=?, trailing_active=?, trail_level=?, alerts=?
                    WHERE id=?
                """, (current_ltp, peak, pnl_pts, pnl_rupees,
                      new_sl, breakeven_active, trailing_active, trail_level, alerts_str, t["id"]))
            conn.commit()
            conn.close()

    def _close_all_open(self, status, reason):
        """Force-close all open trades (EOD or emergency)."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        open_trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        now = ist_now()
        for t in open_trades:
            t = dict(t)
            # Use current pnl (already tracked) or calculate from entry
            pnl = t.get("pnl_rupees", 0) or 0
            exit_price = t.get("current_ltp", t["entry_price"])
            conn.execute("""
                UPDATE trades SET status=?, exit_price=?, exit_time=?, exit_reason=?,
                    pnl_pts=?, pnl_rupees=?
                WHERE id=? AND status='OPEN'
            """, (status, exit_price, now.isoformat(), reason,
                  round(exit_price - t["entry_price"], 2), pnl, t["id"]))
            print(f"[TRADE] {status}: {t['action']} {t['idx']} {t['strike']} — PnL: ₹{pnl:+,.0f}")

            self._trade_alerts.append({
                "type": "TRADE_EXIT",
                "time": now.strftime("%I:%M:%S %p"),
                "message": f"{'⏰' if status == 'EOD_CLOSE' else '🔒'} {status}: {t['action']} {t['idx']} {t['strike']}",
                "details": f"PnL: ₹{pnl:+,.0f} | {reason}",
            })
            self._trade_alerts = self._trade_alerts[-50:]
        conn.commit()
        conn.close()

    def check_position_alerts(self, chains, verdict_data):
        """Check if running positions need alerts based on new market data."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        open_trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        conn.close()

        position_alerts = []
        for trade in open_trades:
            t = dict(trade)
            idx = t["idx"]
            key = idx.lower()
            action = t["action"]

            v = verdict_data.get(key, {}) if verdict_data else {}
            if not v:
                continue

            # Check if verdict REVERSED direction
            v_action = v.get("action", "")
            if v_action and v_action != "NO TRADE" and v_action != action:
                # Opposite signal! Alert!
                position_alerts.append({
                    "tradeId": t["id"],
                    "idx": idx,
                    "action": action,
                    "strike": t["strike"],
                    "type": "REVERSAL_WARNING",
                    "severity": "CRITICAL",
                    "message": f"REVERSAL DETECTED: Your {action} {idx} {t['strike']} is OPEN but verdict now says {v_action} ({v.get('winProbability',0)}%). Consider early exit!",
                    "currentPnl": t["pnl_rupees"],
                    "suggestedAction": "EXIT" if t["pnl_pts"] < 0 else "TIGHTEN_SL",
                })

            # Check if probability dropped below 50%
            win_pct = v.get("winProbability", 0)
            if win_pct > 0:
                our_side = "bullPct" if "CE" in action else "bearPct"
                our_pct = v.get(our_side, 0)
                if our_pct < 45:
                    position_alerts.append({
                        "tradeId": t["id"],
                        "idx": idx,
                        "action": action,
                        "strike": t["strike"],
                        "type": "CONVICTION_DROP",
                        "severity": "HIGH",
                        "message": f"Conviction dropped: {action} probability now {our_pct}% (was {t['probability']}% at entry). Edge weakening.",
                        "currentPnl": t["pnl_rupees"],
                        "suggestedAction": "TIGHTEN_SL",
                    })

        # Store alerts
        if position_alerts:
            conn = _conn()
            for alert in position_alerts:
                conn.execute("UPDATE trades SET alerts=? WHERE id=?",
                             (alert["message"][:500], alert["tradeId"]))
            conn.commit()
            conn.close()

        return position_alerts

    def check_stop_hunts(self, chains):
        """Check SL_HIT trades for reversal (stop hunt detection)."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        sl_trades = conn.execute("""
            SELECT * FROM trades WHERE status='SL_HIT' AND reversal_detected=0
            AND sl_hit_time > ?
        """, ((ist_now() - timedelta(minutes=20)).isoformat(),)).fetchall()
        conn.close()

        for trade in sl_trades:
            t = dict(trade)
            idx = t["idx"]
            strike = t["strike"]
            action = t["action"]

            chain = chains.get(idx, {})
            strike_data = chain.get(strike, {})
            opt = "ce" if "CE" in action else "pe"
            current_ltp = strike_data.get(f"{opt}_ltp", 0)
            entry = t["entry_price"]
            sl = t["sl_price"]

            if current_ltp <= 0:
                continue

            # Stop hunt check: did price recover past entry after hitting SL?
            sl_move = entry - sl  # How far SL was from entry
            recovery = current_ltp - sl  # How much recovered from SL

            if recovery > sl_move * 0.5:
                # Price recovered >50% of the SL distance = stop hunt
                conn = _conn()
                conn.execute("""
                    UPDATE trades SET status='STOP_HUNTED', reversal_detected=1,
                        reversal_price=?, exit_reason=?
                    WHERE id=?
                """, (
                    current_ltp,
                    f"STOP HUNT: SL hit at {sl}, then reversed to {current_ltp:.1f} (recovered {recovery:.1f} pts). Institutional flush detected.",
                    t["id"]
                ))
                conn.commit()
                conn.close()
                print(f"[TRADE] STOP HUNT DETECTED: {action} {idx} {strike} — SL at {sl}, now at {current_ltp:.1f}")

    def should_enter_trade(self, idx, verdict_data):
        """SIMPLE entry logic. Trust the engines.
        Only 4 hard rules:
          1. Market hours (9:20-15:15)
          2. Probability >= 50% (engines voted yes — take the trade)
          3. Daily cap (6 trades max)
          4. No duplicate same direction (1 NIFTY CE at a time)

        NO MORE:
          - Spread requirements (engines already aggregate this)
          - Cooldown after loss (let SL handle losses, take next signal)
          - Profit guard (don't get scared after winning)
          - Loss streak pause (each trade independent)
          - Conviction bumps (trust the threshold)
        """
        if not verdict_data or verdict_data.get("action") == "NO TRADE":
            return False

        now = ist_now()

        # Rule 1: Market hours
        if not _is_trading_day():
            return False
        market_open = (now.hour == 9 and now.minute >= 20) or (10 <= now.hour <= 14) or (now.hour == 15 and now.minute <= 15)
        if not market_open:
            return False

        # Rule 2: Probability threshold (50% — engines already filter quality)
        win_pct = verdict_data.get("winProbability", 0)
        if win_pct < 50:
            return False

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        conn = _conn()

        # Rule 3: Daily trade cap
        today_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE entry_time > ?",
            (today_start,)
        ).fetchone()[0]
        if today_count >= MAX_DAILY_TRADES:
            conn.close()
            return False

        # Concurrent trade cap (high — basically never blocks)
        total_open = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='OPEN'"
        ).fetchone()[0]
        if total_open >= MAX_SIMULTANEOUS_TRADES:
            conn.close()
            return False

        # Rule 4: No duplicate same idx+action open
        action_str = verdict_data.get("action", "")
        dup_open = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='OPEN' AND idx=? AND action=?",
            (idx, action_str)
        ).fetchone()[0]
        conn.close()
        if dup_open > 0:
            return False

        # All checks passed — TAKE THE TRADE
        return True

    # ── PUBLIC API METHODS ──

    def get_position_alerts(self):
        """Get alerts for open positions."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, idx, action, strike, alerts, pnl_rupees, status FROM trades WHERE status='OPEN' AND alerts != '' AND alerts IS NOT NULL"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows if r["alerts"]]

    def get_open_trades(self):
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY entry_time DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_closed_trades(self, days=7):
        cutoff = (ist_now() - timedelta(days=days)).isoformat()
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE status!='OPEN' AND entry_time > ? ORDER BY exit_time DESC",
            (cutoff,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_trades_by_date(self, date_str):
        """Get all trades for a specific date (YYYY-MM-DD)."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE entry_time LIKE ? ORDER BY entry_time DESC",
            (f"{date_str}%",)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_monthly_report(self, year, month):
        """Get monthly stats + all trades for a given month."""
        prefix = f"{year}-{str(month).zfill(2)}"
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE entry_time LIKE ? ORDER BY entry_time DESC",
            (f"{prefix}%",)
        ).fetchall()
        conn.close()

        trades = [dict(r) for r in rows]
        if not trades:
            return {"month": prefix, "trades": [], "stats": {"total": 0}}

        closed = [t for t in trades if t["status"] != "OPEN"]
        wins = [t for t in trades if t["status"] in ("T1_HIT", "T2_HIT")]
        losses = [t for t in trades if t["status"] == "SL_HIT"]
        hunts = [t for t in trades if t["status"] == "STOP_HUNTED"]
        total_pnl = sum(t["pnl_rupees"] for t in closed)
        win_pnls = [(t["pnl_rupees"] or 0) for t in wins]
        loss_pnls = [(t["pnl_rupees"] or 0) for t in losses]

        # Daily breakdown
        daily = {}
        for t in trades:
            day = t["entry_time"][:10]
            if day not in daily:
                daily[day] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0}
            daily[day]["trades"] += 1
            if t["status"] in ("T1_HIT", "T2_HIT"):
                daily[day]["wins"] += 1
            elif t["status"] == "SL_HIT":
                daily[day]["losses"] += 1
            if t["status"] != "OPEN":
                daily[day]["pnl"] += t["pnl_rupees"]

        return {
            "month": prefix,
            "trades": trades,
            "daily": daily,
            "stats": {
                "total": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "stopHunts": len(hunts),
                "winRate": round(len(wins) / len(closed) * 100) if closed else 0,
                "totalPnl": round(total_pnl),
                "avgWin": round(sum(win_pnls) / len(win_pnls)) if win_pnls else 0,
                "avgLoss": round(sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0,
                "bestDay": max(daily.values(), key=lambda x: x["pnl"])["pnl"] if daily else 0,
                "worstDay": min(daily.values(), key=lambda x: x["pnl"])["pnl"] if daily else 0,
            },
        }

    def get_all_dates(self):
        """Get list of all dates that have trades."""
        conn = _conn()
        rows = conn.execute(
            "SELECT DISTINCT substr(entry_time, 1, 10) as d FROM trades ORDER BY d DESC"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]

    def get_stats(self, days=30):
        try:
            return self._get_stats_inner(days)
        except Exception as e:
            print(f"[TRADES] Stats error: {e}")
            return {"total": 0, "open": 0, "wins": 0, "losses": 0, "stopHunts": 0, "breakevens": 0,
                    "winRate": 0, "totalPnl": 0, "closedPnl": 0, "openPnl": 0, "totalProfit": 0,
                    "totalLoss": 0, "avgWin": 0, "avgLoss": 0, "bestTrade": 0, "worstTrade": 0,
                    "totalInvested": 0, "openInvested": 0, "openCurrentValue": 0,
                    "currentStreak": 0, "streakType": "",
                    "initialCapital": INITIAL_CAPITAL, "runningCapital": _get_running_capital(),
                    "capitalUsedPct": 0, "availableCapital": _get_running_capital(), "error": str(e)}

    def _get_stats_inner(self, days=30):
        cutoff = (ist_now() - timedelta(days=days)).isoformat()
        conn = _conn()
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT * FROM trades WHERE entry_time > ?", (cutoff,)
        ).fetchall()
        conn.close()
        all_trades = [dict(r) for r in rows]

        total = len(all_trades)
        if total == 0:
            return {"total": 0, "open": 0, "wins": 0, "losses": 0, "stopHunts": 0, "breakevens": 0,
                    "winRate": 0, "totalPnl": 0, "closedPnl": 0, "openPnl": 0,
                    "totalProfit": 0, "totalLoss": 0, "avgWin": 0, "avgLoss": 0,
                    "bestTrade": 0, "worstTrade": 0, "totalInvested": 0, "openInvested": 0,
                    "openCurrentValue": 0, "currentStreak": 0, "streakType": "",
                    "initialCapital": INITIAL_CAPITAL, "runningCapital": _get_running_capital(),
                    "capitalUsedPct": 0, "availableCapital": _get_running_capital()}

        open_trades = [t for t in all_trades if t["status"] == "OPEN"]
        wins = [t for t in all_trades if t["status"] in ("T1_HIT", "T2_HIT", "TRAIL_EXIT")]
        losses = [t for t in all_trades if t["status"] in ("SL_HIT",)]
        breakevens = [t for t in all_trades if t["status"] == "BREAKEVEN_EXIT"]
        hunts = [t for t in all_trades if t["status"] == "STOP_HUNTED"]
        closed = [t for t in all_trades if t["status"] != "OPEN"]

        # Capital calculations — safe with None/0 values
        total_invested = sum((t["entry_price"] or 0) * (t["qty"] or 0) for t in all_trades)
        open_invested = sum((t["entry_price"] or 0) * (t["qty"] or 0) for t in open_trades)
        open_current_value = sum((t["current_ltp"] or t["entry_price"] or 0) * (t["qty"] or 0) for t in open_trades)
        open_pnl = round(open_current_value - open_invested)

        # Closed PnL
        closed_pnl = sum((t["pnl_rupees"] or 0) for t in closed)
        total_pnl = round(closed_pnl + open_pnl)

        # Loss tracking
        total_loss = sum((t["pnl_rupees"] or 0) for t in closed if (t["pnl_rupees"] or 0) < 0)
        total_profit = sum((t["pnl_rupees"] or 0) for t in closed if (t["pnl_rupees"] or 0) > 0)

        win_pnls = [(t["pnl_rupees"] or 0) for t in wins]
        loss_pnls = [(t["pnl_rupees"] or 0) for t in losses]

        closed_count = len(closed)
        win_rate = round(len(wins) / closed_count * 100) if closed_count > 0 else 0

        # Streak
        streak = 0
        streak_type = ""
        for t in sorted(closed, key=lambda x: x.get("exit_time") or "", reverse=True):
            is_win = t["status"] in ("T1_HIT", "T2_HIT", "TRAIL_EXIT")
            if streak == 0:
                streak_type = "WIN" if is_win else "LOSS"
                streak = 1
            elif (streak_type == "WIN" and is_win) or (streak_type == "LOSS" and not is_win):
                streak += 1
            else:
                break

        return {
            "total": total,
            "open": len(open_trades),
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(breakevens),
            "stopHunts": len(hunts),
            "winRate": win_rate,
            # Capital
            "totalInvested": round(total_invested),
            "openInvested": round(open_invested),
            "openCurrentValue": round(open_current_value),
            "openPnl": open_pnl,
            # PnL
            "closedPnl": round(closed_pnl),
            "totalPnl": total_pnl,
            "totalProfit": round(total_profit),
            "totalLoss": round(total_loss),
            "avgWin": round(sum(win_pnls) / len(win_pnls)) if win_pnls else 0,
            "avgLoss": round(sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0,
            "bestTrade": round(max(win_pnls)) if win_pnls else 0,
            "worstTrade": round(min(loss_pnls)) if loss_pnls else 0,
            # Streak
            "currentStreak": streak,
            "streakType": streak_type,
            # Capital (running = initial + realized P&L)
            "initialCapital": INITIAL_CAPITAL,
            "runningCapital": round(_get_running_capital()),
            "capitalUsedPct": round(open_invested / max(_get_running_capital(), 1) * 100),
            "availableCapital": round(_get_running_capital() - open_invested),
        }

    def get_trade_alerts(self):
        """Get recent trade entry/exit alerts for frontend notifications."""
        return list(reversed(self._trade_alerts))

    def get_stop_hunts(self):
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='STOP_HUNTED' ORDER BY exit_time DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
