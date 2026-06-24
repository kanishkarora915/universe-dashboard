"""
Tests for day_classifier (Task #88, 2026-06-23).

Data-derived from 60d NIFTY OHLC CSV cross-referenced with 320 main trades.
"""
from __future__ import annotations
from datetime import datetime, timedelta
import pytz

import pytest

IST = pytz.timezone("Asia/Kolkata")


class _FakeEngine:
    """Minimal engine surface for testing day_classifier."""

    def __init__(self, idx="NIFTY", day_open=24000, day_high=24050,
                 day_low=23950, current=24010, history=None):
        self.spot_tokens = {idx: 256265}
        self.prices = {256265: {"ltp": current}}
        self.day_high = {idx: day_high}
        self.day_low = {idx: day_low}
        now = datetime.now(IST)
        if history is None:
            # Default: build linear history from day_open to current over last 60 ticks
            history = []
            step = (current - day_open) / 60.0
            for i in range(60):
                history.append({
                    "t": (now - timedelta(minutes=60 - i)).isoformat(),
                    "ltp": day_open + step * i,
                })
        self._spot_history = {idx: history}


# ── Gate 1: DEAD_MARKET_HALT ──────────────────────────────────────────


class TestDeadMarketHalt:
    def test_dead_market_blocks(self, monkeypatch):
        """30min range < 0.2% should block."""
        # Force time-of-day past 10:00
        monkeypatch.setattr("day_classifier._now_ist",
                            lambda: IST.localize(datetime(2026, 6, 23, 11, 0)))
        # All ticks near 24000 (tiny range)
        now = IST.localize(datetime(2026, 6, 23, 11, 0))
        hist = [{"t": (now - timedelta(minutes=i)).isoformat(), "ltp": 24000 + (i % 3)}
                for i in range(30, 0, -1)]
        eng = _FakeEngine(current=24001, history=hist, day_low=23999, day_high=24003)
        from day_classifier import check_dead_market_halt
        block, reason = check_dead_market_halt(eng, "NIFTY")
        assert block is True
        assert "DEAD_MARKET_HALT" in reason

    def test_normal_range_allows(self, monkeypatch):
        """30min range >0.2% should allow."""
        monkeypatch.setattr("day_classifier._now_ist",
                            lambda: IST.localize(datetime(2026, 6, 23, 11, 0)))
        now = IST.localize(datetime(2026, 6, 23, 11, 0))
        hist = []
        for i in range(30, 0, -1):
            ltp = 24000 + i * 5  # 150-point range
            hist.append({"t": (now - timedelta(minutes=i)).isoformat(), "ltp": ltp})
        eng = _FakeEngine(current=24050, history=hist, day_low=23950, day_high=24150)
        from day_classifier import check_dead_market_halt
        block, _ = check_dead_market_halt(eng, "NIFTY")
        assert block is False

    def test_before_10am_disabled(self, monkeypatch):
        """Opening volatility — gate inactive before 10:00 IST."""
        monkeypatch.setattr("day_classifier._now_ist",
                            lambda: IST.localize(datetime(2026, 6, 23, 9, 30)))
        eng = _FakeEngine(current=24001, day_low=24000, day_high=24002)
        from day_classifier import check_dead_market_halt
        block, _ = check_dead_market_halt(eng, "NIFTY")
        assert block is False

    def test_env_disable(self, monkeypatch):
        monkeypatch.setenv("DEAD_MARKET_HALT_DISABLED", "on")
        monkeypatch.setattr("day_classifier._now_ist",
                            lambda: IST.localize(datetime(2026, 6, 23, 11, 0)))
        eng = _FakeEngine(current=24001, day_low=24000, day_high=24002)
        from day_classifier import check_dead_market_halt
        block, _ = check_dead_market_halt(eng, "NIFTY")
        assert block is False


# ── Gate 2: STRONG_TREND_FADE ──────────────────────────────────────────


class TestStrongTrendFade:
    def test_blocks_CE_on_strong_down(self, monkeypatch):
        """Day open 24100, low 23900, current 23910 → strong down → block CE."""
        eng = _FakeEngine(day_open=24100, day_high=24110, day_low=23900, current=23910)
        from day_classifier import check_strong_trend_fade
        block, reason = check_strong_trend_fade(eng, "NIFTY", "BUY CE")
        assert block is True
        assert "STRONG_DOWN" in reason

    def test_blocks_PE_on_strong_up(self, monkeypatch):
        eng = _FakeEngine(day_open=23900, day_high=24100, day_low=23895, current=24090)
        from day_classifier import check_strong_trend_fade
        block, reason = check_strong_trend_fade(eng, "NIFTY", "BUY PE")
        assert block is True
        assert "STRONG_UP" in reason

    def test_allows_aligned_CE_on_strong_up(self, monkeypatch):
        """CE on strong UP day = aligned, should allow."""
        eng = _FakeEngine(day_open=23900, day_high=24100, day_low=23895, current=24090)
        from day_classifier import check_strong_trend_fade
        block, _ = check_strong_trend_fade(eng, "NIFTY", "BUY CE")
        assert block is False

    def test_allows_normal_day(self, monkeypatch):
        """Tiny body % → not strong trend → allow either side."""
        eng = _FakeEngine(day_open=24000, day_high=24050, day_low=23950, current=24005)
        from day_classifier import check_strong_trend_fade
        block, _ = check_strong_trend_fade(eng, "NIFTY", "BUY CE")
        assert block is False
        block2, _ = check_strong_trend_fade(eng, "NIFTY", "BUY PE")
        assert block2 is False


# ── Gate 3: DOWN_DAY_CE_PENALTY ────────────────────────────────────────


class TestDownDayCEPenalty:
    def test_ce_on_down_day_gets_bump(self, monkeypatch):
        """Spot 0.3% below open + CE → +10 threshold."""
        eng = _FakeEngine(day_open=24000, current=23928)  # -0.30% from open
        from day_classifier import down_day_ce_threshold_bump
        bump = down_day_ce_threshold_bump(eng, "NIFTY", "BUY CE")
        assert bump == 10

    def test_pe_on_down_day_no_bump(self, monkeypatch):
        """PE on down day = aligned, no penalty."""
        eng = _FakeEngine(day_open=24000, current=23928)
        from day_classifier import down_day_ce_threshold_bump
        bump = down_day_ce_threshold_bump(eng, "NIFTY", "BUY PE")
        assert bump == 0

    def test_ce_on_up_day_no_bump(self, monkeypatch):
        """CE on up day = aligned, no penalty."""
        eng = _FakeEngine(day_open=24000, current=24050)
        from day_classifier import down_day_ce_threshold_bump
        bump = down_day_ce_threshold_bump(eng, "NIFTY", "BUY CE")
        assert bump == 0

    def test_env_disable(self, monkeypatch):
        monkeypatch.setenv("DOWN_DAY_CE_PENALTY_DISABLED", "on")
        eng = _FakeEngine(day_open=24000, current=23928)
        from day_classifier import down_day_ce_threshold_bump
        bump = down_day_ce_threshold_bump(eng, "NIFTY", "BUY CE")
        assert bump == 0


# ── Diagnostics shape ──────────────────────────────────────────────────


class TestDiagnostics:
    def test_diagnostics_shape(self):
        from day_classifier import diagnostics
        d = diagnostics(engine=None)
        assert "dead_market_halt" in d
        assert "strong_trend_fade" in d
        assert "down_day_ce_penalty" in d
        assert "thresholds" in d
        assert d["thresholds"]["dead_market_range_pct"] == 0.20
        assert d["thresholds"]["down_day_threshold_bump"] == 10

    def test_diagnostics_with_engine(self):
        from day_classifier import diagnostics
        eng = _FakeEngine(current=24050, day_open=24000)
        d = diagnostics(engine=eng)
        assert "NIFTY" in d["indices"]
        assert d["indices"]["NIFTY"]["day_stats"]["current"] == 24050
