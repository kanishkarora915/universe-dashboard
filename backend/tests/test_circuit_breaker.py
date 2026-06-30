"""
Tests for circuit_breaker (Fix 3 — daily loss cap + consecutive loss pause).

Audit context:
  May 14: -₹81,078 single session (1W/7L disaster). No cap meant
  the system kept firing after 3-4 losses, digging deeper.

Two breakers:
  • Daily loss cap (default -₹15,000/day per tab)
  • Consecutive loss pause (default 3 losses → 30-min cool-off)
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


def _seed_trades(db_path, table, rows):
    """rows = [(entry_time, status, pnl_rupees), ...]"""
    conn = sqlite3.connect(str(db_path))
    cols = "id INTEGER PRIMARY KEY AUTOINCREMENT, entry_time TEXT, exit_time TEXT, status TEXT, pnl_rupees REAL"
    conn.execute(f"CREATE TABLE {table} ({cols})")
    for entry_time, status, pnl in rows:
        conn.execute(
            f"INSERT INTO {table} (entry_time, exit_time, status, pnl_rupees) VALUES (?, ?, ?, ?)",
            (entry_time, entry_time, status, pnl),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in [
        "DAILY_LOSS_CAP_ENABLED",
        "CIRCUIT_BREAKER_SHADOW",
        "DAILY_LOSS_LIMIT_MAIN",
        "DAILY_LOSS_LIMIT_SCALPER",
        "CONSECUTIVE_LOSS_LIMIT",
        "COOL_OFF_MINUTES",
    ]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def frozen_noon(monkeypatch):
    """Freeze 'now' to 12:00 IST on today's date so cool-off tests are
    independent of when they run (3:54 AM in pre-market vs 11 AM intraday)."""
    import circuit_breaker
    today = datetime.now(IST).date()
    frozen = IST.localize(datetime(today.year, today.month, today.day, 12, 0, 0))

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen if tz else frozen.replace(tzinfo=None)

    monkeypatch.setattr(circuit_breaker, "datetime", _FrozenDatetime)
    return frozen


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_disabled(self):
        from circuit_breaker import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("DAILY_LOSS_CAP_ENABLED", "on")
        from circuit_breaker import is_enabled
        assert is_enabled() is True

    def test_default_daily_limits(self):
        from circuit_breaker import daily_loss_limit
        assert daily_loss_limit("MAIN") == 15000
        assert daily_loss_limit("SCALPER") == 15000

    def test_per_tab_limits_configurable(self, monkeypatch):
        monkeypatch.setenv("DAILY_LOSS_LIMIT_MAIN", "20000")
        monkeypatch.setenv("DAILY_LOSS_LIMIT_SCALPER", "8000")
        from circuit_breaker import daily_loss_limit
        assert daily_loss_limit("MAIN") == 20000
        assert daily_loss_limit("SCALPER") == 8000

    def test_default_consec_limit_3(self):
        from circuit_breaker import consecutive_loss_limit
        assert consecutive_loss_limit() == 3

    def test_default_cool_off_30min(self):
        from circuit_breaker import cool_off_minutes
        assert cool_off_minutes() == 30


# ── DAILY LOSS CAP ─────────────────────────────────────────────────────

class TestDailyLossCap:
    def test_pnl_summed_for_today(self, monkeypatch, tmp_path):
        import circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 5000),
            (f"{today}T11:00:00+05:30", "SL_HIT", -3000),
            (f"{today}T12:00:00+05:30", "SL_HIT", -8000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert circuit_breaker.today_pnl("MAIN") == -6000

    def test_assess_no_breach_under_limit(self, monkeypatch, tmp_path):
        import circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -5000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        d = circuit_breaker.assess("MAIN")
        assert d["daily_breach"] is False
        assert d["block"] is False

    def test_assess_breach_triggers_block(self, monkeypatch, tmp_path):
        import circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -20000),  # over -15k limit
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        d = circuit_breaker.assess("MAIN")
        assert d["daily_breach"] is True
        assert d["block"] is True
        assert "DAILY_LOSS_CAP" in d["reason"]

    def test_open_trades_not_counted(self, monkeypatch, tmp_path):
        """OPEN trades shouldn't count toward today P&L."""
        import circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "OPEN", -50000),  # OPEN — excluded
            (f"{today}T11:00:00+05:30", "SL_HIT", -5000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert circuit_breaker.today_pnl("MAIN") == -5000


# ── CONSECUTIVE LOSS PAUSE ─────────────────────────────────────────────

class TestConsecutiveLossPause:
    def test_three_losses_in_row_detected(self, monkeypatch, tmp_path):
        import circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -2000),
            (f"{today}T11:00:00+05:30", "SL_HIT", -3000),
            (f"{today}T12:00:00+05:30", "SL_HIT", -4000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert circuit_breaker.recent_consecutive_losses("MAIN") == 3

    def test_win_resets_streak(self, monkeypatch, tmp_path):
        import circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -2000),
            (f"{today}T11:00:00+05:30", "SL_HIT", -3000),
            (f"{today}T12:00:00+05:30", "TRAIL_EXIT", 5000),  # WIN — reset
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        # Newest trade is a win → streak = 0
        assert circuit_breaker.recent_consecutive_losses("MAIN") == 0

    def test_three_loss_streak_triggers_pause_within_cool_off(self, monkeypatch, tmp_path, frozen_noon):
        """3 losses → pause for 30min after last loss."""
        import circuit_breaker
        db = tmp_path / "main.db"
        # Anchor offsets to frozen 12:00 IST
        t1 = (frozen_noon - timedelta(minutes=60)).isoformat()  # 11:00
        t2 = (frozen_noon - timedelta(minutes=30)).isoformat()  # 11:30
        t3 = (frozen_noon - timedelta(minutes=5)).isoformat()   # 11:55 (within cool-off)
        _seed_trades(db, "trades", [
            (t1, "SL_HIT", -2000),
            (t2, "SL_HIT", -3000),
            (t3, "SL_HIT", -4000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        d = circuit_breaker.assess("MAIN")
        assert d["consec_breach"] is True
        assert d["cool_off_active"] is True
        assert d["block"] is True
        assert "CONSEC_LOSS_PAUSE" in d["reason"]

    def test_pause_clears_after_cool_off(self, monkeypatch, tmp_path, frozen_noon):
        """After 30+ min from last loss, pause clears."""
        import circuit_breaker
        db = tmp_path / "main.db"
        # All trades well before frozen 12:00
        t1 = (frozen_noon - timedelta(minutes=120)).isoformat()  # 10:00
        t2 = (frozen_noon - timedelta(minutes=90)).isoformat()   # 10:30
        t3 = (frozen_noon - timedelta(minutes=45)).isoformat()   # 11:15 — > 30m cool-off
        _seed_trades(db, "trades", [
            (t1, "SL_HIT", -2000),
            (t2, "SL_HIT", -3000),
            (t3, "SL_HIT", -4000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        d = circuit_breaker.assess("MAIN")
        assert d["consec_breach"] is True   # streak still 3
        assert d["cool_off_active"] is False  # but cool-off expired
        # Daily loss is -9k, under -15k → no other block
        assert d["block"] is False


# ── should_block (public API) ──────────────────────────────────────────

class TestShouldBlock:
    def test_disabled_never_blocks(self, monkeypatch, tmp_path):
        import circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        # Massive loss but flag off
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -50000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert circuit_breaker.should_block("MAIN") is False

    def test_enabled_blocks_on_breach(self, monkeypatch, tmp_path):
        import circuit_breaker
        monkeypatch.setenv("DAILY_LOSS_CAP_ENABLED", "on")
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -20000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert circuit_breaker.should_block("MAIN") is True

    def test_per_tab_independent(self, monkeypatch, tmp_path):
        """Scalper breach should NOT block MAIN entries."""
        import circuit_breaker
        monkeypatch.setenv("DAILY_LOSS_CAP_ENABLED", "on")

        main_db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(main_db, "trades", [
            (f"{today}T10:00:00+05:30", "TRAIL_EXIT", 5000),
        ])
        _seed_trades(scalp_db, "scalper_trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -30000),  # breach
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", main_db)
        monkeypatch.setattr(circuit_breaker, "_SCALPER_DB", scalp_db)

        assert circuit_breaker.should_block("MAIN") is False
        assert circuit_breaker.should_block("SCALPER") is True

    def test_handles_missing_db(self, monkeypatch, tmp_path):
        """Missing DB → 0 P&L → no block."""
        import circuit_breaker
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", tmp_path / "nope.db")
        assert circuit_breaker.today_pnl("MAIN") == 0.0
        assert circuit_breaker.should_block("MAIN") is False


# ── Status snapshot ────────────────────────────────────────────────────

class TestLastLossTimeRobustness:
    """Regression tests for the 2026-05-21 audit-fix:
    last_loss_time must not return future-dated exit_time rows.

    Bug: lexicographic ORDER BY exit_time DESC selected morning-time
    trades that were "in the future" when test ran in pre-market hours.
    Production safety added `AND exit_time <= now` clause.
    """

    def test_skips_future_exit_time(self, monkeypatch, tmp_path, frozen_noon):
        """Trade with exit_time in the future is NOT returned as last loss."""
        import circuit_breaker
        db = tmp_path / "main.db"
        # Future trade (after frozen noon)
        future_t = (frozen_noon + timedelta(hours=2)).isoformat()
        # Past trade (10 min ago)
        past_t = (frozen_noon - timedelta(minutes=10)).isoformat()
        _seed_trades(db, "trades", [
            (past_t, "SL_HIT", -5000),
            (future_t, "SL_HIT", -3000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        ll = circuit_breaker.last_loss_time("MAIN")
        # Should return the PAST trade time, not the future one
        assert ll is not None
        elapsed = (frozen_noon - ll).total_seconds() / 60
        assert 9 <= elapsed <= 11  # ~10 min ago

    def test_returns_none_when_all_future(self, monkeypatch, tmp_path, frozen_noon):
        """All trades future-dated → no loss found → None."""
        import circuit_breaker
        db = tmp_path / "main.db"
        future_t = (frozen_noon + timedelta(hours=1)).isoformat()
        _seed_trades(db, "trades", [
            (future_t, "SL_HIT", -5000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        assert circuit_breaker.last_loss_time("MAIN") is None


class TestStatus:
    def test_status_returns_dict(self, monkeypatch, tmp_path):
        import circuit_breaker
        db = tmp_path / "main.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        _seed_trades(db, "trades", [
            (f"{today}T10:00:00+05:30", "SL_HIT", -5000),
        ])
        monkeypatch.setattr(circuit_breaker, "_TRADES_DB", db)
        s = circuit_breaker.status("MAIN")
        assert s["tab"] == "MAIN"
        assert s["today_pnl"] == -5000
        assert s["daily_limit"] == -15000
        assert "consec_losses" in s
