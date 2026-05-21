"""
Tests for trade_journal — system self-awareness layer.

Built 2026-05-21 per user vision:
  "System ek bot ki tarah kaam kre. Use pata ho ki main kya kara hu."

Every trade decision logged as structured event:
  ENTRY, SL_UPDATE, PARTIAL_EXIT, PYRAMID_ADD, EXIT, GATE_BLOCKED, ALERT
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _isolated_journal_db(monkeypatch, tmp_path):
    """Use temp DB so tests don't pollute production journal."""
    import trade_journal
    monkeypatch.setattr(trade_journal, "JOURNAL_DB", tmp_path / "journal.db")
    yield


# ── LOG EVENT BASE ──────────────────────────────────────────────────────

class TestLogEvent:
    def test_log_event_creates_db(self, tmp_path):
        from trade_journal import log_event, JOURNAL_DB
        log_event(event_type="TEST", reason="testing")
        # Module's JOURNAL_DB is monkey-patched via fixture
        # Just verify no exception thrown

    def test_log_event_handles_no_context(self):
        from trade_journal import log_event
        log_event(event_type="TEST", reason="basic")  # no context
        # Should not raise

    def test_log_event_handles_invalid_db_gracefully(self, monkeypatch):
        """Even if DB path is broken, log_event should NEVER raise."""
        import trade_journal
        # Make JOURNAL_DB unreadable directory
        monkeypatch.setattr(trade_journal, "JOURNAL_DB", Path("/dev/null/nope/journal.db"))
        trade_journal.log_event(event_type="TEST", reason="should not throw")


# ── ENTRY LOGGING ───────────────────────────────────────────────────────

class TestLogEntry:
    def test_log_entry_captures_all_fields(self):
        from trade_journal import log_entry, get_trade_timeline
        log_entry(
            trade_id=1001,
            tab="SCALPER",
            idx="NIFTY",
            action="BUY CE",
            strike=24000,
            entry_price=150.0,
            qty=75,
            probability=72,
            sl_price=135.0,
            t1_price=165.0,
            t2_price=180.0,
            source="verdict_momentum",
            reasoning="Multi-TF bullish + FII positive",
        )
        timeline = get_trade_timeline(1001)
        assert len(timeline) == 1
        assert timeline[0]["event_type"] == "ENTRY"
        assert timeline[0]["context"]["idx"] == "NIFTY"
        assert timeline[0]["context"]["entry_price"] == 150.0
        assert timeline[0]["context"]["qty"] == 75
        assert "Multi-TF bullish" in timeline[0]["context"]["reasoning"]


# ── SL UPDATE LOGGING ───────────────────────────────────────────────────

class TestLogSLUpdate:
    def test_log_sl_update(self):
        from trade_journal import log_sl_update, get_trade_timeline
        log_sl_update(
            trade_id=1001,
            tab="SCALPER",
            old_sl=135.0,
            new_sl=150.0,
            reason="Breakeven lock at +5%",
            current_price=158.0,
            peak_price=160.0,
            profit_pct=5.3,
            method="breakeven",
        )
        timeline = get_trade_timeline(1001)
        assert len(timeline) == 1
        assert timeline[0]["event_type"] == "SL_UPDATE"
        assert timeline[0]["context"]["delta"] == 15.0
        assert timeline[0]["context"]["method"] == "breakeven"


# ── EXIT LOGGING ────────────────────────────────────────────────────────

class TestLogExit:
    def test_log_exit_computes_gave_back(self):
        from trade_journal import log_exit, get_trade_timeline
        log_exit(
            trade_id=1001,
            tab="SCALPER",
            exit_price=170.0,
            exit_reason="Trail SL hit at ₹170",
            status="TRAIL_EXIT",
            pnl_rupees=15000,
            pnl_pct=13.3,
            peak_price=180.0,
        )
        timeline = get_trade_timeline(1001)
        assert len(timeline) == 1
        ctx = timeline[0]["context"]
        # Gave back = (180-170)/180*100 = 5.56%
        assert abs(ctx["gave_back_from_peak_pct"] - 5.56) < 0.1
        assert ctx["status"] == "TRAIL_EXIT"


# ── GATE BLOCKED LOGGING ────────────────────────────────────────────────

class TestLogGateBlocked:
    def test_log_gate_blocked(self):
        from trade_journal import log_gate_blocked, get_recent_events
        log_gate_blocked(
            tab="SCALPER",
            gate_name="TUESDAY_SKIP",
            idx="NIFTY",
            action="BUY CE",
            reason="NIFTY weekly expiry day",
        )
        events = get_recent_events(limit=10, event_type="GATE_BLOCKED")
        assert len(events) >= 1
        assert events[0]["context"]["gate_name"] == "TUESDAY_SKIP"


# ── QUERY API ───────────────────────────────────────────────────────────

class TestQueryAPI:
    def test_get_trade_timeline_orders_chronologically(self):
        """Multiple events for same trade should come back in order."""
        from trade_journal import (
            log_entry, log_sl_update, log_exit, get_trade_timeline
        )
        log_entry(
            trade_id=2001, tab="MAIN", idx="NIFTY", action="BUY CE",
            strike=24000, entry_price=100, qty=75, probability=70,
            sl_price=85, t1_price=110, t2_price=120, source="verdict",
        )
        log_sl_update(
            trade_id=2001, tab="MAIN",
            old_sl=85, new_sl=95, reason="Breakeven lock",
            current_price=105, profit_pct=5,
        )
        log_exit(
            trade_id=2001, tab="MAIN",
            exit_price=108, exit_reason="T1 hit", status="T1_HIT",
            pnl_rupees=600, pnl_pct=8, peak_price=110,
        )
        timeline = get_trade_timeline(2001)
        assert len(timeline) == 3
        assert timeline[0]["event_type"] == "ENTRY"
        assert timeline[1]["event_type"] == "SL_UPDATE"
        assert timeline[2]["event_type"] == "EXIT"

    def test_get_recent_events_with_filter(self):
        from trade_journal import log_entry, log_exit, get_recent_events
        log_entry(
            trade_id=3001, tab="SCALPER", idx="NIFTY", action="BUY PE",
            strike=24500, entry_price=80, qty=75, probability=65,
            sl_price=70, t1_price=90, t2_price=100, source="verdict_momentum",
        )
        log_exit(
            trade_id=3001, tab="SCALPER",
            exit_price=92, exit_reason="T1 hit", status="T1_HIT",
            pnl_rupees=900, pnl_pct=15, peak_price=95,
        )
        # Filter to EXIT only
        events = get_recent_events(limit=10, event_type="EXIT")
        assert all(e["event_type"] == "EXIT" for e in events)
        # Filter to SCALPER only
        events_s = get_recent_events(limit=10, tab="SCALPER")
        assert all(e.get("tab") == "SCALPER" for e in events_s)


# ── EXPLAIN TRADE ───────────────────────────────────────────────────────

class TestExplainTrade:
    def test_explain_winning_trade_returns_narrative(self):
        from trade_journal import (
            log_entry, log_sl_update, log_exit, explain_trade
        )
        log_entry(
            trade_id=4001, tab="SCALPER", idx="BANKNIFTY", action="BUY CE",
            strike=55000, entry_price=200, qty=35, probability=75,
            sl_price=180, t1_price=220, t2_price=240,
            source="verdict_momentum", reasoning="Strong bull setup",
        )
        log_sl_update(
            trade_id=4001, tab="SCALPER",
            old_sl=180, new_sl=200, reason="Breakeven",
            current_price=215, profit_pct=7.5,
        )
        log_exit(
            trade_id=4001, tab="SCALPER",
            exit_price=235, exit_reason="T2 hit", status="T2_HIT",
            pnl_rupees=1225, pnl_pct=17.5, peak_price=240,
        )
        exp = explain_trade(4001)
        assert exp["outcome"] == "WIN"
        assert exp["event_count"] == 3
        assert len(exp["narrative"]) > 0
        assert any("BANKNIFTY" in line for line in exp["narrative"])

    def test_explain_missing_trade_returns_empty(self):
        from trade_journal import explain_trade
        exp = explain_trade(999999)
        assert exp["event_count"] == 0
        assert "No events" in exp["summary"]


# ── STATS ───────────────────────────────────────────────────────────────

class TestGetStats:
    def test_get_stats_returns_event_counts(self):
        from trade_journal import log_entry, log_exit, log_gate_blocked, get_stats
        # Log a few events
        log_entry(
            trade_id=5001, tab="MAIN", idx="NIFTY", action="BUY CE",
            strike=24000, entry_price=100, qty=75, probability=70,
            sl_price=85, t1_price=110, t2_price=120, source="x",
        )
        log_exit(
            trade_id=5001, tab="MAIN", exit_price=108, exit_reason="x",
            status="T1_HIT", pnl_rupees=600, pnl_pct=8,
        )
        log_gate_blocked(
            tab="SCALPER", gate_name="TUESDAY", idx="NIFTY",
            action="BUY CE", reason="weekly expiry",
        )
        stats = get_stats(days=7)
        assert "event_counts" in stats
        assert stats["total_events"] >= 3
