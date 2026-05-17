"""
Tests for perf_monitor — system metrics sampling.
"""

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import perf_monitor


@pytest.fixture
def temp_db(monkeypatch):
    """Point perf_monitor at a temp council.db."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)
    monkeypatch.setattr(perf_monitor, "_resolve_council_db", lambda: tmp_path)
    monkeypatch.setattr(perf_monitor, "_schema_applied", False)
    yield tmp_path
    tmp_path.unlink(missing_ok=True)


class TestTakeSample:
    def test_sample_contains_all_expected_fields(self, temp_db):
        sample = perf_monitor.take_sample(lambda: None)
        expected_keys = {
            "ts", "iso", "memory_rss_mb", "memory_vms_mb", "memory_available_mb",
            "cpu_percent", "load_avg_1min", "thread_count", "thread_names",
            "disk_used_mb", "disk_free_mb", "disk_percent",
            "engine_running", "engine_last_tick_age_sec", "ws_alive",
            "open_fds", "db_sizes", "extra",
        }
        assert expected_keys.issubset(sample.keys())

    def test_engine_none_reports_zero(self, temp_db):
        sample = perf_monitor.take_sample(lambda: None)
        assert sample["engine_running"] == 0
        assert sample["ws_alive"] == 0
        assert sample["engine_last_tick_age_sec"] is None

    def test_engine_running_reports_correctly(self, temp_db):
        eng = MagicMock()
        eng.running = True
        eng.ticker = object()
        eng._last_tick_time = time.time() - 5
        sample = perf_monitor.take_sample(lambda: eng)
        assert sample["engine_running"] == 1
        assert sample["ws_alive"] == 1
        assert sample["engine_last_tick_age_sec"] > 0

    def test_thread_count_positive(self, temp_db):
        sample = perf_monitor.take_sample(lambda: None)
        assert sample["thread_count"] >= 1  # at least main thread

    def test_sample_persists_to_db(self, temp_db):
        perf_monitor.take_sample(lambda: None)
        latest = perf_monitor.get_latest_sample()
        assert latest is not None
        assert "memory_rss_mb" in latest


class TestHistory:
    def test_history_empty_when_no_samples(self, temp_db):
        history = perf_monitor.get_history(hours=24)
        assert history == []

    def test_history_returns_recent_samples(self, temp_db):
        for _ in range(3):
            perf_monitor.take_sample(lambda: None)
            time.sleep(0.01)
        history = perf_monitor.get_history(hours=24)
        assert len(history) == 3

    def test_history_orders_newest_first(self, temp_db):
        for _ in range(3):
            perf_monitor.take_sample(lambda: None)
            time.sleep(0.02)
        history = perf_monitor.get_history(hours=24)
        # Most recent first
        assert history[0]["ts"] >= history[-1]["ts"]


class TestSchemaIdempotent:
    def test_repeated_init_safe(self, temp_db):
        # Should be safe to call multiple times
        perf_monitor._ensure_schema()
        perf_monitor._schema_applied = False  # force re-init
        perf_monitor._ensure_schema()
        # No exception = pass
        assert True
