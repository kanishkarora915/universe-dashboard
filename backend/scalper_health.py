"""
scalper_health — market-health → scalper aggression level.

Built 2026-05-22 per user vision:
  "Ye automate system ya market health ke hisaab se kare?"

WHY THIS EXISTS

A static SCALPER_AGGRESSIVE flag is dumb — it stays aggressive on a
trending day AND on a choppy theta-crush day. This module lets the
scalper read market health every cycle and pick its OWN aggression
level, so gates only loosen when conditions actually favour scalping.

THREE LEVELS

  AGGRESSIVE  — movement present, healthy VIX, not expiry, no losing
                streak → looser threshold, higher daily cap, shorter
                cooldowns, CHOP allowed, bigger targets.
  BALANCED    — normal conditions → current/default settings.
  DEFENSIVE   — dead/low-vol market, expiry day, VIX spike, opening/
                closing window, or a losing streak → tighter gates,
                fewer trades, smaller size.

SENSORS (all already measured by the system)

  volatility_detector.classify_regime(engine) gives:
    regime, vix, atr_ratio, day_range_pct, is_expiry, time_window
  Plus this module's own read of recent scalper W/L streak.

ENV — SCALPER_ADAPTIVE_HEALTH = off | shadow | live   (default shadow)

  off    — not used; always BALANCED, nothing logged.
  shadow — level computed + logged every cycle, behaviour UNCHANGED.
           This is how we validate the readings before they drive money.
  live   — tuning is actually applied by should_enter_scalp().

The caller checks result["mode"]: only applies tuning when == "live".
Any sensor failure falls back to BALANCED (neutral, never blocks).
"""

from __future__ import annotations
import os
import time
from typing import Dict

# ── Per-level tuning ──────────────────────────────────────────────────
# threshold_delta : added to the win-prob threshold (negative = looser)
# daily_cap       : max trades/day
# cooldown_mult   : multiplies same-strike + flip cooldown minutes
# target_mult     : multiplies T1/T2 (exit-side — applied separately)
# size_mult       : multiplies position size (exit-side)
# allow_chop      : let entries through in a CHOP regime
TUNING: Dict[str, Dict] = {
    "AGGRESSIVE": {
        "threshold_delta": -8,
        "daily_cap": 35,
        "cooldown_mult": 0.4,
        "target_mult": 1.4,
        "size_mult": 1.0,
        "allow_chop": True,
    },
    "BALANCED": {
        "threshold_delta": 0,
        "daily_cap": 15,
        "cooldown_mult": 1.0,
        "target_mult": 1.0,
        "size_mult": 1.0,
        "allow_chop": False,
    },
    "DEFENSIVE": {
        "threshold_delta": 5,
        "daily_cap": 8,
        "cooldown_mult": 1.5,
        "target_mult": 0.85,
        "size_mult": 0.5,
        "allow_chop": False,
    },
}

_CACHE_TTL = 20.0          # seconds — regime moves slowly, cache is cheap
_cache: Dict[str, tuple] = {}   # idx -> (ts, result)


def _mode() -> str:
    """Return 'off' | 'shadow' | 'live'."""
    m = os.environ.get("SCALPER_ADAPTIVE_HEALTH", "shadow").lower().strip()
    return m if m in ("off", "shadow", "live") else "shadow"


def _recent_streak() -> int:
    """Net W−L of the last 5 closed scalper trades today.

    Positive = winning streak, negative = losing streak. Lazy-imports
    scalper_mode to avoid a circular import at module load.
    """
    try:
        from scalper_mode import _conn, ist_now
        today = ist_now().strftime("%Y-%m-%d")
        conn = _conn()
        rows = conn.execute(
            "SELECT pnl_rupees FROM scalper_trades "
            "WHERE status != 'OPEN' AND substr(entry_time,1,10)=? "
            "ORDER BY exit_time DESC LIMIT 5",
            (today,),
        ).fetchall()
        conn.close()
        net = 0
        for r in rows:
            p = (r[0] if not hasattr(r, "keys") else r["pnl_rupees"]) or 0
            net += 1 if p > 0 else (-1 if p < 0 else 0)
        return net
    except Exception:
        return 0


def assess(engine, idx: str = "NIFTY") -> Dict:
    """Read market health → aggression level + tuning.

    Returns:
        {
          "level": "AGGRESSIVE" | "BALANCED" | "DEFENSIVE",
          "score": int 0-100,
          "mode": "off" | "shadow" | "live",
          "regime": str,
          "reasons": [str],
          "tuning": dict,     # see TUNING — caller applies only if mode==live
        }
    """
    mode = _mode()

    if mode == "off":
        return {
            "level": "BALANCED", "score": 50, "mode": "off",
            "regime": "n/a", "reasons": ["adaptive health off"],
            "tuning": dict(TUNING["BALANCED"]),
        }

    # Cache — regime changes slowly; avoid recomputing every tick.
    cached = _cache.get(idx)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1]

    # ── Read sensors ──
    regime_data: Dict = {}
    try:
        from volatility_detector import classify_regime
        regime_data = classify_regime(engine) or {}
    except Exception as e:
        regime_data = {"regime": "UNKNOWN", "error": str(e)}

    regime = regime_data.get("regime", "UNKNOWN")
    vix = regime_data.get("vix", 0) or 0
    atr_ratio = regime_data.get("atr_ratio", 1.0) or 1.0
    time_window = regime_data.get("time_window", "") or ""
    is_expiry = bool(regime_data.get("is_expiry", False))

    reasons = []
    score = 50
    force_defensive = False

    # ── Force-DEFENSIVE conditions (override the score) ──
    if "EXTREME" in regime:
        force_defensive = True
        reasons.append(f"{regime} — panic conditions, stand down")
    if is_expiry or "EXPIRY" in regime:
        force_defensive = True
        reasons.append("expiry day — theta crush, defensive")
    if time_window in ("OPENING_FIRST_5MIN", "PRE_MARKET", "POST_MARKET", "CLOSING"):
        force_defensive = True
        reasons.append(f"{time_window} — no aggressive entries")

    # ── Score adjustments ──
    if atr_ratio >= 1.3:
        score += 15
        reasons.append(f"movement {atr_ratio}x avg — scalpable")
    elif atr_ratio < 0.8:
        score -= 15
        reasons.append(f"dead market {atr_ratio}x avg — theta risk")

    if 12 <= vix <= 18:
        score += 10
        reasons.append(f"VIX {vix} healthy")
    elif vix and vix < 11:
        score -= 12
        reasons.append(f"VIX {vix} compressed — theta-crush risk")
    elif vix > 20:
        score -= 10
        reasons.append(f"VIX {vix} elevated — whipsaw risk")

    if "HIGH-VOL" in regime:
        score -= 8
        reasons.append("high-vol regime — whippy")
    if "LOW-VOL" in regime:
        score -= 10
        reasons.append("low-vol regime — hard to scalp")

    if time_window == "POWER_HOUR":
        score += 8
        reasons.append("power hour — moves available")
    elif time_window == "LUNCH_CHOP":
        score -= 12
        reasons.append("lunch chop window")

    # ── Recent performance ──
    streak = _recent_streak()
    if streak <= -3:
        score -= 15
        reasons.append(f"losing streak ({streak}) — pull back")
    elif streak >= 3:
        score += 8
        reasons.append(f"winning streak (+{streak})")

    score = max(0, min(100, score))

    # ── Bucket ──
    if force_defensive or score < 35:
        level = "DEFENSIVE"
    elif score >= 65:
        level = "AGGRESSIVE"
    else:
        level = "BALANCED"

    if not reasons:
        reasons.append("normal conditions")

    result = {
        "level": level,
        "score": score,
        "mode": mode,
        "regime": regime,
        "reasons": reasons,
        "tuning": dict(TUNING[level]),
    }
    _cache[idx] = (time.time(), result)

    # Shadow log — printed in shadow AND live so the level is always visible.
    print(f"[SCALPER_HEALTH] {idx} level={level} score={score} mode={mode} "
          f"regime={regime} — {'; '.join(reasons[:3])}")

    return result


def diagnostics() -> Dict:
    """State snapshot for the API."""
    return {
        "mode": _mode(),
        "modes_available": ["off", "shadow", "live"],
        "levels": list(TUNING.keys()),
        "tuning": TUNING,
        "description": "market-health → scalper aggression level",
    }
