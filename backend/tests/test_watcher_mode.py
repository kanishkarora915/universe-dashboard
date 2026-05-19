"""
Tests for Fix 4 — watcher_mode gate (active / warn_only / disabled).

Background:
  WATCHER_EXIT cost -₹350k combined (5 main + 9 scalper) over 60 days.
  Avg loss per exit: -₹17k main, -₹29k scalper.
  Watcher fires bypass triggers (HARD_LOSS_CAP, FAST_LOSS_CAP, OI_REVERSAL,
  PEAK_FLOOR_HIT) when trade is already deep underwater — reactive, not
  protective. ₹350k of loss comes from trades the watcher EXITED.

Fix: env-gated mode lets user run in warn_only — Telegram alerts but
NO auto-close. Trades hit natural SL/T1/T2/timeout. After data,
user decides whether to revert to active mode with tuned thresholds.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("WATCHER_MODE", raising=False)


class TestModeReading:
    def test_default_is_active(self, monkeypatch, tmp_path):
        """No env override → DB/default → 'active' (current behavior)."""
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        cfg = position_watcher.get_config()
        assert cfg["watcher_mode"] == "active"

    def test_env_warn_only_overrides(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WATCHER_MODE", "warn_only")
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        cfg = position_watcher.get_config()
        assert cfg["watcher_mode"] == "warn_only"

    def test_env_disabled_overrides(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WATCHER_MODE", "disabled")
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        cfg = position_watcher.get_config()
        assert cfg["watcher_mode"] == "disabled"

    def test_invalid_env_falls_back_to_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WATCHER_MODE", "bananas")
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        cfg = position_watcher.get_config()
        # invalid env → DB default = "active"
        assert cfg["watcher_mode"] == "active"

    def test_env_case_insensitive(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WATCHER_MODE", "WARN_ONLY")
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        cfg = position_watcher.get_config()
        assert cfg["watcher_mode"] == "warn_only"


class TestModeHelpers:
    def test_is_warn_only_true_for_warn_only(self):
        from position_watcher import is_warn_only
        assert is_warn_only({"watcher_mode": "warn_only"}) is True

    def test_is_warn_only_true_for_disabled(self):
        """disabled mode is also warn_only (no auto-close)."""
        from position_watcher import is_warn_only
        assert is_warn_only({"watcher_mode": "disabled"}) is True

    def test_is_warn_only_false_for_active(self):
        from position_watcher import is_warn_only
        assert is_warn_only({"watcher_mode": "active"}) is False

    def test_is_disabled_only_for_disabled(self):
        from position_watcher import is_disabled
        assert is_disabled({"watcher_mode": "disabled"}) is True
        assert is_disabled({"watcher_mode": "warn_only"}) is False
        assert is_disabled({"watcher_mode": "active"}) is False

    def test_helpers_default_to_get_config(self, monkeypatch, tmp_path):
        """When cfg not passed, helpers should read live config."""
        monkeypatch.setenv("WATCHER_MODE", "warn_only")
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        assert position_watcher.is_warn_only() is True
        assert position_watcher.is_disabled() is False


class TestEnvFlow:
    """Verify the full env → config → helper chain works in one shot."""

    def test_flip_to_warn_only(self, monkeypatch, tmp_path):
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        monkeypatch.setenv("WATCHER_MODE", "warn_only")
        cfg = position_watcher.get_config()
        assert position_watcher.is_warn_only(cfg) is True

    def test_flip_to_disabled(self, monkeypatch, tmp_path):
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        monkeypatch.setenv("WATCHER_MODE", "disabled")
        cfg = position_watcher.get_config()
        assert position_watcher.is_warn_only(cfg) is True
        assert position_watcher.is_disabled(cfg) is True

    def test_back_to_active_unsets(self, monkeypatch, tmp_path):
        import position_watcher
        monkeypatch.setattr(position_watcher, "WATCHER_DB", str(tmp_path / "w.db"))
        monkeypatch.delenv("WATCHER_MODE", raising=False)
        cfg = position_watcher.get_config()
        assert position_watcher.is_warn_only(cfg) is False
