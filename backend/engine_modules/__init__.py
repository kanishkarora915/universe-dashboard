"""
engine_modules — split-out concerns from the monolithic engine.py.

Why this exists:
  engine.py grew to 5,500+ LOC. Hard to test, refactor, debug.
  This package gradually pulls out focused responsibilities into
  files <500 LOC each.

Migration strategy:
  • Each submodule defines pure helper functions/classes
  • engine.py imports them and wires them into MarketEngine
  • Public API stays unchanged — main.py / other modules don't change
  • Tests run after each extraction → no regression

Order of extraction (lowest risk first):
  1. ✅ price_action     (just spot history tracking)
  2. ✅ watchdog         (well-isolated WS health monitor)
  3. ✅ cache_populator  (well-isolated background job)
  4. 🟡 pulse_scheduler  (touches engine internals)
  5. ⚠️ ticker          (real-time critical — DEFER)
  6. ⚠️ trade_flow       (touches money — DEFER)

Note: name is `engine_modules` (not `engine`) to avoid conflict with
the existing engine.py file in same dir during gradual migration.
Once migration complete, can rename to `engine/` package.
"""

# Re-exports for convenient imports
from .price_action import record_spot_tick, prune_history
from .watchdog import WSWatchdog
from .cache_populator import CachePopulator

__all__ = [
    "record_spot_tick",
    "prune_history",
    "WSWatchdog",
    "CachePopulator",
]
