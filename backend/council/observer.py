"""
observer — the bridge between engine.py and the council.

DESIGN GOAL
  Let engine.py's existing verdict cycle silently feed the council
  without modifying engine.py more than absolutely necessary.

USAGE FROM ENGINE.PY (Phase 1.5 wire-up, one-line call)

    from council.observer import observe_verdict_cycle
    ...
    # After existing _eng dict is built in engine.py (~line 2975):
    observe_verdict_cycle(
        index=index,
        eng_dict=_eng,
        bull_score=bull_score,
        bear_score=bear_score,
        bull_reasons=bull_reasons,
        bear_reasons=bear_reasons,
    )

  Inside `observe_verdict_cycle`:
    1. Build EngineVotes from _eng dict
    2. Run Council.aggregate()
    3. Save to council.db
    4. Return the verdict (so engine.py CAN inspect it, but doesn't
       have to — Phase 1 ignores the return value)

SAFETY
  • Wrapped in broad try/except — observer crash never propagates.
  • Respects COUNCIL_ENABLED flag — if False, no-op return.
  • Async-friendly: storage write is fire-and-forget on a background
    thread if needed (kept synchronous for now since SQLite WAL is fast).
"""

import threading
import time
from typing import Optional, List

from . import COUNCIL_ENABLED
from .aggregator import Council
from .engines_registry import votes_from_engine_dict
from .vote import CouncilVerdict
from . import storage


# Single council instance — stateless, thread-safe.
_council = Council()

# Lazily initialize DB on first observation
_db_initialized = False
_db_init_lock = threading.Lock()


def _ensure_db():
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:
            return
        try:
            storage.init_db()
            _db_initialized = True
            print("[COUNCIL] council.db initialized")
        except Exception as e:
            print(f"[COUNCIL] DB init failed: {e}")


def observe_verdict_cycle(
    index: str,
    eng_dict: dict,
    bull_score: float,
    bear_score: float,
    bull_reasons: Optional[List[str]] = None,
    bear_reasons: Optional[List[str]] = None,
) -> Optional[CouncilVerdict]:
    """Observe one verdict cycle from engine.py — collect votes, aggregate,
    save. Returns the council verdict (or None if disabled/failed).

    This is the SINGLE entry point engine.py needs to call. Everything
    else (vote construction, aggregation, DB write) happens inside.

    Args:
        index:        "NIFTY" or "BANKNIFTY"
        eng_dict:     per-engine net scores (engine.py's _eng dict)
        bull_score:   total bullish score across all engines
        bear_score:   total bearish score across all engines
        bull_reasons: list of reasoning strings (logged at engine.py)
        bear_reasons: list of reasoning strings

    Returns:
        CouncilVerdict if successful, None otherwise.
        Phase 1: engine.py IGNORES this return value (observe-only).
        Phase 2+: engine.py will use it as an entry gate.
    """
    if not COUNCIL_ENABLED:
        return None

    try:
        _ensure_db()

        # Build votes from engine dict
        votes = votes_from_engine_dict(
            eng_dict,
            bull_score=bull_score,
            bear_score=bear_score,
            bull_reasons=bull_reasons or [],
            bear_reasons=bear_reasons or [],
        )

        # Pulse ID: index + timestamp millis
        pulse_id = f"{index.lower()}_{int(time.time() * 1000)}"

        # Aggregate
        verdict = _council.aggregate(votes, pulse_id=pulse_id)

        # Persist (fire-and-forget on a thread to keep latency off the
        # critical path of engine.py's pulse cycle)
        threading.Thread(
            target=_safe_save,
            args=(verdict,),
            daemon=True,
            name="council-storage",
        ).start()

        return verdict

    except Exception as e:
        # Observer NEVER propagates errors to engine.py
        print(f"[COUNCIL] observe_verdict_cycle error: {e}")
        return None


def _safe_save(verdict: CouncilVerdict) -> None:
    """Background save with isolated error handling."""
    try:
        storage.save_verdict(verdict)
    except Exception as e:
        print(f"[COUNCIL] save_verdict failed: {e}")


def get_observer_health() -> dict:
    """Quick health snapshot — exposed via /api/council/health."""
    try:
        summary = storage.summary_stats(days=1)
        latest = storage.get_latest_verdict()
        return {
            "enabled": COUNCIL_ENABLED,
            "db_initialized": _db_initialized,
            "latest_verdict_at": latest["timestamp"] if latest else None,
            "last_24h": summary,
        }
    except Exception as e:
        return {
            "enabled": COUNCIL_ENABLED,
            "db_initialized": _db_initialized,
            "error": str(e),
        }
