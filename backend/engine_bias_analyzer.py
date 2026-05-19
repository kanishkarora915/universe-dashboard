"""
engine_bias_analyzer — measure structural bias per engine over a rolling window.

WHY THIS EXISTS

Audit of 14d / 35,957 council pulses (2026-05-19) found 3 engines with
severe structural bias:

  oi_flow             +88% bull (10,377 BULL / 681 BEAR)
  price_action        +70% bull (11,842 BULL / 2,054 BEAR)
  seller_positioning  +69% bull (7,347 BULL / 1,367 BEAR)

ROOT CAUSE (verified via code reading):

  oi_flow:
    - PCR > 1.2 fires constantly (Indian market norm: PE writing > CE)
    - CE UNWIND captured as bull, PE UNWIND captured as bear
    - But CE/PE BUILDUP (resistance/support forming) is NOT captured
    - The asymmetry means oi_flow only catches one half of the OI story.

  seller_positioning:
    - Code IS symmetric (pe_ratio > 0.60 → BULL, ce_ratio > 0.60 → BEAR)
    - +69% bias is STRUCTURAL: Indian markets have baseline PE-write
      dominance. pe_ratio > 0.60 fires more often than ce_ratio > 0.60
      simply because that's how the market is.

  price_action:
    - Code is symmetric (momBias BULLISH vs BEARISH)
    - Bias reflects the recent rally regime — CE premiums rising
      more often than PE premiums in trending-up days.

DEEPER FINDING (key insight)

  oi_flow, seller_positioning, price_action all measure correlated
  aspects of the same PE-write-dominance pattern. When they all vote
  bull, the verdict math counts them as 3 independent confirmations,
  but it's actually 1 signal reinforcing itself 3×.

  This explains the calibration finding: at raw_prob >= 90%, actual
  WR is 29%. High consensus = correlated noise, not edge.

THIS MODULE'S ROLE

  Read-only observability:
    • Compute rolling bull/bear bias per engine
    • Flag engines with > N% bias as "structurally one-sided"
    • Expose via /api/engine-bias for the dashboard

  Does NOT modify engine logic. The bias data should inform AGGREGATION
  decisions (downweight correlated engines) — a separate change.
"""

from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_COUNCIL_DB = _DATA_DIR / "council.db"
if not _COUNCIL_DB.exists():
    _COUNCIL_DB = Path(__file__).parent / "council.db"


# Engines that audit identified as suspected-correlated bullish signals.
# When all three vote bull, treat as a single signal, not three.
CORRELATED_BULL_CLUSTER = ["oi_flow", "seller_positioning", "price_action"]


def compute_engine_bias(hours: int = 168) -> dict:
    """Return per-engine bull/bear/neutral counts over last N hours.

    Computes:
      • Raw counts BULL / BEAR / NEUTRAL
      • Bias pct: (BULL - BEAR) / (BULL + BEAR) × 100
      • Fire rate: directional_votes / total_votes
      • Bias flag: "STRUCTURAL_BULL", "STRUCTURAL_BEAR", "DEAD", or "BALANCED"
    """
    if not _COUNCIL_DB.exists():
        return {"error": "council.db not found", "engines": []}

    try:
        conn = sqlite3.connect(str(_COUNCIL_DB))
        cur = conn.execute(
            f"""
            SELECT engine, direction, COUNT(*) AS n
            FROM engine_votes
            WHERE timestamp >= datetime('now', '-{int(hours)} hours')
            GROUP BY engine, direction
            """
        )
        rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    by_engine: dict = {}
    for engine, direction, n in rows:
        by_engine.setdefault(engine, {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0})
        by_engine[engine][direction] = n

    out = []
    for engine, counts in by_engine.items():
        bull = counts.get("BULLISH", 0)
        bear = counts.get("BEARISH", 0)
        neut = counts.get("NEUTRAL", 0)
        total = bull + bear + neut
        directional = bull + bear

        bias_pct = ((bull - bear) / directional * 100) if directional > 0 else 0
        fire_rate = (directional / total * 100) if total > 0 else 0

        # Classify
        if directional == 0:
            flag = "DEAD"
        elif fire_rate < 5:
            flag = "RARE"
        elif bias_pct > 50:
            flag = "STRUCTURAL_BULL"
        elif bias_pct < -50:
            flag = "STRUCTURAL_BEAR"
        else:
            flag = "BALANCED"

        out.append({
            "engine": engine,
            "bull": bull,
            "bear": bear,
            "neutral": neut,
            "total": total,
            "bias_pct": round(bias_pct, 1),
            "fire_rate_pct": round(fire_rate, 1),
            "flag": flag,
            "in_correlated_cluster": engine in CORRELATED_BULL_CLUSTER,
        })

    out.sort(key=lambda r: -abs(r["bias_pct"]))

    return {
        "window_hours": hours,
        "engines": out,
        "correlated_bull_cluster": CORRELATED_BULL_CLUSTER,
        "interpretation": (
            "Engines in the correlated_bull_cluster measure overlapping "
            "aspects of Indian-market PE-write-dominance. When all 3 vote "
            "bullish simultaneously, treat as ONE signal, not three "
            "independent confirmations. See calibration.py — raw_prob >= 90% "
            "has only 29% historical WR (consensus = correlated noise)."
        ),
    }


def is_correlated_cluster_unanimous(engine_votes: dict) -> bool:
    """Given the per-engine dict from one pulse (engine → direction string),
    return True if ALL engines in CORRELATED_BULL_CLUSTER voted BULLISH.

    Useful as a sanity check before firing high-conviction trades.
    """
    for eng in CORRELATED_BULL_CLUSTER:
        if engine_votes.get(eng) != "BULLISH":
            return False
    return True
