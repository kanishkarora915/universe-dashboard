"""
Price action helpers — spot LTP history tracking.

Extracted from engine.py (was inline in _record_price_action method).

Purpose:
  Maintain a bounded buffer of recent spot prices per index.
  Used by entry_filters (5-min trend check, regime detection).

State is stored on the engine instance (`engine._spot_history`)
so tests can verify or mock it independently.

Public API:
  record_spot_tick(history, idx, ltp, now_iso)
    Append a tick to the history dict, prune to last N entries.

  prune_history(history, max_entries=600)
    Trim each index's list to last max_entries items.
"""

from typing import Dict, List, Any

# Default: keep last 600 ticks per index (~30 min @ 3-5s tick rate)
DEFAULT_MAX_ENTRIES = 600


def record_spot_tick(
    history: Dict[str, List[Dict[str, Any]]],
    idx: str,
    ltp: float,
    now_iso: str,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> None:
    """Append one tick to history[idx]; prune to max_entries.

    Args:
        history:     dict mapping idx → list of {"t": iso_ts, "ltp": price}
                     (typically engine._spot_history)
        idx:         index name (NIFTY / BANKNIFTY)
        ltp:         current spot price (must be > 0)
        now_iso:     ISO timestamp string for the tick
        max_entries: keep last N entries (default 600)

    Returns:
        None — mutates history in place.

    Note: Caller is responsible for ensuring ltp > 0 before calling.
    """
    if idx not in history:
        history[idx] = []
    history[idx].append({"t": now_iso, "ltp": ltp})
    if len(history[idx]) > max_entries:
        history[idx] = history[idx][-max_entries:]


def prune_history(
    history: Dict[str, List[Dict[str, Any]]],
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> None:
    """Prune all indices' history lists to last max_entries items.
    Called periodically to bound memory.
    """
    for idx in list(history.keys()):
        if len(history[idx]) > max_entries:
            history[idx] = history[idx][-max_entries:]


def get_recent_window(
    history: Dict[str, List[Dict[str, Any]]],
    idx: str,
    n: int,
) -> List[Dict[str, Any]]:
    """Return last n entries for idx (or empty list if missing/short)."""
    if idx not in history:
        return []
    return history[idx][-n:] if len(history[idx]) >= 1 else []
