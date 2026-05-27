"""
Tests for trend_day_detector (ORB) — Phase 3.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
import pytz

import pytest

from trend_day_detector import (
    detect_trend_day,
    compute_opening_range,
    diagnostics,
)

IST = pytz.timezone("Asia/Kolkata")


def _candle(dt, high, low, open_=None, close=None, volume=1000):
    if open_ is None:
        open_ = (high + low) / 2
    if close is None:
        close = (high + low) / 2
    return {
        "ts": dt.isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _today_5m_candles(highs_lows, today_date=None):
    """Build 5-min candles starting from 9:15 today."""
    if today_date is None:
        today_date = datetime.now(IST).date()
    start = IST.localize(datetime.combine(today_date, datetime.min.time())).replace(hour=9, minute=15)
    candles = []
    for i, (h, l) in enumerate(highs_lows):
        ts = start + timedelta(minutes=5 * i)
        candles.append(_candle(ts, h, l))
    return candles


class TestOpeningRange:
    def test_or_basic(self):
        # 6 candles in first 30 min (9:15-9:45) — OR window
        candles = _today_5m_candles([
            (24050, 24020),
            (24055, 24030),
            (24060, 24040),
            (24070, 24025),
            (24065, 24028),
            (24062, 24030),
        ])
        now = candles[-1]["ts"]
        now_dt = datetime.fromisoformat(now).astimezone(IST) + timedelta(minutes=10)
        or_info = compute_opening_range(candles, now=now_dt, or_minutes=30)
        assert or_info is not None
        assert or_info["high"] == 24070
        assert or_info["low"] == 24020
        assert or_info["size"] == 50
        assert or_info["complete"] is True

    def test_or_incomplete_when_window_not_passed(self):
        candles = _today_5m_candles([
            (24050, 24020),
            (24055, 24030),
        ])
        # 'now' is during OR window — not complete
        now_dt = datetime.fromisoformat(candles[-1]["ts"]).astimezone(IST)
        or_info = compute_opening_range(candles, now=now_dt, or_minutes=30)
        assert or_info["complete"] is False

    def test_or_no_today_candles(self):
        # Candles from yesterday
        yesterday = datetime.now(IST).date() - timedelta(days=1)
        candles = _today_5m_candles([(24050, 24020)], today_date=yesterday)
        or_info = compute_opening_range(candles, now=datetime.now(IST), or_minutes=30)
        assert or_info is None


class TestTrendDay:
    def _orb_candles(self):
        """6 candles 9:15-9:45 setting OR high=24070 low=24020."""
        return _today_5m_candles([
            (24050, 24020),
            (24055, 24030),
            (24060, 24040),
            (24070, 24025),
            (24065, 24028),
            (24062, 24030),
        ])

    def _now_post_or(self):
        # 10:00 today — past OR window
        today = datetime.now(IST).date()
        return IST.localize(datetime.combine(today, datetime.min.time())).replace(hour=10, minute=0)

    def test_bull_break(self):
        candles = self._orb_candles()
        # OR_high=24070, size=50, threshold=50*0.5=25 → break above 24095
        result = detect_trend_day(candles, current_spot=24100, now=self._now_post_or())
        assert result["is_trend_day"] is True
        assert result["direction"] == "BULL"

    def test_bear_break(self):
        candles = self._orb_candles()
        # OR_low=24020, size=50, threshold=25 → break below 23995
        result = detect_trend_day(candles, current_spot=23990, now=self._now_post_or())
        assert result["is_trend_day"] is True
        assert result["direction"] == "BEAR"

    def test_no_break_inside_OR(self):
        candles = self._orb_candles()
        result = detect_trend_day(candles, current_spot=24050, now=self._now_post_or())
        assert result["is_trend_day"] is False
        assert result["direction"] is None

    def test_or_incomplete_returns_low_conf(self):
        candles = _today_5m_candles([(24050, 24020), (24055, 24030)])
        # Set 'now' as still in OR window
        now_dt = datetime.fromisoformat(candles[-1]["ts"]).astimezone(IST)
        result = detect_trend_day(candles, current_spot=24100, now=now_dt)
        # OR not complete yet
        assert result["is_trend_day"] is False

    def test_invalid_spot(self):
        candles = self._orb_candles()
        result = detect_trend_day(candles, current_spot=0, now=self._now_post_or())
        assert result["is_trend_day"] is False


class TestDiagnostics:
    def test_diagnostics_shape(self):
        d = diagnostics()
        assert d["module"] == "trend_day_detector"
        assert "or_minutes" in d
        assert "break_pct" in d
