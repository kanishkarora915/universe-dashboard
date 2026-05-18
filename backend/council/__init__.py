"""
council — multi-engine collaborative decision layer.

PURPOSE
  Move from "weighted-sum voting of 9 isolated engines" to a council
  that debates direction, conviction, and timing — and only fires
  trades when there's genuine multi-engine agreement.

PHASED ROLLOUT (see ARCHITECTURE.md)
  Phase 1  Scaffold + observe-only mode    ← CURRENT
  Phase 2  Hook into entry decisions
  Phase 3  Pre-market briefing + scenarios
  Phase 4  Pullback detector + structure reader
  Phase 5  Post-close learning loop
  Phase 6  Full cutover

KILL SWITCHES
  Each major feature has its own flag. Flip to False → revert to
  last known good state. Worst case: set all to False → system
  behaves exactly like 2026-05-13 baseline.
"""

# ── Feature flags (single source of truth for rollout state) ──────────

COUNCIL_ENABLED = True
"""Master switch. False → entire council package is dormant.
   True (Phase 1) → council collects engine votes, computes verdicts,
   writes to council.db, exposes via /api/council/* — but does NOT
   influence actual trade decisions."""

COUNCIL_ACTIVE = False
"""Phase 2 flag. False → trade decisions made by existing verdict
   engine (unchanged). True → council verdict is REQUIRED before
   any entry fires. Flip only after Phase 1 observe-data validates
   council quality."""

BRIEFING_ENABLED = False
"""Phase 3 flag. Daily pre-market briefing generation + AI narrative."""

SCENARIOS_ENABLED = False
"""Phase 3 flag. Live scenario tree tracking."""

PULLBACK_DETECTOR_ENABLED = False
"""Phase 4 flag. Pullback vs reversal classifier."""

STRUCTURE_READER_ENABLED = False
"""Phase 4 flag. Market structure (HH/HL/LL/LH) tracker."""

LEARNING_LOOP_ENABLED = False
"""Phase 5 flag. Post-close engine accuracy scoring + weight tuning."""


# ── Public API ────────────────────────────────────────────────────────

from .vote import EngineVote, CouncilVerdict, Direction, Action
from .aggregator import Council

# Observer + storage are imported lazily by main.py to avoid circular
# dependencies during package init.

__all__ = [
    # Flags
    "COUNCIL_ENABLED",
    "COUNCIL_ACTIVE",
    "BRIEFING_ENABLED",
    "SCENARIOS_ENABLED",
    "PULLBACK_DETECTOR_ENABLED",
    "STRUCTURE_READER_ENABLED",
    "LEARNING_LOOP_ENABLED",
    # Core types
    "EngineVote",
    "CouncilVerdict",
    "Direction",
    "Action",
    "Council",
]
