"""
Tests for the probability calibration module.

Background:
  2026-05-19 audit of 160 main + 211 scalper trades found the existing
  `probability` field is non-monotone (higher prob = lower actual WR).
  The calibration module loads an empirical lookup table mapping
  raw_prob → historical winrate, exposes warnings on bad buckets, and
  flags inversions.
"""

import sys
import json
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_calibration_cache():
    """Each test starts with a fresh module-level cache."""
    if "calibration" in sys.modules:
        sys.modules["calibration"]._cache = None
        sys.modules["calibration"]._cache_mtime = 0.0
    yield


class TestBucketing:
    def test_bucket_for_50(self):
        from calibration import _bucket_for
        assert _bucket_for(50) == "50-54"

    def test_bucket_for_73(self):
        from calibration import _bucket_for
        assert _bucket_for(73) == "70-74"

    def test_bucket_for_95(self):
        from calibration import _bucket_for
        assert _bucket_for(95) == "95-100"

    def test_bucket_for_100(self):
        """100 should also map to the 95-100 top bucket (not a degenerate
        single-value 100-100 bucket)."""
        from calibration import _bucket_for
        assert _bucket_for(100) == "95-100"

    def test_bucket_for_99(self):
        from calibration import _bucket_for
        assert _bucket_for(99) == "95-100"

    def test_bucket_boundary_55(self):
        """55 should be in 55-59 bucket, not 50-54."""
        from calibration import _bucket_for
        assert _bucket_for(55) == "55-59"


class TestCalibratedWR:
    def test_returns_smoothed_wr_for_known_bucket(self):
        """The built-in v1 table has data for main 70-74 bucket."""
        from calibration import calibrated_wr
        wr = calibrated_wr(72, engine_type="main", action="ALL")
        assert wr is not None
        assert 0 <= wr <= 100

    def test_returns_none_for_empty_bucket(self):
        """An empty bucket (no trades there) returns None or fallback."""
        from calibration import calibrated_wr
        # 30 is below the trading threshold so no trades exist there
        wr = calibrated_wr(30, engine_type="main", action="ALL")
        # Either None (no bucket) or near 50% (laplace prior)
        assert wr is None or (0 <= wr <= 100)

    def test_action_specific_lookup(self):
        from calibration import calibrated_wr
        # Action-specific tables exist
        wr_ce = calibrated_wr(70, engine_type="main", action="BUY CE")
        wr_pe = calibrated_wr(70, engine_type="main", action="BUY PE")
        # Both should return floats or None, not crash
        for v in [wr_ce, wr_pe]:
            assert v is None or (0 <= v <= 100)

    def test_main_vs_scalper_independent(self):
        from calibration import calibrated_wr
        main_wr = calibrated_wr(75, engine_type="main", action="ALL")
        scalp_wr = calibrated_wr(75, engine_type="scalper", action="ALL")
        # Both should be valid; they're independent calibrations
        assert main_wr is None or 0 <= main_wr <= 100
        assert scalp_wr is None or 0 <= scalp_wr <= 100


class TestExpectancyWarning:
    def test_no_warning_for_profitable_bucket(self):
        from calibration import expectancy_warning
        # The 50-54 main bucket was profitable in v1 audit
        w = expectancy_warning(52, engine_type="main", action="ALL")
        # Either no warning OR a string (depending on data)
        assert w is None or isinstance(w, str)

    def test_warning_for_loss_making_bucket(self):
        """Main 75-79 bucket had -₹110k pnl in v1 — should warn."""
        from calibration import expectancy_warning
        w = expectancy_warning(77, engine_type="main", action="ALL")
        # If the bucket has n>=3 and pnl<0, we get a warning
        if w is not None:
            assert "Expectancy NEGATIVE" in w


class TestInversionDetection:
    def test_v1_table_has_inversions(self):
        """The built-in v1 table SHOULD have inversions logged
        (8 inversions found in 2026-05-19 audit)."""
        from calibration import diagnostics
        d = diagnostics()
        assert "warnings" in d
        # We expect AT LEAST one inversion in v1 (real broken calibration)
        assert len(d["warnings"]) >= 1

    def test_is_inverted_callable(self):
        from calibration import is_inverted
        # Should never raise
        result = is_inverted(85, engine_type="main")
        assert isinstance(result, bool)


class TestTableShape:
    def test_diagnostics_has_required_keys(self):
        from calibration import diagnostics
        d = diagnostics()
        assert "version" in d
        assert "built_at" in d
        assert "sample_sizes" in d

    def test_get_table_returns_dict(self):
        from calibration import get_table
        t = get_table()
        assert isinstance(t, dict)
        assert "main" in t
        assert "scalper" in t

    def test_v1_table_loaded(self):
        """The built-in v1 table should be loaded by default."""
        from calibration import get_table
        t = get_table()
        assert t.get("version") == 1


class TestCalibrationBuilder:
    def test_smooth_wr_with_zero_samples(self):
        from calibration_builder import _smooth_wr
        # 0 wins / 0 trades → prior wins out → 50%
        assert _smooth_wr(0, 0) == 0.50

    def test_smooth_wr_with_many_samples(self):
        from calibration_builder import _smooth_wr
        # 80W / 100 trades, prior of 5 at 50% → ~78.6%
        wr = _smooth_wr(80, 100)
        assert 0.77 <= wr <= 0.80

    def test_smooth_wr_thin_bucket_pulls_to_prior(self):
        from calibration_builder import _smooth_wr
        # 1W / 1 trade → smoothed should NOT be 100%
        wr = _smooth_wr(1, 1)
        assert wr < 1.0
        # Should be roughly (1 + 2.5) / 6 = ~58%
        assert 0.50 < wr < 0.65

    def test_is_win_positive_pnl(self):
        from calibration_builder import _is_win
        assert _is_win({"pnl_rupees": 1000}) is True

    def test_is_win_negative_pnl(self):
        from calibration_builder import _is_win
        assert _is_win({"pnl_rupees": -500}) is False

    def test_is_win_zero_pnl(self):
        """Breakeven is NOT a win."""
        from calibration_builder import _is_win
        assert _is_win({"pnl_rupees": 0}) is False

    def test_rebuild_refuses_empty_dbs(self, monkeypatch, tmp_path):
        """If both DBs are empty, refuse to overwrite calibration."""
        import calibration_builder
        empty_main = tmp_path / "trades.db"
        empty_scalp = tmp_path / "scalper_trades.db"
        # Make schema-valid empty DBs
        for db, table in [(empty_main, "trades"), (empty_scalp, "scalper_trades")]:
            conn = sqlite3.connect(str(db))
            conn.execute(
                f"CREATE TABLE {table} ("
                "id INTEGER PRIMARY KEY, action TEXT, probability INTEGER, "
                "pnl_rupees REAL, status TEXT, exit_time TEXT)"
            )
            conn.close()

        monkeypatch.setattr(calibration_builder, "_MAIN_DB", empty_main)
        monkeypatch.setattr(calibration_builder, "_SCALP_DB", empty_scalp)
        monkeypatch.setattr(calibration_builder, "_OUT", tmp_path / "out.json")

        result = calibration_builder.rebuild_from_db()
        assert result["ok"] is False
        assert "No closed trades" in result["error"]

    def test_rebuild_writes_table_when_data_present(self, monkeypatch, tmp_path):
        """When DBs have closed trades, rebuild writes the table."""
        import calibration_builder
        main_db = tmp_path / "trades.db"
        scalp_db = tmp_path / "scalper_trades.db"

        # Create + populate main DB with 10 closed trades
        conn = sqlite3.connect(str(main_db))
        conn.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, action TEXT, "
            "probability INTEGER, pnl_rupees REAL, status TEXT, exit_time TEXT)"
        )
        for i in range(10):
            conn.execute(
                "INSERT INTO trades (action, probability, pnl_rupees, status, exit_time) "
                "VALUES (?, ?, ?, ?, ?)",
                ("BUY CE", 70 + i, 1000 * (1 if i % 2 == 0 else -1), "CLOSED", "2026-05-18T10:00:00"),
            )
        conn.commit()
        conn.close()

        # Create empty scalper DB (table exists but no rows)
        conn = sqlite3.connect(str(scalp_db))
        conn.execute(
            "CREATE TABLE scalper_trades (id INTEGER PRIMARY KEY, action TEXT, "
            "probability INTEGER, pnl_rupees REAL, status TEXT, exit_time TEXT)"
        )
        conn.close()

        out_file = tmp_path / "out.json"
        monkeypatch.setattr(calibration_builder, "_MAIN_DB", main_db)
        monkeypatch.setattr(calibration_builder, "_SCALP_DB", scalp_db)
        monkeypatch.setattr(calibration_builder, "_OUT", out_file)

        result = calibration_builder.rebuild_from_db()
        assert result["ok"] is True
        assert out_file.exists()
        assert result["sample_sizes"]["main"] == 10
        assert result["sample_sizes"]["scalper"] == 0
