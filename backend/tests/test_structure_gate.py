"""
Tests for structure_gate module — Phase 2 entry-gate integration.

Built 2026-05-27.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Clear cache + env vars before each test."""
    for var in [
        "STRUCTURE_MODE", "STRUCTURE_SCALPER_ENABLED", "STRUCTURE_MAIN_ENABLED",
        "STRUCTURE_ALIGNED_ENABLED", "STRUCTURE_COUNTER_TREND_ENABLED",
    ]:
        monkeypatch.delenv(var, raising=False)
    import structure_gate as sg
    sg.clear_cache()


def _struct(verdict):
    """Build a minimal structure result with given verdict."""
    return {"verdict": verdict, "confidence": "HIGH", "reason": ""}


def _cache_entry(structures, alignment):
    import time
    return {
        "ts": time.time(),
        "idx": "NIFTY",
        "structures": structures,
        "alignment": alignment,
    }


def _put_cache(idx, structures_by_tf, alignment):
    """Inject a structure into the cache for testing."""
    import structure_gate as sg
    import time
    sg._structure_cache[idx] = {
        "ts": time.time(),
        "idx": idx,
        "structures": structures_by_tf,
        "alignment": alignment,
    }


# ── Master mode parsing ───────────────────────────────────────────────


class TestMasterMode:
    def test_default_live(self, monkeypatch):
        # 2026-06-17: Default flipped shadow→live after 90d audit
        # proved 5m+15m alignment edge.
        monkeypatch.delenv("STRUCTURE_MODE", raising=False)
        from structure_gate import master_mode
        assert master_mode() == "live"

    def test_shadow(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "shadow")
        from structure_gate import master_mode
        assert master_mode() == "shadow"

    def test_live(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        from structure_gate import master_mode
        assert master_mode() == "live"

    def test_invalid_falls_back_live(self, monkeypatch):
        # 2026-06-17: Invalid values fall back to default (now live)
        monkeypatch.setenv("STRUCTURE_MODE", "garbage")
        from structure_gate import master_mode
        assert master_mode() == "live"

    def test_explicit_off(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "off")
        from structure_gate import master_mode
        assert master_mode() == "off"


# ── Off mode — never gates ────────────────────────────────────────────


class TestOffMode:
    def test_off_always_allows(self, monkeypatch):
        # 2026-06-15: default changed shadow; force off for this test
        monkeypatch.setenv("STRUCTURE_MODE", "off")
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is True
        assert r["mode"] == "off"
        assert r["tuning"] is None

    def test_off_works_without_cache(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "off")
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="BANKNIFTY",
            proposed_action="BUY PE", source="test",
        )
        assert r["allow"] is True
        assert r["mode"] == "off"


# ── Shadow mode — computes but always allows ──────────────────────────


class TestShadowMode:
    def test_shadow_no_data_allows(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "shadow")
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is True
        assert r["mode"] == "no-data"

    def test_shadow_aligned_still_allows(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "shadow")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("UPTREND"), "1h": _struct("UPTREND")},
            {"direction": "BULL", "aligned": True, "conviction": "HIGH"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is True
        assert "shadow" in r["mode"]
        # But tuning should be present (Mode A would be selected)
        assert r["tuning"] is not None

    def test_shadow_skip_still_allows(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "shadow")
        _put_cache("NIFTY",
            {"5m": _struct("DOWNTREND"), "15m": _struct("DOWNTREND"), "1h": _struct("DOWNTREND")},
            {"direction": "BEAR", "aligned": True, "conviction": "HIGH"},
        )
        from structure_gate import evaluate_entry
        # BUY CE against DOWNTREND — would normally skip
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        # Shadow always allows, even when skip would fire
        assert r["allow"] is True


# ── Live mode — Mode A (aligned trend) ────────────────────────────────


class TestLiveModeA:
    def test_all_uptrend_BUY_CE_aligned(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("UPTREND"), "1h": _struct("UPTREND")},
            {"direction": "BULL", "aligned": True, "conviction": "HIGH"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is True
        assert r["mode"] == "aligned"
        assert r["tuning"]["size_mult"] == 1.0
        assert r["tuning"]["use_structural_trail"] is True

    def test_all_downtrend_BUY_PE_aligned(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("BANKNIFTY",
            {"5m": _struct("DOWNTREND"), "15m": _struct("DOWNTREND"), "1h": _struct("DOWNTREND")},
            {"direction": "BEAR", "aligned": True, "conviction": "HIGH"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="BANKNIFTY",
            proposed_action="BUY PE", source="test",
        )
        assert r["allow"] is True
        assert r["mode"] == "aligned"

    def test_uptrend_BUY_PE_skipped(self, monkeypatch):
        """Trying to BUY PE during UPTREND on all TFs → skip."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("UPTREND"), "1h": _struct("UPTREND")},
            {"direction": "BULL", "aligned": True, "conviction": "HIGH"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY PE", source="test",
        )
        assert r["allow"] is False
        assert r["mode"] == "skip"


# ── Live mode — Mode B (counter-trend) ────────────────────────────────


class TestLiveModeB:
    """Counter-trend (Mode B). Strict alignment must be off for these to
    reach the counter-trend branch — when strict is on, 5m+15m aligned
    fires Mode A first and Mode B becomes unreachable."""

    def test_counter_trend_bull(self, monkeypatch):
        """5m+15m BULL but 1h BEAR → counter-trend BUY CE scalp."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        monkeypatch.setenv("STRUCTURE_STRICT_ALIGN_MAIN", "off")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("UPTREND"), "1h": _struct("DOWNTREND")},
            {"direction": "MIXED", "aligned": False, "conviction": "LOW"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is True
        assert r["mode"] == "counter_trend"
        assert r["tuning"]["size_mult"] == 0.4
        assert r["tuning"]["sl_pct"] == 0.05
        assert r["tuning"]["max_hold_min"] == 10

    def test_counter_trend_bear(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        monkeypatch.setenv("STRUCTURE_STRICT_ALIGN_MAIN", "off")
        _put_cache("NIFTY",
            {"5m": _struct("DOWNTREND"), "15m": _struct("DOWNTREND"), "1h": _struct("UPTREND")},
            {"direction": "MIXED", "aligned": False, "conviction": "LOW"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY PE", source="test",
        )
        assert r["allow"] is True
        assert r["mode"] == "counter_trend"


# ── Sub-flag disable ──────────────────────────────────────────────────


class TestSubFlags:
    def test_aligned_disabled_skips_aligned(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        monkeypatch.setenv("STRUCTURE_ALIGNED_ENABLED", "off")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("UPTREND"), "1h": _struct("UPTREND")},
            {"direction": "BULL"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        # Aligned disabled → would have been aligned but now skip
        assert r["mode"] == "skip"

    def test_counter_trend_disabled(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        monkeypatch.setenv("STRUCTURE_STRICT_ALIGN_MAIN", "off")
        monkeypatch.setenv("STRUCTURE_COUNTER_TREND_ENABLED", "off")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("UPTREND"), "1h": _struct("DOWNTREND")},
            {"direction": "MIXED"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        # Counter-trend disabled → skip
        assert r["mode"] == "skip"


# ── No-data fail-safe ─────────────────────────────────────────────────


class TestNoData:
    def test_no_data_live_blocks_by_default(self, monkeypatch):
        """Task #86 (2026-06-22): no-data now BLOCKS by default in live mode.

        Was fail-safe ALLOW which let 24 trades fire on 22-Jun with
        UNKNOWN structure → -₹65k from 3 WATCHER_EXIT bleeders.
        """
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        monkeypatch.delenv("STRUCTURE_BLOCK_ON_NODATA", raising=False)
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is False
        assert r["mode"] == "no-data"
        assert "BLOCKED" in r["reason"]

    def test_no_data_live_legacy_allow(self, monkeypatch):
        """STRUCTURE_BLOCK_ON_NODATA=off reverts to legacy fail-safe allow."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        monkeypatch.setenv("STRUCTURE_BLOCK_ON_NODATA", "off")
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is True
        assert r["mode"] == "no-data"

    def test_no_data_shadow_always_allow(self, monkeypatch):
        """Shadow mode never blocks even with block_on_nodata=on."""
        monkeypatch.setenv("STRUCTURE_MODE", "shadow")
        monkeypatch.delenv("STRUCTURE_BLOCK_ON_NODATA", raising=False)
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is True

    def test_unknown_verdicts_blocked(self, monkeypatch):
        """Cache present but both 5m+15m UNKNOWN → also blocked."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        monkeypatch.delenv("STRUCTURE_BLOCK_ON_NODATA", raising=False)
        _put_cache("NIFTY",
            {"5m": _struct("UNKNOWN"), "15m": _struct("UNKNOWN"), "1h": _struct("UPTREND")},
            {"direction": "UNKNOWN"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(
            engine=None, idx="NIFTY",
            proposed_action="BUY CE", source="test",
        )
        assert r["allow"] is False
        assert r["mode"] == "no-data"


# ── Strict alignment (Task #82, 2026-06-18) ───────────────────────────


class TestStrictAlignment:
    """Data-driven 5m+15m alignment from 90d audit. Default ON."""

    def test_default_strict_on(self, monkeypatch):
        monkeypatch.delenv("STRUCTURE_STRICT_ALIGN_MAIN", raising=False)
        from structure_gate import strict_main_alignment_enabled
        assert strict_main_alignment_enabled() is True

    def test_uptrend_chop_BUY_CE_BLOCKED(self, monkeypatch):
        """The worst bucket (-₹139k/90d). UP/CHOP CE must be blocked."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("CHOP"), "1h": _struct("UPTREND")},
            {"direction": "BULL"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY CE", source="test")
        assert r["allow"] is False
        assert r["mode"] == "skip"
        assert "STRICT block" in r["reason"]

    def test_chop_uptrend_BUY_CE_BLOCKED(self, monkeypatch):
        """Lagging entry (-₹28k/90d). CHOP/UP CE must be blocked."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("CHOP"), "15m": _struct("UPTREND"), "1h": _struct("UPTREND")},
            {"direction": "BULL"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY CE", source="test")
        assert r["allow"] is False
        assert r["mode"] == "skip"

    def test_uptrend_uptrend_BUY_CE_ALLOWED(self, monkeypatch):
        """The winning bucket (+₹133k/90d). UP/UP CE must allow."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("UPTREND"), "1h": _struct("DOWNTREND")},
            {"direction": "BULL"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY CE", source="test")
        assert r["allow"] is True
        assert r["mode"] == "aligned"
        assert "trend_aligned" in r["reason"]

    def test_chop_chop_BUY_CE_ALLOWED(self, monkeypatch):
        """Range play (+₹100k/90d). CHOP/CHOP must allow either direction."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("CHOP"), "15m": _struct("CHOP"), "1h": _struct("CHOP")},
            {"direction": "MIXED"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY CE", source="test")
        assert r["allow"] is True
        assert r["mode"] == "aligned"
        assert "chop_only" in r["reason"]

    def test_chop_chop_BUY_PE_ALLOWED(self, monkeypatch):
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("CHOP"), "15m": _struct("CHOP"), "1h": _struct("CHOP")},
            {"direction": "MIXED"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY PE", source="test")
        assert r["allow"] is True
        assert r["mode"] == "aligned"

    def test_downtrend_chop_BUY_PE_ALLOWED(self, monkeypatch):
        """Downtrend continuation (+₹92k/90d). DOWN/CHOP PE must allow."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("DOWNTREND"), "15m": _struct("CHOP"), "1h": _struct("CHOP")},
            {"direction": "BEAR"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY PE", source="test")
        assert r["allow"] is True
        assert r["mode"] == "aligned"
        assert "downtrend_continuation" in r["reason"]

    def test_downtrend_chop_BUY_CE_BLOCKED(self, monkeypatch):
        """DOWN/CHOP is for PE only. CE entry must be blocked."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("DOWNTREND"), "15m": _struct("CHOP"), "1h": _struct("CHOP")},
            {"direction": "BEAR"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY CE", source="test")
        assert r["allow"] is False
        assert r["mode"] == "skip"

    def test_uptrend_downtrend_BLOCKED(self, monkeypatch):
        """Conflict bucket (-₹51k/90d). UP/DN must be blocked either way."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("DOWNTREND"), "1h": _struct("CHOP")},
            {"direction": "MIXED"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY CE", source="test")
        assert r["allow"] is False
        r2 = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY PE", source="test")
        assert r2["allow"] is False

    def test_strict_off_reverts_to_legacy(self, monkeypatch):
        """STRUCTURE_STRICT_ALIGN_MAIN=off → old _matches_or_neutral kicks in."""
        monkeypatch.setenv("STRUCTURE_MODE", "live")
        monkeypatch.setenv("STRUCTURE_STRICT_ALIGN_MAIN", "off")
        _put_cache("NIFTY",
            {"5m": _struct("UPTREND"), "15m": _struct("CHOP"), "1h": _struct("UPTREND")},
            {"direction": "BULL"},
        )
        from structure_gate import evaluate_entry
        r = evaluate_entry(engine=None, idx="NIFTY",
                            proposed_action="BUY CE", source="test")
        # Legacy: UP/NEUTRAL/UP counts as aligned (NEUTRAL passes)
        assert r["allow"] is True
        assert r["mode"] == "aligned"


# ── Diagnostics ───────────────────────────────────────────────────────


class TestDiagnostics:
    def test_diagnostics_shape(self, monkeypatch):
        # 2026-06-17: default is now 'live'; test the structure is intact
        monkeypatch.delenv("STRUCTURE_MODE", raising=False)
        from structure_gate import diagnostics
        d = diagnostics()
        assert d["master_mode"] == "live"
        assert "mode_a_tuning" in d
        assert "mode_b_tuning" in d
        assert d["mode_a_tuning"]["size_mult"] == 1.0
        assert d["mode_b_tuning"]["size_mult"] == 0.4
        assert d["strict_main_alignment"] is True
