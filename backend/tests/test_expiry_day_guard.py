"""
Tests for expiry_day_guard (Fix 1 — Tuesday NIFTY weekly expiry skip).

Background:
  60-day audit: Tuesday (NIFTY weekly expiry) = combined -₹193,805 loss
  across both engines. 24% of all losses on ONE day of week.
  Theta crusher + max-pain pin = BUYER strategy gets murdered.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("EXPIRY_DAY_SKIP_ENABLED", raising=False)
    monkeypatch.delenv("EXPIRY_DAY_SHADOW", raising=False)
    monkeypatch.delenv("EXPIRY_DAY_SKIP_TUESDAY", raising=False)
    monkeypatch.delenv("EXPIRY_DAY_SKIP_MONDAY", raising=False)
    monkeypatch.delenv("EXPIRY_DAY_ALLOW_LATE_HOUR", raising=False)


def _ist(year, month, day, hour=10, minute=30):
    return IST.localize(datetime(year, month, day, hour, minute))


# ── ENV FLAGS ──────────────────────────────────────────────────────────

class TestEnvFlags:
    def test_default_master_disabled(self):
        from expiry_day_guard import is_enabled
        assert is_enabled() is False

    def test_enabled_when_on(self, monkeypatch):
        monkeypatch.setenv("EXPIRY_DAY_SKIP_ENABLED", "on")
        from expiry_day_guard import is_enabled
        assert is_enabled() is True

    def test_tuesday_default_on(self):
        from expiry_day_guard import skip_tuesday
        assert skip_tuesday() is True

    def test_monday_default_off(self):
        from expiry_day_guard import skip_monday
        assert skip_monday() is False

    def test_late_hour_default_14(self):
        from expiry_day_guard import allow_late_hour
        assert allow_late_hour() == 14

    def test_late_hour_configurable(self, monkeypatch):
        monkeypatch.setenv("EXPIRY_DAY_ALLOW_LATE_HOUR", "15")
        from expiry_day_guard import allow_late_hour
        assert allow_late_hour() == 15


# ── DAY-OF-WEEK DETECTION ──────────────────────────────────────────────

class TestAssess:
    def test_tuesday_morning_skips(self):
        """Tuesday 10:30 AM — should be flagged to skip."""
        from expiry_day_guard import assess
        # 2026-05-19 is a Tuesday
        d = assess(now=_ist(2026, 5, 19, 10, 30))
        assert d["is_tuesday"] is True
        assert d["skip"] is True
        assert "Tuesday" in d["reason"]

    def test_tuesday_late_hour_passes(self):
        """Tuesday 2:30 PM — after pin action, allowed."""
        from expiry_day_guard import assess
        d = assess(now=_ist(2026, 5, 19, 14, 30))
        assert d["is_tuesday"] is True
        assert d["is_late_hour"] is True
        assert d["skip"] is False

    def test_monday_default_passes(self):
        """Monday — by default NOT skipped."""
        from expiry_day_guard import assess
        d = assess(now=_ist(2026, 5, 18, 10, 30))  # Monday
        assert d["is_monday"] is True
        assert d["skip"] is False

    def test_monday_skip_when_enabled(self, monkeypatch):
        monkeypatch.setenv("EXPIRY_DAY_SKIP_MONDAY", "on")
        from expiry_day_guard import assess
        d = assess(now=_ist(2026, 5, 18, 10, 30))
        assert d["is_monday"] is True
        assert d["skip"] is True
        assert "Monday" in d["reason"]

    def test_wednesday_passes(self):
        from expiry_day_guard import assess
        d = assess(now=_ist(2026, 5, 20, 10, 30))  # Wednesday
        assert d["day_of_week"] == "Wed"
        assert d["skip"] is False

    def test_thursday_passes(self):
        from expiry_day_guard import assess
        d = assess(now=_ist(2026, 5, 21, 10, 30))  # Thursday
        assert d["day_of_week"] == "Thu"
        assert d["skip"] is False

    def test_friday_passes(self):
        from expiry_day_guard import assess
        d = assess(now=_ist(2026, 5, 22, 10, 30))  # Friday
        assert d["day_of_week"] == "Fri"
        assert d["skip"] is False

    def test_tuesday_can_disable_skip(self, monkeypatch):
        """User can disable Tuesday skip via env."""
        monkeypatch.setenv("EXPIRY_DAY_SKIP_TUESDAY", "off")
        from expiry_day_guard import assess
        d = assess(now=_ist(2026, 5, 19, 10, 30))
        assert d["is_tuesday"] is True
        assert d["skip"] is False


# ── should_skip — public API ───────────────────────────────────────────

class TestShouldSkip:
    def test_disabled_never_blocks(self):
        """When master flag is OFF, never blocks regardless of day."""
        from expiry_day_guard import should_skip
        result = should_skip(source="test", now=_ist(2026, 5, 19, 10, 30))  # Tuesday
        # Master flag default off → no block
        assert result is False

    def test_enabled_blocks_tuesday(self, monkeypatch):
        monkeypatch.setenv("EXPIRY_DAY_SKIP_ENABLED", "on")
        from expiry_day_guard import should_skip
        result = should_skip(source="test", now=_ist(2026, 5, 19, 10, 30))  # Tuesday
        assert result is True

    def test_enabled_allows_wednesday(self, monkeypatch):
        monkeypatch.setenv("EXPIRY_DAY_SKIP_ENABLED", "on")
        from expiry_day_guard import should_skip
        result = should_skip(source="test", now=_ist(2026, 5, 20, 10, 30))  # Wed
        assert result is False

    def test_enabled_allows_tuesday_late(self, monkeypatch):
        """Tuesday after 14:00 (default) is allowed (post-pin)."""
        monkeypatch.setenv("EXPIRY_DAY_SKIP_ENABLED", "on")
        from expiry_day_guard import should_skip
        result = should_skip(source="test", now=_ist(2026, 5, 19, 14, 30))
        assert result is False

    def test_late_hour_configurable(self, monkeypatch):
        """Configure allow_late_hour=15 — 14:30 should still block."""
        monkeypatch.setenv("EXPIRY_DAY_SKIP_ENABLED", "on")
        monkeypatch.setenv("EXPIRY_DAY_ALLOW_LATE_HOUR", "15")
        from expiry_day_guard import should_skip
        result = should_skip(source="test", now=_ist(2026, 5, 19, 14, 30))
        assert result is True
