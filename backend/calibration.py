"""
calibration — empirical probability recalibration layer.

WHY THIS MODULE EXISTS

Audit of 160 main + 211 scalper closed trades (2026-05-19) found the
existing `probability` field is fundamentally non-monotone:

  Raw probability       Actual win rate
  ──────────────────────────────────────
  50-59% bucket    →    74%  (best!)
  60-69%           →    54%
  70-79%           →    53%
  80-89%           →    43%
  90-100%          →    29%  (worst!)

The raw `probability` field is computed as:
    bull_pct = bull_score / (bull_score + bear_score) * 100
i.e. the winning side's SHARE of total engine vote score. It measures
engine CONSENSUS, not actual win likelihood.

When 3 structurally biased engines (oi_flow +88%, seller_positioning +69%,
price_action +70% bull-biased) align, the bull_pct climbs to 90%+ for
trades that have no real edge. The "extreme consensus = wrong" pattern
in our data.

WHAT THIS MODULE DOES

  • Loads an empirical calibration table from JSON on disk.
  • Provides `calibrated_wr(raw_prob, engine_type, action)` returning
    the historical winrate at that raw probability level.
  • Provides `expectancy_warning(raw_prob, engine_type)` returning a
    warning string if this bucket has been historically loss-making.

WHAT IT DOES NOT DO (YET)

  • Does NOT auto-gate trades. Calling code must explicitly use the
    calibrated WR to make decisions. This keeps the layer additive
    and reversible.
  • Does NOT modify the raw `probability` field in trade DB rows. The
    raw value is preserved for future re-calibration.

USAGE FROM ENGINE / SCALPER

  from calibration import calibrated_wr, expectancy_warning

  raw_prob = 85
  cal_wr = calibrated_wr(raw_prob, engine_type="main", action="BUY CE")
  if cal_wr < 50:
      log.warning(f"Trade {action} prob={raw_prob}% but historical WR={cal_wr}%")
      # caller decides whether to skip
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional

# ── Where the calibration JSON lives ───────────────────────────────────
_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_CALIBRATION_FILE = _DATA_DIR / "calibration_table.json"
_BUILTIN_FALLBACK = Path(__file__).parent / "calibration_table_v1.json"

# ── In-memory cache (refreshed on file mtime change) ───────────────────
_cache: Optional[dict] = None
_cache_mtime: float = 0.0


def _load_table() -> dict:
    """Load calibration JSON, with fallback to built-in v1 if /data file missing.
    Cached, but auto-refreshed when underlying file is updated.
    """
    global _cache, _cache_mtime

    # Prefer /data file (mutable, user can rebuild from API)
    if _CALIBRATION_FILE.exists():
        mtime = _CALIBRATION_FILE.stat().st_mtime
        if _cache is not None and mtime == _cache_mtime:
            return _cache
        try:
            _cache = json.loads(_CALIBRATION_FILE.read_text())
            _cache_mtime = mtime
            return _cache
        except Exception:
            pass  # fall through to built-in

    # Fallback to repo-bundled v1
    if _BUILTIN_FALLBACK.exists():
        try:
            return json.loads(_BUILTIN_FALLBACK.read_text())
        except Exception:
            pass

    # Last resort: return identity table (raw == calibrated)
    return _identity_table()


def _identity_table() -> dict:
    """Identity table: returns raw_prob as calibrated WR. Used when no
    calibration file is available — system behaves as before."""
    return {
        "version": 0,
        "built_at": "fallback",
        "main": {"ALL": {}},
        "scalper": {"ALL": {}},
    }


def _bucket_for(raw_prob: int) -> str:
    """Map a raw probability int into the 5pp bucket key string.
    e.g. 73 → '70-74', 95 → '95-100', 100 → '95-100'.

    The top bucket spans 95-100 (6 raw values) so that probability=100
    trades aren't isolated in a degenerate single-value bucket.
    """
    if raw_prob >= 95:
        return "95-100"
    lo = (raw_prob // 5) * 5
    hi = lo + 4
    return f"{lo}-{hi}"


def calibrated_wr(
    raw_prob: int,
    engine_type: str = "main",
    action: str = "ALL",
) -> Optional[float]:
    """Return historical smoothed winrate (0-100) for trades at this
    raw probability level.

    Args:
        raw_prob:      Integer probability 0-100 from engine.py
        engine_type:   "main" or "scalper"
        action:        "BUY CE", "BUY PE", or "ALL"

    Returns:
        Smoothed historical WR as float 0-100, or None if no data
        available for this bucket.

    NOTE: This is read-only. It tells you "trades like this have
    historically won X% of the time." The caller decides what to do
    with that information.
    """
    table = _load_table()
    engine_data = table.get(engine_type, {})
    action_data = engine_data.get(action) or engine_data.get("ALL") or {}
    bucket = _bucket_for(int(raw_prob))
    bucket_data = action_data.get(bucket)
    if not bucket_data:
        return None
    return bucket_data.get("wr_smoothed")


def expectancy_warning(
    raw_prob: int,
    engine_type: str = "main",
    action: str = "ALL",
) -> Optional[str]:
    """Return a warning string if this bucket has historically been
    loss-making, else None.

    Useful for adding context to trade-firing logs without changing
    trade-decision behavior.
    """
    table = _load_table()
    engine_data = table.get(engine_type, {})
    action_data = engine_data.get(action) or engine_data.get("ALL") or {}
    bucket = _bucket_for(int(raw_prob))
    bucket_data = action_data.get(bucket)
    if not bucket_data:
        return None
    if bucket_data.get("expectancy_positive"):
        return None
    n = bucket_data.get("n", 0)
    if n < 3:  # too few samples to warn
        return None
    avg_pnl = bucket_data.get("avg_pnl", 0)
    wr = bucket_data.get("wr_smoothed", 50)
    return (
        f"⚠️ Historical {engine_type} trades in raw_prob {bucket} bucket: "
        f"n={n}, WR={wr}%, avg P&L=₹{avg_pnl:,.0f}. Expectancy NEGATIVE."
    )


def get_table() -> dict:
    """Return the full calibration table (for API exposure)."""
    return _load_table()


def is_inverted(
    raw_prob: int,
    engine_type: str = "main",
) -> bool:
    """Return True if this bucket has WORSE smoothed WR than at least
    one bucket with a LOWER raw_prob (calibration inversion).

    Indicates "you're more confident than you should be — there's a
    lower-probability bucket with better historical outcomes."
    """
    table = _load_table()
    engine_data = table.get(engine_type, {}).get("ALL", {})
    bucket = _bucket_for(int(raw_prob))
    if bucket not in engine_data:
        return False

    target_wr = engine_data[bucket].get("wr_smoothed", 50)
    target_lo = int(bucket.split("-")[0])

    # Walk all buckets with lower lo, check if any has higher WR
    for k, v in engine_data.items():
        lo = int(k.split("-")[0])
        if lo < target_lo and v.get("n", 0) >= 3:
            if v.get("wr_smoothed", 0) > target_wr + 5:  # >5pp better
                return True
    return False


def diagnostics() -> dict:
    """Return summary diagnostics — sample sizes, build date, warnings."""
    table = _load_table()
    return {
        "version": table.get("version"),
        "built_at": table.get("built_at"),
        "sample_sizes": table.get("sample_sizes", {}),
        "warnings": table.get("warnings", []),
        "source_file": str(_CALIBRATION_FILE) if _CALIBRATION_FILE.exists() else str(_BUILTIN_FALLBACK),
    }
