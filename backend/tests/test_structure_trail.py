"""
Tests for structure_trail — Phase 3.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
import pytz

import pytest

from structure_trail import (
    should_exit_on_break,
    compute_trail_level,
    diagnostics,
)

IST = pytz.timezone("Asia/Kolkata")


def _candle(dt, high, low, open_=None, close=None):
    if open_ is None:
        open_ = (high + low) / 2
    if close is None:
        close = (high + low) / 2
    return {
        "ts": dt.isoformat() if isinstance(dt, datetime) else dt,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
    }


def _seq_candles(start_dt, highs_lows):
    out = []
    for i, (h, l) in enumerate(highs_lows):
        ts = start_dt + timedelta(minutes=5 * i)
        out.append(_candle(ts, h, l))
    return out


class TestBullBreak:
    def test_bull_break_on_LL_after_entry(self):
        """Pre-entry uptrend with HL=24000; post-entry forms LL at 23950."""
        start = IST.localize(datetime(2026, 5, 27, 9, 15))

        # Pre-entry: 7 candles forming swing low at index 2 (price=24000)
        pre = _seq_candles(start, [
            (24050, 24020),
            (24055, 24010),
            (24060, 24000),    # swing low @ price 24000
            (24070, 24015),
            (24080, 24020),
            (24075, 24025),
            (24072, 24030),
        ])
        entry_ts = start + timedelta(minutes=30)  # entry at end of pre

        # Post-entry: forms a new swing low at price 23950 (LL)
        post_start = start + timedelta(minutes=35)
        post = _seq_candles(post_start, [
            (24070, 24010),
            (24060, 23990),
            (24050, 23950),    # new LL @ 23950
            (24040, 23970),
            (24030, 23980),
        ])
        all_candles = pre + post

        result = should_exit_on_break(
            candles_5m=all_candles, trade_direction="BULL",
            entry_spot=24050, entry_ts=entry_ts,
        )
        assert result["should_exit"] is True
        assert "UPTREND_BROKEN" in result["reason"]
        assert result["broken_at"] == 23950

    def test_no_break_when_HL_intact(self):
        """Pre-entry uptrend; post-entry stays above pre-entry low."""
        start = IST.localize(datetime(2026, 5, 27, 9, 15))
        pre = _seq_candles(start, [
            (24050, 24020),
            (24055, 24010),
            (24060, 24000),
            (24070, 24015),
            (24080, 24020),
            (24075, 24025),
            (24072, 24030),
        ])
        entry_ts = start + timedelta(minutes=30)
        post_start = start + timedelta(minutes=35)
        # Post-entry stays well above 24000
        post = _seq_candles(post_start, [
            (24090, 24030),
            (24095, 24050),    # higher low
            (24100, 24070),
            (24110, 24080),
            (24105, 24085),
        ])
        result = should_exit_on_break(
            candles_5m=pre + post, trade_direction="BULL",
            entry_spot=24070, entry_ts=entry_ts,
        )
        assert result["should_exit"] is False


class TestBearBreak:
    def test_bear_break_on_HH_after_entry(self):
        """Pre-entry downtrend; post-entry forms HH → exit BUY PE."""
        start = IST.localize(datetime(2026, 5, 27, 9, 15))
        pre = _seq_candles(start, [
            (24070, 24030),
            (24065, 24025),
            (24080, 24050),    # swing high @ 24080
            (24060, 24020),
            (24050, 24010),
            (24055, 24015),
            (24052, 24018),
        ])
        entry_ts = start + timedelta(minutes=30)
        post_start = start + timedelta(minutes=35)
        post = _seq_candles(post_start, [
            (24070, 24020),
            (24090, 24040),
            (24110, 24050),    # new HH @ 24110 > 24080
            (24100, 24060),
            (24095, 24070),
        ])
        result = should_exit_on_break(
            candles_5m=pre + post, trade_direction="BEAR",
            entry_spot=24050, entry_ts=entry_ts,
        )
        assert result["should_exit"] is True
        assert "DOWNTREND_BROKEN" in result["reason"]
        assert result["broken_at"] == 24110


class TestEdgeCases:
    def test_no_pre_entry_swing(self):
        """If pre-entry has no swing, can't check break — should not exit."""
        start = IST.localize(datetime(2026, 5, 27, 9, 15))
        candles = _seq_candles(start, [(24050, 24020), (24055, 24025)])
        result = should_exit_on_break(
            candles_5m=candles, trade_direction="BULL",
            entry_spot=24050,
            entry_ts=start + timedelta(minutes=15),
        )
        assert result["should_exit"] is False
        assert "no pre-entry swing" in result["reason"]

    def test_unknown_direction(self):
        start = IST.localize(datetime(2026, 5, 27, 9, 15))
        candles = _seq_candles(start, [(24050, 24020)] * 10)
        result = should_exit_on_break(
            candles_5m=candles, trade_direction="SIDEWAYS",
            entry_spot=24050, entry_ts=start,
        )
        assert result["should_exit"] is False
        assert "unknown direction" in result["reason"]


class TestTrailLevel:
    def test_trail_level_bull(self):
        start = IST.localize(datetime(2026, 5, 27, 9, 15))
        candles = _seq_candles(start, [
            (24050, 24020),
            (24055, 24010),
            (24060, 24000),    # swing low @ 24000
            (24070, 24015),
            (24080, 24025),
            (24075, 24028),
            (24072, 24030),
        ])
        level = compute_trail_level(candles, "BULL")
        assert level == 24000

    def test_trail_level_bear(self):
        start = IST.localize(datetime(2026, 5, 27, 9, 15))
        candles = _seq_candles(start, [
            (24070, 24030),
            (24065, 24025),
            (24080, 24050),    # swing high @ 24080
            (24060, 24020),
            (24050, 24010),
            (24055, 24015),
            (24052, 24018),
        ])
        level = compute_trail_level(candles, "BEAR")
        assert level == 24080

    def test_trail_level_no_swings(self):
        level = compute_trail_level([], "BULL")
        assert level is None


class TestDiagnostics:
    def test_diagnostics_shape(self):
        d = diagnostics()
        assert d["module"] == "structure_trail"
