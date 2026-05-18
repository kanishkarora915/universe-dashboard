"""
Tests for health_monitor — the periodic Telegram trade/system snapshot.
"""

import sys
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import health_monitor


# ─────────────────────────────────────────────────────────────────────
# Fixtures — minimal trade DBs we can poke
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_data_dir(monkeypatch, tmp_path):
    """Redirect health_monitor to use a temp dir for both DBs."""
    monkeypatch.setattr(health_monitor, "_data_dir", lambda: tmp_path)
    # Create empty trades.db schema matching trade_logger
    trades_db = tmp_path / "trades.db"
    conn = sqlite3.connect(str(trades_db))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            idx TEXT, action TEXT, strike INTEGER,
            entry_time TEXT, exit_time TEXT,
            entry_price REAL, exit_price REAL,
            current_ltp REAL, peak_ltp REAL,
            status TEXT, pnl_rupees REAL, source TEXT
        )
    """)
    conn.commit()
    conn.close()
    # Create empty scalper schema
    scalp_db = tmp_path / "scalper_trades.db"
    conn = sqlite3.connect(str(scalp_db))
    conn.execute("""
        CREATE TABLE scalper_trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT, idx TEXT, strike INTEGER, side TEXT,
            entry_time TEXT, exit_time TEXT,
            entry_price REAL, exit_price REAL, current_ltp REAL,
            status TEXT, pnl_rupees REAL
        )
    """)
    conn.commit()
    conn.close()
    return tmp_path


def _insert_main_trade(tmp_path, **kwargs):
    """Insert one row into trades.db. Caller supplies relevant fields."""
    defaults = {
        "idx": "NIFTY", "action": "BUY CE", "strike": 24500,
        "entry_time": "2026-05-17T09:30:00", "entry_price": 100.0,
        "current_ltp": 100.0, "peak_ltp": 100.0, "status": "OPEN",
        "pnl_rupees": 0.0, "source": "verdict",
    }
    defaults.update(kwargs)
    conn = sqlite3.connect(str(tmp_path / "trades.db"))
    cols = ",".join(defaults.keys())
    placeholders = ",".join("?" * len(defaults))
    conn.execute(f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
                 tuple(defaults.values()))
    conn.commit()
    conn.close()


def _insert_scalper_trade(tmp_path, **kwargs):
    defaults = {
        "symbol": "NIFTY24500CE", "idx": "NIFTY", "strike": 24500, "side": "CE",
        "entry_time": "2026-05-17T09:35:00", "entry_price": 80.0,
        "current_ltp": 80.0, "status": "OPEN", "pnl_rupees": 0.0,
    }
    defaults.update(kwargs)
    conn = sqlite3.connect(str(tmp_path / "scalper_trades.db"))
    cols = ",".join(defaults.keys())
    placeholders = ",".join("?" * len(defaults))
    conn.execute(f"INSERT INTO scalper_trades ({cols}) VALUES ({placeholders})",
                 tuple(defaults.values()))
    conn.commit()
    conn.close()


def _today_dt():
    """Naive datetime matching what _ist_now returns when pytz fails."""
    return datetime.now()


# ─────────────────────────────────────────────────────────────────────
# Market hours detection
# ─────────────────────────────────────────────────────────────────────

class TestMarketHours:
    def test_weekday_during_market(self):
        # Monday 10:30 AM IST (weekday=0, 10:30)
        d = datetime(2026, 5, 18, 10, 30)
        assert health_monitor._is_market_hours(d) is True

    def test_weekday_before_market(self):
        # Monday 09:00 AM IST → before open
        d = datetime(2026, 5, 18, 9, 0)
        assert health_monitor._is_market_hours(d) is False

    def test_weekday_after_market(self):
        # Monday 16:00 IST → after close
        d = datetime(2026, 5, 18, 16, 0)
        assert health_monitor._is_market_hours(d) is False

    def test_saturday_always_false(self):
        # Saturday 11 AM
        d = datetime(2026, 5, 16, 11, 0)
        assert health_monitor._is_market_hours(d) is False

    def test_market_open_boundary(self):
        d = datetime(2026, 5, 18, 9, 15)
        assert health_monitor._is_market_hours(d) is True

    def test_market_close_boundary(self):
        d = datetime(2026, 5, 18, 15, 30)
        assert health_monitor._is_market_hours(d) is True


class TestEodWindow:
    def test_eod_window_exactly_at_1535(self):
        d = datetime(2026, 5, 18, 15, 35)
        assert health_monitor._is_eod_window(d) is True

    def test_eod_window_at_1539(self):
        d = datetime(2026, 5, 18, 15, 39)
        assert health_monitor._is_eod_window(d) is True

    def test_eod_window_at_1540_too_late(self):
        d = datetime(2026, 5, 18, 15, 40)
        assert health_monitor._is_eod_window(d) is False

    def test_eod_skipped_on_weekend(self):
        d = datetime(2026, 5, 17, 15, 35)  # Sunday
        assert health_monitor._is_eod_window(d) is False


# ─────────────────────────────────────────────────────────────────────
# INR formatting
# ─────────────────────────────────────────────────────────────────────

class TestFormatInr:
    def test_positive_small(self):
        assert health_monitor._format_inr(1500) == "+₹1,500"

    def test_negative_small(self):
        assert health_monitor._format_inr(-1500) == "-₹1,500"

    def test_zero(self):
        assert health_monitor._format_inr(0) == "+₹0"

    def test_lakh_format(self):
        assert health_monitor._format_inr(125000) == "+₹1.25L"

    def test_negative_lakh(self):
        assert health_monitor._format_inr(-200000) == "-₹2.00L"


# ─────────────────────────────────────────────────────────────────────
# Trade DB queries
# ─────────────────────────────────────────────────────────────────────

class TestMainTradesQuery:
    def test_empty_db_returns_zeros(self, temp_data_dir):
        result = health_monitor._get_main_trades_today(_today_dt())
        assert result["total_count"] == 0
        assert result["realized_pnl"] == 0.0
        assert result["open"] == []
        assert result["closed"] == []

    def test_one_open_trade(self, temp_data_dir):
        today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _insert_main_trade(
            temp_data_dir,
            entry_time=today_iso,
            entry_price=120.0,
            current_ltp=135.0,
            pnl_rupees=1500.0,
            status="OPEN",
        )
        result = health_monitor._get_main_trades_today(_today_dt())
        assert result["total_count"] == 1
        assert len(result["open"]) == 1
        assert result["unrealized_pnl"] == 1500.0
        assert result["open"][0]["symbol"] == "NIFTY 24500 CE"

    def test_closed_trades_win_loss_split(self, temp_data_dir):
        today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _insert_main_trade(temp_data_dir, entry_time=today_iso,
                           status="T1_HIT", pnl_rupees=2500.0)
        _insert_main_trade(temp_data_dir, entry_time=today_iso,
                           status="SL_HIT", pnl_rupees=-1000.0)
        _insert_main_trade(temp_data_dir, entry_time=today_iso,
                           status="T2_HIT", pnl_rupees=5000.0)
        result = health_monitor._get_main_trades_today(_today_dt())
        assert result["wins"] == 2
        assert result["losses"] == 1
        assert result["realized_pnl"] == 6500.0

    def test_yesterday_trades_ignored(self, temp_data_dir):
        _insert_main_trade(temp_data_dir,
                           entry_time="2026-04-01T10:00:00",
                           status="T1_HIT", pnl_rupees=1000.0)
        result = health_monitor._get_main_trades_today(_today_dt())
        assert result["total_count"] == 0


class TestScalperTradesQuery:
    def test_empty_scalper_db(self, temp_data_dir):
        result = health_monitor._get_scalper_trades_today(_today_dt())
        assert result["total_count"] == 0

    def test_scalper_trade_counted(self, temp_data_dir):
        today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _insert_scalper_trade(
            temp_data_dir,
            entry_time=today_iso,
            status="EXIT_T1",
            pnl_rupees=800.0,
        )
        result = health_monitor._get_scalper_trades_today(_today_dt())
        assert result["total_count"] == 1
        assert result["wins"] == 1
        assert result["realized_pnl"] == 800.0


# ─────────────────────────────────────────────────────────────────────
# Engine state snapshot
# ─────────────────────────────────────────────────────────────────────

class TestEngineState:
    def test_none_engine(self):
        s = health_monitor._get_engine_state(None)
        assert s["running"] is False
        assert s["ticker_alive"] is False
        assert s["is_stale"] is True

    def test_healthy_engine(self):
        eng = MagicMock()
        eng.running = True
        eng.ticker = object()
        import time as _t
        eng._last_tick_time = _t.time() - 2  # 2 sec ago
        s = health_monitor._get_engine_state(eng)
        assert s["running"] is True
        assert s["ticker_alive"] is True
        assert s["is_stale"] is False

    def test_stale_engine(self):
        eng = MagicMock()
        eng.running = True
        eng.ticker = object()
        import time as _t
        eng._last_tick_time = _t.time() - 120  # 2 min stale
        s = health_monitor._get_engine_state(eng)
        assert s["is_stale"] is True


# ─────────────────────────────────────────────────────────────────────
# Report rendering
# ─────────────────────────────────────────────────────────────────────

class TestReportRendering:
    def test_health_report_contains_key_sections(self, temp_data_dir):
        msg = health_monitor.build_health_report(None, datetime(2026, 5, 18, 10, 30))
        assert "System Health" in msg
        assert "Engine" in msg
        assert "Trades Today" in msg
        assert "Main (PnL Tab)" in msg
        assert "Scalper" in msg
        assert "Net Day P&L" in msg

    def test_eod_summary_with_no_trades(self, temp_data_dir):
        msg = health_monitor.build_eod_summary(None)
        assert "EOD Summary" in msg
        assert "No trades today" in msg

    def test_eod_summary_with_trades(self, temp_data_dir):
        today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _insert_main_trade(temp_data_dir, entry_time=today_iso,
                           status="T1_HIT", pnl_rupees=3000.0)
        msg = health_monitor.build_eod_summary(None)
        assert "EOD Summary" in msg
        assert "1W / 0L" in msg or "1 trades" in msg
