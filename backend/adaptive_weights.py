"""
adaptive_weights — daily auto-adjust engine weights based on rolling accuracy.

PHILOSOPHY (user request 2026-06-17):
  "Multi-TF aaj kharab tha kal acha hoga — same time-frame.
   Static disable = lose good days. Track rolling performance,
   boost hot streaks, reduce cold streaks. NEVER permanent kill."

WHAT IT DOES
  1. For each engine, compute rolling accuracy over multiple windows
     - rolling_5d  — recent trend (responsive)
     - rolling_10d — medium-term
     - rolling_30d — baseline (slow)
  2. Compare 5-day vs 30-day baseline
  3. Adjust weight up/down based on streak
  4. Write updated weights to engine_weights.json
  5. Log every adjustment for audit

ADJUSTMENT RULES
  IF rolling_5d > baseline + 5  → HOT STREAK   → boost weight 30%
  IF rolling_5d > baseline + 3  → improving    → boost weight 15%
  IF rolling_5d < baseline - 5  → COLD STREAK  → reduce weight 50%
  IF rolling_5d < baseline - 3  → declining    → reduce weight 25%
  ELSE                          → steady       → small adjustment

CLAMPS
  Weight always in [1, 50]  — engine never gets 0 (kept as 1 minimum
  to keep small voice in mix) and never dominates (capped at 50).
  This way no engine is permanently killed — if it recovers, it
  gets weight back.

INVOCATION
  Run daily at 8:30 AM IST after WebSocket restart.
  Or: on-demand via /api/admin/adaptive-weights/recompute
  Or: from main.py daemon thread.

ENV
  ADAPTIVE_WEIGHTS_ENABLED=on  (default off — explicit opt-in until validated)
  ADAPTIVE_WEIGHTS_MIN_DATA_POINTS=20  per-engine minimum to act
"""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")
_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_WEIGHTS_PATH = Path(__file__).parent / "engine_weights.json"
_AUDIT_DB = _DATA_DIR / "adaptive_weights.db"


def _ist_now() -> datetime:
    return datetime.now(IST)


def _is_enabled() -> bool:
    # 2026-06-17: User approved production activation after shadow validated
    # (fii_dii correctly detected as DECLINING with 1032 data points).
    # Set ADAPTIVE_WEIGHTS_ENABLED=off to revert to shadow-only.
    return os.environ.get("ADAPTIVE_WEIGHTS_ENABLED", "on").lower() in ("on", "1", "true")


def _min_data_points() -> int:
    try:
        return int(os.environ.get("ADAPTIVE_WEIGHTS_MIN_DATA_POINTS", "20"))
    except Exception:
        return 20


# ── Audit log DB ──────────────────────────────────────────────────

def _init_db():
    conn = sqlite3.connect(str(_AUDIT_DB), timeout=10.0)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weight_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            engine TEXT NOT NULL,
            rolling_5d REAL,
            rolling_10d REAL,
            rolling_30d REAL,
            baseline REAL,
            streak TEXT,                    -- HOT / IMPROVING / COLD / DECLINING / STEADY
            weight_before REAL,
            weight_after REAL,
            data_points_5d INTEGER,
            data_points_30d INTEGER,
            reason TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wa_run ON weight_adjustments(run_at)")
    conn.commit()
    conn.close()


# ── Rolling accuracy computation (uses existing accuracy report) ──

def _compute_rolling_per_engine(days: int) -> Dict[str, Dict]:
    """Query engine accuracy for the last N days from backtest_log.

    Uses the SAME data source as /api/reports/engine-accuracy (which
    confirms 4000+ data points per engine). Per-engine accuracy:
    when this engine scored > 0 (contributed to verdict), what was the
    30-min outcome? Counts WIN/LOSS rows.

    Returns: { engine_name: { 'accuracy': X, 'data_points': Y } }
    """
    try:
        from ml_feedback import _bt_conn, COL_TO_ENGINE
    except Exception as e:
        print(f"[ADAPTIVE_WEIGHTS] ml_feedback import failed: {e}")
        return {}

    conn = _bt_conn()
    if conn is None:
        print(f"[ADAPTIVE_WEIGHTS] backtest_log unavailable")
        return {}

    cutoff = (_ist_now() - timedelta(days=days)).isoformat()
    try:
        rows = conn.execute(
            "SELECT * FROM backtest_log WHERE timestamp > ? AND checked = 1",
            (cutoff,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {}

    out: Dict[str, Dict] = {}
    for col, engine_name in COL_TO_ENGINE.items():
        try:
            active = [r for r in rows if (r[col] is not None) and (r[col] > 0)]
        except (IndexError, KeyError):
            active = []
        valid = [r for r in active if r["outcome_30min"] in ("WIN", "LOSS")]
        if not valid:
            out[engine_name] = {"accuracy": 0.0, "data_points": 0}
            continue
        wins = sum(1 for r in valid if r["outcome_30min"] == "WIN")
        out[engine_name] = {
            "accuracy": round(wins / len(valid) * 100, 1),
            "data_points": len(valid),
        }
    return out


def _legacy_query_council_direct_UNUSED(days: int) -> Dict[str, Dict]:
    """Fallback: read council.db directly for engine vote outcomes."""
    council_path = _DATA_DIR / "council.db"
    if not council_path.exists():
        return {}
    cutoff = (_ist_now() - timedelta(days=days)).isoformat()
    out: Dict[str, Dict] = {}
    try:
        conn = sqlite3.connect(str(council_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        # council_verdicts has per-engine votes + outcome
        rows = conn.execute(
            "SELECT raw_payload, bias_accuracy FROM council_verdicts "
            "WHERE timestamp >= ? AND bias_accuracy IS NOT NULL",
            (cutoff,)
        ).fetchall()
        conn.close()
        per_engine_total: Dict[str, int] = {}
        per_engine_correct: Dict[str, int] = {}
        for r in rows:
            try:
                payload = json.loads(r["raw_payload"] or "{}")
                accuracy = (r["bias_accuracy"] or "").upper()
                was_correct = accuracy in ("CORRECT", "PARTIAL_CORRECT", "TRUE")
                voters = payload.get("voters") or {}
                # voters: { engine_name: { score: ..., bias: BULL/BEAR/NEUTRAL } }
                council_bias = payload.get("bias", "NEUTRAL")
                for engine, vdata in voters.items():
                    if not isinstance(vdata, dict):
                        continue
                    engine_bias = (vdata.get("bias") or "NEUTRAL").upper()
                    if engine_bias == "NEUTRAL":
                        continue
                    per_engine_total[engine] = per_engine_total.get(engine, 0) + 1
                    # Engine "correct" if it voted with council AND council was right
                    voted_with_council = engine_bias == council_bias
                    if voted_with_council and was_correct:
                        per_engine_correct[engine] = per_engine_correct.get(engine, 0) + 1
                    elif not voted_with_council and not was_correct:
                        # Engine voted opposite to wrong council → also correct
                        per_engine_correct[engine] = per_engine_correct.get(engine, 0) + 1
            except Exception:
                continue
        for engine, total in per_engine_total.items():
            correct = per_engine_correct.get(engine, 0)
            out[engine] = {
                "accuracy": (correct / total * 100) if total else 0.0,
                "data_points": total,
            }
    except Exception as e:
        print(f"[ADAPTIVE_WEIGHTS] council direct read failed: {e}")
    return out


# ── Streak classification ─────────────────────────────────────────

def _classify_streak(rolling_5d: float, baseline: float) -> str:
    diff = rolling_5d - baseline
    if diff >= 5:    return "HOT"
    if diff >= 3:    return "IMPROVING"
    if diff <= -5:   return "COLD"
    if diff <= -3:   return "DECLINING"
    return "STEADY"


def _adjust_weight(current_weight: float, streak: str, manually_disabled: bool = False) -> float:
    # Respect manual override — if user pinned engine OFF (0), keep it OFF
    # regardless of streak. User judgement > auto-tune for explicit choices.
    if manually_disabled:
        return 0.0
    if   streak == "HOT":        new = current_weight * 1.30
    elif streak == "IMPROVING":  new = current_weight * 1.15
    elif streak == "COLD":       new = current_weight * 0.50
    elif streak == "DECLINING":  new = current_weight * 0.75
    else:                        new = current_weight * 1.0
    # Clamp — NEVER zero (keep small voice), NEVER above 50
    return round(max(1.0, min(50.0, new)), 1)


# ── Main routine ──────────────────────────────────────────────────

def recompute_and_save() -> Dict:
    """Compute rolling accuracy, adjust weights, save to engine_weights.json,
    audit-log every change. Returns a summary dict.
    """
    _init_db()
    summary = {
        "ran_at": _ist_now().isoformat(),
        "enabled": _is_enabled(),
        "adjustments": [],
        "skipped": [],
        "weights_before": {},
        "weights_after": {},
    }

    # Load current weights
    try:
        with open(_WEIGHTS_PATH) as f:
            weights = json.load(f)
    except Exception as e:
        summary["error"] = f"weights read failed: {e}"
        return summary
    summary["weights_before"] = dict(weights)

    # Manual override — engines pinned OFF by user
    # Stored in weights file under "_disabled_engines": ["vwap", "fii_dii", ...]
    manually_disabled = set(weights.get("_disabled_engines") or [])
    summary["manually_disabled"] = list(manually_disabled)

    # Compute rolling accuracies
    rolling_5d  = _compute_rolling_per_engine(days=5)
    rolling_10d = _compute_rolling_per_engine(days=10)
    rolling_30d = _compute_rolling_per_engine(days=30)

    min_dp = _min_data_points()
    conn = sqlite3.connect(str(_AUDIT_DB), timeout=10.0)

    # Iterate every engine in weights file
    for engine_name, current_weight in list(weights.items()):
        if engine_name in ("last_updated", "auto_adjusted", "_disabled_engines"):
            continue  # metadata keys
        if not isinstance(current_weight, (int, float)):
            continue

        # User manually disabled — pin at 0, skip auto-adjust
        if engine_name in manually_disabled:
            if current_weight != 0:
                weights[engine_name] = 0
                summary["adjustments"].append({
                    "engine": engine_name,
                    "weight_before": current_weight,
                    "weight_after": 0,
                    "streak": "MANUAL_OFF",
                    "rolling_5d": None,
                    "rolling_30d": None,
                    "data_points_5d": 0,
                })
            else:
                summary["skipped"].append({
                    "engine": engine_name,
                    "reason": "manually disabled (pinned at 0)",
                })
            continue

        d5 = rolling_5d.get(engine_name, {}) or {}
        d10 = rolling_10d.get(engine_name, {}) or {}
        d30 = rolling_30d.get(engine_name, {}) or {}

        acc5 = d5.get("accuracy")
        acc10 = d10.get("accuracy")
        acc30 = d30.get("accuracy")
        dp5 = d5.get("data_points", 0)
        dp30 = d30.get("data_points", 0)

        # Skip if insufficient data
        if dp5 < min_dp or dp30 < min_dp:
            summary["skipped"].append({
                "engine": engine_name,
                "reason": f"insufficient data (5d={dp5}, 30d={dp30}, min={min_dp})",
            })
            continue
        if acc5 is None or acc30 is None:
            summary["skipped"].append({
                "engine": engine_name,
                "reason": "no accuracy data",
            })
            continue

        baseline = acc30
        streak = _classify_streak(acc5, baseline)
        new_weight = _adjust_weight(current_weight, streak)

        # Audit log entry (always, even when no change)
        conn.execute(
            "INSERT INTO weight_adjustments "
            "(run_at, engine, rolling_5d, rolling_10d, rolling_30d, baseline, "
            "streak, weight_before, weight_after, data_points_5d, data_points_30d, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary["ran_at"], engine_name,
                round(acc5, 2) if acc5 is not None else None,
                round(acc10, 2) if acc10 is not None else None,
                round(acc30, 2) if acc30 is not None else None,
                round(baseline, 2),
                streak, current_weight, new_weight,
                dp5, dp30,
                f"5d={acc5:.1f}% vs 30d={acc30:.1f}% → {streak}",
            )
        )

        if new_weight != current_weight:
            weights[engine_name] = new_weight
            summary["adjustments"].append({
                "engine": engine_name,
                "weight_before": current_weight,
                "weight_after": new_weight,
                "streak": streak,
                "rolling_5d": round(acc5, 1),
                "rolling_30d": round(baseline, 1),
                "data_points_5d": dp5,
            })

    conn.commit()
    conn.close()

    # Persist updated weights only if enabled
    if _is_enabled() and summary["adjustments"]:
        weights["last_updated"] = summary["ran_at"]
        weights["auto_adjusted"] = True
        try:
            with open(_WEIGHTS_PATH, "w") as f:
                json.dump(weights, f, indent=2)
            print(f"[ADAPTIVE_WEIGHTS] saved {len(summary['adjustments'])} adjustments to engine_weights.json")
        except Exception as e:
            summary["error"] = f"weights save failed: {e}"
    elif not _is_enabled():
        summary["mode"] = "shadow"
        print(f"[ADAPTIVE_WEIGHTS] SHADOW mode — {len(summary['adjustments'])} would-be adjustments NOT saved")

    summary["weights_after"] = dict(weights) if _is_enabled() else summary["weights_before"]
    return summary


# ── Read-only diagnostics ─────────────────────────────────────────

def get_recent_runs(limit: int = 10) -> List[Dict]:
    """Return the last N adjustment runs from audit log."""
    if not _AUDIT_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(_AUDIT_DB), timeout=10.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT run_at FROM weight_adjustments "
            "ORDER BY run_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        out = []
        for r in rows:
            ts = r["run_at"]
            details = conn.execute(
                "SELECT engine, rolling_5d, rolling_30d, streak, weight_before, weight_after, "
                "data_points_5d, reason FROM weight_adjustments WHERE run_at=?",
                (ts,)
            ).fetchall()
            out.append({
                "run_at": ts,
                "engines": [dict(d) for d in details],
            })
        conn.close()
        return out
    except Exception as e:
        print(f"[ADAPTIVE_WEIGHTS] runs read failed: {e}")
        return []
