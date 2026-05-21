"""
Tests for engine_correlation_analyzer (Level-2 refactor measurement).

Built 2026-05-21 to validate: which of the 11 engines vote together
(correlated noise) vs which measure unique signals (genuine edge).
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def _seed_votes(db_path, vote_rows):
    """vote_rows = [(pulse_id, engine, direction), ...]"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE engine_votes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT, pulse_id TEXT, engine TEXT, direction TEXT, "
        "conviction REAL DEFAULT 0)"
    )
    now = datetime.now().isoformat()
    for pid, engine, direction in vote_rows:
        conn.execute(
            "INSERT INTO engine_votes (timestamp, pulse_id, engine, direction, conviction) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, pid, engine, direction, 5.0),
        )
    conn.commit()
    conn.close()


# ── PAIRWISE CORRELATION ──────────────────────────────────────────────

class TestPairwiseCorrelation:
    def test_no_data_returns_empty(self, monkeypatch, tmp_path):
        import engine_correlation_analyzer as eca
        monkeypatch.setattr(eca, "_COUNCIL_DB", tmp_path / "nope.db")
        result = eca.compute_pairwise_correlation(hours=24)
        assert result.get("pairs") == [] or "error" in result

    def test_two_engines_always_agree_bullish(self, monkeypatch, tmp_path):
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        # 10 pulses, both engines always BULLISH
        rows = []
        for i in range(10):
            rows.append((f"p{i}", "eng_a", "BULLISH"))
            rows.append((f"p{i}", "eng_b", "BULLISH"))
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.compute_pairwise_correlation(hours=24)
        pair = result["pairs"][0]
        assert pair["agreement_rate"] == 1.0
        assert pair["verdict"] == "HIGHLY_CORRELATED"

    def test_two_engines_perfectly_opposite(self, monkeypatch, tmp_path):
        """One always BULL, other always BEAR → 0% agreement."""
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        rows = []
        for i in range(10):
            rows.append((f"p{i}", "eng_a", "BULLISH"))
            rows.append((f"p{i}", "eng_b", "BEARISH"))
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.compute_pairwise_correlation(hours=24)
        pair = result["pairs"][0]
        assert pair["agreement_rate"] == 0.0
        assert pair["verdict"] == "INDEPENDENT"

    def test_neutral_votes_excluded(self, monkeypatch, tmp_path):
        """Pulses where either engine is NEUTRAL should not count."""
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        rows = [
            ("p1", "eng_a", "BULLISH"),
            ("p1", "eng_b", "BULLISH"),  # both fire → agree
            ("p2", "eng_a", "NEUTRAL"),
            ("p2", "eng_b", "BULLISH"),  # one neutral → skip
            ("p3", "eng_a", "BEARISH"),
            ("p3", "eng_b", "BEARISH"),  # both fire → agree
            ("p4", "eng_a", "BULLISH"),
            ("p4", "eng_b", "BEARISH"),  # both fire → disagree
        ]
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.compute_pairwise_correlation(hours=24)
        pair = result["pairs"][0]
        # 3 directional pulses (p1, p3, p4), 2 agreements
        assert pair["directional_pulses"] == 3
        assert pair["agreement_pulses"] == 2
        assert abs(pair["agreement_rate"] - 2/3) < 0.01

    def test_three_engines_full_matrix(self, monkeypatch, tmp_path):
        """3 engines → 3 pairs (A-B, A-C, B-C)."""
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        # A and B always agree (BULL), C always disagrees (BEAR)
        rows = []
        for i in range(10):
            rows.append((f"p{i}", "A", "BULLISH"))
            rows.append((f"p{i}", "B", "BULLISH"))
            rows.append((f"p{i}", "C", "BEARISH"))
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.compute_pairwise_correlation(hours=24)
        assert len(result["pairs"]) == 3
        # A-B should be HIGHLY (100%), A-C and B-C should be INDEPENDENT (0%)
        rates = {(p["engine_a"], p["engine_b"]): p["agreement_rate"] for p in result["pairs"]}
        assert rates[("A", "B")] == 1.0
        assert rates[("A", "C")] == 0.0
        assert rates[("B", "C")] == 0.0


# ── CLUSTERING ─────────────────────────────────────────────────────────

class TestFindCorrelatedClusters:
    def test_no_correlation_no_clusters(self, monkeypatch, tmp_path):
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        # 3 engines, all independent (mixed directions)
        rows = []
        for i in range(10):
            rows.append((f"p{i}", "A", "BULLISH" if i % 3 == 0 else "BEARISH"))
            rows.append((f"p{i}", "B", "BULLISH" if i % 2 == 0 else "BEARISH"))
            rows.append((f"p{i}", "C", "BEARISH" if i % 4 == 0 else "BULLISH"))
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.find_correlated_clusters(hours=24, threshold=0.8)
        assert result["clusters"] == []
        assert set(result["independent"]) == {"A", "B", "C"}

    def test_pair_above_threshold_forms_cluster(self, monkeypatch, tmp_path):
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        rows = []
        for i in range(10):
            rows.append((f"p{i}", "A", "BULLISH"))
            rows.append((f"p{i}", "B", "BULLISH"))  # A↔B always agree
            rows.append((f"p{i}", "C", "BEARISH"))  # C disagrees
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.find_correlated_clusters(hours=24, threshold=0.7)
        # Cluster should contain {A, B}, C independent
        assert len(result["clusters"]) == 1
        assert set(result["clusters"][0]["engines"]) == {"A", "B"}
        assert "C" in result["independent"]

    def test_transitive_closure_extends_cluster(self, monkeypatch, tmp_path):
        """If A↔B and B↔C both above threshold, cluster = {A, B, C}
        even if A↔C is below."""
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        rows = []
        # A and B always agree
        # B and C always agree
        # A and C therefore always agree too (transitive in this case)
        for i in range(10):
            rows.append((f"p{i}", "A", "BULLISH"))
            rows.append((f"p{i}", "B", "BULLISH"))
            rows.append((f"p{i}", "C", "BULLISH"))
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.find_correlated_clusters(hours=24, threshold=0.7)
        assert len(result["clusters"]) == 1
        assert set(result["clusters"][0]["engines"]) == {"A", "B", "C"}

    def test_avg_and_min_correlation_reported(self, monkeypatch, tmp_path):
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        # A and B always agree (1.0), include them in cluster
        rows = []
        for i in range(10):
            rows.append((f"p{i}", "A", "BULLISH"))
            rows.append((f"p{i}", "B", "BULLISH"))
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.find_correlated_clusters(hours=24, threshold=0.7)
        c = result["clusters"][0]
        assert c["avg_correlation"] == 1.0
        assert c["min_correlation"] == 1.0


# ── CONSOLIDATION SUGGESTIONS ─────────────────────────────────────────

class TestSuggestConsolidation:
    def test_returns_hypothesis_groups(self, monkeypatch, tmp_path):
        import engine_correlation_analyzer as eca
        # Use empty DB so we still get structure
        monkeypatch.setattr(eca, "_COUNCIL_DB", tmp_path / "empty.db")
        result = eca.suggest_consolidation(hours=24)
        assert "hypothesis_groups" in result
        assert "OI_HEALTH" in result["hypothesis_groups"]
        assert "TREND_STRENGTH" in result["hypothesis_groups"]

    def test_matches_hypothesis_when_cluster_overlaps(self, monkeypatch, tmp_path):
        """When observed cluster overlaps with a hypothesis group,
        it should be marked as matching."""
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        # oi_flow + seller_positioning + smart_money always agree → should
        # match OI_HEALTH hypothesis (which contains those 3)
        rows = []
        for i in range(15):
            for eng in ("oi_flow", "seller_positioning", "smart_money"):
                rows.append((f"p{i}", eng, "BULLISH"))
        _seed_votes(db, rows)
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        result = eca.suggest_consolidation(hours=24, threshold=0.7)
        assert len(result["observed_clusters"]) == 1
        c = result["observed_clusters"][0]
        assert c["matches_hypothesis"] == "OI_HEALTH"
        assert c["overlap_with_hypothesis"] == 3


# ── REPORT GENERATION ─────────────────────────────────────────────────

class TestGenerateTextReport:
    def test_report_includes_pulse_count(self, monkeypatch, tmp_path):
        import engine_correlation_analyzer as eca
        db = tmp_path / "c.db"
        _seed_votes(db, [
            ("p1", "A", "BULLISH"),
            ("p1", "B", "BULLISH"),
        ])
        monkeypatch.setattr(eca, "_COUNCIL_DB", db)
        report = eca.generate_text_report(hours=24)
        assert "ENGINE CORRELATION ANALYSIS" in report
        assert "1 pulses" in report

    def test_report_lists_hypothesis_groups(self, monkeypatch, tmp_path):
        import engine_correlation_analyzer as eca
        monkeypatch.setattr(eca, "_COUNCIL_DB", tmp_path / "empty.db")
        report = eca.generate_text_report(hours=24)
        assert "HYPOTHESIS" in report
        assert "OI_HEALTH" in report
