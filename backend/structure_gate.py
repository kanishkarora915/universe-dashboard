"""
structure_gate — integration layer between price_structure and trade entry.

Built 2026-05-27 (Phase 2 of Option Y).

WHY THIS EXISTS

`price_structure` is a PURE module (no I/O). `structure_gate` is the
glue that:
  1. Periodically refreshes structure verdicts via Kite REST history
  2. Caches them in memory for low-latency entry-gate checks
  3. Decides per-entry: MODE A (aligned), MODE B (counter-trend), or SKIP
  4. Returns the matching tuning (size, SL, targets, hold) for that mode

DEFAULT BEHAVIOUR

  STRUCTURE_MODE=off (default) → evaluate_entry() returns {allow=True,
  mode="off"} unconditionally. Caller sees NO change.

  STRUCTURE_MODE=shadow         → evaluate runs + logs, but caller
                                  treats allow=True regardless.
  STRUCTURE_MODE=live           → caller obeys the verdict.

SUB-FLAGS (only when STRUCTURE_MODE != off)

  STRUCTURE_SCALPER_ENABLED  (default on)  — gate scalper entries
  STRUCTURE_MAIN_ENABLED     (default on)  — gate main engine entries
  STRUCTURE_ALIGNED_ENABLED  (default on)  — Mode A trades allowed
  STRUCTURE_COUNTER_TREND_ENABLED (default on) — Mode B trades allowed

MODE A (aligned trend) — fired when:
  - 1h structure matches proposed direction
  - 15m structure matches (or NEUTRAL/CHOP)
  - 5m structure matches (or NEUTRAL/CHOP)
  Params: full size, SL -8%, 30% book at T1 +10%, 70% structural trail.

MODE B (counter-trend scalp) — fired when:
  - 5m + 15m aligned in proposed direction
  - 1h opposite direction
  Params: 0.4x size, SL -5%, T1 +6%, 10-min hold cap.

SKIP — when CHOP on majority of TFs or conflict beyond the above.

FAILURE MODES

  Aggregator errors → evaluate_entry returns {allow=True, mode="error"}.
  Cache empty/stale → returns {allow=True, mode="no-data"}.
  Background thread crash → caught + logged + restarts.
"""

from __future__ import annotations
import os
import time
import threading
from typing import Dict, Optional, List


# ── Master mode + sub-flags ───────────────────────────────────────────


def master_mode() -> str:
    """off | shadow | live.

    2026-06-15: Default flipped off→shadow. Multi-TF Bill Williams Dow
    Theory gate runs and LOGS decisions but does NOT block any trades.
    2026-06-17 (90d audit): Default flipped shadow→live. Data PROVES
    5m+15m aligned = +₹3,901/trade NET; counter-trend = -₹1,149/trade.
    Free edge sitting in shadow. Set STRUCTURE_MODE=shadow to revert
    to observation-only, STRUCTURE_MODE=off to fully disable.
    """
    m = os.environ.get("STRUCTURE_MODE", "live").lower().strip()
    return m if m in ("off", "shadow", "live") else "live"


def scalper_enabled() -> bool:
    return os.environ.get("STRUCTURE_SCALPER_ENABLED", "on").lower() == "on"


def main_enabled() -> bool:
    return os.environ.get("STRUCTURE_MAIN_ENABLED", "on").lower() == "on"


def aligned_enabled() -> bool:
    return os.environ.get("STRUCTURE_ALIGNED_ENABLED", "on").lower() == "on"


def counter_trend_enabled() -> bool:
    return os.environ.get("STRUCTURE_COUNTER_TREND_ENABLED", "on").lower() == "on"


def block_on_nodata_enabled() -> bool:
    """When True, refuse trades during structure-cache cold-start.

    BUG OBSERVED 2026-06-22:
      Morning session 9:20-12:30 fired 24 trades with structure_5m=UNKNOWN
      and structure_15m=UNKNOWN. Background refresh thread had not yet
      populated the cache; fail-safe in evaluate_entry returned allow=True
      for every entry. STRICT alignment was effectively bypassed → 3
      WATCHER_EXIT trades cost -₹65,025.

      19-Jun (only 2 trades, +₹22,636) proved strict alignment works when
      cache is populated. Issue is purely the cold-start window.

    With STRUCTURE_BLOCK_ON_NODATA=on, evaluate_entry returns allow=False
    when no cache + no engine to refresh. Default on.
    Disable: STRUCTURE_BLOCK_ON_NODATA=off (reverts to fail-safe allow).
    """
    return os.environ.get("STRUCTURE_BLOCK_ON_NODATA", "on").lower() == "on"


def strict_main_alignment_enabled() -> bool:
    """Task #82 — HARD 5m+15m alignment for main mode.

    90d audit (2026-06-18) showed CHOP-on-one-side trades are the
    biggest loss bucket. Old _matches_or_neutral let UPTREND/CHOP +
    CE through → -₹139k/90d (worst bucket). Strict rule enforces
    the data-proven good patterns only.

    Patterns allowed (data-derived):
      BUY CE: 5m=UPTREND AND 15m=UPTREND       (+₹133k 90d)
              5m=CHOP    AND 15m=CHOP          (+₹100k 90d, CHOP-only play)
      BUY PE: 5m=DOWNTREND AND 15m=DOWNTREND   (+₹87k  90d)
              5m=DOWNTREND AND 15m=CHOP        (+₹92k  90d, continuation)
              5m=CHOP    AND 15m=CHOP          (CHOP-only play)
      Anything else → SKIP (saves -₹430k aggregate in mismatch buckets)

    Disable: STRUCTURE_STRICT_ALIGN_MAIN=off (revert to old _matches_or_neutral)
    """
    return os.environ.get("STRUCTURE_STRICT_ALIGN_MAIN", "on").lower() == "on"


# ── Mode-specific tuning (env-overridable) ────────────────────────────


def _f(env_key: str, default: float) -> float:
    try:
        return float(os.environ.get(env_key, str(default)) or default)
    except Exception:
        return default


def _i(env_key: str, default: int) -> int:
    try:
        return int(float(os.environ.get(env_key, str(default)) or default))
    except Exception:
        return default


def mode_a_tuning() -> Dict:
    """Aligned-trend Mode A — full size, ride structure."""
    return {
        "size_mult": _f("STRUCTURE_MODE_A_SIZE_MULT", 1.0),
        "sl_pct": _f("STRUCTURE_MODE_A_SL_PCT", 0.08),
        "t1_pct": _f("STRUCTURE_MODE_A_T1_PCT", 0.10),
        "t1_book_pct": _f("STRUCTURE_MODE_A_T1_BOOK_PCT", 0.30),
        "t2_pct": _f("STRUCTURE_MODE_A_T2_PCT", 0.20),
        "max_hold_min": _i("STRUCTURE_MODE_A_MAX_HOLD_MIN", 360),  # 6 hrs - till EOD
        "use_structural_trail": True,
    }


def mode_b_tuning() -> Dict:
    """Counter-trend Mode B — small + fast."""
    return {
        "size_mult": _f("STRUCTURE_MODE_B_SIZE_MULT", 0.4),
        "sl_pct": _f("STRUCTURE_MODE_B_SL_PCT", 0.05),
        "t1_pct": _f("STRUCTURE_MODE_B_T1_PCT", 0.06),
        "t1_book_pct": 1.0,  # full exit at T1 — no runner
        "t2_pct": None,      # no T2
        "max_hold_min": _i("STRUCTURE_MODE_B_MAX_HOLD_MIN", 10),
        "use_structural_trail": False,
    }


# ── Per-index structure cache ─────────────────────────────────────────


_CACHE_TTL_SEC = 300            # 5 min — matches refresh cadence
_REFRESH_INTERVAL_SEC = 300     # background thread refresh every 5 min
_structure_cache: Dict[str, Dict] = {}    # idx -> {ts, alignment, structures}
_cache_lock = threading.Lock()


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


def _verdict_to_direction(verdict: str) -> Optional[str]:
    if verdict == "UPTREND":
        return "BULL"
    if verdict == "DOWNTREND":
        return "BEAR"
    return None


def update_structure(kite, idx: str) -> Optional[Dict]:
    """Refresh structure for `idx` — fetch candles + detect + cache.

    Returns the new cache entry, or None on failure.
    """
    try:
        import price_structure as ps
        import historical_loader as hl

        structures = {
            "5m": ps.detect_structure(
                hl.load_index_history(kite, idx, "5minute", days=2)
            ),
            "15m": ps.detect_structure(
                hl.load_index_history(kite, idx, "15minute", days=2)
            ),
            "1h": ps.detect_structure(
                hl.load_index_history(kite, idx, "60minute", days=5)
            ),
        }
        alignment = ps.align_timeframes(structures)

        entry = {
            "ts": time.time(),
            "idx": idx,
            "structures": structures,
            "alignment": alignment,
        }
        with _cache_lock:
            _structure_cache[idx] = entry
        return entry
    except Exception as e:
        print(f"[STRUCTURE_GATE] update_structure({idx}) failed: {e}")
        return None


def get_cached_structure(idx: str) -> Optional[Dict]:
    """Return cached structure for `idx` if fresh, else None."""
    with _cache_lock:
        entry = _structure_cache.get(idx)
    if not entry:
        return None
    if (time.time() - entry["ts"]) > _CACHE_TTL_SEC:
        return None
    return entry


def clear_cache() -> None:
    """Drop all cached structures — used after a token refresh or tests."""
    with _cache_lock:
        _structure_cache.clear()


# ── Mode decision ─────────────────────────────────────────────────────


def _strict_pattern_match(d_5m: str, d_15m: str, target: str) -> Optional[str]:
    """Data-driven 5m+15m alignment rules from 90d audit (2026-06-18).

    Returns the matching pattern label (e.g. 'trend_aligned',
    'chop_only', 'downtrend_continuation') or None if no match.

    Bucket P&L over 90d (from /api/admin/trade-attribution?days=90):
      UPTREND/UPTREND   = +₹132,642  (n=34, WR 70.6%) ← BULL trend aligned
      DOWNTREND/DOWNTREND = +₹87,030 (n=13, WR 76.9%) ← BEAR trend aligned
      DOWNTREND/CHOP    = +₹91,843  (n=30, WR 63.3%)  ← BEAR continuation
      CHOP/CHOP         = +₹99,546  (n=32, WR 71.9%)  ← range play, both dir

    Mismatch buckets we explicitly SKIP:
      UPTREND/CHOP      = -₹139,060 (worst single bucket)
      UPTREND/UNKNOWN   = -₹51,587
      UPTREND/DOWNTREND = -₹51,098
      CHOP/UPTREND      = -₹28,087
      CHOP/DOWNTREND    = -₹28,335
      DOWNTREND/UPTREND = -₹28,734
      All UNKNOWN cells = -₹100k+ aggregate
    """
    if target == "BULL":
        if d_5m == "BULL" and d_15m == "BULL":
            return "trend_aligned"
        if d_5m == "NEUTRAL" and d_15m == "NEUTRAL":
            return "chop_only"
        return None
    if target == "BEAR":
        if d_5m == "BEAR" and d_15m == "BEAR":
            return "trend_aligned"
        if d_5m == "BEAR" and d_15m == "NEUTRAL":
            return "downtrend_continuation"
        if d_5m == "NEUTRAL" and d_15m == "NEUTRAL":
            return "chop_only"
        return None
    return None


def _decide_mode(
    proposed_dir: Optional[str],
    structures: Dict[str, Dict],
) -> Dict:
    """Pick MODE A / MODE B / SKIP given per-TF structure + proposed direction.

    Returns:
        {
          "mode": "aligned" | "counter_trend" | "skip",
          "allow": bool,
          "reason": str,
          "tf_breakdown": {"5m": "BULL/BEAR/NEUTRAL", ...},
        }
    """
    if not proposed_dir:
        return {
            "mode": "skip", "allow": False,
            "reason": "no direction inferred from action",
            "tf_breakdown": {},
        }

    # Per-TF direction (BULL / BEAR / NEUTRAL/CHOP/UNKNOWN)
    def _dir(s):
        d = _verdict_to_direction(s.get("verdict")) if s else None
        return d or "NEUTRAL"

    d_5m = _dir(structures.get("5m", {}))
    d_15m = _dir(structures.get("15m", {}))
    d_1h = _dir(structures.get("1h", {}))
    breakdown = {"5m": d_5m, "15m": d_15m, "1h": d_1h}

    # ── STRICT ALIGNMENT (Task #82, 2026-06-18) ──
    # Data-driven 5m+15m only rule. 1h is informational, not gating
    # (90d data shows 1h adds noise — UNKNOWN/missing 1h buckets bled).
    if strict_main_alignment_enabled():
        pattern = _strict_pattern_match(d_5m, d_15m, proposed_dir)
        if pattern and aligned_enabled():
            return {
                "mode": "aligned", "allow": True,
                "reason": (
                    f"STRICT {pattern}: 5m={d_5m}, 15m={d_15m} → {proposed_dir}"
                ),
                "tf_breakdown": breakdown,
            }
        # Counter-trend Mode B kept for explicit 5m+15m aligned + 1h opposite
        short_aligned = (d_5m == proposed_dir and d_15m == proposed_dir)
        opposite = "BEAR" if proposed_dir == "BULL" else "BULL"
        if short_aligned and d_1h == opposite and counter_trend_enabled():
            return {
                "mode": "counter_trend", "allow": True,
                "reason": (
                    f"Counter-trend scalp — 5m+15m {proposed_dir}, 1h {opposite}"
                ),
                "tf_breakdown": breakdown,
            }
        return {
            "mode": "skip", "allow": False,
            "reason": (
                f"STRICT block — 5m={d_5m}, 15m={d_15m} does not match "
                f"{proposed_dir} pattern (90d audit: mismatch buckets bled -₹430k)"
            ),
            "tf_breakdown": breakdown,
        }

    # ── LEGACY permissive alignment (STRUCTURE_STRICT_ALIGN_MAIN=off) ──
    # Aligned: all 3 TFs match the proposed direction (NEUTRAL counts as match)
    def _matches_or_neutral(tf_dir, target):
        return tf_dir == target or tf_dir == "NEUTRAL"

    aligned = all(_matches_or_neutral(d, proposed_dir) for d in (d_5m, d_15m, d_1h))
    # At least one TF must STRONGLY match (not all neutral)
    has_strong_match = any(d == proposed_dir for d in (d_5m, d_15m, d_1h))

    if aligned and has_strong_match and aligned_enabled():
        return {
            "mode": "aligned", "allow": True,
            "reason": (
                f"All TFs aligned with {proposed_dir} "
                f"(5m={d_5m}, 15m={d_15m}, 1h={d_1h})"
            ),
            "tf_breakdown": breakdown,
        }

    # Counter-trend: 5m + 15m match proposed; 1h opposite
    short_aligned = (d_5m == proposed_dir and d_15m == proposed_dir)
    opposite = "BEAR" if proposed_dir == "BULL" else "BULL"
    if short_aligned and d_1h == opposite and counter_trend_enabled():
        return {
            "mode": "counter_trend", "allow": True,
            "reason": (
                f"Counter-trend scalp — 5m+15m {proposed_dir}, 1h {opposite}"
            ),
            "tf_breakdown": breakdown,
        }

    # Otherwise: skip (chop / conflict)
    return {
        "mode": "skip", "allow": False,
        "reason": (
            f"Structure does not support {proposed_dir} "
            f"(5m={d_5m}, 15m={d_15m}, 1h={d_1h})"
        ),
        "tf_breakdown": breakdown,
    }


# ── Public API: evaluate_entry ────────────────────────────────────────


def evaluate_entry(
    *,
    engine,
    idx: str,
    proposed_action: str,
    source: str = "unknown",
) -> Dict:
    """Decide whether structure supports a proposed entry.

    Args:
        engine: live engine instance (needed if cache miss → refresh)
        idx: NIFTY / BANKNIFTY
        proposed_action: 'BUY CE' / 'BUY PE'
        source: caller label for logging

    Returns:
        {
          "allow": bool,                # caller obeys when mode == "live"
          "mode": "off" | "shadow" | "aligned" | "counter_trend" |
                  "skip" | "no-data" | "error",
          "tuning": dict | None,        # size/SL/T1/etc when allowed
          "alignment": dict | None,
          "reason": str,
          "master_mode": str,
        }

    Default-safe: master OFF or any failure → allow=True, mode descriptive.
    """
    mm = master_mode()
    proposed_dir = _action_to_direction(proposed_action)

    # OFF — never gate
    if mm == "off":
        return {
            "allow": True, "mode": "off", "tuning": None,
            "alignment": None,
            "reason": "STRUCTURE_MODE=off — no gate active",
            "master_mode": mm,
        }

    # Get structure (cache first, else refresh)
    cached = get_cached_structure(idx)
    if not cached and engine is not None:
        try:
            kite = getattr(engine, "kite", None)
            if kite is None:
                # Fallback: try session
                from main import session as _s
                kite = _s.get("kite") if _s else None
            if kite:
                cached = update_structure(kite, idx)
        except Exception as e:
            print(f"[STRUCTURE_GATE] cache miss refresh failed: {e}")

    if not cached:
        # 2026-06-22 BUG FIX: under STRUCTURE_BLOCK_ON_NODATA=on (default),
        # refuse to trade during cold-start window. Today's session showed
        # 24 morning trades passed with structure=UNKNOWN because cache
        # wasn't populated by 9:20 — 3 of them hit WATCHER_EXIT for -₹65k.
        # Block-on-no-data treats cache-miss as "do not trade yet" not
        # "permissive fall-through".
        block_nodata = block_on_nodata_enabled() and mm == "live"
        result = {
            "allow": not block_nodata, "mode": "no-data", "tuning": None,
            "alignment": None,
            "reason": (
                f"no structure data for {idx} — "
                f"{'BLOCKED (cold-start)' if block_nodata else 'fail-safe allow'}"
            ),
            "master_mode": mm,
        }
        if mm in ("shadow", "live"):
            print(
                f"[STRUCTURE_GATE_{mm.upper()}] {source} {idx} {proposed_action} "
                f"→ NO-DATA allow={result['allow']}"
            )
        return result

    # Cache present but verdicts UNKNOWN → same problem, treat as no-data
    structures_check = cached.get("structures", {}) or {}
    v5 = (structures_check.get("5m") or {}).get("verdict", "") or "UNKNOWN"
    v15 = (structures_check.get("15m") or {}).get("verdict", "") or "UNKNOWN"
    if v5 == "UNKNOWN" and v15 == "UNKNOWN" and block_on_nodata_enabled() and mm == "live":
        print(
            f"[STRUCTURE_GATE_LIVE] {source} {idx} {proposed_action} "
            f"→ UNKNOWN verdicts (5m+15m both UNKNOWN) — BLOCKED"
        )
        return {
            "allow": False, "mode": "no-data", "tuning": None,
            "alignment": cached.get("alignment"),
            "reason": f"5m+15m verdicts both UNKNOWN — BLOCKED (need fresh data)",
            "master_mode": mm,
        }

    structures = cached["structures"]
    alignment = cached["alignment"]

    # Decide mode
    decision = _decide_mode(proposed_dir, structures)
    mode = decision["mode"]
    reason = decision["reason"]

    # Tuning per mode
    tuning = None
    if mode == "aligned":
        tuning = mode_a_tuning()
    elif mode == "counter_trend":
        tuning = mode_b_tuning()

    # Shadow log every check
    print(
        f"[STRUCTURE_GATE_{mm.upper()}] {source} {idx} {proposed_action} "
        f"→ mode={mode} allow={decision['allow']} {reason}"
    )

    # In shadow mode, never actually block
    if mm == "shadow":
        return {
            "allow": True, "mode": f"shadow:{mode}", "tuning": tuning,
            "alignment": alignment, "reason": f"shadow — would be {mode}: {reason}",
            "master_mode": mm,
        }

    # Live mode — obey
    return {
        "allow": decision["allow"], "mode": mode, "tuning": tuning,
        "alignment": alignment, "reason": reason, "master_mode": mm,
    }


# ── Background refresh thread ─────────────────────────────────────────


_refresh_thread: Optional[threading.Thread] = None
_refresh_stop_evt = threading.Event()


def _market_session_active() -> bool:
    """True between 9:00 and 15:30 IST (10 min before open through close).

    Used to pick refresh cadence — fast 60s during session, slow 300s off-hours.
    """
    try:
        import pytz as _pytz
        from datetime import datetime as _dt
        _ist = _pytz.timezone("Asia/Kolkata")
        now = _dt.now(_ist)
        if now.weekday() >= 5:
            return False
        minute_of_day = now.hour * 60 + now.minute
        return 9 * 60 <= minute_of_day <= 15 * 60 + 30
    except Exception:
        return False


def _refresh_loop(engine_getter):
    """Background loop — refresh structure for tracked indices.

    2026-06-22 FIX: refresh cadence is now market-aware.
      - In session (9:00-15:30 IST): every 60s — keeps cache fresh enough
        that strict alignment can fire instantly at market open.
      - Off-session: every 300s — saves Kite quota.
      - First iteration: immediate refresh (no initial wait).

    engine_getter: callable that returns the current engine (so we always
    use the live one after token refreshes / engine swaps).
    """
    print(f"[STRUCTURE_GATE] refresh loop started (session-aware cadence)")
    first_run = True
    while not _refresh_stop_evt.is_set():
        try:
            if master_mode() == "off":
                _refresh_stop_evt.wait(_REFRESH_INTERVAL_SEC)
                continue
            engine = engine_getter()
            if engine is None or not getattr(engine, "running", False):
                _refresh_stop_evt.wait(30 if _market_session_active() else 60)
                continue
            kite = getattr(engine, "kite", None)
            if kite is None:
                try:
                    from main import session as _s
                    kite = _s.get("kite") if _s else None
                except Exception:
                    kite = None
            if kite is None:
                _refresh_stop_evt.wait(30 if _market_session_active() else 60)
                continue
            for idx in ("NIFTY", "BANKNIFTY"):
                try:
                    update_structure(kite, idx)
                    if first_run:
                        cur = get_cached_structure(idx)
                        if cur:
                            structs = cur.get("structures", {}) or {}
                            v5 = (structs.get("5m") or {}).get("verdict", "?")
                            v15 = (structs.get("15m") or {}).get("verdict", "?")
                            print(
                                f"[STRUCTURE_GATE] cache primed {idx}: "
                                f"5m={v5}, 15m={v15}"
                            )
                except Exception as e:
                    print(f"[STRUCTURE_GATE] refresh {idx} error: {e}")
            first_run = False
        except Exception as e:
            print(f"[STRUCTURE_GATE] refresh loop error: {e}")
        # Market-aware cadence — 60s in session keeps cache hot
        wait_sec = 60 if _market_session_active() else _REFRESH_INTERVAL_SEC
        _refresh_stop_evt.wait(wait_sec)
    print("[STRUCTURE_GATE] refresh loop stopped")


def start_refresh_thread(engine_getter) -> bool:
    """Start the background refresh thread (idempotent)."""
    global _refresh_thread
    if _refresh_thread is not None and _refresh_thread.is_alive():
        return False
    _refresh_stop_evt.clear()
    _refresh_thread = threading.Thread(
        target=_refresh_loop,
        args=(engine_getter,),
        daemon=True,
        name="structure-refresh",
    )
    _refresh_thread.start()
    return True


def stop_refresh_thread() -> None:
    _refresh_stop_evt.set()


# ── Diagnostics ───────────────────────────────────────────────────────


def diagnostics() -> Dict:
    """Module config + cache snapshot for the API."""
    with _cache_lock:
        cache_info = {
            idx: {
                "age_sec": time.time() - entry["ts"],
                "alignment": entry["alignment"],
            }
            for idx, entry in _structure_cache.items()
        }
    return {
        "master_mode": master_mode(),
        "scalper_enabled": scalper_enabled(),
        "main_enabled": main_enabled(),
        "aligned_enabled": aligned_enabled(),
        "counter_trend_enabled": counter_trend_enabled(),
        "strict_main_alignment": strict_main_alignment_enabled(),
        "block_on_nodata": block_on_nodata_enabled(),
        "mode_a_tuning": mode_a_tuning(),
        "mode_b_tuning": mode_b_tuning(),
        "cache_entries": len(cache_info),
        "cache": cache_info,
        "refresh_thread_alive": (
            _refresh_thread is not None and _refresh_thread.is_alive()
        ),
        "cache_ttl_sec": _CACHE_TTL_SEC,
        "refresh_interval_sec": _REFRESH_INTERVAL_SEC,
    }
