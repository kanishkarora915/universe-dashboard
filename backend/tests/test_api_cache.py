"""
Tests for api_cache.py — in-memory cache helpers.

Critical because: cache bugs = stale data shown to user, OR endpoints
falling through to compute on every hit (negating cache benefit).

Run: pytest backend/tests/test_api_cache.py -v
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from api_cache import (
    cache_get,
    cache_get_stale,
    cache_set,
    cache_invalidate,
    cache_invalidate_prefix,
    cache_stats,
    _memory_cache,
    _cache_timestamps,
)


@pytest.fixture(autouse=True)
def clear_cache_each_test():
    """Reset cache state before each test."""
    _memory_cache.clear()
    _cache_timestamps.clear()
    yield


class TestBasicGetSet:
    def test_set_then_get(self):
        cache_set("foo", {"value": 42})
        assert cache_get("foo") == {"value": 42}

    def test_get_missing_returns_none(self):
        assert cache_get("missing_key") is None

    def test_set_overwrites(self):
        cache_set("foo", "v1")
        cache_set("foo", "v2")
        assert cache_get("foo") == "v2"


class TestStaleness:
    def test_fresh_value_returned(self):
        cache_set("foo", "bar")
        # Within max_age → returned
        assert cache_get("foo", max_age_sec=10) == "bar"

    def test_stale_value_returns_none(self):
        cache_set("foo", "bar")
        # Manually backdate timestamp
        _cache_timestamps["foo"] = time.time() - 100
        # max_age=10 → too old → None
        assert cache_get("foo", max_age_sec=10) is None

    def test_get_stale_returns_anyway(self):
        """cache_get_stale ignores age — always returns cached value if exists"""
        cache_set("foo", "bar")
        _cache_timestamps["foo"] = time.time() - 1000
        assert cache_get_stale("foo") == "bar"

    def test_get_stale_missing_returns_none(self):
        assert cache_get_stale("missing") is None


class TestInvalidation:
    def test_invalidate_single_key(self):
        cache_set("foo", "bar")
        cache_invalidate("foo")
        assert cache_get("foo") is None

    def test_invalidate_missing_doesnt_error(self):
        # Should not raise
        cache_invalidate("doesnt_exist")

    def test_invalidate_prefix(self):
        cache_set("trades_open", [1, 2])
        cache_set("trades_closed", [3])
        cache_set("chain_NIFTY", {"a": 1})

        removed = cache_invalidate_prefix("trades_")
        assert removed == 2
        assert cache_get("trades_open") is None
        assert cache_get("trades_closed") is None
        assert cache_get("chain_NIFTY") == {"a": 1}  # untouched

    def test_invalidate_prefix_returns_count(self):
        cache_set("a", 1)
        cache_set("b", 2)
        cache_set("ab", 3)

        # 'a' prefix matches: a, ab
        assert cache_invalidate_prefix("a") == 2


class TestStats:
    def test_stats_empty(self):
        s = cache_stats()
        assert s["total_keys"] == 0
        assert s["keys"] == []

    def test_stats_with_data(self):
        cache_set("foo", {"x": 1})
        cache_set("bar", {"y": 2})
        s = cache_stats()
        assert s["total_keys"] == 2
        keys = [k["key"] for k in s["keys"]]
        assert "foo" in keys and "bar" in keys

    def test_stats_includes_age(self):
        cache_set("foo", "v")
        time.sleep(0.1)
        s = cache_stats()
        foo_entry = next(k for k in s["keys"] if k["key"] == "foo")
        assert foo_entry["age_sec"] > 0


class TestMaxKeysCap:
    """Cache must not grow unbounded — soft cap at MAX_KEYS=200."""

    def test_max_keys_eviction(self):
        from api_cache import MAX_KEYS

        # Add MAX_KEYS + 5 entries
        for i in range(MAX_KEYS + 5):
            cache_set(f"key_{i}", i)

        # Total should be at most MAX_KEYS (eviction kicked in)
        # Allow tiny race — eviction is approximate
        assert len(_memory_cache) <= MAX_KEYS + 1


class TestThreadSafety:
    """Cache uses lock — concurrent set must not corrupt state."""

    def test_concurrent_writes_dont_crash(self):
        import threading

        def writer(start_i):
            for i in range(50):
                cache_set(f"k_{start_i}_{i}", i)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not have crashed; keys should exist
        assert len(_memory_cache) > 0
