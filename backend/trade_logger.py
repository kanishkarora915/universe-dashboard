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
MAX_RISK_PER_TRADE_PCT = 1.5  # Risk 1.5% of running capital per trade (₹15K on ₹10L)
MAX_DAILY_LOSS_PCT = 5  # Stop trading after 5% daily loss
MAX_SIMULTANEOUS_TRADES = 1  # Only 1 trade at a time (across all indices)

# NSE Holidays 2026 (add more as needed)
NSE_HOLIDAYS = {
    "2026-01-26", "2026-02-19", "2026-03-10", "2026-03-17",
    "2026-03-30", "2026-03-31", "2026-04-02", "2026-04-14",
    "2026-05-01", "2026-05-25", "2026-06-19", "2026-07-07",
    "2026-07-10", "2026-08-14", "2026-08-15", "2026-08-28",
    "2026-09-18", "2026-10-02", "2026-10-20", "2026-10-21",
    "2026-10-23", "2026-11-04", "2026-11-19", "2026-12-25",
}

LOT_CONFIG = {
    "NIFTY": {"lot_size": 65},
    "BANKNIFTY": {"lot_size": 30},
}


def _is_trading_day():
    """Check if today is a valid trading day (not weekend/holiday)."""
    now = ist_now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if now.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
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


def calc_position_size(idx, entry_price, sl_price=0):
    """Risk-based position sizing.

    Instead of fixed lots, calculate based on:
    - Running capital (reduces after losses)
    - Max risk per trade = 1.5% of running capital
    - SL distance determines lot count
    """
    cfg = LOT_CONFIG.get(idx, LOT_CONFIG["NIFTY"])
    lot_size = cfg["lot_size"]

    if entry_price <= 0:
        return 1, lot_size, lot_size

    running_capital = _get_running_capital()
    max_risk = running_capital * MAX_RISK_PER_TRADE_PCT / 100  # e.g., ₹15,000 on ₹10L

    # Risk per unit = entry - SL
    if sl_price > 0 and sl_price < entry_price:
        risk_per_unit = entry_price - sl_price
    else:
        risk_per_unit = entry_price * 0.15  # Default 15% SL

    risk_per_unit = max(risk_per_unit, 1)  # Prevent division by zero

    # Max qty we can afford based on risk
    max_qty_by_risk = int(max_risk / risk_per_unit)

    # Also cap by capital (can't buy more than capital allows)
    max_qty_by_capital = int(running_capital * 0.5 / max(entry_price, 1))  # 50% max capital usage

    # Take the stricter of the two
    max_qty = min(max_qty_by_risk, max_qty_by_capital)

    # Convert to lots
    lots = max(1, min(max_qty // lot_size, 20))  # Min 1 lot, max 20 lots
    qty = lots * lot_size

    return lots, lot_size, qty


def ist_now():
    return datetime.now(IST)


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


def _conn():
    return sqlite3.connect(DB_PATH)


class TradeManager:
    def __init__(self):
        self._last_verdict_check = 0
        self._last_sl_check = 0
        self._cached_verdict = {}  # Cached verdict from last check
        self._sl_override_count = {}  # {trade_id: count} — max 3 overrides per trade

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

    def log_trade(self, idx, action, strike, entry_price, probability, source="verdict", expiry="",
                  straddle=0, big_wall=0):
        """Log a new trade entry with smart SL/targets."""
        if entry_price <= 0:
            return None

        # Smart SL: 20% of entry for cheap premiums, 15% for expensive, minimum ₹5 drop
        if entry_price < 100:
            sl_price = round(entry_price * 0.80)  # 20% SL for cheap options
        else:
            sl_price = round(entry_price * 0.85)  # 15% SL for expensive options
        sl_price = max(sl_price, round(entry_price - max(straddle * 0.15, 5)))
        sl_price = min(sl_price, round(entry_price * 0.85))  # Never tighter than 15%

        # Position size based on risk (uses running capital, not fixed ₹10L)
        lots, lot_size, qty = calc_position_size(idx, entry_price, sl_price)

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
        print(f"[TRADE] NEW: {action} {idx} {strike} @ {entry_price} | SL: {sl_price} | T1: {t1_price} | T2: {t2_price} | {lots}L x {lot_size} = {qty} qty | Capital: ₹{capital_used:,} | Prob: {probability}%")
        return trade_id

    def check_and_update(self, chains, prices, spot_tokens, token_to_info):
        """Smart trade management: breakeven, trailing SL, alerts, early exit."""
        conn = _conn()
        conn.row_factory = sqlite3.Row
        open_trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        conn.close()

        for trade in open_trades:
            t = dict(trade)
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

            # STAGE 1: BREAKEVEN — activate when 15% profit from entry
            if not breakeven_active and profit_pct >= 15:
                breakeven_active = 1
                new_sl = entry
                trail_level = "BREAKEVEN"
                alerts_list.append(f"BREAKEVEN activated at +{profit_pct:.0f}% — SL moved to entry ₹{entry}")
                print(f"[TRADE] BREAKEVEN: {action} {idx} {strike} — SL moved to entry ₹{entry} (was ₹{sl})")

            # STAGE 2: TRAILING SL — after breakeven, trail SL to lock profits
            if breakeven_active:
                # Trail at 60% of peak profit (keep 60% of max gain)
                trail_from_peak = round(peak - (peak - entry) * 0.40)  # Lock 60% of peak gain
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

            # Check T2 (full target)
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
                final_pnl_pts = round(exit_price - entry, 2)
                final_pnl_rupees = round(final_pnl_pts * t["qty"], 2)
                conn.execute("""
                    UPDATE trades SET current_ltp=?, peak_ltp=?, pnl_pts=?, pnl_rupees=?,
                        sl_price=?, breakeven_active=?, trailing_active=?, trail_level=?,
                        status=?, exit_price=?, exit_time=?, exit_reason=?, alerts=?
                    WHERE id=?
                """, (current_ltp, peak, final_pnl_pts, final_pnl_rupees,
                      new_sl, breakeven_active, trailing_active, trail_level,
                      new_status, exit_price, ist_now().isoformat(), exit_reason, alerts_str, t["id"]))
                print(f"[TRADE] CLOSED: {action} {idx} {strike} — {new_status} — PnL: {final_pnl_pts} pts (₹{final_pnl_rupees:+,.0f})")

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
        """Smart entry with full validation — weekday, holiday, capital, risk."""
        if not verdict_data or verdict_data.get("action") == "NO TRADE":
            return False

        now = ist_now()

        # ── CALENDAR CHECK: No weekends, no holidays ──
        if not _is_trading_day():
            return False

        # ── MARKET HOURS: Only 9:20 AM to 3:15 PM (not 3:20, tighter) ──
        market_open = (now.hour == 9 and now.minute >= 20) or (10 <= now.hour <= 14) or (now.hour == 15 and now.minute <= 15)
        if not market_open:
            return False

        # ── EXPIRY DAY: No new trades after 2 PM on Thursday ──
        if now.weekday() == 3 and now.hour >= 14:
            return False

        win_pct = verdict_data.get("winProbability", 0)
        bull_pct = verdict_data.get("bullPct", 50)
        bear_pct = verdict_data.get("bearPct", 50)
        spread = abs(bull_pct - bear_pct)

        # ── QUALITY: Need HIGH confidence only (75%+) ──
        if win_pct < 70:
            return False
        if spread < 25:
            return False  # Too close = market confused

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        conn = _conn()

        # ── GLOBAL POSITION LIMIT: Max 1 open trade across ALL indices ──
        total_open = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='OPEN'"
        ).fetchone()[0]
        if total_open >= MAX_SIMULTANEOUS_TRADES:
            conn.close()
            return False

        # ── DAILY LOSS LIMIT: Count ALL closed losing trades, not just SL_HIT ──
        today_loss = conn.execute(
            "SELECT COALESCE(SUM(pnl_rupees), 0) FROM trades WHERE entry_time > ? AND status != 'OPEN' AND pnl_rupees < 0",
            (today_start,)
        ).fetchone()[0] or 0
        running_capital = _get_running_capital()
        if today_loss < -(running_capital * MAX_DAILY_LOSS_PCT / 100):
            conn.close()
            return False

        # ── LOSS COOLDOWN: After any loss, wait 45 min + need 75%+ ──
        last_loss = conn.execute(
            "SELECT exit_time FROM trades WHERE entry_time > ? AND status != 'OPEN' AND pnl_rupees < 0 ORDER BY exit_time DESC LIMIT 1",
            (today_start,)
        ).fetchone()
        if last_loss and last_loss[0]:
            try:
                loss_time = datetime.fromisoformat(last_loss[0])
                time_since = (now - loss_time).total_seconds()
                if time_since < 2700:  # 45 min cooldown
                    conn.close()
                    return False
                if time_since < 5400 and win_pct < 75:  # 90 min: need 75%+
                    conn.close()
                    return False
            except Exception:
                pass

        # ── DAILY LOSS COUNT: After 2 losses today (any exit reason), stop ──
        today_loss_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE entry_time > ? AND status != 'OPEN' AND pnl_rupees < 0",
            (today_start,)
        ).fetchone()[0]
        if today_loss_count >= 2:
            conn.close()
            return False  # Hard stop after 2 losses
        if today_loss_count >= 1 and win_pct < 75:
            conn.close()
            return False  # After 1 loss, need 75%+

        conn.close()
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

    def get_stop_hunts(self):
        conn = _conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='STOP_HUNTED' ORDER BY exit_time DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
