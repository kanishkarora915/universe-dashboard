"""
Times Tab Real Data Engine — Chronological event timeline with maths + logic.

Aggregates events from multiple sources:
  - oi_shift_detector → wall shifts
  - rejection_engine.hidden_events → mass buy/write/cover/unwind
  - daily_training → seller behavior, time window perf
  - smart_autopsy_mind → day pattern matches

Each event gets:
  - WHAT (event type)
  - WHEN (exact time)
  - WHY (smart money logic)
  - MATH (numbers backing it)
  - TRAP detection
  - TODAY indication
  - NEXT DAY verdict
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent


def ist_now():
    return datetime.now(IST)


def get_today_events(idx="NIFTY"):
    """Aggregate ALL events from today across systems, sorted by time."""
    events = []
    today = ist_now().strftime("%Y-%m-%d")

    # 1. OI Shift events (oi_shifts.db)
    events.extend(_fetch_oi_shifts(idx))

    # 2. Hidden activity events (rejection_zones.db)
    events.extend(_fetch_hidden_events(idx, today))

    # 3. Volatility regime changes (volatility.db)
    events.extend(_fetch_volatility_changes(today))

    # 4. Trade outcomes (trades.db + scalper_trades.db)
    events.extend(_fetch_trade_events(today))

    # 5. Trinity regime transitions
    events.extend(_fetch_trinity_regimes(today))

    # Sort chronologically (oldest first)
    events.sort(key=lambda e: e.get("ts_ms", 0))

    # Add narrative (WHY) to each event
    for e in events:
        e["math"] = _compute_math(e)
        e["why"] = _explain_why(e)
        e["trap"] = _detect_trap(e)

    return events


def _fetch_oi_shifts(idx):
    db = _data_dir / "oi_shifts.db"
    if not db.exists():
        return []
    today = ist_now().strftime("%Y-%m-%d")
    out = []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM shift_events
            WHERE idx=? AND date(ts) = ?
            ORDER BY ts ASC
        """, (idx, today)).fetchall()
        conn.close()

        for r in rows:
            r = dict(r)
            ts = datetime.fromisoformat(r["ts"]) if r.get("ts") else ist_now()
            out.append({
                "ts": r["ts"],
                "ts_ms": int(ts.timestamp() * 1000),
                "time_str": ts.strftime("%H:%M"),
                "type": "OI_WALL_SHIFT",
                "title": f"⚡ WALL SHIFT — {r['side']} {r['from_strike']} → {r['to_strike']}",
                "side": r["side"],
                "from_strike": r["from_strike"],
                "to_strike": r["to_strike"],
                "from_oi": r.get("from_oi", 0),
                "to_oi": r.get("to_oi", 0),
                "magnitude_pct": r.get("shift_magnitude_pct", 0),
                "description": r.get("description", ""),
            })
    except Exception as e:
        print(f"[TIMES] OI shifts fetch error: {e}")
    return out


def _fetch_hidden_events(idx, today):
    db = _data_dir / "rejection_zones.db"
    if not db.exists():
        return []
    out = []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM hidden_events
            WHERE idx=? AND substr(time, 1, 10) = ?
            ORDER BY time ASC
        """, (idx, today)).fetchall()
        conn.close()

        for r in rows:
            r = dict(r)
            time_str = (r.get("time") or "").split(" ")[1] if " " in (r.get("time") or "") else ""
            try:
                # time format from rejection_engine: "YYYY-MM-DD HH:MM"
                ts_full = r.get("time", "")
                if " " in ts_full:
                    date_p, time_p = ts_full.split(" ")
                    ts = datetime.strptime(f"{date_p} {time_p}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
                    ts_ms = int(ts.timestamp() * 1000)
                else:
                    ts_ms = 0
            except Exception:
                ts_ms = 0

            event_emoji = {
                "MASS BUY ENTRY": "🟢",
                "MASS WRITE": "🔴",
                "MASS COVER": "🟢",
                "MASS UNWIND": "🔴",
                "STEALTH BUILD": "🤫",
                "STEALTH UNWIND": "👻",
            }.get(r.get("event_type", ""), "⚡")

            out.append({
                "ts": r.get("time"),
                "ts_ms": ts_ms,
                "time_str": time_str,
                "type": f"HIDDEN_{r.get('event_type', '').replace(' ', '_')}",
                "title": f"{event_emoji} {r.get('event_type', '')} — {r.get('side', '')} {r.get('strike', '')}",
                "side": r.get("side"),
                "strike": r.get("strike"),
                "lots": r.get("lots_moved", 0),
                "oi_delta": r.get("oi_delta", 0),
                "premium_delta": r.get("premium_delta", 0),
                "description": r.get("description", ""),
            })
    except Exception as e:
        print(f"[TIMES] hidden events fetch error: {e}")
    return out


def _fetch_volatility_changes(today):
    db = _data_dir / "volatility.db"
    if not db.exists():
        return []
    out = []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM regime_log
            WHERE date(ts) = ?
            ORDER BY ts ASC
        """, (today,)).fetchall()
        conn.close()

        # Track regime changes only (not every 30s log)
        last_regime = None
        for r in rows:
            r = dict(r)
            if r["regime"] == last_regime:
                continue
            last_regime = r["regime"]
            ts = datetime.fromisoformat(r["ts"])
            out.append({
                "ts": r["ts"],
                "ts_ms": int(ts.timestamp() * 1000),
                "time_str": ts.strftime("%H:%M"),
                "type": "REGIME_CHANGE",
                "title": f"🌊 REGIME → {r['regime']}",
                "regime": r["regime"],
                "vix": r.get("vix"),
                "atr_ratio": r.get("atr_ratio"),
                "time_window": r.get("time_window"),
                "notes": r.get("notes", ""),
            })
    except Exception as e:
        print(f"[TIMES] volatility fetch error: {e}")
    return out


def _fetch_trade_events(today):
    out = []
    for db_name, table in [("trades.db", "trades"), ("scalper_trades.db", "scalper_trades")]:
        db_path = _data_dir / db_name
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"""
                SELECT * FROM {table}
                WHERE date(entry_time) = ? AND status != 'OPEN'
                ORDER BY entry_time ASC
            """, (today,)).fetchall()
            conn.close()

            for r in rows:
                r = dict(r)
                try:
                    ts = datetime.fromisoformat(r["entry_time"])
                    ts_ms = int(ts.timestamp() * 1000)
                except Exception:
                    ts_ms = 0
                source = "MAIN" if table == "trades" else "SCALPER"
                pnl = r.get("pnl_rupees", 0) or 0
                emoji = "✅" if pnl > 0 else "❌" if pnl < 0 else "⚪"
                out.append({
                    "ts": r.get("entry_time"),
                    "ts_ms": ts_ms,
                    "time_str": ts.strftime("%H:%M") if ts_ms else "",
                    "type": f"TRADE_{source}",
                    "title": f"{emoji} {source} {r.get('action', '')} {r.get('strike', '')} → {r.get('status', '')} (₹{pnl:+,.0f})",
                    "action": r.get("action"),
                    "strike": r.get("strike"),
                    "status": r.get("status"),
                    "entry_price": r.get("entry_price"),
                    "exit_price": r.get("exit_price"),
                    "pnl": pnl,
                    "exit_reason": r.get("exit_reason"),
                })
        except Exception as e:
            print(f"[TIMES] {db_name} fetch error: {e}")
    return out


def _fetch_trinity_regimes(today):
    db = _data_dir / "trinity.db"
    if not db.exists():
        return []
    out = []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # Track regime transitions (only when regime changes)
        rows = conn.execute("""
            SELECT ts, regime, confidence FROM trinity_ticks
            WHERE date(datetime(ts/1000, 'unixepoch', '+05:30')) = ?
            AND regime IS NOT NULL AND regime NOT IN ('UNKNOWN', 'TRANSITIONING')
            ORDER BY ts ASC
        """, (today,)).fetchall()
        conn.close()

        last = None
        for r in rows:
            r = dict(r)
            if r["regime"] == last:
                continue
            last = r["regime"]
            ts = datetime.fromtimestamp(r["ts"] / 1000, tz=IST)
            out.append({
                "ts": ts.isoformat(),
                "ts_ms": r["ts"],
                "time_str": ts.strftime("%H:%M"),
                "type": "TRINITY_REGIME",
                "title": f"🎯 TRINITY → {r['regime']}",
                "regime": r["regime"],
                "confidence": r.get("confidence"),
            })
    except Exception as e:
        print(f"[TIMES] trinity fetch error: {e}")
    return out


# ─────────────────────────────────────────────────────────
# Math + Logic Generators
# ─────────────────────────────────────────────────────────

def _compute_math(event):
    """Compute the maths backing this event."""
    t = event.get("type", "")

    if t == "OI_WALL_SHIFT":
        from_oi = event.get("from_oi", 0) / 100000
        to_oi = event.get("to_oi", 0) / 100000
        diff = abs(to_oi - from_oi)
        return f"{diff:.1f}L OI shifted from {event.get('from_strike')} → {event.get('to_strike')}. New strike has {to_oi:.1f}L (was {from_oi:.1f}L)."

    if t.startswith("HIDDEN_"):
        lots = event.get("lots", 0)
        oi_delta = event.get("oi_delta", 0) / 100000
        prem_delta = event.get("premium_delta", 0)
        return f"{lots} lots moved ({oi_delta:+.2f}L OI). Premium changed ₹{prem_delta:+.2f}."

    if t == "REGIME_CHANGE":
        return f"VIX {event.get('vix', 0):.1f}, ATR {event.get('atr_ratio', 1):.1f}x avg"

    if t.startswith("TRADE_"):
        entry = event.get("entry_price", 0)
        exit_p = event.get("exit_price", 0)
        if entry and exit_p:
            change = ((exit_p - entry) / entry * 100)
            return f"Entry ₹{entry} → Exit ₹{exit_p} ({change:+.1f}%)"

    if t == "TRINITY_REGIME":
        return f"Trinity confidence {event.get('confidence', 0):.0f}%"

    return ""


def _explain_why(event):
    """Generate the WHY (smart money logic) for this event."""
    t = event.get("type", "")

    if t == "OI_WALL_SHIFT":
        side = event.get("side")
        f_strike = event.get("from_strike", 0)
        t_strike = event.get("to_strike", 0)
        moved_up = t_strike > f_strike
        if side == "CE":
            if moved_up:
                return "Sellers RAISED resistance ceiling — they expect price to test higher levels (BULLISH for BUY CE)"
            else:
                return "Sellers LOWERED resistance — they expect price stays below new wall (BEARISH for BUY CE)"
        else:  # PE
            if moved_up:
                return "Sellers RAISED support floor — they expect downside limit higher (BULLISH for BUY CE)"
            else:
                return "Sellers LOWERED support — floor breaking, downside expanding (BEARISH for BUY CE)"

    if t == "HIDDEN_MASS_BUY_ENTRY":
        side = event.get("side")
        if side == "CE":
            return "Big institution BUYING CE — bullish directional bet on this strike"
        return "Big institution BUYING PE — bearish directional bet on this strike"

    if t == "HIDDEN_MASS_WRITE":
        side = event.get("side")
        if side == "CE":
            return "Sellers WRITING CE wall — building resistance, expecting price stays below"
        return "Sellers WRITING PE wall — building support, expecting price stays above"

    if t == "HIDDEN_MASS_COVER":
        side = event.get("side")
        if side == "CE":
            return "CE writers EXITING — resistance breaking, smart money expects rally"
        return "PE writers EXITING — support breaking, smart money expects drop"

    if t == "HIDDEN_MASS_UNWIND":
        return "Buyers GIVING UP — directional bet failed, position closed"

    if t == "HIDDEN_STEALTH_BUILD":
        side = event.get("side")
        return f"Stealth {side} positioning — institution loading without moving premium (planning big move)"

    if t == "REGIME_CHANGE":
        regime = event.get("regime", "")
        if "EXTREME" in regime:
            return "Panic conditions — all systems pause, wait for stabilization"
        if "EXPIRY" in regime:
            return "Expiry day theta crush — reduce size, tighten SL, scalper only"
        if "HIGH-VOL" in regime:
            return "Volatility expansion — wider SL, larger targets, smaller qty"
        if regime == "NORMAL":
            return "Normal conditions — full system active, default thresholds"

    if t == "TRINITY_REGIME":
        regime = event.get("regime", "")
        if "REAL_RALLY" in regime:
            return "Spot+Future+Synthetic all aligned UP — real bullish move (high conf BUY CE)"
        if "REAL_CRASH" in regime:
            return "All 3 streams aligned DOWN — real bearish move (high conf BUY PE)"
        if "BULL_TRAP" in regime:
            return "Spot rising but future shrinking + synthetic lagging — TRAP, retail buying CE will fail"
        if "BEAR_TRAP" in regime:
            return "Spot falling but future expanding + synthetic leading — TRAP, retail buying PE will fail"

    if t.startswith("TRADE_"):
        status = event.get("status", "")
        action = event.get("action", "")
        if status in ("T1_HIT", "T2_HIT"):
            return f"Trade reached target — {action} signal was correct"
        if status == "TRAIL_EXIT":
            return f"Trail SL locked profit — momentum faded after move"
        if status == "SL_HIT":
            return f"Trade hit SL — {action} signal was wrong (engine miscalibration)"
        if status == "REVERSAL_EXIT":
            return f"Trade reversed within 10 min — quick exit limited damage"
        if status == "MANUAL_EXIT":
            return f"User manually exited — discretionary decision"
        if status == "TIMEOUT_EXIT":
            return f"Max hold time reached — exit at current"

    return ""


def _detect_trap(event):
    """Detect if this event is a retail trap setup."""
    t = event.get("type", "")

    if t == "OI_WALL_SHIFT":
        side = event.get("side")
        f_strike = event.get("from_strike", 0)
        t_strike = event.get("to_strike", 0)
        moved_up = t_strike > f_strike
        if side == "CE" and moved_up:
            return f"Retail PE buyers at {f_strike} stuck — wall already moved to {t_strike}"
        if side == "CE" and not moved_up:
            return f"Retail CE buyers above {t_strike} stuck — sellers capping there now"
        if side == "PE" and not moved_up:
            return f"Retail thinking floor at {f_strike} but actually broken to {t_strike}"

    if t == "HIDDEN_MASS_COVER":
        side = event.get("side")
        if side == "CE":
            return "Retail PE buyers will get squeezed — covering = upside coming"
        else:
            return "Retail CE buyers will get squeezed — covering = downside coming"

    if t == "HIDDEN_STEALTH_BUILD":
        return "Most retail unaware — institution positioning before big move"

    if t == "TRINITY_REGIME" and "TRAP" in (event.get("regime", "")):
        return event.get("regime", "")

    return None


# ─────────────────────────────────────────────────────────
# Today's Story + Tomorrow Indication
# ─────────────────────────────────────────────────────────

def get_today_story(idx="NIFTY"):
    """Combined narrative of today's market story + tomorrow indication."""
    events = get_today_events(idx)

    bull_events = sum(1 for e in events if "BULL" in (e.get("regime", "") or "")
                      or e.get("type") == "HIDDEN_MASS_COVER" and e.get("side") == "CE"
                      or e.get("type") == "HIDDEN_MASS_WRITE" and e.get("side") == "PE")
    bear_events = sum(1 for e in events if "BEAR" in (e.get("regime", "") or "")
                      or e.get("type") == "HIDDEN_MASS_COVER" and e.get("side") == "PE"
                      or e.get("type") == "HIDDEN_MASS_WRITE" and e.get("side") == "CE")

    # Today bias
    if bull_events > bear_events * 1.5:
        bias = "BULLISH"
    elif bear_events > bull_events * 1.5:
        bias = "BEARISH"
    else:
        bias = "MIXED"

    # Trade outcomes
    trade_events = [e for e in events if e.get("type", "").startswith("TRADE_")]
    wins = sum(1 for e in trade_events if e.get("pnl", 0) > 0)
    losses = sum(1 for e in trade_events if e.get("pnl", 0) < 0)
    net_pnl = sum(e.get("pnl", 0) for e in trade_events)

    # Wall shifts count (chaos indicator)
    wall_shifts = sum(1 for e in events if e.get("type") == "OI_WALL_SHIFT")

    return {
        "date": ist_now().strftime("%Y-%m-%d"),
        "idx": idx,
        "total_events": len(events),
        "bull_events": bull_events,
        "bear_events": bear_events,
        "bias": bias,
        "wall_shifts_count": wall_shifts,
        "trades_today": len(trade_events),
        "wins": wins,
        "losses": losses,
        "net_pnl": net_pnl,
        "events_recent": events[-15:],  # last 15 events
        "events_all": events,
    }
