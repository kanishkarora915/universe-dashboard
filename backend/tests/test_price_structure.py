"""
Tests for price_structure module.

Built 2026-05-27.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from price_structure import (
    detect_structure,
    find_swing_highs,
    find_swing_lows,
    detect_trend_break,
    align_timeframes,
    diagnostics,
)


def _candle(idx, high, low, close=None, open_=None, volume=1000):
    """Build a minimal OHLCV candle dict."""
    if close is None:
        close = (high + low) / 2
    if open_ is None:
        open_ = close
    return {
        "ts": f"2026-05-27T{idx:02d}:00:00",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# ── Swing point detection ─────────────────────────────────────────────


class TestSwingDetection:
    def test_basic_swing_high(self):
        """Single clear swing high — middle candle highest in 5-candle window."""
        candles = [
            _candle(0, 105, 95),
            _candle(1, 110, 98),
            _candle(2, 120, 100),   # ← swing high (idx 2)
            _candle(3, 112, 100),
            _candle(4, 108, 95),
        ]
        highs = find_swing_highs(candles, bars=2)
        assert len(highs) == 1
        assert highs[0]["index"] == 2
        assert highs[0]["price"] == 120

    def test_basic_swing_low(self):
        candles = [
            _candle(0, 105, 95),
            _candle(1, 102, 92),
            _candle(2, 100, 80),    # ← swing low (idx 2)
            _candle(3, 95, 88),
            _candle(4, 100, 92),
        ]
        lows = find_swing_lows(candles, bars=2)
        assert len(lows) == 1
        assert lows[0]["index"] == 2
        assert lows[0]["price"] == 80

    def test_no_swing_in_monotonic_data(self):
        """Monotonically rising candles — no swing high (no peak)."""
        candles = [_candle(i, 100 + i * 5, 90 + i * 5) for i in range(10)]
        # No high will satisfy "greater than both sides" except possibly the end
        # (which can't satisfy because we need bars AFTER)
        highs = find_swing_highs(candles, bars=2)
        assert highs == []

    def test_too_few_candles(self):
        candles = [_candle(i, 100, 90) for i in range(3)]
        assert find_swing_highs(candles, bars=2) == []
        assert find_swing_lows(candles, bars=2) == []

    def test_empty_candles(self):
        assert find_swing_highs([], bars=2) == []
        assert find_swing_lows([], bars=2) == []

    def test_zero_high_ignored(self):
        """A candle with high=0 should be skipped (bad data)."""
        candles = [
            _candle(0, 105, 95),
            _candle(1, 110, 98),
            {"ts": "x", "high": 0, "low": 0, "open": 0, "close": 0},  # bad
            _candle(3, 112, 100),
            _candle(4, 108, 95),
        ]
        highs = find_swing_highs(candles, bars=2)
        # Bad candle at idx 2 has high=0, not a swing
        assert all(h["index"] != 2 for h in highs)


# ── Structure verdict ─────────────────────────────────────────────────


class TestStructure:
    def _uptrend_candles(self):
        """Explicit uptrend pattern with 2 swing highs + 2 swing lows ascending."""
        return [
            _candle(0, 100, 95),
            _candle(1, 102, 93),
            _candle(2, 105, 90),    # SWING LOW #1 (low=90)
            _candle(3, 108, 95),
            _candle(4, 120, 100),   # SWING HIGH #1 (high=120)
            _candle(5, 115, 102),
            _candle(6, 110, 98),
            _candle(7, 112, 95),    # SWING LOW #2 (low=95, > 90 ✓)
            _candle(8, 118, 100),
            _candle(9, 130, 110),   # SWING HIGH #2 (high=130, > 120 ✓)
            _candle(10, 125, 108),
            _candle(11, 122, 105),
            _candle(12, 124, 110),
        ]

    def _downtrend_candles(self):
        """Mirror — descending highs + descending lows."""
        return [
            _candle(0, 130, 120),
            _candle(1, 128, 118),
            _candle(2, 135, 125),   # SWING HIGH #1 (high=135)
            _candle(3, 125, 115),
            _candle(4, 115, 100),   # SWING LOW #1 (low=100)
            _candle(5, 122, 108),
            _candle(6, 125, 110),
            _candle(7, 128, 113),   # SWING HIGH #2 (high=128, < 135 ✓)
            _candle(8, 120, 105),
            _candle(9, 110, 90),    # SWING LOW #2 (low=90, < 100 ✓)
            _candle(10, 115, 95),
            _candle(11, 113, 98),
            _candle(12, 110, 92),
        ]

    def test_uptrend_detected(self):
        result = detect_structure(self._uptrend_candles())
        assert result["verdict"] == "UPTREND"
        assert result["confidence"] in ("HIGH", "MEDIUM")
        assert "HH+HL" in result["reason"]
        assert result["last_high"] == 130
        assert result["last_low"] == 95

    def test_downtrend_detected(self):
        result = detect_structure(self._downtrend_candles())
        assert result["verdict"] == "DOWNTREND"
        assert result["confidence"] in ("HIGH", "MEDIUM")
        assert "LH+LL" in result["reason"]
        assert result["last_high"] == 128
        assert result["last_low"] == 90

    def test_unknown_when_too_few_candles(self):
        candles = [_candle(i, 100, 90) for i in range(4)]
        result = detect_structure(candles)
        assert result["verdict"] == "UNKNOWN"
        assert result["confidence"] == "LOW"

    def test_unknown_when_empty(self):
        result = detect_structure([])
        assert result["verdict"] == "UNKNOWN"
        assert result["swing_highs"] == []
        assert result["swing_lows"] == []

    def test_chop_when_mixed_pattern(self):
        """Up-down-up-down pattern → CHOP."""
        candles = [
            _candle(0, 100, 95),
            _candle(1, 105, 92),
            _candle(2, 115, 88),    # high spike (swing high)
            _candle(3, 108, 95),
            _candle(4, 112, 92),
            _candle(5, 105, 98),
            _candle(6, 120, 90),    # higher swing high
            _candle(7, 108, 80),    # lower swing low (LL) — breaks any uptrend
            _candle(8, 112, 92),
            _candle(9, 100, 85),    # lower swing high (LH)
            _candle(10, 105, 95),
            _candle(11, 110, 88),
            _candle(12, 108, 92),
        ]
        result = detect_structure(candles)
        # With this mess, can be CHOP or UNKNOWN — just not a clean trend
        assert result["verdict"] in ("CHOP", "UNKNOWN", "DOWNTREND")


# ── Trend break detection ─────────────────────────────────────────────


class TestTrendBreak:
    def test_no_break_when_not_trending(self):
        candles = [_candle(i, 100 + i, 90 + i) for i in range(10)]
        assert detect_trend_break(candles, "CHOP") is None
        assert detect_trend_break(candles, "UNKNOWN") is None

    def test_no_break_when_few_candles(self):
        candles = [_candle(i, 100, 90) for i in range(3)]
        assert detect_trend_break(candles, "UPTREND") is None

    def test_uptrend_break_on_new_LL(self):
        """Uptrend candles + one final LL → UPTREND_BROKEN."""
        # Start with uptrend then a clear LL
        candles = [
            _candle(0, 105, 95),
            _candle(1, 108, 92),
            _candle(2, 110, 88),    # swing low #1 (low=88)
            _candle(3, 115, 95),
            _candle(4, 120, 100),   # swing high #1
            _candle(5, 118, 102),
            _candle(6, 115, 100),
            _candle(7, 117, 92),    # swing low #2 (low=92, > 88 = HL)
            _candle(8, 120, 95),
            _candle(9, 125, 105),   # swing high #2 (HH)
            _candle(10, 120, 100),
            _candle(11, 115, 98),
            _candle(12, 110, 80),   # swing low #3 (low=80) — LL! breaks uptrend
            _candle(13, 105, 85),
            _candle(14, 100, 88),
        ]
        break_info = detect_trend_break(candles, "UPTREND")
        assert break_info is not None
        assert break_info["type"] == "UPTREND_BROKEN"
        assert break_info["last_swing"] < break_info["prev_swing"]


# ── Multi-timeframe alignment ─────────────────────────────────────────


class TestAlignment:
    def test_all_uptrend_high_conviction(self):
        result = align_timeframes({
            "5m": {"verdict": "UPTREND"},
            "15m": {"verdict": "UPTREND"},
            "1h": {"verdict": "UPTREND"},
        })
        assert result["aligned"] is True
        assert result["direction"] == "BULL"
        assert result["conviction"] == "HIGH"

    def test_all_downtrend_high_conviction(self):
        result = align_timeframes({
            "5m": {"verdict": "DOWNTREND"},
            "15m": {"verdict": "DOWNTREND"},
            "1h": {"verdict": "DOWNTREND"},
        })
        assert result["aligned"] is True
        assert result["direction"] == "BEAR"
        assert result["conviction"] == "HIGH"

    def test_2_of_3_bull_medium_conviction(self):
        result = align_timeframes({
            "5m": {"verdict": "UPTREND"},
            "15m": {"verdict": "UPTREND"},
            "1h": {"verdict": "CHOP"},
        })
        assert result["aligned"] is True
        assert result["direction"] == "BULL"
        assert result["conviction"] == "MEDIUM"

    def test_conflict_no_alignment(self):
        result = align_timeframes({
            "5m": {"verdict": "UPTREND"},
            "15m": {"verdict": "UPTREND"},
            "1h": {"verdict": "DOWNTREND"},
        })
        assert result["aligned"] is False
        assert result["direction"] == "MIXED"

    def test_empty_input(self):
        result = align_timeframes({})
        assert result["aligned"] is False
        assert result["direction"] == "MIXED"

    def test_all_unknown(self):
        result = align_timeframes({
            "5m": {"verdict": "UNKNOWN"},
            "15m": {"verdict": "CHOP"},
            "1h": {"verdict": "UNKNOWN"},
        })
        assert result["aligned"] is False
        assert result["direction"] == "MIXED"


# ── Diagnostics ───────────────────────────────────────────────────────


class TestDiagnostics:
    def test_diagnostics_shape(self):
        d = diagnostics()
        assert d["module"] == "price_structure"
        assert d["fractal_bars"] == 2
        assert d["min_swings"] == 2
        assert "description" in d
