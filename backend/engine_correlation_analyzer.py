"""
engine_correlation_analyzer — measure how independent the 11 engines really are.

WHY THIS MODULE EXISTS

User insight 2026-05-21:
  "Jab tak sare engines align krte hai bullish/bearish, move ALREADY ho
   chuka hota hai. PCR kuch bhi ho — sideways bhi ho sakta hai, reversal
   bhi. Engine bias hota hai kyunki wo dekhte hai jo market mein hora hai."

The 11 engines look independent but the audit suggested many measure
overlapping aspects of the same underlying market state. If true, the
"vote" math (bull_pct = sum of bull engines / total) is fundamentally
broken because correlated engines pile on the same signal multiple times.

This module MEASURES the actual correlation between engines using their
recorded votes in council.db (35,957+ pulses over 14 days).

WHAT IT COMPUTES

For each (engine_A, engine_B) pair, given all pulses where BOTH engines
voted directionally (not NEUTRAL):

  agreement_rate = (pulses both BULL or both BEAR) / total directional pulses

  agreement_rate > 0.80  →  HIGHLY correlated (same underlying signal)
  agreement_rate 0.60-0.80 → moderately correlated
  agreement_rate < 0.60   →  independent signals

PURE READ — no behavior change. Just measurement that informs the
Level-2 engine consolidation refactor.
"""

from __future__ import annotations
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Optional

_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
_COUNCIL_DB = _DATA_DIR / "council.db"
if not _COUNCIL_DB.exists():
    _COUNCIL_DB = Path(__file__).parent / "council.db"


# Suggested consolidation hypothesis (will be validated by data)
HYPOTHESIS_GROUPS = {
    "OI_HEALTH": ["oi_flow", "seller_positioning", "smart_money"],
    "TREND_STRENGTH": ["multi_timeframe", "vwap", "market_context"],
    "PRICE_DYNAMICS": ["price_action"],
    "MACRO_CONTEXT": ["fii_dii", "global_cues"],
    "SPECIAL_SIGNALS": ["predictive", "trap_fingerprints"],
}


def _pull_pulse_votes(hours: int = 336) -> dict:
    """Pull all engine votes from last N hours grouped by pulse_id.

    Returns:
        {pulse_id: {engine_name: direction, ...}, ...}
        direction ∈ {"BULLISH", "BEARISH", "NEUTRAL"}
    """
    if not _COUNCIL_DB.exists():
        return {}

    pulses: dict = defaultdict(dict)
    try:
        conn = sqlite3.connect(str(_COUNCIL_DB))
        cur = conn.execute(
            f"""
            SELECT pulse_id, engine, direction
            FROM engine_votes
            WHERE timestamp >= datetime('now', '-{int(hours)} hours')
            """
        )
        for pid, engine, direction in cur.fetchall():
            pulses[pid][engine] = direction
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return dict(pulses)


def compute_pairwise_correlation(hours: int = 336) -> dict:
    """For each pair of engines, compute agreement rate.

    Returns dict with structure:
      {
        "window_hours": int,
        "pulse_count": int,
        "engines": list of engine names found in data,
        "pairs": list of {
            "engine_a": str,
            "engine_b": str,
            "directional_pulses": int,   # pulses where both fired directionally
            "agreement_pulses": int,     # pulses where both agreed (BULL+BULL or BEAR+BEAR)
            "agreement_rate": float,     # 0.0-1.0
            "verdict": str,              # "HIGHLY_CORRELATED" | "MODERATELY" | "INDEPENDENT"
        },
        ...
      }
    """
    pulses = _pull_pulse_votes(hours=hours)
    if not pulses:
        return {"error": "no pulse data", "pairs": []}

    # Collect all engine names
    all_engines = set()
    for vote_map in pulses.values():
        all_engines.update(vote_map.keys())
    engines = sorted(all_engines)

    # For each pair, count agreement/disagreement
    pair_stats = []
    for i, eng_a in enumerate(engines):
        for eng_b in engines[i+1:]:
            both_bull = 0
            both_bear = 0
            a_bull_b_bear = 0
            a_bear_b_bull = 0

            for vote_map in pulses.values():
                d_a = vote_map.get(eng_a)
                d_b = vote_map.get(eng_b)
                if d_a in (None, "NEUTRAL") or d_b in (None, "NEUTRAL"):
                    continue
                if d_a == "BULLISH" and d_b == "BULLISH":
                    both_bull += 1
                elif d_a == "BEARISH" and d_b == "BEARISH":
                    both_bear += 1
                elif d_a == "BULLISH" and d_b == "BEARISH":
                    a_bull_b_bear += 1
                elif d_a == "BEARISH" and d_b == "BULLISH":
                    a_bear_b_bull += 1

            directional = both_bull + both_bear + a_bull_b_bear + a_bear_b_bull
            agreement = both_bull + both_bear

            if directional == 0:
                continue

            rate = agreement / directional
            if rate >= 0.80:
                verdict = "HIGHLY_CORRELATED"
            elif rate >= 0.60:
                verdict = "MODERATELY_CORRELATED"
            else:
                verdict = "INDEPENDENT"

            pair_stats.append({
                "engine_a": eng_a,
                "engine_b": eng_b,
                "directional_pulses": directional,
                "agreement_pulses": agreement,
                "both_bull": both_bull,
                "both_bear": both_bear,
                "a_bull_b_bear": a_bull_b_bear,
                "a_bear_b_bull": a_bear_b_bull,
                "agreement_rate": round(rate, 3),
                "verdict": verdict,
            })

    pair_stats.sort(key=lambda r: -r["agreement_rate"])

    return {
        "window_hours": hours,
        "pulse_count": len(pulses),
        "engines": engines,
        "pairs": pair_stats,
    }


def find_correlated_clusters(hours: int = 336, threshold: float = 0.70) -> dict:
    """Build clusters of mutually-correlated engines.

    Uses transitive closure: if A↔B and B↔C both have rate >= threshold,
    then {A, B, C} form a cluster (even if A↔C is just below threshold).

    Returns:
        {
          "threshold": float,
          "clusters": [
            {
              "engines": [str, ...],
              "avg_correlation": float,
              "min_correlation": float,
            },
            ...
          ],
          "independent": [str, ...],  # engines not in any cluster
        }
    """
    corr = compute_pairwise_correlation(hours=hours)
    if not corr.get("pairs"):
        return {
            "threshold": threshold,
            "window_hours": hours,
            "pulse_count": corr.get("pulse_count", 0),
            "clusters": [],
            "independent": [],
        }

    # Build adjacency map of "correlated" connections
    adj = defaultdict(set)
    rates: dict = {}
    for p in corr["pairs"]:
        if p["agreement_rate"] >= threshold:
            a, b = p["engine_a"], p["engine_b"]
            adj[a].add(b)
            adj[b].add(a)
            rates[(a, b)] = p["agreement_rate"]
            rates[(b, a)] = p["agreement_rate"]

    # Build connected-component clusters via BFS
    all_engines = set(corr["engines"])
    visited = set()
    clusters = []

    for engine in all_engines:
        if engine in visited:
            continue
        if engine not in adj:
            continue
        # BFS from this engine
        cluster = set()
        queue = [engine]
        while queue:
            cur = queue.pop()
            if cur in cluster:
                continue
            cluster.add(cur)
            visited.add(cur)
            queue.extend(adj[cur] - cluster)

        if len(cluster) >= 2:
            # Compute avg + min correlation within cluster
            engines_list = sorted(cluster)
            pair_rates = []
            for i, x in enumerate(engines_list):
                for y in engines_list[i+1:]:
                    if (x, y) in rates:
                        pair_rates.append(rates[(x, y)])
            avg_r = sum(pair_rates) / len(pair_rates) if pair_rates else 0
            min_r = min(pair_rates) if pair_rates else 0
            clusters.append({
                "engines": engines_list,
                "size": len(engines_list),
                "avg_correlation": round(avg_r, 3),
                "min_correlation": round(min_r, 3),
                "pair_count": len(pair_rates),
            })

    independent = sorted(all_engines - visited)
    clusters.sort(key=lambda c: -c["size"])

    return {
        "threshold": threshold,
        "window_hours": hours,
        "pulse_count": corr["pulse_count"],
        "clusters": clusters,
        "independent": independent,
    }


def suggest_consolidation(hours: int = 336, threshold: float = 0.70) -> dict:
    """Suggest engine merger groups based on actual correlation data.

    Compares observed clusters against the HYPOTHESIS_GROUPS dict to see
    if the user's intuition about which engines overlap is confirmed.
    """
    clusters_data = find_correlated_clusters(hours=hours, threshold=threshold)
    observed_clusters = clusters_data["clusters"]

    # Compare each observed cluster vs hypothesis groups
    matches = []
    for obs in observed_clusters:
        obs_engines = set(obs["engines"])
        best_match = None
        best_overlap = 0
        for group_name, group_engines in HYPOTHESIS_GROUPS.items():
            overlap = len(obs_engines & set(group_engines))
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = group_name
        matches.append({
            **obs,
            "matches_hypothesis": best_match,
            "overlap_with_hypothesis": best_overlap,
        })

    return {
        "threshold": threshold,
        "window_hours": hours,
        "pulse_count": clusters_data["pulse_count"],
        "observed_clusters": matches,
        "independent_engines": clusters_data["independent"],
        "hypothesis_groups": HYPOTHESIS_GROUPS,
        "interpretation": (
            "Engines in the same observed cluster vote together >= threshold "
            "of the time. They likely measure overlapping signals and should "
            "be merged into a single meta-engine for Level-2 refactor. "
            "Independent engines (no cluster) measure unique signals — keep separate."
        ),
    }


def generate_text_report(hours: int = 336) -> str:
    """Plain-text report for CLI / log inspection."""
    data = suggest_consolidation(hours=hours)
    lines = []
    lines.append("=" * 72)
    lines.append(f"ENGINE CORRELATION ANALYSIS — last {hours} hours ({data['pulse_count']} pulses)")
    lines.append("=" * 72)
    lines.append("")

    lines.append("CORRELATED CLUSTERS (engines that vote together >= 70% of pulses):")
    lines.append("")
    if not data["observed_clusters"]:
        lines.append("  (no clusters found — all engines vote independently)")
    for c in data["observed_clusters"]:
        lines.append(f"  Cluster ({c['size']} engines, avg corr {c['avg_correlation']*100:.0f}%):")
        for e in c["engines"]:
            lines.append(f"    • {e}")
        if c.get("matches_hypothesis"):
            lines.append(f"    → matches hypothesis: {c['matches_hypothesis']}")
        lines.append("")

    lines.append("INDEPENDENT ENGINES (no high correlation with others):")
    for e in data["independent_engines"]:
        lines.append(f"  • {e}")
    lines.append("")

    lines.append("HYPOTHESIS (from 2026-05-21 user discussion):")
    for group_name, engines in HYPOTHESIS_GROUPS.items():
        lines.append(f"  {group_name}: {', '.join(engines)}")

    return "\n".join(lines)
