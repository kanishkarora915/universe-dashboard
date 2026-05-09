"""
Tests for db_migrations.py — schema versioning system.

Critical because: failed migrations could corrupt DBs or block deploy.

Run: pytest backend/tests/test_db_migrations.py -v
"""

import sys
import os
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from db_migrations import (
    register,
    apply_all_pending,
    get_version,
    get_applied_versions,
    status,
    _registry,
)


@pytest.fixture
def temp_db():
    """Create a temp SQLite DB, clear registry per-test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    _registry.clear()
    yield path
    os.unlink(path)
    _registry.clear()


class TestRegisterMigration:
    def test_register_single(self, temp_db):
        register(temp_db, 1, "test", "CREATE TABLE x (id INTEGER)")
        assert temp_db in _registry
        assert len(_registry[temp_db]) == 1

    def test_register_multiple(self, temp_db):
        register(temp_db, 1, "first", "CREATE TABLE x (id INTEGER)")
        register(temp_db, 2, "second", "ALTER TABLE x ADD COLUMN name TEXT")
        assert len(_registry[temp_db]) == 2

    def test_register_dedupes_by_version(self, temp_db):
        """Registering same version twice should NOT duplicate."""
        register(temp_db, 1, "first", "CREATE TABLE x (id INTEGER)")
        register(temp_db, 1, "first_again", "CREATE TABLE y (id INTEGER)")
        assert len(_registry[temp_db]) == 1


class TestApplyMigrations:
    def test_apply_new_migration(self, temp_db):
        register(temp_db, 1, "create_users",
                 "CREATE TABLE users (id INTEGER PRIMARY KEY)")

        result = apply_all_pending(temp_db)

        assert result["applied"] == [1]
        assert result["errors"] == []

        # Verify table exists
        conn = sqlite3.connect(temp_db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    def test_apply_idempotent(self, temp_db):
        """Running twice — second time should skip already-applied."""
        register(temp_db, 1, "test", "CREATE TABLE x (id INTEGER)")

        r1 = apply_all_pending(temp_db)
        r2 = apply_all_pending(temp_db)

        assert r1["applied"] == [1]
        assert r2["applied"] == []
        assert r2["skipped"] == [1]

    def test_apply_in_order(self, temp_db):
        """Multiple migrations applied in version order."""
        register(temp_db, 2, "second", "ALTER TABLE x ADD COLUMN c2 TEXT")
        register(temp_db, 1, "first", "CREATE TABLE x (id INTEGER, c1 TEXT)")

        result = apply_all_pending(temp_db)
        assert result["applied"] == [1, 2]  # ordered by version

    def test_failed_migration_stops_chain(self, temp_db):
        """Bad SQL in v2 → v2 fails, v3 NOT attempted."""
        register(temp_db, 1, "good", "CREATE TABLE x (id INTEGER)")
        register(temp_db, 2, "bad", "INVALID SQL HERE")
        register(temp_db, 3, "good_too", "CREATE TABLE y (id INTEGER)")

        result = apply_all_pending(temp_db)

        assert 1 in result["applied"]
        assert any(e["version"] == 2 for e in result["errors"])
        assert 3 not in result["applied"]  # chain stopped


class TestVersionTracking:
    def test_get_version_zero(self, temp_db):
        """Fresh DB → version 0"""
        assert get_version(temp_db) == 0

    def test_get_version_after_apply(self, temp_db):
        register(temp_db, 5, "test", "CREATE TABLE x (id INTEGER)")
        apply_all_pending(temp_db)
        assert get_version(temp_db) == 5

    def test_get_applied_versions(self, temp_db):
        register(temp_db, 1, "a", "CREATE TABLE a (id INTEGER)")
        register(temp_db, 3, "c", "CREATE TABLE c (id INTEGER)")
        register(temp_db, 2, "b", "CREATE TABLE b (id INTEGER)")

        apply_all_pending(temp_db)
        applied = get_applied_versions(temp_db)
        assert sorted(applied) == [1, 2, 3]


class TestStatus:
    def test_status_empty_registry(self):
        _registry.clear()
        s = status()
        assert s == {}

    def test_status_with_pending(self, temp_db):
        register(temp_db, 1, "a", "CREATE TABLE a (id INTEGER)")
        register(temp_db, 2, "b", "ALTER TABLE a ADD COLUMN b TEXT")

        # Don't apply — both pending
        s = status()
        assert s[temp_db]["registered_count"] == 2
        assert s[temp_db]["applied_count"] == 0
        assert s[temp_db]["pending"] == [1, 2]

    def test_status_all_applied(self, temp_db):
        register(temp_db, 1, "a", "CREATE TABLE a (id INTEGER)")
        apply_all_pending(temp_db)

        s = status()
        assert s[temp_db]["current_version"] == 1
        assert s[temp_db]["pending"] == []
