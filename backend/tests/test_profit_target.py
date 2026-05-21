"""
Tests for profit_target — "book win and walk away" mode.

Built 2026-05-21 per user vision:
  "Dine mein 15-20 trade le aur market se paisa banakr nikle"
  (Take 15-20 trades a day and EXIT after making money)

When today's per-tab P&L >= target → block new entries.
Open positions still managed normally.
"""

import os
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


def _seed_trades(db_path, table, rows):
    """rows = [(entry_time, status, pnl_rupees), ...]"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"CREATE TABLE {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "entry_time TEXT, exit_time TEXT, status TEXT, pnl_rupees REAL)"
    )
    for entry_time, status, pnl in rows:
        conn.execute(
            f"INSERT INTO {table} (entry_time, exit_time, status, pnl_rupees) "
            "VALUES (?, ?, ?, ?)",
            (entry_time, entry_time, status, pnl),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in [
        "PROFIT_TARGET_ENABLED",
        "PROFIT_TARGET_MAIN",
        "PROFIT_TARGET_SCALPER",
        "PROFIT_TARGET_SHADOW",
    ]:
        monkeypatch.delenv(var, raising=False)


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_disabled(self):
        from profit_target import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("PROFIT_TARGET_ENABLED", "on")
        from profit_target import is_enabled
        assert is_enabled() is True

    def test_default_target_15000(self):
        from profit_target import profit_target
        assert profit_target("MAIN") == 15000
        assert profit_target("SCALPER") == 15000

    def test_per_tab_target_configurable(self, monkeypatch):
        monkeypatch.setenv("PROFIT_TARGET_MAIN", "30000")
        monkeypatch.setenv("PROFIT_TARGET_SCALPER", "10000")
        from profit_target import profit_target
        assert profit_target("MAIN") == 30000
        assert profit_target("SCALPER") == 10000


# ── ASSESS LOGIC ───────────────────────────────────────────────────────

class TestAssess:
    def test_under_target_no_block(self, monkeypatch, tmp_path):
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 5000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        d = profit_target.assess("MAIN")
        assert d["target_hit"] is False
        assert d["block"] is False
        assert d["today_pnl"] == 5000
        assert d["amount_to_go"] == 10000

    def test_target_hit_triggers_block(self, monkeypatch, tmp_path):
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 8000),
            (f"{today}T11:00:00+05:30", "TRAIL_EXIT", 9000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        d = profit_target.assess("MAIN")
        assert d["target_hit"] is True
        assert d["block"] is True
        assert "PROFIT_TARGET_HIT" in d["reason"]
        assert d["today_pnl"] == 17000
        assert d["amount_to_go"] < 0  # exceeded by ₹2000

    def test_exactly_at_target_blocks(self, monkeypatch, tmp_path):
        """P&L exactly == target → block (don't push further)."""
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 15000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        d = profit_target.assess("MAIN")
        assert d["target_hit"] is True
        assert d["block"] is True

    def test_per_tab_independent(self, monkeypatch, tmp_path):
        """Scalper target hit shouldn't block MAIN."""
        import profit_target, circuit_breaker
        main_db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(main_db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 2000),
        ])
        _seed_trades(scalp_db, "scalper_trades", [
            (f"{today}T10:00:00+05:30", "T1_HIT", 20000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", main_db)
        monkeypatch.setattr(circuit_breaker, "_SCALPER_DB", scalp_db)
        assert profit_target.assess("MAIN")["block"] is False
        assert profit_target.assess("SCALPER")["block"] is True

    def test_loss_does_not_trigger(self, monkeypatch, tmp_path):
        """If you're in a loss, you obviously haven't hit target."""
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -5000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        d = profit_target.assess("MAIN")
        assert d["block"] is False
        assert d["pct_to_target"] < 0


# ── SHOULD_BLOCK (public API) ──────────────────────────────────────────

class TestShouldBlock:
    def test_disabled_never_blocks(self, monkeypatch, tmp_path):
        """Even if target hit, disabled flag → no block."""
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 100000),  # way over
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert profit_target.should_block("MAIN") is False

    def test_enabled_blocks_on_target_hit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PROFIT_TARGET_ENABLED", "on")
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 20000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert profit_target.should_block("MAIN") is True

    def test_enabled_no_block_when_under(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PROFIT_TARGET_ENABLED", "on")
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 5000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert profit_target.should_block("MAIN") is False


# ── STATUS ─────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_includes_pct_to_target(self, monkeypatch, tmp_path):
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 7500),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        s = profit_target.status("MAIN")
        assert s["target"] == 15000
        assert s["today_pnl"] == 7500
        assert s["pct_to_target"] == 0.5
        assert s["amount_to_go"] == 7500

    def test_status_reports_exceeded(self, monkeypatch, tmp_path):
        import profit_target, circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 22500),  # 150% of target
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        s = profit_target.status("MAIN")
        assert s["pct_to_target"] == 1.5
        assert s["amount_to_go"] == -7500
        assert s["target_hit"] is True
