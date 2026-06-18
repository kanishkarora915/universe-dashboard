"""
Tests for scalper_health — market-health → scalper aggression level.

Built 2026-05-22.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("SCALPER_ADAPTIVE_HEALTH", raising=False)
    import scalper_health
    scalper_health._cache.clear()


def _regime(**kw):
    """Build a classify_regime() return dict with sane defaults."""
    base = {
        "regime": "NORMAL", "vix": 15, "atr_ratio": 1.0,
        "day_range_pct": 0.8, "is_expiry": False, "time_window": "MIDDAY",
    }
    base.update(kw)
    return base


# ── MODE PARSING ───────────────────────────────────────────────────────

class TestMode:
    def test_default_live(self, monkeypatch):
        # Default flipped shadow → live on 2026-06-18 (W1 D5 activation)
        monkeypatch.delenv("SCALPER_ADAPTIVE_HEALTH", raising=False)
        from scalper_health import _mode
        assert _mode() == "live"

    def test_off(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "off")
        from scalper_health import _mode
        assert _mode() == "off"

    def test_shadow(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "shadow")
        from scalper_health import _mode
        assert _mode() == "shadow"

    def test_live(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "live")
        from scalper_health import _mode
        assert _mode() == "live"

    def test_invalid_falls_back_live(self, monkeypatch):
        # Was shadow fallback; now live fallback (matches new default)
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "garbage")
        from scalper_health import _mode
        assert _mode() == "live"


# ── OFF MODE ───────────────────────────────────────────────────────────

class TestOffMode:
    def test_off_always_balanced(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "off")
        from scalper_health import assess
        r = assess(engine=None, idx="NIFTY")
        assert r["level"] == "BALANCED"
        assert r["mode"] == "off"


# ── LEVEL CLASSIFICATION ───────────────────────────────────────────────

class TestLevels:
    def test_healthy_market_aggressive(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "shadow")
        import scalper_health
        monkeypatch.setattr("volatility_detector.classify_regime",
                            lambda eng: _regime(vix=15, atr_ratio=1.5))
        monkeypatch.setattr(scalper_health, "_recent_streak", lambda: 0)
        r = scalper_health.assess(engine=object(), idx="NIFTY")
        assert r["level"] == "AGGRESSIVE"

    def test_expiry_forces_defensive(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "shadow")
        import scalper_health
        monkeypatch.setattr("volatility_detector.classify_regime",
                            lambda eng: _regime(regime="EXPIRY-DAY", is_expiry=True,
                                                 vix=15, atr_ratio=1.5))
        monkeypatch.setattr(scalper_health, "_recent_streak", lambda: 0)
        r = scalper_health.assess(engine=object(), idx="NIFTY")
        assert r["level"] == "DEFENSIVE"

    def test_extreme_forces_defensive(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "shadow")
        import scalper_health
        monkeypatch.setattr("volatility_detector.classify_regime",
                            lambda eng: _regime(regime="EXTREME", vix=28))
        monkeypatch.setattr(scalper_health, "_recent_streak", lambda: 0)
        r = scalper_health.assess(engine=object(), idx="NIFTY")
        assert r["level"] == "DEFENSIVE"

    def test_dead_market_defensive(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "shadow")
        import scalper_health
        monkeypatch.setattr("volatility_detector.classify_regime",
                            lambda eng: _regime(regime="LOW-VOL", vix=10,
                                                 atr_ratio=0.7, time_window="LUNCH_CHOP"))
        monkeypatch.setattr(scalper_health, "_recent_streak", lambda: 0)
        r = scalper_health.assess(engine=object(), idx="NIFTY")
        assert r["level"] == "DEFENSIVE"

    def test_normal_market_balanced(self, monkeypatch):
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "shadow")
        import scalper_health
        monkeypatch.setattr("volatility_detector.classify_regime",
                            lambda eng: _regime(vix=15, atr_ratio=1.0))
        monkeypatch.setattr(scalper_health, "_recent_streak", lambda: 0)
        r = scalper_health.assess(engine=object(), idx="NIFTY")
        assert r["level"] == "BALANCED"

    def test_losing_streak_drags_down(self, monkeypatch):
        """Healthy market but a losing streak → drops out of AGGRESSIVE."""
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "shadow")
        import scalper_health
        monkeypatch.setattr("volatility_detector.classify_regime",
                            lambda eng: _regime(vix=15, atr_ratio=1.5))
        monkeypatch.setattr(scalper_health, "_recent_streak", lambda: -4)
        r = scalper_health.assess(engine=object(), idx="NIFTY")
        # 50 +15 +10 -15 = 60 → BALANCED, not AGGRESSIVE
        assert r["level"] == "BALANCED"


# ── SENSOR FAILURE ─────────────────────────────────────────────────────

class TestSafety:
    def test_sensor_failure_falls_back(self, monkeypatch):
        """classify_regime throwing must not crash — neutral result."""
        monkeypatch.setenv("SCALPER_ADAPTIVE_HEALTH", "shadow")
        import scalper_health

        def _boom(eng):
            raise RuntimeError("sensor down")

        monkeypatch.setattr("volatility_detector.classify_regime", _boom)
        monkeypatch.setattr(scalper_health, "_recent_streak", lambda: 0)
        r = scalper_health.assess(engine=object(), idx="NIFTY")
        assert r["level"] in ("AGGRESSIVE", "BALANCED", "DEFENSIVE")


# ── TUNING + DIAGNOSTICS ───────────────────────────────────────────────

class TestTuningShape:
    def test_tuning_keys(self):
        from scalper_health import TUNING
        for level in ("AGGRESSIVE", "BALANCED", "DEFENSIVE"):
            t = TUNING[level]
            for k in ("threshold_delta", "daily_cap", "cooldown_mult",
                      "target_mult", "size_mult", "allow_chop"):
                assert k in t

    def test_aggressive_looser_than_defensive(self):
        from scalper_health import TUNING
        assert (TUNING["AGGRESSIVE"]["threshold_delta"]
                < TUNING["DEFENSIVE"]["threshold_delta"])
        assert TUNING["AGGRESSIVE"]["daily_cap"] > TUNING["DEFENSIVE"]["daily_cap"]
        assert TUNING["AGGRESSIVE"]["allow_chop"] is True
        assert TUNING["DEFENSIVE"]["allow_chop"] is False

    def test_diagnostics_shape(self, monkeypatch):
        monkeypatch.delenv("SCALPER_ADAPTIVE_HEALTH", raising=False)
        from scalper_health import diagnostics
        d = diagnostics()
        assert d["mode"] == "live"  # default flipped 2026-06-18
        assert set(d["modes_available"]) == {"off", "shadow", "live"}
        assert set(d["levels"]) == {"AGGRESSIVE", "BALANCED", "DEFENSIVE"}
