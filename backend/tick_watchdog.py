"""
tick_watchdog — isolated tick-freshness monitor with multi-stage recovery.

WHY THIS EXISTS

  The core engine has its own tick tracking (`engine._last_tick_time`)
  and inline recovery logic in engine.py. But that recovery runs in the
  engine's own event loop — if the engine's dispatcher hangs, so does
  the recovery. Also, every future change to engine.py risks breaking
  the embedded watchdog silently.

  This module is a DEDICATED, INDEPENDENT watchdog. Design goals:

    1. OWN THREAD (daemon)     — never blocks caller
    2. READ-ONLY on engine     — only touches engine._last_tick_time
    3. NO business logic import — pure infrastructure
    4. STDLIB + pytz + requests — minimal footprint
    5. Env-controlled          — every threshold overridable
    6. Graceful failure        — outer try/except keeps it alive
    7. Multi-stage recovery    — escalating actions before self-kill

  Because it is a separate file, edits to engine.py, main.py, etc
  cannot silently break it. Only intentional edits here can.

RECOVERY STAGES

  Stage 0: Healthy       — last tick within TICK_STALE_WARN_SEC
  Stage 1: Warn          — stale >= TICK_STALE_WARN_SEC  (default 20s)
                           Telegram alert (throttled 1/10min)
  Stage 2: Reconnect     — stale >= TICK_STALE_RECONNECT_SEC (default 45s)
                           Call engine._restart_ticker() if available
  Stage 3: Engine restart — stale >= TICK_STALE_RESTART_SEC (default 90s)
                           Call engine.stop() + engine.start()
  Stage 4: Process kill  — stale >= TICK_STALE_KILL_SEC (default 180s)
                           os._exit(2) — Render auto-restarts container

  Between stages, STAGE_COOLDOWN_SEC (default 30s) prevents thrash.

  All stages log to structured_log if available, Telegram if configured.

ISOLATION CONTRACT

  This module MUST NOT:
    - Import trading logic (trade_logger, scalper_mode, position_watcher)
    - Mutate engine state beyond stop/start/restart_ticker calls
    - Depend on any SQL DB (uses local state file only)
    - Fail loud into caller (all errors caught + logged)

  This module MUST:
    - Run entirely in its own daemon thread
    - Handle missing engine gracefully (returns None → skip cycle)
    - Handle missing telegram gracefully (silent skip)
    - Provide diagnostics() for /api/admin endpoint

ENV OVERRIDES

  TICK_WATCHDOG_DISABLED=1              — turn off entirely
  TICK_WATCHDOG_CHECK_INTERVAL_SEC=5    — poll cadence (default 5s)
  TICK_STALE_WARN_SEC=20
  TICK_STALE_RECONNECT_SEC=45
  TICK_STALE_RESTART_SEC=90
  TICK_STALE_KILL_SEC=180
  TICK_WATCHDOG_KILL_ENABLED=on         — allow Stage 4 process kill
  TICK_WATCHDOG_MARKET_ONLY=on          — skip watchdog off-market hours
"""
from __future__ import annotations

import os
import sys
import time
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Optional

try:
    import pytz
    _IST = pytz.timezone("Asia/Kolkata")
except Exception:  # pragma: no cover — pytz is a hard dep of the project
    _IST = None


# ── Module-level state (thread-safe read via `state_snapshot()`) ─────

_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "started": False,
    "started_at": None,
    "current_stage": 0,
    "stale_since_ts": 0.0,
    "last_action_ts": 0.0,
    "last_action": "",
    "last_tick_seen_ts": 0.0,
    "cycles": 0,
    "stage_1_fired": 0,
    "stage_2_fired": 0,
    "stage_3_fired": 0,
    "stage_4_fired": 0,
    "recoveries": 0,
    "last_error": "",
}
_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


# ── Env helpers ──────────────────────────────────────────────────────

def _f(env_key: str, default: float) -> float:
    try:
        v = os.environ.get(env_key, "").strip()
        return float(v) if v else default
    except Exception:
        return default


def _enabled(env_key: str, default: bool = True) -> bool:
    v = os.environ.get(env_key, "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    return default


# ── IST market-hours helper ──────────────────────────────────────────

def _in_market_hours() -> bool:
    """9:15-15:30 IST Mon-Fri. Returns True on failure (fail-safe active)."""
    try:
        if _IST is None:
            return True
        now = datetime.now(_IST)
        if now.weekday() >= 5:
            return False
        minute_of_day = now.hour * 60 + now.minute
        return 9 * 60 + 15 <= minute_of_day <= 15 * 60 + 30
    except Exception:
        return True


# ── Optional side channels (never required) ──────────────────────────

def _send_telegram(msg: str, key: Optional[str] = None) -> None:
    """Best-effort Telegram send — silent skip if not configured."""
    try:
        import telegram_alerts as _tg
        if _tg.is_enabled():
            _tg.send(msg, key=key)
    except Exception:
        pass


def _log_event(kind: str, **fields: Any) -> None:
    """Best-effort structured log — silent skip if not configured."""
    try:
        import structured_logger as _sl
        _sl.log_event(kind, **fields)
    except Exception:
        pass


# ── Engine-facing helpers (all defensive) ────────────────────────────

def _read_last_tick_ts(engine: Any) -> float:
    """Return engine._last_tick_time or 0.0 on any failure."""
    try:
        v = getattr(engine, "_last_tick_time", 0)
        return float(v or 0)
    except Exception:
        return 0.0


def _engine_running(engine: Any) -> bool:
    try:
        return bool(getattr(engine, "running", False))
    except Exception:
        return False


def _try(fn: Callable[[], Any], name: str) -> bool:
    """Call fn(); return True on success. Never raises."""
    try:
        fn()
        return True
    except Exception as e:
        with _state_lock:
            _state["last_error"] = f"{name}: {e}"
        return False


# ── Recovery actions ─────────────────────────────────────────────────

def _stage_1_warn(engine: Any, stale_sec: float) -> None:
    _log_event("tick_watchdog_stage_1_warn", stale_sec=round(stale_sec, 1))
    _send_telegram(
        f"⚠️ Tick watchdog Stage 1 — last tick {stale_sec:.0f}s ago",
        key="tick_watchdog_warn",
    )


def _stage_2_reconnect(engine: Any, stale_sec: float) -> None:
    _log_event("tick_watchdog_stage_2_reconnect", stale_sec=round(stale_sec, 1))
    _send_telegram(
        f"🔧 Tick watchdog Stage 2 — reconnecting ticker "
        f"(stale {stale_sec:.0f}s)",
        key="tick_watchdog_reconnect",
    )
    restart_ticker = getattr(engine, "_restart_ticker", None)
    if callable(restart_ticker):
        _try(restart_ticker, "restart_ticker")


def _stage_3_restart(engine: Any, stale_sec: float) -> None:
    _log_event("tick_watchdog_stage_3_restart", stale_sec=round(stale_sec, 1))
    _send_telegram(
        f"🚨 Tick watchdog Stage 3 — restarting engine "
        f"(stale {stale_sec:.0f}s)",
        key="tick_watchdog_restart",
    )
    stop = getattr(engine, "stop", None)
    start = getattr(engine, "start", None)
    if callable(stop):
        _try(stop, "engine.stop")
    time.sleep(2)
    if callable(start):
        _try(start, "engine.start")


def _stage_4_kill(stale_sec: float) -> None:
    """Last resort — exit process. Render auto-restarts the container."""
    _log_event("tick_watchdog_stage_4_kill", stale_sec=round(stale_sec, 1))
    _send_telegram(
        f"🆘 Tick watchdog Stage 4 — process self-kill (stale {stale_sec:.0f}s). "
        f"Container will auto-restart.",
        key="tick_watchdog_kill",
    )
    # Give telegram a moment to actually flush
    time.sleep(1)
    try:
        os._exit(2)
    except Exception:
        # os._exit should never fail, but if it does, raise SystemExit
        raise SystemExit(2)


# ── The watchdog loop ────────────────────────────────────────────────

def _loop(engine_getter: Callable[[], Any]) -> None:
    check_interval = _f("TICK_WATCHDOG_CHECK_INTERVAL_SEC", 5.0)
    warn_sec = _f("TICK_STALE_WARN_SEC", 20.0)
    reconnect_sec = _f("TICK_STALE_RECONNECT_SEC", 45.0)
    restart_sec = _f("TICK_STALE_RESTART_SEC", 90.0)
    kill_sec = _f("TICK_STALE_KILL_SEC", 180.0)
    cooldown_sec = _f("TICK_WATCHDOG_STAGE_COOLDOWN_SEC", 30.0)
    kill_enabled = _enabled("TICK_WATCHDOG_KILL_ENABLED", default=True)
    market_only = _enabled("TICK_WATCHDOG_MARKET_ONLY", default=True)

    print(
        f"[TICK_WATCHDOG] loop started — check={check_interval:.0f}s "
        f"warn={warn_sec:.0f}s reconnect={reconnect_sec:.0f}s "
        f"restart={restart_sec:.0f}s kill={kill_sec:.0f}s "
        f"(kill_enabled={kill_enabled}, market_only={market_only})"
    )

    with _state_lock:
        _state["started"] = True
        _state["started_at"] = time.time()

    while not _stop_event.is_set():
        try:
            with _state_lock:
                _state["cycles"] += 1

            # Env kill switch (checked every cycle so it's live-toggleable)
            if _enabled("TICK_WATCHDOG_DISABLED", default=False):
                _stop_event.wait(check_interval)
                continue

            # Market-hours skip
            if market_only and not _in_market_hours():
                # Reset transient state so re-entry to market is clean
                with _state_lock:
                    _state["current_stage"] = 0
                    _state["stale_since_ts"] = 0.0
                _stop_event.wait(30)
                continue

            engine = None
            try:
                engine = engine_getter()
            except Exception as e:
                with _state_lock:
                    _state["last_error"] = f"engine_getter: {e}"

            if engine is None or not _engine_running(engine):
                # Engine not up yet — reset stale tracking, wait.
                with _state_lock:
                    _state["current_stage"] = 0
                    _state["stale_since_ts"] = 0.0
                _stop_event.wait(check_interval)
                continue

            now = time.time()
            last_tick_ts = _read_last_tick_ts(engine)
            with _state_lock:
                _state["last_tick_seen_ts"] = last_tick_ts

            # If engine.running is True but _last_tick_time is 0, the engine
            # hasn't observed a first tick yet. Treat this like a slow start
            # (age 999) so the escalation clock starts.
            tick_age = (now - last_tick_ts) if last_tick_ts > 0 else 999.0

            # ── Healthy ──────────────────────────────────────────────
            if tick_age <= warn_sec:
                with _state_lock:
                    prev_stage = _state["current_stage"]
                if prev_stage > 0:
                    with _state_lock:
                        _state["recoveries"] += 1
                    _log_event("tick_watchdog_recovered", from_stage=prev_stage)
                    _send_telegram(
                        f"💚 Tick watchdog recovered — was at Stage {prev_stage}",
                        key="tick_watchdog_recovered",
                    )
                with _state_lock:
                    _state["current_stage"] = 0
                    _state["stale_since_ts"] = 0.0
                    _state["last_action_ts"] = 0.0
                    _state["last_action"] = "healthy"
                _stop_event.wait(check_interval)
                continue

            # ── Stale ───────────────────────────────────────────────
            with _state_lock:
                if _state["stale_since_ts"] == 0.0:
                    _state["stale_since_ts"] = now - tick_age
                stale_since = _state["stale_since_ts"]
                current_stage = _state["current_stage"]
                last_action_ts = _state["last_action_ts"]

            stale_duration = now - stale_since
            cooldown_ok = (now - last_action_ts) >= cooldown_sec

            # Progressive escalation — highest applicable stage wins
            target_stage = 0
            if tick_age >= kill_sec:
                target_stage = 4
            elif tick_age >= restart_sec:
                target_stage = 3
            elif tick_age >= reconnect_sec:
                target_stage = 2
            elif tick_age >= warn_sec:
                target_stage = 1

            if target_stage > current_stage and cooldown_ok:
                new_stage = target_stage
                with _state_lock:
                    _state["current_stage"] = new_stage
                    _state["last_action_ts"] = now
                    _state["last_action"] = f"stage_{new_stage}"
                    _state[f"stage_{new_stage}_fired"] += 1

                if new_stage == 1:
                    _stage_1_warn(engine, tick_age)
                elif new_stage == 2:
                    _stage_2_reconnect(engine, tick_age)
                elif new_stage == 3:
                    _stage_3_restart(engine, tick_age)
                elif new_stage == 4:
                    if kill_enabled:
                        _stage_4_kill(tick_age)  # never returns
                    else:
                        # Kill disabled — just log
                        _log_event("tick_watchdog_stage_4_suppressed",
                                   stale_sec=round(tick_age, 1))

            _stop_event.wait(check_interval)

        except Exception as e:
            # Outer safety net — never let the loop die
            with _state_lock:
                _state["last_error"] = f"loop_outer: {e}"
            print(f"[TICK_WATCHDOG] loop error (continuing): {e}")
            _stop_event.wait(check_interval)

    with _state_lock:
        _state["started"] = False
    print("[TICK_WATCHDOG] loop stopped")


# ── Public API ───────────────────────────────────────────────────────

def start_watchdog(engine_getter: Callable[[], Any]) -> bool:
    """Start the daemon watchdog thread.

    Args:
      engine_getter: zero-arg callable that returns the current engine
                     instance (or None if not yet initialized). Must not
                     block. Called every check cycle.

    Returns:
      True if a new thread was spawned, False if already running or if
      TICK_WATCHDOG_DISABLED is set.
    """
    global _thread
    if _enabled("TICK_WATCHDOG_DISABLED", default=False):
        print("[TICK_WATCHDOG] disabled via TICK_WATCHDOG_DISABLED — not starting")
        return False
    if _thread is not None and _thread.is_alive():
        return False
    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop,
        args=(engine_getter,),
        daemon=True,
        name="tick-watchdog",
    )
    _thread.start()
    return True


def stop_watchdog() -> None:
    """Signal the loop to stop (for tests + shutdown)."""
    _stop_event.set()


def diagnostics() -> Dict[str, Any]:
    """Snapshot of watchdog state for /api/admin/tick-watchdog."""
    with _state_lock:
        snap = dict(_state)
    now = time.time()
    snap["thread_alive"] = _thread is not None and _thread.is_alive()
    snap["in_market_hours"] = _in_market_hours()
    if snap.get("last_tick_seen_ts", 0) > 0:
        snap["tick_age_sec"] = round(now - snap["last_tick_seen_ts"], 1)
    else:
        snap["tick_age_sec"] = None
    if snap.get("stale_since_ts", 0) > 0:
        snap["stale_duration_sec"] = round(now - snap["stale_since_ts"], 1)
    else:
        snap["stale_duration_sec"] = 0.0
    snap["thresholds"] = {
        "warn_sec": _f("TICK_STALE_WARN_SEC", 20.0),
        "reconnect_sec": _f("TICK_STALE_RECONNECT_SEC", 45.0),
        "restart_sec": _f("TICK_STALE_RESTART_SEC", 90.0),
        "kill_sec": _f("TICK_STALE_KILL_SEC", 180.0),
        "check_interval_sec": _f("TICK_WATCHDOG_CHECK_INTERVAL_SEC", 5.0),
        "cooldown_sec": _f("TICK_WATCHDOG_STAGE_COOLDOWN_SEC", 30.0),
        "kill_enabled": _enabled("TICK_WATCHDOG_KILL_ENABLED", True),
        "market_only": _enabled("TICK_WATCHDOG_MARKET_ONLY", True),
        "disabled": _enabled("TICK_WATCHDOG_DISABLED", False),
    }
    return snap
