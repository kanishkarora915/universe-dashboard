"""
Tests for engine_bias_analyzer.

Verifies:
  • compute_engine_bias produces correct counts/flags from a seeded DB
  • is_correlated_cluster_unanimous detects unanimous bull cluster
  • Bias classification thresholds (STRUCTURAL_BULL/BEAR/BALANCED/RARE/DEAD)
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def _seed_db(db_path, votes):
    """votes = [(engine, direction, count), ...]"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE engine_votes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT NOT NULL, "
        "pulse_id TEXT, "
        "engine TEXT NOT NULL, "
        "direction TEXT NOT NULL, "
        "conviction REAL DEFAULT 0)"
    )
    now = datetime.now()
    for engine, direction, count in votes:
        for i in range(count):
            ts = (now - timedelta(minutes=i)).isoformat()
            conn.execute(
                "INSERT INTO engine_votes (timestamp, pulse_id, engine, direction, conviction) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, f"p{i}", engine, direction, 5.0),
            )
    conn.commit()
    conn.close()


class TestComputeEngineBias:
    def test_structural_bull_flag(self, monkeypatch, tmp_path):
        import engine_bias_analyzer
        db = tmp_path / "council.db"
        _seed_db(db, [
            ("oi_flow", "BULLISH", 100),
            ("oi_flow", "BEARISH", 5),
            ("oi_flow", "NEUTRAL", 20),
        ])
        monkeypatch.setattr(engine_bias_analyzer, "_COUNCIL_DB", db)
        result = engine_bias_analyzer.compute_engine_bias(hours=24)
        engines = {e["engine"]: e for e in result["engines"]}
        assert engines["oi_flow"]["flag"] == "STRUCTURAL_BULL"
        assert engines["oi_flow"]["bias_pct"] > 50

    def test_structural_bear_flag(self, monkeypatch, tmp_path):
        import engine_bias_analyzer
        db = tmp_path / "council.db"
        _seed_db(db, [
            ("test_engine", "BULLISH", 5),
            ("test_engine", "BEARISH", 100),
            ("test_engine", "NEUTRAL", 20),
        ])
        monkeypatch.setattr(engine_bias_analyzer, "_COUNCIL_DB", db)
        result = engine_bias_analyzer.compute_engine_bias(hours=24)
        engines = {e["engine"]: e for e in result["engines"]}
        assert engines["test_engine"]["flag"] == "STRUCTURAL_BEAR"

    def test_balanced_flag(self, monkeypatch, tmp_path):
        import engine_bias_analyzer
        db = tmp_path / "council.db"
        _seed_db(db, [
            ("balanced_engine", "BULLISH", 50),
            ("balanced_engine", "BEARISH", 50),
            ("balanced_engine", "NEUTRAL", 20),
        ])
        monkeypatch.setattr(engine_bias_analyzer, "_COUNCIL_DB", db)
        result = engine_bias_analyzer.compute_engine_bias(hours=24)
        engines = {e["engine"]: e for e in result["engines"]}
        assert engines["balanced_engine"]["flag"] == "BALANCED"
        assert abs(engines["balanced_engine"]["bias_pct"]) < 50

    def test_dead_flag_when_only_neutral(self, monkeypatch, tmp_path):
        import engine_bias_analyzer
        db = tmp_path / "council.db"
        _seed_db(db, [
            ("dead_engine", "NEUTRAL", 100),
        ])
        monkeypatch.setattr(engine_bias_analyzer, "_COUNCIL_DB", db)
        result = engine_bias_analyzer.compute_engine_bias(hours=24)
        engines = {e["engine"]: e for e in result["engines"]}
        assert engines["dead_engine"]["flag"] == "DEAD"

    def test_rare_flag_when_low_fire_rate(self, monkeypatch, tmp_path):
        """An engine firing < 5% of the time gets RARE flag."""
        import engine_bias_analyzer
        db = tmp_path / "council.db"
        _seed_db(db, [
            ("rare_engine", "BULLISH", 1),
            ("rare_engine", "BEARISH", 1),
            ("rare_engine", "NEUTRAL", 100),  # 98% neutral
        ])
        monkeypatch.setattr(engine_bias_analyzer, "_COUNCIL_DB", db)
        result = engine_bias_analyzer.compute_engine_bias(hours=24)
        engines = {e["engine"]: e for e in result["engines"]}
        assert engines["rare_engine"]["flag"] == "RARE"

    def test_correlated_cluster_flag(self, monkeypatch, tmp_path):
        """Engines in CORRELATED_BULL_CLUSTER get the flag."""
        import engine_bias_analyzer
        db = tmp_path / "council.db"
        _seed_db(db, [
            ("oi_flow", "BULLISH", 10),
            ("oi_flow", "BEARISH", 5),
            ("fii_dii", "BULLISH", 10),
            ("fii_dii", "BEARISH", 5),
        ])
        monkeypatch.setattr(engine_bias_analyzer, "_COUNCIL_DB", db)
        result = engine_bias_analyzer.compute_engine_bias(hours=24)
        engines = {e["engine"]: e for e in result["engines"]}
        assert engines["oi_flow"]["in_correlated_cluster"] is True
        assert engines["fii_dii"]["in_correlated_cluster"] is False

    def test_handles_missing_db(self, monkeypatch, tmp_path):
        import engine_bias_analyzer
        nonexistent = tmp_path / "nope.db"
        monkeypatch.setattr(engine_bias_analyzer, "_COUNCIL_DB", nonexistent)
        result = engine_bias_analyzer.compute_engine_bias(hours=24)
        assert "error" in result


class TestCorrelatedClusterUnanimous:
    def test_all_three_bull_is_unanimous(self):
        from engine_bias_analyzer import is_correlated_cluster_unanimous
        votes = {
            "oi_flow": "BULLISH",
            "price_action": "BULLISH",
            "seller_positioning": "BULLISH",
            "fii_dii": "BEARISH",  # other engines irrelevant
        }
        assert is_correlated_cluster_unanimous(votes) is True

    def test_one_dissent_breaks_unanimity(self):
        from engine_bias_analyzer import is_correlated_cluster_unanimous
        votes = {
            "oi_flow": "BULLISH",
            "price_action": "BULLISH",
            "seller_positioning": "BEARISH",  # dissent
        }
        assert is_correlated_cluster_unanimous(votes) is False

    def test_neutral_breaks_unanimity(self):
        from engine_bias_analyzer import is_correlated_cluster_unanimous
        votes = {
            "oi_flow": "BULLISH",
            "price_action": "NEUTRAL",  # not bull
            "seller_positioning": "BULLISH",
        }
        assert is_correlated_cluster_unanimous(votes) is False

    def test_missing_engine_breaks_unanimity(self):
        from engine_bias_analyzer import is_correlated_cluster_unanimous
        votes = {
            "oi_flow": "BULLISH",
            "price_action": "BULLISH",
            # seller_positioning missing
        }
        assert is_correlated_cluster_unanimous(votes) is False


class TestClusterMembership:
    def test_cluster_has_three_known_engines(self):
        from engine_bias_analyzer import CORRELATED_BULL_CLUSTER
        assert "oi_flow" in CORRELATED_BULL_CLUSTER
        assert "price_action" in CORRELATED_BULL_CLUSTER
        assert "seller_positioning" in CORRELATED_BULL_CLUSTER
        assert len(CORRELATED_BULL_CLUSTER) == 3
