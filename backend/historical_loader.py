"""
historical_loader — fetch yesterday's candles from Kite REST API.

WHY THIS EXISTS

Trend structure detection needs swing points from price history. Without
historical data, a fresh container starts "structure-blind" at 9:15 AM
and stays blind for:
  • 50 min for 5m structure  (need 10 candles)
  • 2 hr  for 15m structure  (need 8 candles)
  • Effectively all day for 1h structure

This module fetches the last few days of candles via Kite's
historical_data API so structure is ready from market open.

USAGE

    from historical_loader import load_index_history, load_all_indices_history

    # Single index, single TF
    candles = load_index_history(kite, "NIFTY", "5minute", days=2)

    # All indices, all TFs
    history = load_all_indices_history(kite, days_60min=5)
    # → {"NIFTY": {"5minute": [...], "15minute": [...], "60minute": [...]}, ...}

CACHE

    Light in-memory cache (30 min TTL) to avoid hammering Kite REST
    on repeated calls. The endpoint /api/structure/state benefits.

FAILURE MODES

    All functions fail SAFE: any Kite error returns an empty list.
    Callers fall back to live-only data.
"""

from __future__ import annotations
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import time
import pytz

IST = pytz.timezone("Asia/Kolkata")


# Standard Kite instrument tokens for spot indices.
# These are stable values for NSE/BSE — change here if Kite ever updates.
SPOT_INSTRUMENT_TOKENS = {
    "NIFTY": 256265,
    "BANKNIFTY": 260105,
    "VIX": 264969,
}


# ── Simple TTL cache ──────────────────────────────────────────────────

_CACHE_TTL_SEC = 1800   # 30 min — historical 5m/15m/1h doesn't change often
_cache: Dict[tuple, tuple] = {}   # (idx, interval, days) -> (ts, candles)


def _cache_get(key: tuple) -> Optional[List[Dict]]:
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL_SEC:
        return entry[1]
    return None


def _cache_put(key: tuple, value: List[Dict]) -> None:
    _cache[key] = (time.time(), value)


def clear_cache() -> None:
    """Drop all cached history. Use after token refresh or for testing."""
    _cache.clear()


# ── Core fetchers ─────────────────────────────────────────────────────


def load_index_history(
    kite,
    index: str,
    interval: str = "5minute",
    days: int = 2,
) -> List[Dict]:
    """Fetch historical candles for a spot index via Kite REST API.

    Args:
        kite: KiteConnect instance with valid access_token
        index: 'NIFTY' / 'BANKNIFTY' / 'VIX'
        interval: '5minute' / '15minute' / '60minute' / 'day'
        days: lookback in calendar days (Kite skips weekends/holidays)

    Returns:
        list of dicts: [{ts, open, high, low, close, volume}, ...]
        Sorted chronologically (oldest first).
        Empty list on any failure (caller falls back to live-only).
    """
    if kite is None:
        return []

    token = SPOT_INSTRUMENT_TOKENS.get(index)
    if not token:
        print(f"[HISTORICAL] unknown index: {index}")
        return []

    # Cache check
    cache_key = (index, interval, days)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        now = datetime.now(IST)
        from_dt = now - timedelta(days=days)
        raw = kite.historical_data(
            instrument_token=token,
            from_date=from_dt.strftime("%Y-%m-%d"),
            to_date=now.strftime("%Y-%m-%d"),
            interval=interval,
        )

        # Normalize to our standard OHLCV dict format
        candles = []
        for c in raw or []:
            ts_val = c.get("date")
            ts_str = (
                ts_val.isoformat() if hasattr(ts_val, "isoformat") else str(ts_val)
            )
            candles.append({
                "ts": ts_str,
                "open": float(c.get("open", 0) or 0),
                "high": float(c.get("high", 0) or 0),
                "low": float(c.get("low", 0) or 0),
                "close": float(c.get("close", 0) or 0),
                "volume": int(c.get("volume", 0) or 0),
            })

        _cache_put(cache_key, candles)
        print(
            f"[HISTORICAL] {index} {interval}: fetched {len(candles)} candles "
            f"({days}d lookback)"
        )
        return candles
    except Exception as e:
        print(f"[HISTORICAL] fetch failed for {index} {interval}: {e}")
        return []


def load_all_indices_history(
    kite,
    indices: Optional[List[str]] = None,
    days_short: int = 2,
    days_60min: int = 5,
) -> Dict[str, Dict[str, List[Dict]]]:
    """Load historical data for multiple indices × multiple timeframes.

    Args:
        kite: KiteConnect instance
        indices: list of index names (default ["NIFTY", "BANKNIFTY"])
        days_short: lookback for 5m / 15m (2 days = enough for swings)
        days_60min: lookback for 60m / day (5 days = bigger context)

    Returns:
        {
          "NIFTY":     {"5minute": [...], "15minute": [...], "60minute": [...]},
          "BANKNIFTY": {"5minute": [...], "15minute": [...], "60minute": [...]},
        }
    """
    if indices is None:
        indices = ["NIFTY", "BANKNIFTY"]
    out: Dict[str, Dict[str, List[Dict]]] = {}
    for idx in indices:
        out[idx] = {
            "5minute": load_index_history(kite, idx, "5minute", days=days_short),
            "15minute": load_index_history(kite, idx, "15minute", days=days_short),
            "60minute": load_index_history(kite, idx, "60minute", days=days_60min),
        }
    return out


# ── Diagnostics ───────────────────────────────────────────────────────


def diagnostics() -> Dict:
    return {
        "module": "historical_loader",
        "cache_ttl_sec": _CACHE_TTL_SEC,
        "cache_entries": len(_cache),
        "supported_indices": list(SPOT_INSTRUMENT_TOKENS.keys()),
        "supported_intervals": ["5minute", "15minute", "60minute", "day"],
    }
