"""
fno_universe.py — NSE F&O stock universe loader.

Pulls the full list of F&O stocks (~190) from Kite Connect's instruments
API. Cached for 24 hours since the list changes monthly (SEBI revisions).

Public API:
    get_fno_symbols(kite) -> list of {symbol, token, exchange, lot_size, ...}
    refresh_universe(kite) -> force re-fetch from Kite
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional


# Module cache
_universe_cache: Optional[List[Dict]] = None
_cache_ts: float = 0.0
_cache_lock = threading.Lock()
_CACHE_TTL_SEC = 24 * 3600  # 24 hours


def _is_fno_stock(instrument: dict) -> bool:
    """Determine if instrument is an F&O underlying stock."""
    # F&O equity = NSE segment, type EQ, but appears in NFO as well
    # The cleanest check: instrument has a futures contract in NFO
    return (
        instrument.get("segment") == "NSE"
        and instrument.get("instrument_type") == "EQ"
    )


def refresh_universe(kite) -> List[Dict]:
    """Fetch fresh F&O universe list from Kite instruments API.

    Strategy:
      1. Pull all NFO instruments (futures + options)
      2. Extract unique underlying symbols
      3. Cross-reference with NSE EQ instruments to get spot tokens
    """
    global _universe_cache, _cache_ts

    if kite is None:
        return _universe_cache or []

    try:
        # Pull both segments
        nfo_instruments = kite.instruments("NFO")
        nse_instruments = kite.instruments("NSE")

        # Extract unique underlying symbols from NFO (futures + options)
        fno_underlying_names = set()
        for inst in nfo_instruments:
            name = inst.get("name", "").strip().upper()
            if not name:
                continue
            # Skip indexes (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, etc.)
            if name in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}:
                continue
            fno_underlying_names.add(name)

        # Build symbol map from NSE EQ instruments
        nse_map = {}
        for inst in nse_instruments:
            sym = inst.get("tradingsymbol", "").upper()
            if inst.get("instrument_type") == "EQ" and sym:
                nse_map[sym] = inst

        # Assemble universe
        universe = []
        for name in sorted(fno_underlying_names):
            spot = nse_map.get(name)
            if not spot:
                continue
            universe.append({
                "symbol": name,
                "token": spot.get("instrument_token"),
                "exchange": "NSE",
                "name": spot.get("name", name),
                "tick_size": spot.get("tick_size"),
                "lot_size": None,  # will be filled from futures
            })

        # Fill lot_size from nearest-month futures
        for inst in nfo_instruments:
            if inst.get("instrument_type") != "FUT":
                continue
            name = inst.get("name", "").strip().upper()
            for u in universe:
                if u["symbol"] == name and u["lot_size"] is None:
                    u["lot_size"] = inst.get("lot_size")
                    break

        with _cache_lock:
            _universe_cache = universe
            _cache_ts = time.time()

        print(f"[FNO-UNIVERSE] refreshed: {len(universe)} F&O stocks")
        return universe
    except Exception as e:
        print(f"[FNO-UNIVERSE] refresh failed: {e}")
        return _universe_cache or []


def get_fno_symbols(kite=None) -> List[Dict]:
    """Return cached F&O universe. Refreshes if stale (>24hr) and kite available."""
    with _cache_lock:
        cached = _universe_cache
        age = time.time() - _cache_ts if _cache_ts else float("inf")
    if cached and age < _CACHE_TTL_SEC:
        return cached
    if kite is None:
        return cached or []
    return refresh_universe(kite)


def diagnostics() -> Dict:
    """Snapshot for API."""
    with _cache_lock:
        return {
            "cached_count": len(_universe_cache) if _universe_cache else 0,
            "cache_age_sec": round(time.time() - _cache_ts, 1) if _cache_ts else None,
            "cache_ttl_sec": _CACHE_TTL_SEC,
            "sample": (_universe_cache or [])[:5],
        }
