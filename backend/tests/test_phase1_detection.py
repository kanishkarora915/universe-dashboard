"""
Tests for Phase 1 DETECTION modules:
  • regime_monitor
  • pattern_shift_detector
  • daily_diagnostic
  • anomaly_alerts

Built 2026-05-21 to enable early-warning system for regime shifts
(W18 → W19 type collapses caught in 3-5 trades instead of week-end).
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


def _seed(db_path, table, rows):
    """rows = [(entry_time_iso, exit_time_iso, status, pnl), ...]"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"CREATE TABLE {table} ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "entry_time TEXT, exit_time TEXT, status TEXT, "
        "pnl_rupees REAL, entry_price REAL DEFAULT 100, "
        "exit_price REAL DEFAULT 100, peak_ltp REAL DEFAULT 100, "
        "action TEXT DEFAULT 'BUY CE', idx TEXT DEFAULT 'NIFTY', "
        "strike INTEGER DEFAULT 23500)"
    )
    for et, xt, st, pnl in rows:
        conn.execute(
            f"INSERT INTO {table} (entry_time, exit_time, status, pnl_rupees) "
            "VALUES (?, ?, ?, ?)",
            (et, xt, st, pnl),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in [
        "REGIME_MONITOR_ENABLED",
        "PATTERN_SHIFT_ENABLED",
        "DAILY_DIAGNOSTIC_ENABLED",
        "ANOMALY_ALERTS_ENABLED",
    ]:
        monkeypatch.delenv(var, raising=False)


# ── REGIME MONITOR ──────────────────────────────────────────────────────

class TestRegimeMonitorKPIs:
    def test_kpis_empty_returns_empty(self):
        from regime_monitor import compute_kpis
        assert compute_kpis([]) == {}

    def test_kpis_for_winning_trades(self):
        from regime_monitor import compute_kpis
        trades = [
            {"entry_time": "2026-05-21T10:00:00+05:30",
             "exit_time": "2026-05-21T10:15:00+05:30",
             "status": "T1_HIT", "pnl_rupees": 5000},
            {"entry_time": "2026-05-21T11:00:00+05:30",
             "exit_time": "2026-05-21T11:15:00+05:30",
             "status": "TRAIL_EXIT", "pnl_rupees": 3000},
        ]
        kpis = compute_kpis(trades)
        assert kpis["wr_pct"] == 100.0
        assert kpis["avg_win"] == 4000
        assert kpis["t1_hit_rate"] == 50.0
        assert kpis["_n_trades"] == 2

    def test_kpis_handle_losses(self):
        from regime_monitor import compute_kpis
        trades = [
            {"entry_time": "2026-05-21T10:00:00+05:30",
             "exit_time": "2026-05-21T10:15:00+05:30",
             "status": "SL_HIT", "pnl_rupees": -2000},
            {"entry_time": "2026-05-21T11:00:00+05:30",
             "exit_time": "2026-05-21T11:15:00+05:30",
             "status": "REVERSAL_EXIT", "pnl_rupees": -1500},
        ]
        kpis = compute_kpis(trades)
        assert kpis["wr_pct"] == 0
        assert kpis["avg_loss"] == -1750
        assert kpis["reversal_exit_rate"] == 50.0
        assert kpis["sl_hit_rate"] == 50.0


class TestRegimeAssessment:
    def test_insufficient_baseline_returns_ok(self, monkeypatch, tmp_path):
        """When baseline < 20 trades, returns OK with warning."""
        import regime_monitor
        db = tmp_path / "main.db"
        # Only 5 baseline trades
        _seed(db, "trades", [
            (f"2026-05-{15+i}T10:00:00+05:30", f"2026-05-{15+i}T10:15:00+05:30",
             "T1_HIT", 1000) for i in range(5)
        ])
        scalp_db = tmp_path / "scalp.db"
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(regime_monitor, "_TRADES_DB", db)
        monkeypatch.setattr(regime_monitor, "_SCALPER_DB", scalp_db)
        result = regime_monitor.assess(tab="MAIN")
        assert result["severity"] == "OK"
        assert "Insufficient" in result["summary"]

    def test_normal_data_returns_ok(self, monkeypatch, tmp_path):
        """Random uniform data should produce OK severity."""
        import regime_monitor
        db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        # 30 days of trades, all similar profile
        rows = []
        for i in range(30):
            date_str = f"2026-04-{(i % 28) + 1:02d}"
            for j in range(3):
                pnl = 1000 if (i + j) % 2 == 0 else -800
                status = "T1_HIT" if pnl > 0 else "SL_HIT"
                rows.append((
                    f"{date_str}T1{j}:00:00+05:30",
                    f"{date_str}T1{j}:15:00+05:30",
                    status, pnl,
                ))
        _seed(db, "trades", rows)
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(regime_monitor, "_TRADES_DB", db)
        monkeypatch.setattr(regime_monitor, "_SCALPER_DB", scalp_db)
        result = regime_monitor.assess(tab="MAIN")
        # Should be OK or INFO at worst
        assert result["severity"] in ("OK", "INFO")


# ── PATTERN SHIFT DETECTOR ──────────────────────────────────────────────

class TestPatternShiftDetector:
    def test_no_today_trades_returns_ok(self, monkeypatch, tmp_path):
        import pattern_shift_detector
        db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        _seed(db, "trades", [])
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(pattern_shift_detector, "_TRADES_DB", db)
        monkeypatch.setattr(pattern_shift_detector, "_SCALPER_DB", scalp_db)
        d = pattern_shift_detector.detect_shifts(tab="MAIN")
        assert d["alert_level"] == "OK"
        assert d["today_n"] == 0

    def test_consecutive_losses_triggers_alert(self, monkeypatch, tmp_path):
        """4 losing exits in a row → CRITICAL."""
        import pattern_shift_detector
        db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        # 4 losses in a row + some baseline data
        rows_today = [
            (f"{today}T10:00:00+05:30", f"{today}T10:15:00+05:30", "SL_HIT", -2000),
            (f"{today}T10:30:00+05:30", f"{today}T10:45:00+05:30", "WATCHER_EXIT", -3000),
            (f"{today}T11:00:00+05:30", f"{today}T11:15:00+05:30", "REVERSAL_EXIT", -1500),
            (f"{today}T11:30:00+05:30", f"{today}T11:45:00+05:30", "SL_HIT", -2500),
        ]
        # Baseline
        rows_baseline = [
            (f"2026-04-{i:02d}T10:00:00+05:30",
             f"2026-04-{i:02d}T10:15:00+05:30",
             "T1_HIT", 1500) for i in range(10, 30)
        ]
        _seed(db, "trades", rows_today + rows_baseline)
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(pattern_shift_detector, "_TRADES_DB", db)
        monkeypatch.setattr(pattern_shift_detector, "_SCALPER_DB", scalp_db)
        d = pattern_shift_detector.detect_shifts(tab="MAIN")
        assert d["alert_level"] == "CRITICAL"
        assert d["consecutive_losses"] == 4

    def test_consecutive_watcher_exits_critical(self, monkeypatch, tmp_path):
        """2 watcher exits in a row → CRITICAL."""
        import pattern_shift_detector
        db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        rows_today = [
            (f"{today}T10:00:00+05:30", f"{today}T10:15:00+05:30", "WATCHER_EXIT", -8000),
            (f"{today}T10:30:00+05:30", f"{today}T10:45:00+05:30", "WATCHER_EXIT", -6000),
        ]
        rows_baseline = [
            (f"2026-04-{i:02d}T10:00:00+05:30",
             f"2026-04-{i:02d}T10:15:00+05:30",
             "T1_HIT", 1500) for i in range(10, 30)
        ]
        _seed(db, "trades", rows_today + rows_baseline)
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(pattern_shift_detector, "_TRADES_DB", db)
        monkeypatch.setattr(pattern_shift_detector, "_SCALPER_DB", scalp_db)
        d = pattern_shift_detector.detect_shifts(tab="MAIN")
        assert d["alert_level"] == "CRITICAL"
        assert d["consecutive_watcher_exits"] == 2

    def test_normal_session_returns_ok(self, monkeypatch, tmp_path):
        import pattern_shift_detector
        db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        # Mostly winning trades today
        rows_today = [
            (f"{today}T10:00:00+05:30", f"{today}T10:15:00+05:30", "T1_HIT", 2000),
            (f"{today}T10:30:00+05:30", f"{today}T10:45:00+05:30", "TRAIL_EXIT", 1500),
            (f"{today}T11:00:00+05:30", f"{today}T11:15:00+05:30", "T2_HIT", 3000),
        ]
        rows_baseline = [
            (f"2026-04-{i:02d}T10:00:00+05:30",
             f"2026-04-{i:02d}T10:15:00+05:30",
             "T1_HIT" if i % 2 == 0 else "SL_HIT",
             1500 if i % 2 == 0 else -1000) for i in range(10, 30)
        ]
        _seed(db, "trades", rows_today + rows_baseline)
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(pattern_shift_detector, "_TRADES_DB", db)
        monkeypatch.setattr(pattern_shift_detector, "_SCALPER_DB", scalp_db)
        d = pattern_shift_detector.detect_shifts(tab="MAIN")
        assert d["alert_level"] in ("OK", "INFO")
        assert d["consecutive_losses"] == 0


# ── DAILY DIAGNOSTIC ────────────────────────────────────────────────────

class TestDailyDiagnostic:
    def test_no_trades_today_returns_empty_report(self, monkeypatch, tmp_path):
        import daily_diagnostic
        db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        _seed(db, "trades", [])
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(daily_diagnostic, "_TRADES_DB", db)
        monkeypatch.setattr(daily_diagnostic, "_SCALPER_DB", scalp_db)
        report = daily_diagnostic.generate_report()
        assert report["total"] == 0
        assert "No trades" in report["summary"]

    def test_winning_day_report(self, monkeypatch, tmp_path):
        import daily_diagnostic
        db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        rows = [
            (f"{today}T10:00:00+05:30", f"{today}T10:15:00+05:30", "T1_HIT", 5000),
            (f"{today}T11:00:00+05:30", f"{today}T11:15:00+05:30", "TRAIL_EXIT", 3000),
            (f"{today}T12:00:00+05:30", f"{today}T12:15:00+05:30", "SL_HIT", -2000),
        ]
        _seed(db, "trades", rows)
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(daily_diagnostic, "_TRADES_DB", db)
        monkeypatch.setattr(daily_diagnostic, "_SCALPER_DB", scalp_db)
        report = daily_diagnostic.generate_report(date_iso=today)
        assert report["total"] == 3
        assert report["wins"] == 2
        assert report["losses"] == 1
        assert report["wr_pct"] == 66.7
        assert report["net_pnl"] == 6000
        assert "Profitable day" in report["verdict"] or "EXCELLENT" in report["verdict"]

    def test_telegram_format_includes_summary(self, monkeypatch, tmp_path):
        import daily_diagnostic
        db = tmp_path / "main.db"
        scalp_db = tmp_path / "scalp.db"
        today = datetime.now(IST).strftime("%Y-%m-%d")
        rows = [
            (f"{today}T10:00:00+05:30", f"{today}T10:15:00+05:30", "T1_HIT", 5000),
        ]
        _seed(db, "trades", rows)
        _seed(scalp_db, "scalper_trades", [])
        monkeypatch.setattr(daily_diagnostic, "_TRADES_DB", db)
        monkeypatch.setattr(daily_diagnostic, "_SCALPER_DB", scalp_db)
        report = daily_diagnostic.generate_report(date_iso=today)
        tg_msg = daily_diagnostic.format_telegram(report)
        assert "EOD REPORT" in tg_msg
        assert "Trades: 1" in tg_msg
        assert "Verdict:" in tg_msg


# ── ANOMALY ALERTS ──────────────────────────────────────────────────────

class TestAnomalyAlerts:
    def test_disabled_returns_quickly(self, monkeypatch):
        monkeypatch.setenv("ANOMALY_ALERTS_ENABLED", "off")
        from anomaly_alerts import run_periodic_checks
        result = run_periodic_checks()
        assert result["enabled"] is False
        assert result["alerts_fired"] == []

    def test_outside_market_hours_no_check(self, monkeypatch):
        """Outside 9:15-15:30 IST, returns without checking."""
        import anomaly_alerts
        # Force "after-market hours" by mocking the time check
        monkeypatch.setattr(anomaly_alerts, "_market_hours", lambda: False)
        result = anomaly_alerts.run_periodic_checks()
        assert result.get("in_market_hours") is False
        assert result["alerts_fired"] == []

    def test_get_status_includes_throttle(self):
        from anomaly_alerts import get_status
        s = get_status()
        assert "enabled" in s
        assert "today_alerts_fired" in s
        assert "throttle_state" in s

    def test_can_alert_throttling(self):
        from anomaly_alerts import _can_alert, _mark_alerted, _today_alerts, _last_alert
        # Clear state
        _last_alert.clear()
        _today_alerts.clear()
        # First call should be allowed
        assert _can_alert("test_key", cooldown_min=60) is True
        _mark_alerted("test_key")
        # Immediate second call should be blocked
        assert _can_alert("test_key", cooldown_min=60) is False

    def test_once_per_day_blocks_second_fire(self):
        from anomaly_alerts import _can_alert, _mark_alerted, _today_alerts, _last_alert
        _last_alert.clear()
        _today_alerts.clear()
        # First fire allowed
        assert _can_alert("daily_key", once_per_day=True) is True
        _mark_alerted("daily_key", once_per_day=True)
        # Even after long time, second fire blocked because once_per_day
        _last_alert["daily_key"] = 0  # simulate long ago
        assert _can_alert("daily_key", once_per_day=True) is False
