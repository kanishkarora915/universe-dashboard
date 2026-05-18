"""
Tests for the auto-login status DB layer + Telegram alerts.

Verifies:
  • Schema initialization for the auto_login_attempts table
  • Per-attempt logging works + persists
  • Status & summary queries return correctly grouped data
  • telegram_alerts module gracefully handles missing config
"""

import sys
import os
import time
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from council import storage


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(monkeypatch):
    """Each test gets a fresh isolated council.db."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)
    monkeypatch.setattr(storage, "_resolve_db_path", lambda: tmp_path)
    monkeypatch.setattr(storage, "_schema_applied", False)
    storage.init_db()
    yield tmp_path
    tmp_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────
# Auto-login attempts persistence
# ─────────────────────────────────────────────────────────────────────

class TestAutoLoginPersistence:
    def test_schema_creates_auto_login_table(self, temp_db):
        import sqlite3
        conn = sqlite3.connect(str(temp_db))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        conn.close()
        assert "auto_login_attempts" in tables

    def test_log_success_attempt(self, temp_db):
        storage.log_autologin_attempt(
            trigger_source="daemon",
            status="success",
            access_token_preview="abc12345",
            duration_ms=850,
            extra={"attempt": 1},
        )
        rows = storage.get_recent_autologin_attempts()
        assert len(rows) == 1
        r = rows[0]
        assert r["trigger_source"] == "daemon"
        assert r["status"] == "success"
        assert r["access_token_preview"] == "abc12345"
        assert r["duration_ms"] == 850

    def test_log_failed_attempt_with_error(self, temp_db):
        storage.log_autologin_attempt(
            trigger_source="daemon",
            status="failed",
            error="TOTP mismatch",
            duration_ms=1200,
        )
        rows = storage.get_recent_autologin_attempts()
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["error"] == "TOTP mismatch"

    def test_get_recent_ordered_by_timestamp_desc(self, temp_db):
        # Insert 3 attempts with slight delays
        for i in range(3):
            storage.log_autologin_attempt(
                trigger_source="daemon",
                status="success" if i == 2 else "failed",
                duration_ms=i * 100,
            )
            time.sleep(0.01)  # ensure distinct timestamps
        rows = storage.get_recent_autologin_attempts()
        assert len(rows) == 3
        # Most recent first → 3rd insert (success) should be first
        assert rows[0]["status"] == "success"
        # Earliest insert should be last
        assert rows[-1]["duration_ms"] == 0

    def test_logging_failures_dont_propagate(self, temp_db, monkeypatch):
        """Even if DB write fails, caller shouldn't crash."""
        # Force _conn to raise
        def broken_conn():
            raise IOError("DB unavailable")
        monkeypatch.setattr(storage, "_conn", broken_conn)
        # Should NOT raise
        storage.log_autologin_attempt(
            trigger_source="daemon", status="failed", error="test",
        )


# ─────────────────────────────────────────────────────────────────────
# Summary aggregation
# ─────────────────────────────────────────────────────────────────────

class TestAutoLoginSummary:
    def test_empty_summary_returns_zero_totals(self, temp_db):
        summary = storage.get_autologin_summary(days=7)
        assert summary["days"] == 7
        assert summary["by_day"] == {}
        assert summary["totals"] == {}

    def test_summary_groups_by_day_and_source(self, temp_db):
        # 3 attempts today: 1 daemon success, 1 daemon fail, 1 manual success
        storage.log_autologin_attempt(trigger_source="daemon",        status="success")
        storage.log_autologin_attempt(trigger_source="daemon",        status="failed")
        storage.log_autologin_attempt(trigger_source="manual",        status="success")
        storage.log_autologin_attempt(trigger_source="external_cron", status="success")

        summary = storage.get_autologin_summary(days=1)
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in summary["by_day"]
        by_source = summary["by_day"][today]
        assert by_source["daemon"]["success"] == 1
        assert by_source["daemon"]["failed"] == 1
        assert by_source["daemon"]["total"] == 2
        assert by_source["manual"]["total"] == 1
        assert by_source["external_cron"]["total"] == 1

        # totals
        assert summary["totals"]["success"] == 3
        assert summary["totals"]["failed"] == 1


# ─────────────────────────────────────────────────────────────────────
# Telegram alerts
# ─────────────────────────────────────────────────────────────────────

class TestTelegramAlertsDisabled:
    """When env vars unset, all calls should be safe no-ops."""

    def test_is_enabled_false_when_no_token(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        # Force reload — env reads happen at import time
        import importlib
        import telegram_alerts
        importlib.reload(telegram_alerts)
        assert telegram_alerts.is_enabled() is False

    def test_send_silent_when_disabled(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        import importlib
        import telegram_alerts
        importlib.reload(telegram_alerts)
        # Should not raise, not call requests
        telegram_alerts.send("test message")
        telegram_alerts.alert_engine_started("daemon", "abc123")
        telegram_alerts.alert_engine_down("dead")


class TestTelegramAlertsThrottle:
    def test_same_key_throttled(self, monkeypatch):
        # Pretend enabled
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        import importlib
        import telegram_alerts
        importlib.reload(telegram_alerts)

        # Mock the actual sender so we count calls without HTTP
        send_calls = []
        monkeypatch.setattr(
            telegram_alerts, "_send_sync",
            lambda text, parse_mode="Markdown": send_calls.append(text) or True,
        )

        # 5 rapid sends with same key → only 1 should actually send
        for i in range(5):
            telegram_alerts.send(f"message {i}", key="test_throttle")

        # Wait for any threads
        time.sleep(0.2)

        assert len(send_calls) == 1, f"Expected 1 send, got {len(send_calls)}"

    def test_different_keys_not_throttled(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        import importlib
        import telegram_alerts
        importlib.reload(telegram_alerts)

        send_calls = []
        monkeypatch.setattr(
            telegram_alerts, "_send_sync",
            lambda text, parse_mode="Markdown": send_calls.append(text) or True,
        )

        # 3 sends with different keys → all should fire
        telegram_alerts.send("msg A", key="key_a")
        telegram_alerts.send("msg B", key="key_b")
        telegram_alerts.send("msg C", key="key_c")
        time.sleep(0.2)

        assert len(send_calls) == 3
