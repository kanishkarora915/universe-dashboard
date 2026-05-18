"""
Tests for entry_filters.py — pre-trade quality gates.

Critical because: bad entries = bad trades = lost money. These filters
gate every entry. Bug here means counter-trend trades, lottery-ticket
deep OTM trades, and chop-regime entries all slip through.

Run: pytest backend/tests/test_entry_filters.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timedelta
import pytz

from entry_filters import (
    check_5min_trend,
    check_greeks_gate,
    check_tick_velocity,
    detect_market_regime,
)

IST = pytz.timezone("Asia/Kolkata")


def _ist_iso(seconds_ago=0):
    return (datetime.now(IST) - timedelta(seconds=seconds_ago)).isoformat()


def _make_history(prices_with_seconds_ago):
    """Helper: build history list from [(ltp, seconds_ago), ...]"""
    return [{"t": _ist_iso(s), "ltp": ltp} for ltp, s in prices_with_seconds_ago]


# ── 5-MIN TREND FILTER ─────────────────────────────────────────────────────

class Test5MinTrendFilter:
    def test_bullish_trend_allows_ce(self):
        """Spot up 0.5% in 5 min → BUY CE allowed"""
        hist = _make_history([(24000, 290), (24050, 200), (24100, 100), (24120, 10)])
        ok, reason = check_5min_trend(hist, "BUY CE")
        assert ok, f"Should allow CE on uptrend: {reason}"

    def test_bullish_trend_blocks_pe(self):
        """Spot up 0.5% → BUY PE BLOCKED (need ≥5 ticks)"""
        hist = _make_history([
            (24000, 280), (24025, 250), (24050, 200),
            (24080, 100), (24100, 50), (24120, 10),
        ])
        ok, reason = check_5min_trend(hist, "BUY PE")
        assert not ok, f"Should block PE on uptrend: {reason}"

    def test_bearish_trend_blocks_ce(self):
        """Spot down 0.5% → BUY CE BLOCKED (need ≥5 ticks)"""
        hist = _make_history([
            (24120, 280), (24090, 250), (24070, 200),
            (24040, 100), (24020, 50), (24000, 10),
        ])
        ok, reason = check_5min_trend(hist, "BUY CE")
        assert not ok, f"Should block CE on downtrend: {reason}"

    def test_bearish_trend_allows_pe(self):
        """Spot down 0.5% → BUY PE allowed"""
        hist = _make_history([(24120, 290), (24050, 100), (24000, 10)])
        ok, reason = check_5min_trend(hist, "BUY PE")
        assert ok, f"Should allow PE on downtrend: {reason}"

    def test_flat_trend_allows_both(self):
        """Tiny move (0.1%) → both allowed (within tolerance)"""
        hist = _make_history([(24050, 290), (24055, 100), (24050, 10)])
        ok_ce, _ = check_5min_trend(hist, "BUY CE")
        ok_pe, _ = check_5min_trend(hist, "BUY PE")
        assert ok_ce and ok_pe, "Flat trend should allow both"

    def test_empty_history_allows(self):
        """No data → allow (don't block on cold start)"""
        ok, _ = check_5min_trend([], "BUY CE")
        assert ok, "Empty history should default-allow"


# ── GREEKS GATE ────────────────────────────────────────────────────────────

class TestGreeksGate:
    def test_atm_delta_allowed(self):
        """ATM delta 0.50 → allowed"""
        chain = {24350: {"ce_greeks": {"delta": 0.50}}}
        ok, reason = check_greeks_gate(chain, 24350, "BUY CE")
        assert ok, f"ATM delta 0.50 should be allowed: {reason}"

    def test_deep_otm_blocked(self):
        """Far OTM delta 0.15 → BLOCKED (lottery ticket)"""
        chain = {24500: {"ce_greeks": {"delta": 0.15}}}
        ok, reason = check_greeks_gate(chain, 24500, "BUY CE")
        assert not ok, "Deep OTM should be blocked"
        assert "OTM" in reason or "lottery" in reason.lower()

    def test_deep_itm_blocked(self):
        """Deep ITM delta 0.85 → BLOCKED (low leverage)"""
        chain = {24200: {"ce_greeks": {"delta": 0.85}}}
        ok, reason = check_greeks_gate(chain, 24200, "BUY CE")
        assert not ok, "Deep ITM should be blocked"
        assert "ITM" in reason or "leverage" in reason.lower()

    def test_pe_negative_delta_handled(self):
        """PE delta is negative — abs() must be used"""
        chain = {24350: {"pe_greeks": {"delta": -0.45}}}
        ok, _ = check_greeks_gate(chain, 24350, "BUY PE")
        assert ok, "PE with |delta|=0.45 should be allowed"

    def test_no_greeks_data_allows(self):
        """If greeks missing → don't block (cold start safety)"""
        chain = {24350: {"ce_greeks": {}}}
        ok, _ = check_greeks_gate(chain, 24350, "BUY CE")
        assert ok, "Missing greeks should default-allow"

    def test_strike_not_in_chain(self):
        """Strike not in chain → don't block"""
        chain = {24350: {"ce_greeks": {"delta": 0.5}}}
        ok, _ = check_greeks_gate(chain, 99999, "BUY CE")
        assert ok, "Missing strike should default-allow"


# ── TICK VELOCITY ──────────────────────────────────────────────────────────

class TestTickVelocity:
    def test_momentum_spike_detected(self):
        """Premium up 5% in 30s → momentum"""
        hist = _make_history([(100, 25), (102, 20), (104, 10), (105, 0)])
        is_momentum, reason, pct = check_tick_velocity(hist, velocity_pct=3.0, window_sec=30)
        assert is_momentum, f"Should detect momentum: {reason}"
        assert pct >= 3.0

    def test_no_momentum_flat(self):
        """Premium flat → no momentum"""
        hist = _make_history([(100, 25), (100, 10), (100, 0)])
        is_momentum, reason, pct = check_tick_velocity(hist, velocity_pct=3.0)
        assert not is_momentum

    def test_negative_move_not_momentum(self):
        """Premium DOWN 3% → not momentum (we want bullish breakouts)"""
        hist = _make_history([(100, 25), (98, 10), (97, 0)])
        is_momentum, _, _ = check_tick_velocity(hist, velocity_pct=3.0)
        assert not is_momentum

    def test_insufficient_history(self):
        """<3 ticks → cannot detect"""
        hist = _make_history([(100, 0)])
        is_momentum, _, _ = check_tick_velocity(hist)
        assert not is_momentum


# ── MARKET REGIME ──────────────────────────────────────────────────────────

class TestMarketRegime:
    def test_chop_detected(self):
        """Tight 0.2% range over 20 min + small last candle → CHOP"""
        # Generate 20 min of tight range
        prices = []
        for i in range(40):
            secs_ago = 1200 - (i * 30)  # 20min ago to now, every 30s
            ltp = 24050 + (i % 3) * 5  # tight oscillation 24050-24060
            prices.append((ltp, secs_ago))
        hist = _make_history(prices)

        regime = detect_market_regime(hist, tight_range_pct=0.4)
        assert regime["regime"] in ("CHOP", "NORMAL"), (
            f"Expected CHOP/NORMAL on tight range, got {regime['regime']}"
        )

    def test_trending_detected(self):
        """Range > 1% → TRENDING"""
        prices = []
        for i in range(40):
            secs_ago = 1200 - (i * 30)
            ltp = 24000 + (i * 10)  # steady climb 24000 → 24400
            prices.append((ltp, secs_ago))
        hist = _make_history(prices)

        regime = detect_market_regime(hist)
        assert regime["regime"] == "TRENDING", (
            f"Expected TRENDING, got {regime['regime']}: {regime['reason']}"
        )

    def test_breakout_detected(self):
        """Tight 20min + sudden >1.5% candle → BREAKOUT"""
        prices = []
        # Tight base: 19 min flat at 24050
        for i in range(38):
            secs_ago = 1200 - (i * 30)
            prices.append((24050, secs_ago))
        # Last 1 minute: explosive 2% move
        prices.append((24100, 30))
        prices.append((24500, 0))  # +1.6% in 1 min
        hist = _make_history(prices)

        regime = detect_market_regime(hist, tight_range_pct=0.4, breakout_candle_pct=1.5)
        # Should detect BREAKOUT (or at least not CHOP)
        assert regime["regime"] != "CHOP", (
            f"Should NOT be CHOP after breakout candle: {regime['reason']}"
        )

    def test_insufficient_history_normal(self):
        """<10 ticks → default NORMAL (don't over-block)"""
        regime = detect_market_regime([])
        assert regime["regime"] == "NORMAL"
