"""
Tests for trinity.prune — DB maintenance.
"""

import sys
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from trinity import prune


@pytest.fixture
def temp_trinity_db(monkeypatch):
    """Each test gets a fresh trinity.db at a tmp path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)
    monkeypatch.setattr(prune, "_trinity_db_path", lambda: tmp_path)

    # Create minimal trinity schema matching storage.py
    conn = sqlite3.connect(str(tmp_path))
    conn.execute("""
        CREATE TABLE trinity_ticks (
            ts INTEGER, spot REAL, future REAL, synthetic REAL,
            deviation REAL, premium REAL
        )
    """)
    conn.execute("""
        CREATE TABLE trinity_strike_data (
            ts INTEGER, strike INTEGER, side TEXT,
            ltp REAL, oi INTEGER, volume INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE trinity_signals (
            ts INTEGER, regime TEXT, conviction REAL
        )
    """)
    conn.commit()
    conn.close()

    yield tmp_path
    tmp_path.unlink(missing_ok=True)


def _insert_ticks(db_path: Path, count: int, age_seconds: int = 0):
    """Insert `count` tick rows aged `age_seconds` from now."""
    conn = sqlite3.connect(str(db_path))
    base_ts = int((time.time() - age_seconds) * 1000)
    for i in range(count):
        conn.execute(
            "INSERT INTO trinity_ticks (ts, spot, future, synthetic, deviation, premium) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (base_ts + i, 23000 + i, 23010 + i, 23005 + i, 0.0, 10),
        )
    conn.commit()
    conn.close()


def _insert_strike_data(db_path: Path, count: int, age_seconds: int = 0):
    conn = sqlite3.connect(str(db_path))
    base_ts = int((time.time() - age_seconds) * 1000)
    for i in range(count):
        conn.execute(
            "INSERT INTO trinity_strike_data (ts, strike, side, ltp, oi, volume) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (base_ts + i, 23000, "CE", 100.0, 50000, 1000),
        )
    conn.commit()
    conn.close()


class TestPrune:
    def test_prune_empty_db_no_crash(self, temp_trinity_db):
        result = prune.prune_trinity_db()
        assert result["error"] is None
        assert result["ticks_deleted"] == 0
        assert result["strike_rows_deleted"] == 0

    def test_prune_deletes_old_ticks_only(self, temp_trinity_db):
        # 100 recent ticks (1 hour old) — should be KEPT
        _insert_ticks(temp_trinity_db, count=100, age_seconds=3600)
        # 100 old ticks (10 days old) — should be DELETED (default 7-day keep)
        _insert_ticks(temp_trinity_db, count=100, age_seconds=10 * 86400)

        result = prune.prune_trinity_db(raw_ticks_keep_days=7)
        assert result["error"] is None
        assert result["ticks_deleted"] == 100

        # Verify only recent remained
        conn = sqlite3.connect(str(temp_trinity_db))
        remaining = conn.execute("SELECT COUNT(*) FROM trinity_ticks").fetchone()[0]
        conn.close()
        assert remaining == 100

    def test_prune_deletes_old_strike_data(self, temp_trinity_db):
        _insert_strike_data(temp_trinity_db, count=50, age_seconds=3600)
        _insert_strike_data(temp_trinity_db, count=50, age_seconds=20 * 86400)

        result = prune.prune_trinity_db(strike_data_keep_days=14)
        assert result["strike_rows_deleted"] == 50

    def test_prune_doesnt_touch_signals(self, temp_trinity_db):
        """Signals table should never be pruned by this function."""
        conn = sqlite3.connect(str(temp_trinity_db))
        # Very old signal
        conn.execute(
            "INSERT INTO trinity_signals (ts, regime, conviction) VALUES (?, ?, ?)",
            (int((time.time() - 365 * 86400) * 1000), "BULL_TRAP", 0.8),
        )
        conn.commit()
        conn.close()

        prune.prune_trinity_db()

        conn = sqlite3.connect(str(temp_trinity_db))
        n = conn.execute("SELECT COUNT(*) FROM trinity_signals").fetchone()[0]
        conn.close()
        assert n == 1  # signal preserved

    def test_vacuum_reclaims_space(self, temp_trinity_db):
        # Insert a lot, prune, verify file shrinks
        _insert_ticks(temp_trinity_db, count=5000, age_seconds=10 * 86400)
        size_before = temp_trinity_db.stat().st_size

        result = prune.prune_trinity_db(do_vacuum=True)

        size_after = temp_trinity_db.stat().st_size
        assert size_after < size_before  # VACUUM reclaimed space
        assert result["vacuum_ran"] is True

    def test_prune_returns_useful_stats(self, temp_trinity_db):
        _insert_ticks(temp_trinity_db, count=10, age_seconds=10 * 86400)
        result = prune.prune_trinity_db()
        assert "started_at" in result
        assert "duration_sec" in result
        assert "size_before_mb" in result
        assert "size_after_mb" in result
        assert "freed_mb" in result
        assert result["duration_sec"] >= 0


class TestGetStats:
    def test_stats_empty_db(self, temp_trinity_db):
        stats = prune.get_trinity_db_stats()
        assert stats["exists"] is True
        assert stats["size_mb"] >= 0
        assert "trinity_ticks" in stats["tables"]
        assert stats["tables"]["trinity_ticks"]["row_count"] == 0

    def test_stats_with_data(self, temp_trinity_db):
        _insert_ticks(temp_trinity_db, count=42, age_seconds=3600)
        stats = prune.get_trinity_db_stats()
        assert stats["tables"]["trinity_ticks"]["row_count"] == 42
        assert stats["tables"]["trinity_ticks"]["min_ts"] is not None
        assert stats["tables"]["trinity_ticks"]["max_ts"] is not None
