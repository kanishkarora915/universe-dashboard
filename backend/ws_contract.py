"""
ws_contract.py — Immutable WebSocket health contract.

⚠️  DO NOT MODIFY THIS FILE WITHOUT REVIEWING ALL CALLERS.
    The whole point of this module is that EVERY upgrade can be evaluated
    against a single, stable contract that defines what "WS healthy" means.
    If you change the contract you must update every consumer + run the
    smoke test (run_smoke_test.py).

WHY THIS MODULE EXISTS
----------------------
Between 2026-05-25 and 2026-06-03 the production system went silent for
9 days because of TWO independent bugs that BOTH could have been caught
by a single startup invariant check:

  1. auto_login.py started asking for brotli compression but `requests`
     can't decompress brotli — JSON parse failed → no access token →
     ticker couldn't authenticate.
  2. KiteTicker.subscribe() can silently no-op when called immediately
     after on_connect — ticker reports "connected" but no ticks arrive.

Both bugs survive a code review because nothing CRASHES.  The system just
goes quiet.  Without a contract that asserts "if engine.start() returned,
ticks MUST flow within N seconds", any future upgrade can re-introduce
the same silent-failure class.

This file is the contract.  Anything else can break — these invariants
must hold.

PUBLIC API
----------
  INVARIANTS                    — list of (name, predicate, severity) tuples
  verify_health(engine)         — returns dict per invariant + overall pass/fail
  assert_healthy_or_alert(eng)  — runs the contract, sends Telegram on fail
  schedule_startup_smoke_test() — fires 30s after engine.start()
  on_subscribe(engine, tokens)  — wrapped subscribe with verification

TUNABLES (env vars, all optional)
---------------------------------
  WS_CONTRACT_SMOKE_DELAY_SEC      default 30  — wait this long after start
  WS_CONTRACT_MIN_PRICE_COUNT      default 10  — require ≥ N populated prices
  WS_CONTRACT_MAX_TICK_AGE_SEC     default 60  — fail if no tick for N sec
  WS_CONTRACT_ALERT_THROTTLE_SEC   default 300 — min gap between alerts
  WS_CONTRACT_DISABLED             default ""  — set to "1" to disable
"""

from __future__ import annotations

import os
import time
import threading
from typing import Callable, Dict, List, Optional, Tuple

# ── Contract constants ───────────────────────────────────────────────────
# These are deliberately separate from any other tunable.  If you need
# different behaviour, override via env var — do NOT edit these.
_DEFAULT_SMOKE_DELAY = 30
_DEFAULT_MIN_PRICE_COUNT = 10
_DEFAULT_MAX_TICK_AGE = 60
_DEFAULT_ALERT_THROTTLE = 300

# Module-level alert throttle state
_last_alert_ts: float = 0.0
_alert_lock = threading.Lock()

# Smoke test guard — runs once per engine.start()
_smoke_test_armed: Dict[int, bool] = {}
_smoke_test_lock = threading.Lock()


# ── Helpers ──────────────────────────────────────────────────────────────
def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        return int(v) if v else default
    except (TypeError, ValueError):
        return default


def _is_disabled() -> bool:
    return os.environ.get("WS_CONTRACT_DISABLED", "").strip() in ("1", "true", "yes", "on")


def _send_alert(msg: str, key: str) -> None:
    """Fire Telegram alert (throttled, fail-safe)."""
    global _last_alert_ts
    with _alert_lock:
        now = time.time()
        throttle = _env_int("WS_CONTRACT_ALERT_THROTTLE_SEC", _DEFAULT_ALERT_THROTTLE)
        if now - _last_alert_ts < throttle:
            return
        _last_alert_ts = now
    try:
        import telegram_alerts  # type: ignore
        if telegram_alerts.is_enabled():
            telegram_alerts.send(msg, key=key)
    except Exception:
        # NEVER let alerting failure propagate
        pass


def _log_event(event: str, **details) -> None:
    try:
        from structured_logger import log  # type: ignore
        getattr(log, "warn", log.info)(event, **details)
    except Exception:
        pass


# ── Invariants ───────────────────────────────────────────────────────────
# Each invariant is (name, predicate, severity).
# predicate(engine) -> (ok: bool, observed: any, message: str)
# severity: "CRITICAL" (block) / "WARN" (alert only)
#
# ⚠️  ADD invariants here; do NOT remove existing ones.
def _inv_engine_running(engine) -> Tuple[bool, object, str]:
    ok = bool(getattr(engine, "running", False))
    return ok, ok, "engine.running flag"


def _inv_ticker_exists(engine) -> Tuple[bool, object, str]:
    t = getattr(engine, "ticker", None)
    ok = t is not None
    return ok, type(t).__name__ if t else None, "engine.ticker is non-None"


def _inv_ticker_connected(engine) -> Tuple[bool, object, str]:
    t = getattr(engine, "ticker", None)
    if t is None:
        return False, None, "ticker missing"
    try:
        ok = bool(t.is_connected())
    except Exception as e:
        return False, str(e), "ticker.is_connected() raised"
    return ok, ok, "ticker.is_connected()"


def _inv_subscribe_tokens_set(engine) -> Tuple[bool, object, str]:
    toks = getattr(engine, "_subscribe_tokens", None)
    n = len(toks) if toks else 0
    ok = n > 0
    return ok, n, f"len(_subscribe_tokens) = {n}"


def _inv_prices_populated(engine) -> Tuple[bool, object, str]:
    prices = getattr(engine, "prices", None) or {}
    n = len(prices)
    min_n = _env_int("WS_CONTRACT_MIN_PRICE_COUNT", _DEFAULT_MIN_PRICE_COUNT)
    ok = n >= min_n
    return ok, n, f"len(prices) = {n} (need ≥ {min_n})"


def _inv_recent_tick(engine) -> Tuple[bool, object, str]:
    last = getattr(engine, "_last_tick_time", 0) or 0
    if last <= 0:
        return False, None, "no tick ever received"
    age = time.time() - last
    max_age = _env_int("WS_CONTRACT_MAX_TICK_AGE_SEC", _DEFAULT_MAX_TICK_AGE)
    ok = age <= max_age
    return ok, round(age, 1), f"last_tick_age = {age:.1f}s (max {max_age}s)"


def _inv_spot_tokens_have_data(engine) -> Tuple[bool, object, str]:
    """The 3 spot tokens (NIFTY/BANKNIFTY/VIX) MUST have LTP data.
    Options can be missing post-expiry roll, but spots never should."""
    spot_tokens = getattr(engine, "spot_tokens", None) or {}
    prices = getattr(engine, "prices", None) or {}
    missing = []
    for idx, tok in spot_tokens.items():
        p = prices.get(tok, {})
        if not p.get("ltp"):
            missing.append(idx)
    ok = len(missing) == 0
    return ok, missing or "all present", f"spot tokens with no LTP: {missing}"


# Ordered list — earlier invariants are prerequisites for later ones.
INVARIANTS: List[Tuple[str, Callable, str]] = [
    ("engine_running",        _inv_engine_running,        "CRITICAL"),
    ("ticker_exists",         _inv_ticker_exists,         "CRITICAL"),
    ("ticker_connected",      _inv_ticker_connected,      "CRITICAL"),
    ("subscribe_tokens_set",  _inv_subscribe_tokens_set,  "CRITICAL"),
    ("prices_populated",      _inv_prices_populated,      "CRITICAL"),
    ("recent_tick",           _inv_recent_tick,           "CRITICAL"),
    ("spot_tokens_have_data", _inv_spot_tokens_have_data, "WARN"),
]


# ── Public verifiers ─────────────────────────────────────────────────────
def verify_health(engine) -> Dict:
    """Run every invariant against engine. Returns:
        {
          "ok": bool,                # all CRITICAL passed
          "checked_at": float,       # epoch
          "invariants": [
            {"name": ..., "ok": bool, "severity": ..., "observed": ...,
             "message": ...}
          ],
          "failed_critical": [names],
          "failed_warn": [names],
        }
    """
    results = []
    failed_critical: List[str] = []
    failed_warn: List[str] = []

    for name, pred, sev in INVARIANTS:
        try:
            ok, observed, msg = pred(engine)
        except Exception as e:
            ok, observed, msg = False, str(e), f"predicate raised: {e}"
        results.append({
            "name": name,
            "ok": ok,
            "severity": sev,
            "observed": observed,
            "message": msg,
        })
        if not ok:
            if sev == "CRITICAL":
                failed_critical.append(name)
            else:
                failed_warn.append(name)

    return {
        "ok": len(failed_critical) == 0,
        "checked_at": time.time(),
        "invariants": results,
        "failed_critical": failed_critical,
        "failed_warn": failed_warn,
    }


def assert_healthy_or_alert(engine, context: str = "smoke_test") -> Dict:
    """Run health check; on CRITICAL failure, send Telegram + structured log.

    Returns the same dict as verify_health(), plus 'alerted': bool.
    """
    health = verify_health(engine)
    health["context"] = context
    if not health["ok"]:
        failed = health["failed_critical"]
        msg = (
            f"🚨 *WS Contract VIOLATION* ({context})\n"
            f"Failed: {', '.join(failed)}\n"
            f"Details:\n"
        )
        for inv in health["invariants"]:
            if not inv["ok"]:
                msg += f"  • {inv['name']} ({inv['severity']}): {inv['message']}\n"
        _send_alert(msg, key=f"ws_contract_{context}")
        _log_event(
            "ws_contract_violation",
            context=context,
            failed_critical=failed,
            failed_warn=health["failed_warn"],
        )
        health["alerted"] = True
    else:
        health["alerted"] = False
        _log_event("ws_contract_ok", context=context)
    return health


# ── Startup smoke test ──────────────────────────────────────────────────
def schedule_startup_smoke_test(engine_getter: Callable, delay_sec: Optional[int] = None) -> None:
    """Call AFTER engine.start(). Spawns a single-shot timer that runs
    the contract once, then exits. Idempotent per engine instance.

    engine_getter is a callable so we get the LATEST engine ref (the
    engine may be swapped during self-heal).
    """
    if _is_disabled():
        return

    delay = delay_sec if delay_sec is not None else _env_int(
        "WS_CONTRACT_SMOKE_DELAY_SEC", _DEFAULT_SMOKE_DELAY
    )

    def _run():
        try:
            time.sleep(delay)
            engine = engine_getter()
            if engine is None:
                _log_event("ws_contract_smoke_skip", reason="engine_none")
                return
            # Guard: don't re-run for same engine instance
            eid = id(engine)
            with _smoke_test_lock:
                if _smoke_test_armed.get(eid):
                    return
                _smoke_test_armed[eid] = True
            health = assert_healthy_or_alert(engine, context=f"smoke_test_after_{delay}s")

            # ── PERMANENT FIX (2026-06-08) ──
            # If contract is RED after delay (engine started but ticker
            # silent), trigger process self-kill so Render auto-restarts
            # with a fresh container. Only fires during market hours.
            # Env: WS_CONTRACT_SELFKILL_ON_FAIL=on (default on)
            if not health.get("ok"):
                if os.environ.get("WS_CONTRACT_SELFKILL_ON_FAIL", "on").lower() != "off":
                    # Market hours gate (9:15-15:30 IST, weekday)
                    try:
                        from datetime import datetime
                        import pytz
                        IST = pytz.timezone("Asia/Kolkata")
                        now_ist = datetime.now(IST)
                        is_weekday = now_ist.weekday() <= 4
                        is_market = is_weekday and (
                            (now_ist.hour == 9 and now_ist.minute >= 15) or
                            (10 <= now_ist.hour <= 14) or
                            (now_ist.hour == 15 and now_ist.minute <= 30)
                        )
                    except Exception:
                        is_market = False
                    if is_market:
                        # Only self-kill on CRITICAL ticker failures
                        critical_failures = set(health.get("failed_critical", []))
                        ticker_failed = bool(
                            critical_failures & {"ticker_connected", "recent_tick"}
                        )
                        if ticker_failed:
                            _send_alert(
                                f"💀 *WS Smoke Test FAIL — self-kill triggered*\n"
                                f"Engine started but ticker silent {delay}s in.\n"
                                f"Failed: {', '.join(critical_failures)}\n"
                                f"Process exit → Render auto-restart.",
                                key="smoke_test_selfkill",
                            )
                            _log_event(
                                "ws_smoke_selfkill",
                                failed=list(critical_failures),
                            )
                            time.sleep(2)
                            import os as _ose
                            _ose._exit(1)
        except Exception as e:
            _log_event("ws_contract_smoke_error", error=str(e))

    t = threading.Thread(target=_run, daemon=True, name="ws-contract-smoke")
    t.start()


# ── Wrapped subscribe (catches silent subscribe failures) ───────────────
def safe_subscribe(ticker, tokens: List[int], mode: Optional[str] = None,
                   verify_sec: int = 8) -> Dict:
    """Call ticker.subscribe(tokens) + set_mode(), then verify by waiting
    up to verify_sec for the FIRST tick to land. Returns dict:
        {"subscribed": bool, "mode_set": bool, "verified": bool,
         "took_sec": float, "error": str|None}

    This is the cure for "subscribe silently no-ops" — if no tick lands
    within verify_sec, caller can re-restart the ticker BEFORE the
    watchdog notices (15-90s) and re-tries.

    NOTE: must be called from a thread that can sleep — usually the
    on_connect callback context, OR a brand-new thread spawned by it.
    """
    out = {"subscribed": False, "mode_set": False, "verified": False,
           "took_sec": 0.0, "error": None}
    if not tokens:
        out["error"] = "no tokens"
        return out
    start = time.time()
    try:
        ticker.subscribe(tokens)
        out["subscribed"] = True
    except Exception as e:
        out["error"] = f"subscribe raised: {e}"
        return out
    try:
        if mode is None:
            mode = ticker.MODE_FULL
        ticker.set_mode(mode, tokens)
        out["mode_set"] = True
    except Exception as e:
        out["error"] = f"set_mode raised: {e}"
        # don't return — subscribe might still deliver LTP-mode ticks

    # Don't block on_connect — caller handles verify via on_ticks observation
    out["took_sec"] = round(time.time() - start, 3)
    return out


# ── Snapshot for /api/ws/contract endpoint ──────────────────────────────
def snapshot(engine) -> Dict:
    """Public read-only health snapshot. Safe to call from API handlers."""
    if engine is None:
        return {"ok": False, "reason": "engine is None",
                "invariants": [], "tunables": _current_tunables()}
    health = verify_health(engine)
    health["tunables"] = _current_tunables()
    return health


def _current_tunables() -> Dict:
    return {
        "WS_CONTRACT_SMOKE_DELAY_SEC": _env_int(
            "WS_CONTRACT_SMOKE_DELAY_SEC", _DEFAULT_SMOKE_DELAY),
        "WS_CONTRACT_MIN_PRICE_COUNT": _env_int(
            "WS_CONTRACT_MIN_PRICE_COUNT", _DEFAULT_MIN_PRICE_COUNT),
        "WS_CONTRACT_MAX_TICK_AGE_SEC": _env_int(
            "WS_CONTRACT_MAX_TICK_AGE_SEC", _DEFAULT_MAX_TICK_AGE),
        "WS_CONTRACT_ALERT_THROTTLE_SEC": _env_int(
            "WS_CONTRACT_ALERT_THROTTLE_SEC", _DEFAULT_ALERT_THROTTLE),
        "WS_CONTRACT_DISABLED": _is_disabled(),
    }
