"""
calibration_builder — rebuilds the calibration table from current trade history.

Pulls all closed trades from trades.db + scalper_trades.db, groups by
(engine_type, action, raw_prob bucket), applies Laplace smoothing, and
writes the resulting table to /data/calibration_table.json.

Run via /api/calibration/rebuild — never auto-runs.
"""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_OUT = _DATA_DIR / "calibration_table.json"

# Match scalper_mode.SCALPER_DB / trades.db locations
_MAIN_DB = _DATA_DIR / "trades.db"
if not _MAIN_DB.exists():
    _MAIN_DB = Path(__file__).parent / "trades.db"

_SCALP_DB = _DATA_DIR / "scalper_trades.db"
if not _SCALP_DB.exists():
    _SCALP_DB = Path(__file__).parent / "scalper_trades.db"


def _fetch_closed(db_path: Path, table: str) -> list:
    """Return [{action, probability, pnl_rupees}, ...] for closed trades."""
    if not db_path.exists():
        return []
    rows = []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"SELECT action, probability, pnl_rupees "
            f"FROM {table} "
            f"WHERE status != 'OPEN' AND probability IS NOT NULL "
            f"AND exit_time IS NOT NULL"
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return rows


def _smooth_wr(wins: int, n: int, prior_n: int = 5, prior_wr: float = 0.50) -> float:
    """Laplace-smoothed winrate. Pulls thin buckets toward 50%."""
    return (wins + prior_n * prior_wr) / (n + prior_n)


def _is_win(row: dict) -> bool:
    return (row.get("pnl_rupees") or 0) > 0


def _build_engine_calibration(trades: list) -> dict:
    """For one engine's trades, build {action: {bucket: stats}} map.
    5pp buckets from 50-54 through 95-100.
    """
    # Last bucket spans 95-100 (6 raw values) — avoids degenerate
    # single-value bucket at probability=100.
    buckets = list(range(50, 95, 5)) + [95]   # 50,55,60,...,90,95
    table: dict = {}

    for action in ["BUY CE", "BUY PE", "ALL"]:
        rows = trades if action == "ALL" else [t for t in trades if t["action"] == action]
        table[action] = {}

        for lo in buckets:
            hi = 100 if lo == 95 else lo + 4
            bucket_rows = [t for t in rows if lo <= int(t.get("probability") or 0) <= hi]
            n = len(bucket_rows)
            wins = sum(1 for t in bucket_rows if _is_win(t))
            pnl = sum((t.get("pnl_rupees") or 0) for t in bucket_rows)
            wr_raw = wins / n if n else None
            wr_sm = _smooth_wr(wins, n) if n else 0.50
            avg_pnl = pnl / n if n else 0

            table[action][f"{lo}-{hi}"] = {
                "n": n,
                "wins": wins,
                "losses": n - wins,
                "wr_raw": round(wr_raw * 100, 1) if wr_raw is not None else None,
                "wr_smoothed": round(wr_sm * 100, 1),
                "total_pnl": round(pnl, 0),
                "avg_pnl": round(avg_pnl, 0),
                "expectancy_positive": pnl > 0,
            }
    return table


def _detect_inversions(calibration: dict, engine_name: str) -> list:
    """Find buckets where WR drops as raw_prob increases. These are
    actionable signals that the raw probability score is broken.
    """
    warnings = []
    all_buckets = calibration.get("ALL", {})
    keys = sorted(all_buckets.keys(), key=lambda k: int(k.split("-")[0]))
    for i in range(1, len(keys)):
        prev = all_buckets[keys[i - 1]]
        cur = all_buckets[keys[i]]
        if prev.get("n", 0) < 3 or cur.get("n", 0) < 3:
            continue
        drop = prev.get("wr_smoothed", 0) - cur.get("wr_smoothed", 0)
        if drop > 5:  # >5pp drop = significant inversion
            warnings.append({
                "engine": engine_name,
                "type": "INVERSION",
                "from_bucket": int(keys[i - 1].split("-")[0]),
                "to_bucket": int(keys[i].split("-")[0]),
                "wr_drop_pp": round(drop, 1),
                "message": (
                    f"{engine_name}: raw_prob {keys[i-1]}% has higher WR "
                    f"({prev.get('wr_smoothed')}%) than raw_prob {keys[i]}% "
                    f"({cur.get('wr_smoothed')}%) — calibration inverted"
                ),
            })
    return warnings


def rebuild_from_db() -> dict:
    """Rebuild calibration table from live DBs, write to /data, return summary."""
    main_trades = _fetch_closed(_MAIN_DB, "trades")
    scalp_trades = _fetch_closed(_SCALP_DB, "scalper_trades")

    main_cal = _build_engine_calibration(main_trades)
    scalp_cal = _build_engine_calibration(scalp_trades)

    warnings = _detect_inversions(main_cal, "main") + _detect_inversions(scalp_cal, "scalper")

    out = {
        "version": 1,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source": "rebuild_from_db()",
        "sample_sizes": {
            "main": len(main_trades),
            "scalper": len(scalp_trades),
        },
        "prior": {"n": 5, "wr": 0.50, "method": "laplace"},
        "main": main_cal,
        "scalper": scalp_cal,
        "warnings": warnings,
    }

    # Refuse to overwrite if sample sizes look implausible (likely DB error)
    if len(main_trades) == 0 and len(scalp_trades) == 0:
        return {
            "ok": False,
            "error": "No closed trades found in either DB — refusing to overwrite calibration table",
            "main_db": str(_MAIN_DB),
            "scalp_db": str(_SCALP_DB),
        }

    try:
        _OUT.parent.mkdir(parents=True, exist_ok=True)
        _OUT.write_text(json.dumps(out, indent=2))
    except Exception as e:
        return {"ok": False, "error": f"Failed to write {_OUT}: {e}"}

    return {
        "ok": True,
        "built_at": out["built_at"],
        "wrote_to": str(_OUT),
        "sample_sizes": out["sample_sizes"],
        "inversions_found": len(warnings),
        "top_warnings": warnings[:5],
    }
