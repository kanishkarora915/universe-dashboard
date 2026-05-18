"""
API Response Cache — fast in-memory cache for hot endpoints.

Pattern: engine pre-computes responses on its own cycle, stores in cache.
API endpoints just read from cache (sub-ms).

This decouples API latency from compute latency. Tab switching becomes
instant because endpoints don't recompute on every request.

Usage:
    from api_cache import cache_get, cache_set, cache_get_stale

    # Reader (API endpoint):
    cached = cache_get("live", max_age_sec=10)
    if cached is not None:
        return cached
    # fallback to compute

    # Writer (engine background thread):
    cache_set("live", engine.get_live_data())

Memory bounded — automatic eviction when over MAX_KEYS.
Thread-safe via simple dict operations (Python GIL).
"""

import time
from threading import Lock
from typing import Any, Optional, Dict

_memory_cache: Dict[str, Any] = {}
_cache_timestamps: Dict[str, float] = {}
_lock = Lock()

# Soft cap — prevents runaway memory if many keys cached over long uptime
MAX_KEYS = 200


def cache_get(key: str, max_age_sec: float = 30.0) -> Optional[Any]:
    """Return cached value if it's fresh (within max_age_sec).
    Returns None on cache miss OR expired entry. NEVER computes/blocks.

    Args:
        key:           cache key, e.g. "live", "chain_NIFTY"
        max_age_sec:   max acceptable staleness in seconds

    Returns:
        Cached value, or None if not present / too old.
    """
    if key not in _memory_cache:
        return None
    age = time.time() - _cache_timestamps.get(key, 0)
    if age > max_age_sec:
        return None
    return _memory_cache[key]


def cache_get_stale(key: str) -> Optional[Any]:
    """Return cached value REGARDLESS of age. Use as fallback when
    fresh data unavailable (e.g., engine just started, populator hasn't
    run yet). Caller decides whether stale data is acceptable.

    Returns:
        Cached value, or None if key never set.
    """
    return _memory_cache.get(key)


def cache_set(key: str, value: Any) -> None:
    """Store value in cache with current timestamp. Always succeeds.
    Triggers LRU-ish eviction if cache grows beyond MAX_KEYS.
    """
    with _lock:
        _memory_cache[key] = value
        _cache_timestamps[key] = time.time()

        # Soft cap — evict oldest entries when over limit
        if len(_memory_cache) > MAX_KEYS:
            oldest_key = min(
                _cache_timestamps,
                key=lambda k: _cache_timestamps[k]
            )
            _memory_cache.pop(oldest_key, None)
            _cache_timestamps.pop(oldest_key, None)


def cache_invalidate(key: str) -> None:
    """Remove key from cache (used when data is known to have changed,
    e.g., trade just opened/closed → invalidate trades:open cache).
    """
    _memory_cache.pop(key, None)
    _cache_timestamps.pop(key, None)


def cache_invalidate_prefix(prefix: str) -> int:
    """Remove all keys matching prefix. Returns count of removed keys.
    Useful for invalidating groups (e.g., "trades:" invalidates all trade
    caches in one call).
    """
    keys_to_remove = [k for k in _memory_cache.keys() if k.startswith(prefix)]
    for k in keys_to_remove:
        _memory_cache.pop(k, None)
        _cache_timestamps.pop(k, None)
    return len(keys_to_remove)


def cache_stats() -> Dict[str, Any]:
    """Return current cache state for monitoring/debug.
    Used by /api/cache/stats endpoint (admin).
    """
    now = time.time()
    return {
        "total_keys": len(_memory_cache),
        "max_keys": MAX_KEYS,
        "keys": [
            {
                "key": k,
                "age_sec": round(now - _cache_timestamps.get(k, 0), 2),
                "size_bytes_approx": len(str(_memory_cache.get(k, ""))),
            }
            for k in sorted(_memory_cache.keys())
        ],
    }
