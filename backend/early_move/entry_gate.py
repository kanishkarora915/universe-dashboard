"""
entry_gate — wires the aggregator verdict into the live trade-entry path.

WHY THIS EXISTS (Week 4 — final piece)

The 5 detectors + aggregator are built. But the aggregator only
produces a VERDICT — it doesn't touch trades yet. This module is the
bridge: it lets the aggregator's leading-indicator verdict influence
actual entries.

THREE MODES (env-controlled)

  EARLY_MOVE_ENTRY_MODE = off       (default)
     Pure shadow. Aggregator runs, logs verdict, but NEVER affects
     trades. Used to collect proof before activating.

  EARLY_MOVE_ENTRY_MODE = veto
     Aggregator can only BLOCK trades. If aggregator says BLOCKED
     (IV crush / fakeout / exhaustion) OR FIRE in the OPPOSITE
     direction → the trade is skipped. It cannot CREATE trades.
     Conservative — strictly reduces bad trades.

  EARLY_MOVE_ENTRY_MODE = full
     Aggregator can both BLOCK and CONFIRM. A trade that the legacy
     confluence wants is allowed only if the aggregator doesn't veto.
     (Independent FIRE path — aggregator creating its own trades —
      is intentionally NOT in this module yet; that's a later step
      once shadow data proves the edge.)

WHY NO "AGGREGATOR FIRES ITS OWN TRADES" YET

  The detectors shipped days ago. Zero validated shadow data. Letting
  an unproven detector CREATE trades is reckless. veto/full modes only
  let it FILTER existing confluence trades — strictly safer. The
  independent-fire path unlocks after 1-2 weeks of shadow validation.

INTEGRATION

  scalper_mode.should_enter_scalp() and engine.py pending-confirmation
  both call evaluate_entry(). It returns {allow, reason, verdict}.
  Caller skips the trade when allow is False.

Always shadow-logs — even in 'off' mode — so the comparison data
accumulates from day one.
"""

from __future__ import annotations
import os
from typing import Dict, Optional


def entry_mode() -> str:
    """Return current mode: 'off' | 'veto' | 'full'."""
    m = os.environ.get("EARLY_MOVE_ENTRY_MODE", "off").lower().strip()
    return m if m in ("off", "veto", "full") else "off"


def is_shadow_enabled() -> bool:
    return os.environ.get("EARLY_MOVE_ENTRY_SHADOW", "on").lower() == "on"


def _opposite(a: str, b: str) -> bool:
    """True if directions a and b are opposite (BULL vs BEAR)."""
    pair = {a, b}
    return pair == {"BULL", "BEAR"}


def _action_to_direction(action: str) -> Optional[str]:
    """Convert 'BUY CE' → 'BULL', 'BUY PE' → 'BEAR'."""
    if not action:
        return None
    a = action.upper()
    if "CE" in a:
        return "BULL"
    if "PE" in a:
        return "BEAR"
    return None


def evaluate_entry(
    *,
    engine,
    idx: str,
    proposed_action: str,
    source: str = "unknown",
) -> Dict:
    """Decide whether the aggregator permits this proposed entry.

    Args:
        engine: the live engine instance
        idx: index name (NIFTY / BANKNIFTY)
        proposed_action: "BUY CE" or "BUY PE" the caller wants to fire
        source: caller label for logging

    Returns:
        {
          "allow": bool,        # False → caller must skip the trade
          "reason": str,
          "mode": str,
          "verdict": dict,      # full aggregator verdict
        }

    Behaviour by mode:
        off   → always allow (shadow log only)
        veto  → block if aggregator BLOCKED or FIRE-opposite
        full  → same as veto for now (independent-fire deferred)
    """
    mode = entry_mode()
    proposed_dir = _action_to_direction(proposed_action)

    # Run the aggregator
    verdict = {}
    try:
        from early_move import aggregator
        verdict = aggregator.get_verdict(engine=engine, idx=idx)
    except Exception as e:
        # Aggregator failure must NEVER block a legit trade
        verdict = {"verdict": "NO_TRADE", "error": str(e)}

    v_type = verdict.get("verdict", "NO_TRADE")
    v_dir = verdict.get("direction")

    # ── Shadow log (always, even in off mode) ──
    if is_shadow_enabled():
        print(
            f"[EARLY_MOVE_ENTRY_SHADOW] {source} {idx} proposed={proposed_action} "
            f"({proposed_dir}) → aggregator={v_type}/{v_dir} "
            f"conf={verdict.get('confidence', 0)} mode={mode}"
        )

    # ── OFF mode — never affect trades ──
    if mode == "off":
        return {
            "allow": True,
            "reason": f"early_move OFF (shadow only) — aggregator said {v_type}",
            "mode": mode,
            "verdict": verdict,
        }

    # ── VETO / FULL mode ──
    # Block 1: aggregator explicitly BLOCKED (IV crush / fakeout / exhaustion)
    if v_type == "BLOCKED":
        return {
            "allow": False,
            "reason": (
                f"EARLY_MOVE VETO: aggregator BLOCKED — "
                f"{verdict.get('blocked_by', '?')}: "
                f"{verdict.get('action', '')[:120]}"
            ),
            "mode": mode,
            "verdict": verdict,
        }

    # Block 2: aggregator FIRE in the OPPOSITE direction
    if v_type == "FIRE" and v_dir and proposed_dir and _opposite(v_dir, proposed_dir):
        return {
            "allow": False,
            "reason": (
                f"EARLY_MOVE VETO: leading detectors say {v_dir} "
                f"but trade is {proposed_dir} — directional conflict "
                f"({verdict.get('detectors_agreed', 0)} detectors)"
            ),
            "mode": mode,
            "verdict": verdict,
        }

    # Otherwise allow (aggregator agrees, neutral, or no opinion)
    reason = f"early_move {mode}: aggregator {v_type}"
    if v_type == "FIRE" and v_dir == proposed_dir:
        reason = (
            f"EARLY_MOVE CONFIRM: leading detectors AGREE {v_dir} "
            f"({verdict.get('detectors_agreed', 0)} detectors, "
            f"conf {verdict.get('confidence', 0)})"
        )
    return {
        "allow": True,
        "reason": reason,
        "mode": mode,
        "verdict": verdict,
    }


def diagnostics() -> Dict:
    """State snapshot for API."""
    return {
        "mode": entry_mode(),
        "shadow": is_shadow_enabled(),
        "modes_available": ["off", "veto", "full"],
        "description": {
            "off": "shadow only — aggregator never affects trades",
            "veto": "aggregator can BLOCK trades (crush/fakeout/conflict)",
            "full": "veto + confirm (independent-fire path deferred)",
        },
    }
