"""
trade_journal — every decision logged. System self-awareness.

WHY THIS MODULE EXISTS

User vision 2026-05-21:
  "System ek bot ki tarah kaam kre. Use pata ho ki main kya kara hu."
  (System should work like a bot — IT should know what it's doing/done.)

Currently when a trade happens, we know WHAT happened but not WHY in
structured form. Reasons are scattered:
  • Entry reason in scalper trades.entry_reasoning (text blob)
  • Exit reason in trades.exit_reason (text blob)
  • SL changes printed to logs (not queryable)
  • No clear "decision lineage" per trade

This module captures EVERY decision as a structured event:
  • Entry: which engines, probability, regime, gates passed
  • SL move: old → new, why
  • Partial exit: % booked, why
  • Pyramid add: condition that triggered
  • Final exit: trigger + state at exit

Each event has:
  • timestamp
  • trade_id
  • tab (MAIN/SCALPER)
  • event_type (ENTRY / SL_UPDATE / PARTIAL_EXIT / PYRAMID / EXIT / GATE_BLOCKED)
  • reason (human-readable)
  • context (JSON: all relevant numbers)

QUERYABLE VIA API

  GET /api/journal/trade/{trade_id}     — full event timeline
  GET /api/journal/recent?n=50          — last 50 events
  GET /api/journal/explain/{trade_id}   — natural-language explanation

GOAL: When you ask "why did trade #142 happen?", system has the answer.
"""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
JOURNAL_DB = _DATA_DIR / "trade_journal.db"


# Event types
EVENT_ENTRY = "ENTRY"
EVENT_SL_UPDATE = "SL_UPDATE"
EVENT_PARTIAL_EXIT = "PARTIAL_EXIT"
EVENT_PYRAMID_ADD = "PYRAMID_ADD"
EVENT_EXIT = "EXIT"
EVENT_GATE_BLOCKED = "GATE_BLOCKED"
EVENT_REGIME_CHANGE = "REGIME_CHANGE"
EVENT_ALERT = "ALERT"


def _init_db():
    """Initialize journal DB schema."""
    conn = sqlite3.connect(str(JOURNAL_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            trade_id INTEGER,
            tab TEXT,
            event_type TEXT NOT NULL,
            reason TEXT,
            context_json TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_trade ON journal_events(trade_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal_events(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_type ON journal_events(event_type)")
    conn.commit()
    conn.close()


def log_event(
    *,
    event_type: str,
    trade_id: Optional[int] = None,
    tab: Optional[str] = None,
    reason: str = "",
    context: Optional[Dict[str, Any]] = None,
):
    """Log a single decision/event. Safe to call frequently."""
    try:
        _init_db()
        ts = datetime.now(IST).isoformat()
        ctx_json = json.dumps(context or {}, default=str)
        conn = sqlite3.connect(str(JOURNAL_DB))
        conn.execute(
            "INSERT INTO journal_events (timestamp, trade_id, tab, event_type, reason, context_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, trade_id, tab, event_type, reason, ctx_json),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        # Journal failure must NEVER block trading
        print(f"[JOURNAL] log failed (ignored): {e}")


def log_entry(
    *,
    trade_id: int,
    tab: str,
    idx: str,
    action: str,
    strike: int,
    entry_price: float,
    qty: int,
    probability: int,
    sl_price: float,
    t1_price: float,
    t2_price: float,
    source: str,
    reasoning: str = "",
    engine_votes: Optional[Dict] = None,
    gates_passed: Optional[List[str]] = None,
    regime_state: Optional[str] = None,
):
    """Log trade entry with full context."""
    log_event(
        event_type=EVENT_ENTRY,
        trade_id=trade_id,
        tab=tab,
        reason=f"{action} {idx} {strike} @ ₹{entry_price} | {reasoning[:200]}",
        context={
            "idx": idx,
            "action": action,
            "strike": strike,
            "entry_price": entry_price,
            "qty": qty,
            "probability": probability,
            "sl_price": sl_price,
            "t1_price": t1_price,
            "t2_price": t2_price,
            "source": source,
            "reasoning": reasoning,
            "engine_votes": engine_votes,
            "gates_passed": gates_passed,
            "regime_state": regime_state,
        },
    )


def log_sl_update(
    *,
    trade_id: int,
    tab: str,
    old_sl: float,
    new_sl: float,
    reason: str,
    current_price: float,
    peak_price: Optional[float] = None,
    profit_pct: Optional[float] = None,
    method: str = "trail",
):
    """Log SL change."""
    log_event(
        event_type=EVENT_SL_UPDATE,
        trade_id=trade_id,
        tab=tab,
        reason=f"SL ₹{old_sl} → ₹{new_sl} ({method}): {reason[:150]}",
        context={
            "old_sl": old_sl,
            "new_sl": new_sl,
            "delta": round(new_sl - old_sl, 2),
            "current_price": current_price,
            "peak_price": peak_price,
            "profit_pct": profit_pct,
            "method": method,
        },
    )


def log_partial_exit(
    *,
    trade_id: int,
    tab: str,
    qty_booked: int,
    qty_remaining: int,
    price: float,
    profit_pct: float,
    reason: str,
):
    """Log partial profit booking."""
    log_event(
        event_type=EVENT_PARTIAL_EXIT,
        trade_id=trade_id,
        tab=tab,
        reason=f"Booked {qty_booked} qty @ ₹{price} ({profit_pct:+.1f}%): {reason[:150]}",
        context={
            "qty_booked": qty_booked,
            "qty_remaining": qty_remaining,
            "price": price,
            "profit_pct": profit_pct,
        },
    )


def log_pyramid_add(
    *,
    trade_id: int,
    tab: str,
    qty_added: int,
    add_price: float,
    new_avg_entry: float,
    reason: str,
):
    """Log pyramid position add."""
    log_event(
        event_type=EVENT_PYRAMID_ADD,
        trade_id=trade_id,
        tab=tab,
        reason=f"Added {qty_added} qty @ ₹{add_price}, new avg ₹{new_avg_entry}: {reason[:150]}",
        context={
            "qty_added": qty_added,
            "add_price": add_price,
            "new_avg_entry": new_avg_entry,
        },
    )


def log_exit(
    *,
    trade_id: int,
    tab: str,
    exit_price: float,
    exit_reason: str,
    status: str,
    pnl_rupees: float,
    pnl_pct: float,
    peak_price: Optional[float] = None,
):
    """Log final exit."""
    gave_back = None
    if peak_price and peak_price > 0:
        gave_back = round((peak_price - exit_price) / peak_price * 100, 2)
    log_event(
        event_type=EVENT_EXIT,
        trade_id=trade_id,
        tab=tab,
        reason=f"EXIT @ ₹{exit_price} ({status}) P&L ₹{pnl_rupees:+,.0f} ({pnl_pct:+.1f}%)",
        context={
            "exit_price": exit_price,
            "status": status,
            "pnl_rupees": pnl_rupees,
            "pnl_pct": pnl_pct,
            "peak_price": peak_price,
            "gave_back_from_peak_pct": gave_back,
            "exit_reason_raw": exit_reason,
        },
    )


def log_gate_blocked(
    *,
    tab: str,
    gate_name: str,
    idx: str,
    action: str,
    reason: str,
    extra: Optional[Dict] = None,
):
    """Log a gate blocking an entry (so we know what we skipped)."""
    log_event(
        event_type=EVENT_GATE_BLOCKED,
        trade_id=None,  # no trade was created
        tab=tab,
        reason=f"{gate_name} blocked {action} {idx}: {reason[:150]}",
        context={
            "gate_name": gate_name,
            "idx": idx,
            "action": action,
            "extra": extra or {},
        },
    )


def log_alert(
    *,
    severity: str,
    message: str,
    source: str,
    context: Optional[Dict] = None,
):
    """Log a system alert."""
    log_event(
        event_type=EVENT_ALERT,
        trade_id=None,
        tab=None,
        reason=f"[{severity}] {source}: {message[:200]}",
        context={
            "severity": severity,
            "source": source,
            "extra": context or {},
        },
    )


# ── Query API ───────────────────────────────────────────────────────────

def get_trade_timeline(trade_id: int) -> List[Dict]:
    """Get all events for a single trade in chronological order."""
    if not JOURNAL_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(JOURNAL_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM journal_events WHERE trade_id = ? ORDER BY id ASC",
            (trade_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["context"] = json.loads(d.pop("context_json", "{}"))
            except Exception:
                d["context"] = {}
            result.append(d)
        return result
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_recent_events(limit: int = 50, event_type: Optional[str] = None,
                     tab: Optional[str] = None) -> List[Dict]:
    """Get recent events with optional filters."""
    if not JOURNAL_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(JOURNAL_DB))
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM journal_events WHERE 1=1"
        args = []
        if event_type:
            sql += " AND event_type = ?"
            args.append(event_type)
        if tab:
            sql += " AND tab = ?"
            args.append(tab)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["context"] = json.loads(d.pop("context_json", "{}"))
            except Exception:
                d["context"] = {}
            result.append(d)
        return result
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def explain_trade(trade_id: int) -> Dict:
    """Generate a natural-language explanation of a trade's lifecycle.

    Returns:
        {
          "trade_id": int,
          "summary": str (one-line),
          "narrative": List[str] (step-by-step),
          "event_count": int,
          "outcome": str ("WIN" / "LOSS"),
        }
    """
    events = get_trade_timeline(trade_id)
    if not events:
        return {
            "trade_id": trade_id,
            "summary": "No events found for this trade",
            "narrative": [],
            "event_count": 0,
            "outcome": "UNKNOWN",
        }

    narrative = []
    entry_event = None
    exit_event = None

    for e in events:
        ts = e["timestamp"][:19].replace("T", " ")
        et = e["event_type"]
        ctx = e["context"]
        reason = e["reason"]

        if et == EVENT_ENTRY:
            entry_event = e
            narrative.append(
                f"📥 {ts}: Entered {ctx.get('action','?')} {ctx.get('idx','?')} "
                f"strike {ctx.get('strike','?')} @ ₹{ctx.get('entry_price','?')} "
                f"× {ctx.get('qty','?')} qty (prob {ctx.get('probability','?')}%)"
            )
            if ctx.get("source"):
                narrative.append(f"   Source: {ctx['source']}")
            if ctx.get("reasoning"):
                narrative.append(f"   Reasoning: {ctx['reasoning'][:150]}")
            narrative.append(
                f"   Initial SL ₹{ctx.get('sl_price','?')}, "
                f"T1 ₹{ctx.get('t1_price','?')}, T2 ₹{ctx.get('t2_price','?')}"
            )

        elif et == EVENT_SL_UPDATE:
            narrative.append(
                f"📊 {ts}: SL moved ₹{ctx.get('old_sl','?')} → ₹{ctx.get('new_sl','?')} "
                f"({ctx.get('method','?')})"
            )

        elif et == EVENT_PARTIAL_EXIT:
            narrative.append(
                f"💰 {ts}: Booked {ctx.get('qty_booked','?')} qty @ ₹{ctx.get('price','?')} "
                f"(+{ctx.get('profit_pct','?')}%)"
            )

        elif et == EVENT_PYRAMID_ADD:
            narrative.append(
                f"📈 {ts}: Added {ctx.get('qty_added','?')} qty @ ₹{ctx.get('add_price','?')} "
                f"(new avg ₹{ctx.get('new_avg_entry','?')})"
            )

        elif et == EVENT_EXIT:
            exit_event = e
            narrative.append(
                f"📤 {ts}: EXIT @ ₹{ctx.get('exit_price','?')} ({ctx.get('status','?')})"
            )
            narrative.append(
                f"   P&L: ₹{ctx.get('pnl_rupees','?'):+,.0f} ({ctx.get('pnl_pct','?'):+.1f}%)"
            )
            gb = ctx.get("gave_back_from_peak_pct")
            if gb:
                narrative.append(f"   Gave back from peak: {gb}%")

    # Summary
    if exit_event:
        pnl = exit_event["context"].get("pnl_rupees", 0)
        outcome = "WIN" if pnl > 0 else "LOSS"
        summary = (
            f"Trade #{trade_id}: {entry_event['context'].get('action','?')} "
            f"{entry_event['context'].get('idx','?')} → "
            f"{exit_event['context'].get('status','?')} "
            f"₹{pnl:+,.0f} ({outcome})"
        )
    else:
        outcome = "OPEN"
        summary = f"Trade #{trade_id}: still open"

    return {
        "trade_id": trade_id,
        "summary": summary,
        "narrative": narrative,
        "event_count": len(events),
        "outcome": outcome,
    }


def get_stats(days: int = 7) -> Dict:
    """Aggregate journal stats — event counts by type."""
    if not JOURNAL_DB.exists():
        return {}
    cutoff = (datetime.now(IST) - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(str(JOURNAL_DB))
        rows = conn.execute(
            "SELECT event_type, COUNT(*) FROM journal_events "
            "WHERE timestamp >= ? GROUP BY event_type",
            (cutoff,),
        ).fetchall()
        return {
            "period_days": days,
            "event_counts": dict(rows),
            "total_events": sum(c for _, c in rows),
        }
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass
