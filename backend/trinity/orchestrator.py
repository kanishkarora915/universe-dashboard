"""
Trinity Orchestrator — runs every second, ties together:
  spot/future/synthetic → composite → deviation → regime → signal → persist.

Called by engine's tick loop (1Hz). All state in trinity_state singleton.

Edge cases (spec §10):
  - First 5 min: collect data, no signals
  - News spike (spot >0.3% in 10s): pause signals 60s
  - Lunch hour: cap confidence at 75%
  - Stale tick (>5s): mark DEGRADED
  - Expiry day: 3-min EMA (auto-switch)
"""

import time
from . import storage
from . import websocket_manager as wsm
from . import tick_processor as tp
from . import synthetic_calculator as sc
from . import regime_classifier as rc
from . import trap_detector as td
from . import strike_recommender as sr


# Default risk capital — overridable via /api/trinity/config
RISK_CAPITAL = 1_000_000

# News spike pause state
_news_spike_until = 0
_last_signal_ts = 0
SIGNAL_COOLDOWN_SEC = 30   # don't emit same signal within 30s

NIFTY_NAME = "NIFTY"


def get_spot_ltp(engine):
    tok = engine.spot_tokens.get(NIFTY_NAME)
    if not tok:
        return None
    return engine.prices.get(tok, {}).get("ltp", 0) or None


def get_future_ltp(engine, fut_token):
    if not fut_token:
        return None
    return engine.prices.get(fut_token, {}).get("ltp", 0) or None


def ensure_fut_token(engine, state):
    """Lazy-resolve NIFTY-FUT token + add to engine WS subscription."""
    if state.fut_token:
        return state.fut_token
    nfo = getattr(engine, "nfo_instruments", []) or []
    if not nfo:
        return None
    tok, meta = wsm.find_nifty_fut_token(nfo)
    if tok:
        state.fut_token = tok
        state.fut_meta = meta
        wsm.add_fut_to_engine_subscription(engine, tok)
        print(f"[TRINITY] NIFTY-FUT resolved: {meta}")
    return tok


def step(engine):
    """One Trinity tick — call this every 1 sec from engine loop."""
    global _news_spike_until, _last_signal_ts

    state = tp.get_state()

    # Resolve future token if first run
    fut_token = ensure_fut_token(engine, state)

    # Get spot
    spot = get_spot_ltp(engine)
    if not spot:
        return None

    # Get future
    future = get_future_ltp(engine, fut_token)
    if future is None:
        future = spot  # degrade gracefully — premium will be 0
        state.degraded = True
    else:
        state.degraded = False

    # ATM
    atm = wsm.compute_atm_strike(spot, strike_gap=50)

    # Per-strike synthetics
    per_strike = sc.compute_per_strike_synthetics(engine, atm, strike_gap=50, min_volume=100)
    composite_synthetic, used = sc.compute_composite_synthetic(per_strike)
    if composite_synthetic is None:
        # Not enough valid strikes
        composite_synthetic = spot

    # Premium + EMA
    premium = sc.compute_future_premium(future, spot)
    if premium is None:
        premium = 0.0
    ema = state.premium_ema_3min if tp.is_expiry_day() else state.premium_ema_5min
    baseline = ema.update(premium)
    premium_delta = premium - baseline

    # Trinity deviation
    trinity_deviation = sc.compute_trinity_deviation(composite_synthetic, spot)

    # Build snapshot
    snapshot = tp.aggregate_1sec_bar(
        spot=round(spot, 2),
        future=round(future, 2),
        synthetic=round(composite_synthetic, 2),
        deviation=round(trinity_deviation, 2) if trinity_deviation is not None else 0.0,
        premium=round(premium, 2),
    )

    # Push to ring buffer first so velocity calc has it
    state.bar_buffer.push(snapshot)

    # Velocities
    velocities = tp.compute_velocities(state, lookback_secs=1)

    # OI concentration (used in trap confidence)
    oi_conc = rc.compute_oi_concentration_score(per_strike)

    # News spike check
    if tp.detect_news_spike(state):
        _news_spike_until = time.time() + 60
        # Don't change regime detection, just suppress signals

    # Classify regime
    regime, confidence, reasons = rc.classify_regime(
        state, snapshot, velocities, premium_delta,
        oi_concentration_score=oi_conc,
        expiry_day=tp.is_expiry_day(),
    )

    # Lunch hour cap (spec §10.5)
    if tp.is_lunch_hour():
        confidence = min(confidence, 75.0)

    # First 5 minutes: no signals (spec §10.2)
    if tp.is_first_5min():
        regime = "TRANSITIONING"
        confidence = 0
        reasons = ["First 5 min — synthetic stabilizing, no signals"]

    snapshot["regime"] = regime
    snapshot["confidence"] = confidence

    # Update state regime
    state.transition_regime(regime)

    # Persist tick
    try:
        storage.save_tick(snapshot)
    except Exception as e:
        print(f"[TRINITY] save_tick error: {e}")

    # Trap zones
    trap_zones = td.compute_trap_zones(engine, spot, state, regime)

    # Signal generation
    new_signal = None
    now = time.time()
    signal_paused = now < _news_spike_until
    cooldown_ok = (now - _last_signal_ts) >= SIGNAL_COOLDOWN_SEC

    actionable_regimes = ("REAL_RALLY", "REAL_CRASH", "BULL_TRAP", "BEAR_TRAP")
    if (not signal_paused and cooldown_ok and regime in actionable_regimes
            and confidence >= 65 and not state.degraded and not tp.is_first_5min()):
        # Only emit at regime ENTRY (first 1-2 sec after transition)
        if state.regime_duration_secs() < 5:
            try:
                signal = sr.build_signal(
                    engine, regime, confidence, atm, spot,
                    reasoning=" · ".join(reasons),
                    trap_zones=trap_zones,
                    risk_capital=RISK_CAPITAL,
                )
                if signal and signal.get("signal_type", "").startswith("BUY_"):
                    sid = storage.save_signal({
                        **signal,
                        "trap_zone_upper": signal.get("trap_zone_upper"),
                        "trap_zone_lower": signal.get("trap_zone_lower"),
                    })
                    signal["id"] = sid
                    new_signal = signal
                    _last_signal_ts = now
                    state.last_signal_at = now
                    print(f"[TRINITY] NEW SIGNAL: {signal['signal_type']} {signal.get('strike')} "
                          f"@ ₹{signal.get('premium')} conf={confidence:.1f}%")
            except Exception as e:
                print(f"[TRINITY] signal build error: {e}")

    # Persist per-strike data (sample every 5s to avoid DB bloat)
    if int(now) % 5 == 0:
        try:
            strike_rows = []
            for s, info in per_strike.items():
                if info["valid"]:
                    strike_rows.append({"strike": s, "type": "CE",
                                        "ltp": info["ce_ltp"], "oi": info["ce_oi"],
                                        "volume": info["ce_volume"], "iv": 0})
                    strike_rows.append({"strike": s, "type": "PE",
                                        "ltp": info["pe_ltp"], "oi": info["pe_oi"],
                                        "volume": info["pe_volume"], "iv": 0})
            storage.save_strike_batch(snapshot["ts"], strike_rows)
        except Exception as e:
            print(f"[TRINITY] strike persist error: {e}")

    # Build full result
    return {
        "snapshot": snapshot,
        "atm": atm,
        "premium_baseline": round(baseline, 2),
        "premium_delta": round(premium_delta, 2),
        "velocities": {k: round(v, 4) for k, v in velocities.items()},
        "regime": regime,
        "confidence": confidence,
        "reasons": reasons,
        "per_strike": per_strike,
        "strike_deviations": sc.compute_strike_deviations(per_strike, spot),
        "trap_zones": trap_zones,
        "new_signal": new_signal,
        "degraded": state.degraded,
        "news_spike_active": signal_paused,
        "lunch_hour": tp.is_lunch_hour(),
        "first_5min": tp.is_first_5min(),
        "expiry_day": tp.is_expiry_day(),
        "fut_token": fut_token,
        "fut_meta": state.fut_meta,
    }


def get_status(engine):
    state = tp.get_state()
    fut_token = state.fut_token or ensure_fut_token(engine, state)
    spot = get_spot_ltp(engine) or 0
    atm = wsm.compute_atm_strike(spot, 50) if spot else 0
    sub_status = wsm.get_subscription_status(engine, fut_token, atm)
    return {
        "running": engine is not None,
        "spot": spot,
        "atm": atm,
        "fut_token": fut_token,
        "fut_meta": state.fut_meta,
        "subscription": sub_status,
        "buffer_size": len(state.bar_buffer),
        "current_regime": state.current_regime,
        "regime_duration_secs": round(state.regime_duration_secs(), 1),
        "degraded": state.degraded,
        "lunch_hour": tp.is_lunch_hour(),
        "first_5min": tp.is_first_5min(),
        "expiry_day": tp.is_expiry_day(),
        "ts": int(time.time() * 1000),
    }


def get_snapshot():
    state = tp.get_state()
    latest = state.bar_buffer.latest()
    return latest or {}


def set_risk_capital(value):
    global RISK_CAPITAL
    try:
        RISK_CAPITAL = max(10000, int(value))
        return RISK_CAPITAL
    except Exception:
        return RISK_CAPITAL
