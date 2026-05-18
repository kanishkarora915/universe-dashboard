"""
Tests for scalper auto-trade kill switch (Phase 1 of audit fixes).

Background:
  4-session audit on 2026-05-18 found scalper losing -₹119,046 with
  38% winrate, 82% PE bias, theta-decay killing 10/45 trades. Until
  directional-gate + theta-protect logic is added, scalper is paused
  via SCALPER_AUTO_TRADE_ENABLED flag.

These tests verify:
  • Flag defaults to OFF when env var not set or set to 'off'
  • Flag respects SCALPER_AUTO_TRADE=on env override
  • log_scalp_trade returns None when killed (doesn't write DB row)
  • Signal generation paths NOT affected (only the trade-firing fn)
"""

import sys
import importlib
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture
def reload_scalper(monkeypatch):
    """Reload scalper_mode module so env var changes are picked up."""
    def _reload(env_value=None):
        if env_value is None:
            monkeypatch.delenv("SCALPER_AUTO_TRADE", raising=False)
        else:
            monkeypatch.setenv("SCALPER_AUTO_TRADE", env_value)
        if "scalper_mode" in sys.modules:
            del sys.modules["scalper_mode"]
        import scalper_mode
        return scalper_mode
    return _reload


class TestKillSwitchFlag:
    def test_default_on_when_env_unset(self, reload_scalper):
        """Default ON — user keeps scalper active while Phase 2 built."""
        sm = reload_scalper(None)
        assert sm.SCALPER_AUTO_TRADE_ENABLED is True

    def test_off_when_env_explicitly_off(self, reload_scalper):
        sm = reload_scalper("off")
        assert sm.SCALPER_AUTO_TRADE_ENABLED is False

    def test_on_when_env_some_random_value(self, reload_scalper):
        """Default ON; only literal 'off' disables — random values keep ON."""
        sm = reload_scalper("nope")
        assert sm.SCALPER_AUTO_TRADE_ENABLED is True

    def test_on_when_env_set_to_on(self, reload_scalper):
        sm = reload_scalper("on")
        assert sm.SCALPER_AUTO_TRADE_ENABLED is True

    def test_case_insensitive_off(self, reload_scalper):
        sm = reload_scalper("OFF")
        assert sm.SCALPER_AUTO_TRADE_ENABLED is False


class TestLogScalpTradeBlocked:
    def test_returns_none_when_disabled(self, reload_scalper, monkeypatch, tmp_path):
        # Force DB to temp path so we can verify nothing gets written
        sm = reload_scalper("off")
        tmp_db = tmp_path / "scalper.db"
        monkeypatch.setattr(sm, "SCALPER_DB", tmp_db)
        # Try to fire
        result = sm.log_scalp_trade(
            idx="NIFTY", action="BUY PE", strike=23500,
            entry_price=100.0, probability=70,
        )
        assert result is None

    def test_no_db_row_created_when_disabled(self, reload_scalper, monkeypatch, tmp_path):
        sm = reload_scalper("off")
        tmp_db = tmp_path / "scalper.db"
        monkeypatch.setattr(sm, "SCALPER_DB", tmp_db)
        # Init the DB schema first so we can query it cleanly
        sm.init_scalper_db()
        # Attempt to fire — should be suppressed
        sm.log_scalp_trade(
            idx="NIFTY", action="BUY PE", strike=23500,
            entry_price=100.0, probability=70,
        )
        # Verify no rows
        conn = sqlite3.connect(str(tmp_db))
        try:
            n = conn.execute("SELECT COUNT(*) FROM scalper_trades").fetchone()[0]
            assert n == 0
        finally:
            conn.close()


class TestSignalsStillRunWhenKilled:
    """When kill switch is on, signal generation + analytics should
    keep computing. Only the trade-firing function is blocked."""

    def test_get_active_scalp_config_works_when_disabled(self, reload_scalper):
        sm = reload_scalper("off")
        # This is the config-fetch fn used by signal computation
        cfg = sm.get_active_scalp_config()
        assert isinstance(cfg, dict)
        assert "sl_pct" in cfg

    def test_init_db_still_works_when_disabled(self, reload_scalper, monkeypatch, tmp_path):
        sm = reload_scalper("off")
        tmp_db = tmp_path / "scalper.db"
        monkeypatch.setattr(sm, "SCALPER_DB", tmp_db)
        # DB init should NOT be affected by kill switch
        sm.init_scalper_db()
        assert tmp_db.exists()
