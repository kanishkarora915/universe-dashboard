"""
UNIVERSE Backend — FastAPI server for Kite Connect integration.
Routes: OAuth login/callback, live data, option chain, historical, unusual activity, WebSocket.
Serves React frontend static build in production.
"""

import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Sentry — error tracking (free tier, only active if SENTRY_DSN set) ──
# Initialized BEFORE FastAPI import so it can wrap everything.
_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            traces_sample_rate=0.1,         # 10% perf traces (free tier ok)
            profiles_sample_rate=0.1,
            send_default_pii=False,         # privacy
            release=os.getenv("RENDER_GIT_COMMIT", "unknown"),
            environment=("production" if os.getenv("RENDER") else "dev"),
        )
        print(f"[SENTRY] Initialized — release={os.getenv('RENDER_GIT_COMMIT', 'dev')[:7]}")
    except Exception as _se:
        print(f"[SENTRY] init skipped: {_se}")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from kiteconnect import KiteConnect

from engine import MarketEngine
from trade_logger import ist_now
from historical_validation import run_validation, get_real_trade_analysis
from trade_autopsy import (
    get_trade_autopsy, get_win_loss_patterns, get_gap_prediction,
    get_gap_history, init_db as autopsy_init_db,
)
from trading_times import (
    get_live_dashboard as tt_live, get_today_timeline as tt_timeline,
    get_daily_report as tt_daily, get_weekly_report as tt_weekly,
    get_monthly_report as tt_monthly, init_db as tt_init_db,
)
from ml_feedback import (
    get_engine_accuracy, get_optimal_weights, get_hourly_analysis,
    get_pattern_analysis, get_weekly_report, get_weights_info,
    apply_recommended_weights, reset_weights, get_trading_windows,
    run_auto_train, get_training_history, get_auto_train_status,
)
from alerts import (
    init_db as alerts_init_db, push_alert, list_alerts,
    get_unread_counts, mark_read, dismiss as alerts_dismiss, pin as alerts_pin,
)

# ── Config ───────────────────────────────────────────────────────────────

PORT = int(os.getenv("PORT", 8000))
# In production (Render), frontend is served from same origin
# In dev, frontend runs on separate Vite port
IS_PROD = os.getenv("RENDER", "") == "true" or os.path.exists(Path(__file__).parent.parent / "dist")
FRONTEND_URL = os.getenv("FRONTEND_URL", "")  # Set on Render, e.g. https://universe-dashboard.onrender.com

# Build path for static files
DIST_DIR = Path(__file__).parent.parent / "dist"

# ── Data cache (persists across sessions) ────────────────────────────────

_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
CACHE_FILE = _data_dir / "data_cache.json"

def load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
    except Exception:
        pass
    return {}

def save_cache(key: str, data):
    try:
        cache = load_cache()
        cache[key] = data
        CACHE_FILE.write_text(json.dumps(cache))
    except Exception:
        pass

def get_cached(key: str):
    cache = load_cache()
    return cache.get(key)

# ── Global state ─────────────────────────────────────────────────────────

session = {
    "api_key": None,
    "api_secret": None,
    "access_token": None,
    "kite": None,
}

engine: Optional[MarketEngine] = None
event_loop: Optional[asyncio.AbstractEventLoop] = None


# ── App lifespan ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global event_loop, engine
    event_loop = asyncio.get_event_loop()

    # ── AUTO-RESUME engine from cached access_token after deploys ──
    # Render restarts the container on every deploy, which wipes in-memory
    # state including `engine = None`. Without this, we'd need a manual
    # login click each deploy and lose 2-3 min of pulse data.
    # access_token.json lives on the /data persistent disk so survives.
    try:
        token_file = _data_dir / "access_token.json"
        if token_file.exists() and engine is None:
            token_data = json.loads(token_file.read_text())
            api_key = token_data.get("api_key", "")
            access_token = token_data.get("access_token", "")
            api_secret = token_data.get("api_secret", "")
            if api_key and access_token:
                kite = KiteConnect(api_key=api_key)
                kite.set_access_token(access_token)

                # ── Verify cached token before starting the engine ──
                # Kite access_tokens expire daily at 6 AM IST. The previous
                # behaviour started the engine with whatever token was on
                # disk; if it was yesterday's, the ticker would fail and we
                # waited up to 5 min for self-heal to notice. kite.profile()
                # is a cheap call that fails fast on an expired token, so
                # we can attempt a fresh login immediately at cold start.
                token_ok = False
                try:
                    kite.profile()
                    token_ok = True
                    print(f"[STARTUP] Cached token {access_token[:8]}… verified")
                except Exception as ve:
                    print(f"[STARTUP] Cached token DEAD ({ve}) — attempting fresh Kite login")
                    try:
                        import auto_login as al
                        required = ["KITE_USER_ID", "KITE_PASSWORD", "KITE_TOTP_SECRET",
                                    "KITE_API_KEY", "KITE_API_SECRET"]
                        if all(os.environ.get(k) for k in required):
                            new_token = al.kite_login()
                            if new_token:
                                access_token = new_token
                                api_key = os.environ["KITE_API_KEY"]
                                api_secret = os.environ.get("KITE_API_SECRET", "")
                                kite = KiteConnect(api_key=api_key)
                                kite.set_access_token(access_token)
                                token_ok = True
                                print(f"[STARTUP] Fresh login OK — new token {access_token[:8]}…")
                        else:
                            print("[STARTUP] Fresh-login env vars missing — engine NOT started, self-heal will retry")
                    except Exception as fle:
                        print(f"[STARTUP] Fresh login failed: {fle} — engine NOT started, self-heal will retry")

                if token_ok:
                    session["api_key"] = api_key
                    session["api_secret"] = api_secret
                    session["access_token"] = access_token
                    session["kite"] = kite
                    try:
                        from trade_logger import save_nse_holidays_from_kite
                        save_nse_holidays_from_kite(kite)
                    except Exception:
                        pass
                    engine = MarketEngine(api_key=api_key, access_token=access_token, loop=event_loop)
                    engine.start()
                    try:
                        from trinity import api_routes as _tr
                        _tr.attach_engine(engine)
                    except Exception as _e:
                        print(f"[TRINITY] attach_engine failed: {_e}")
                    # ── WS CONTRACT SMOKE TEST (post-engine.start) ──
                    # Schedules a single check 30s after engine.start to verify
                    # ticks are flowing. On failure → Telegram alert + log.
                    # This catches "engine started but ticker silent" regressions
                    # introduced by any future code change.
                    try:
                        import ws_contract as _wsc
                        _wsc.schedule_startup_smoke_test(lambda: engine)
                        print("[STARTUP] ws_contract smoke test scheduled")
                    except Exception as _wse:
                        print(f"[STARTUP] ws_contract schedule failed: {_wse}")
                    print(f"[STARTUP] Engine auto-resumed from cached token {access_token[:8]}…")
            else:
                print("[STARTUP] access_token.json present but missing fields — manual login needed")
        elif engine is None:
            print("[STARTUP] No access_token.json found — waiting for manual login")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[STARTUP] Auto-resume failed (will need manual login): {e}")

    # ── In-process auto-login daemon ──
    # Kite tokens expire daily at 6 AM IST. This thread wakes at 6:05 AM,
    # runs the full Kite login flow (credentials + TOTP), saves the fresh
    # access_token, and restarts the engine — all from inside the Render
    # container. Eliminates dependency on the external AWS EC2 daemon.
    #
    # To enable, set these env vars on Render:
    #   KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET,
    #   KITE_API_KEY, KITE_API_SECRET
    # If any are missing, the daemon logs and exits — falls back to the
    # AWS daemon / manual login flow (no crash).
    try:
        import threading as _th
        _th.Thread(
            target=_autologin_daemon,
            daemon=True,
            name="autologin-daemon",
        ).start()
    except Exception as _e:
        print(f"[STARTUP] autologin daemon spawn failed: {_e}")

    # ── F&O Scanner — daily 1-3 day swing analysis for ~190 F&O stocks ──
    # User-requested 2026-06-08. Scans full F&O universe at 08:00 IST,
    # caches result. Detects bull/bear setups, predicted target + R/R.
    # Runs ONCE per day — minimal CPU impact.
    try:
        import fno_scanner as _fno
        _fno.start_daily_scan_thread(lambda: session.get("kite"))
        print("[STARTUP] fno-scanner daemon spawned")
    except Exception as _e:
        print(f"[STARTUP] fno-scanner spawn failed: {_e}")

    # ── Market context (200-day chart structure) refresh thread ──
    # Pulls 200d daily candles via Kite REST, computes S/R zones,
    # 200d trend, ATR. Caches per-index. Used as ADDITIVE bonus to
    # verdict score — never blocks a trade based on chart alone.
    # User principle (2026-06-05): "smart system no strict, strict only
    # to not bleed losses".
    try:
        import market_context as _mc
        _mc.start_refresh_thread(lambda: session.get("kite"))
        print("[STARTUP] market-context refresh thread spawned")
    except Exception as _e:
        print(f"[STARTUP] market-context spawn failed: {_e}")

    # ── Structure refresh thread (Phase 2 — 2026-05-27) ──
    # Background thread refreshes per-index trend structure every 5 min
    # via Kite REST history. Only does work when STRUCTURE_MODE != off,
    # so default-OFF deployment has zero cost. The cached verdicts feed
    # the G14 (scalper) + G0f (main) entry gates.
    try:
        import structure_gate as _sg
        _sg.start_refresh_thread(lambda: engine)
        print("[STARTUP] structure-refresh thread spawned")
    except Exception as _e:
        print(f"[STARTUP] structure-refresh spawn failed: {_e}")

    # ── EOD Daily Report Telegram Push (3:30 PM IST) ──
    # 2026-06-11: User requested daily date-wise report.
    # Sends formatted summary to Telegram each day after market close.
    # Uses existing /api/admin/daily-report data + telegram_alerts.
    try:
        import threading as _th
        _th.Thread(
            target=_eod_telegram_daemon,
            daemon=True,
            name="eod-telegram-daemon",
        ).start()
        print("[STARTUP] eod-telegram daemon spawned")
    except Exception as _e:
        print(f"[STARTUP] eod-telegram spawn failed: {_e}")

    # ── Stale-token reaper — wipes the cached Kite token at 06:00 IST ──
    # Kite revokes the access token at 6 AM IST sharp. Holding onto the
    # cached /data/access_token.json past 6 AM means any consumer
    # (lifespan resume, /api/status, manual checks) could see a stale
    # "logged in" state while the ticker is actually dead. This thread
    # deletes the file + stops the engine + clears the session at 6 AM
    # so the dashboard correctly displays "logged out, waiting" between
    # 06:00 and the 08:50 daemon refresh.
    try:
        import threading as _th
        _th.Thread(
            target=_stale_token_reaper,
            daemon=True,
            name="stale-token-reaper",
        ).start()
    except Exception as _e:
        print(f"[STARTUP] stale-token-reaper spawn failed: {_e}")

    # ── DAILY PROCESS RESTART daemon (2026-06-08 root-cause fix) ──
    # Twisted reactor (used by kiteconnect KiteTicker) is a SINGLETON
    # per Python process — cannot restart after disconnect. Overnight
    # network blip / Kite WS server restart kills the reactor → next
    # morning ticker.connect() silently fails despite valid token.
    # ONLY fix: new process.
    #
    # This daemon fires os._exit(1) at 08:30 IST every trading day
    # (before 8:50 auto-login, before 9:15 market open). Render
    # auto-restarts → fresh Python process → fresh reactor → ticker
    # connects clean every single morning. ZERO drift risk.
    try:
        import threading as _th
        import time as _t_proc
        import pytz as _pytz_proc
        from datetime import datetime as _dt_proc

        def _daily_restart_loop():
            _IST = _pytz_proc.timezone("Asia/Kolkata")
            last_restart_date = None
            print("[DAILY-RESTART] daemon armed — fires at 08:30 IST weekdays")
            while True:
                try:
                    now = _dt_proc.now(_IST)
                    today_iso = now.strftime("%Y-%m-%d")
                    # Weekday (Mon-Fri) + 08:30-08:35 window + not yet today
                    is_weekday = now.weekday() <= 4
                    in_window = (now.hour == 8 and 30 <= now.minute < 35)
                    if is_weekday and in_window and last_restart_date != today_iso:
                        print(f"[DAILY-RESTART] {now.isoformat()} — "
                              f"firing daily process restart (Twisted reactor refresh)")
                        try:
                            import telegram_alerts
                            if telegram_alerts.is_enabled():
                                telegram_alerts.send(
                                    "🔄 *Daily 8:30 IST process restart*\n"
                                    "Fresh process for fresh Twisted reactor.\n"
                                    "Back online in 60-90 sec.",
                                    key="daily_restart",
                                )
                        except Exception:
                            pass
                        last_restart_date = today_iso
                        _t_proc.sleep(2)
                        import os as _os_dr
                        _os_dr._exit(0)  # clean exit → Render restart
                except Exception as e:
                    print(f"[DAILY-RESTART] loop error: {e}")
                _t_proc.sleep(30)  # check every 30s

        _th.Thread(target=_daily_restart_loop, daemon=True,
                   name="daily-restart").start()
    except Exception as _e:
        print(f"[STARTUP] daily-restart spawn failed: {_e}")

    # ── Disk auto-prune daemon (2026-06-04) ──
    # Runs once at startup + then daily at 5 AM IST (before token reaper).
    # Calls the same logic as POST /api/admin/disk/cleanup actions
    # all_safe + prune_council + prune_trap_data. Prevents the 4.3 GB
    # disk-full incident from recurring — no manual intervention needed.
    try:
        import threading as _th, time as _time
        import pytz as _pytz_dp
        from datetime import datetime as _dt_dp

        def _disk_auto_prune():
            _IST_dp = _pytz_dp.timezone("Asia/Kolkata")
            print("[DISK-PRUNE] daemon started — runs at 05:00 IST daily")
            last_prune_date = None

            def _do_prune():
                """Run all safe prune actions; log result."""
                try:
                    # 1. WAL checkpoint all DBs
                    import sqlite3 as _sq3
                    from pathlib import Path as _PP
                    count = 0
                    for p in _PP(_data_dir).glob("*.db"):
                        try:
                            c = _sq3.connect(str(p), timeout=5.0)
                            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                            c.close()
                            count += 1
                        except Exception:
                            pass
                    print(f"[DISK-PRUNE] WAL checkpoint: {count} DBs")

                    # 2. Council prune (14d engine_votes / verdicts / perf)
                    council_path = _PP(_data_dir) / "council.db"
                    if council_path.exists():
                        from datetime import timedelta as _td_dp
                        cutoff_14d = (_dt_dp.now(_IST_dp) - _td_dp(days=14)).isoformat()
                        cutoff_30d = (_dt_dp.now(_IST_dp) - _td_dp(days=30)).isoformat()
                        c = _sq3.connect(str(council_path), timeout=30.0)
                        deleted = 0
                        for tbl, col, cutoff in [
                            ("engine_votes", "timestamp", cutoff_14d),
                            ("council_verdicts", "timestamp", cutoff_14d),
                            ("perf_samples", "iso", cutoff_14d),
                            ("auto_login_attempts", "timestamp", cutoff_30d),
                        ]:
                            try:
                                cur = c.execute(
                                    f"DELETE FROM {tbl} WHERE {col} < ?", (cutoff,))
                                deleted += cur.rowcount
                            except Exception:
                                pass
                        c.commit()
                        try:
                            c.execute("VACUUM")
                            c.commit()
                        except Exception:
                            pass
                        c.close()
                        print(f"[DISK-PRUNE] council.db: {deleted} rows deleted + VACUUM")

                    # 3. Trap data prune (14d)
                    try:
                        import trap_engine
                        if trap_engine.DB_PATH is None:
                            trap_engine.DB_PATH = str(_PP(_data_dir) / "trap_data.db")
                        trap_engine._purge_old(14)
                    except Exception as _te:
                        print(f"[DISK-PRUNE] trap_engine prune skipped: {_te}")

                    # 4. Report final disk state
                    import shutil as _sh
                    usage = _sh.disk_usage(str(_data_dir))
                    free_mb = usage.free / 1024 / 1024
                    pct = (usage.total - usage.free) / usage.total * 100
                    print(f"[DISK-PRUNE] done — free {free_mb:.0f} MB, used {pct:.1f}%")

                    # 5. Telegram alert (silent except critical)
                    try:
                        import telegram_alerts
                        if telegram_alerts.is_enabled() and pct > 80:
                            telegram_alerts.send(
                                f"⚠️ Disk still {pct:.0f}% after auto-prune. "
                                f"Free: {free_mb:.0f} MB. Check manually.",
                                key="disk_prune_warn")
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[DISK-PRUNE] error: {e}")

            # Run once at startup so any existing disk pressure clears
            _time.sleep(60)  # let engine boot fully
            _do_prune()

            # Daily loop
            while True:
                try:
                    now = _dt_dp.now(_IST_dp)
                    today_iso = now.strftime("%Y-%m-%d")
                    # Fire between 05:00 and 05:10 IST, once per day
                    if (now.hour == 5 and now.minute < 10
                            and last_prune_date != today_iso):
                        _do_prune()
                        last_prune_date = today_iso
                except Exception as e:
                    print(f"[DISK-PRUNE] loop error: {e}")
                _time.sleep(60)  # check every minute

        _th.Thread(
            target=_disk_auto_prune,
            daemon=True,
            name="disk-auto-prune",
        ).start()
    except Exception as _e:
        print(f"[STARTUP] disk auto-prune spawn failed: {_e}")

    # ── Engine self-heal monitor (Layer 3 of bulletproof auto-login) ──
    # During market hours (09:15-15:30 IST), if engine is detected
    # dead (None or running=False), attempt automatic recovery:
    #   1. Try cached token from /data/access_token.json (fast path)
    #   2. If cached token dead → full Kite login via auto_login.py
    #   3. Either succeeds → engine alive, Telegram alert
    #      Both fail → Telegram CRITICAL, manual login needed
    try:
        import threading as _th
        _th.Thread(
            target=_engine_selfheal_monitor,
            daemon=True,
            name="engine-selfheal",
        ).start()
    except Exception as _e:
        print(f"[STARTUP] engine self-heal monitor spawn failed: {_e}")

    # ── Health monitor (periodic Telegram status + trade reports) ──
    # Every 30 min during market hours (09:15-15:30 IST), sends a
    # health snapshot + today's trade activity to Telegram. Plus an
    # EOD summary at 15:35 IST every weekday.
    try:
        import threading as _th
        from health_monitor import run_monitor as _hm_run

        def _engine_getter():
            return engine  # closure over the module-level engine ref

        _th.Thread(
            target=_hm_run,
            args=(_engine_getter,),
            daemon=True,
            name="health-monitor",
        ).start()
    except Exception as _e:
        print(f"[STARTUP] health monitor spawn failed: {_e}")

    # ── Performance monitor (5-min sampling for crash post-mortems) ──
    # Samples CPU/memory/threads/disk/engine state every 5 min and
    # writes to council.db perf_samples. When engine dies unexpectedly,
    # query this table to see exactly what was happening.
    try:
        import threading as _th
        from perf_monitor import run_sampler as _pm_run

        def _engine_getter_pm():
            return engine

        _th.Thread(
            target=_pm_run,
            args=(_engine_getter_pm,),
            daemon=True,
            name="perf-monitor",
        ).start()
    except Exception as _e:
        print(f"[STARTUP] perf monitor spawn failed: {_e}")

    # ── Phase 1: Anomaly alerts background scheduler ──
    # Every 15 min during market hours, run regime_monitor + pattern_shift
    # checks and fire Telegram alerts on threshold breaches.
    # Also runs EOD diagnostic at 15:35 IST.
    try:
        import threading as _th
        import time as _time
        from datetime import datetime as _dt
        import pytz as _pytz

        def _anomaly_scheduler():
            _IST = _pytz.timezone("Asia/Kolkata")
            last_eod_date = None
            while True:
                try:
                    # Periodic checks every 15 min
                    from anomaly_alerts import run_periodic_checks, run_eod_diagnostic
                    run_periodic_checks()

                    # EOD diagnostic at 15:35 IST (once per day)
                    now = _dt.now(_IST)
                    today_iso = now.strftime("%Y-%m-%d")
                    if (now.hour == 15 and now.minute >= 35 and last_eod_date != today_iso):
                        print(f"[ANOMALY_ALERTS] Running EOD diagnostic for {today_iso}")
                        run_eod_diagnostic()
                        last_eod_date = today_iso
                except Exception as _e:
                    print(f"[ANOMALY_ALERTS] scheduler error: {_e}")
                _time.sleep(15 * 60)  # check every 15 min

        _th.Thread(
            target=_anomaly_scheduler,
            daemon=True,
            name="anomaly-scheduler",
        ).start()
        print("[STARTUP] anomaly alerts scheduler started")
    except Exception as _e:
        print(f"[STARTUP] anomaly alerts spawn failed: {_e}")

    yield
    if engine:
        engine.stop()


# Engine-swap lock — serializes engine creation/destruction. Without this
# two concurrent callers (e.g. lifespan + daemon + manual login race) could
# each tear down the old engine and create a new one, leaving a leaked
# engine alive in the background. With it, swaps are atomic.
import threading as _engine_swap_threading
_engine_swap_lock = _engine_swap_threading.Lock()


def _start_engine_with_token(api_key: str, access_token: str, api_secret: str = "") -> tuple:
    """Start MarketEngine with given Kite credentials.

    Shared by: lifespan auto-resume, /api/auto-login handler, /api/callback
    handler, and the in-process auto-login daemon. Returns (ok, message).
    Serialized via _engine_swap_lock — concurrent calls wait, preventing
    the dual-engine overlap that froze the dashboard after manual login.
    """
    global engine
    # Acquire swap lock with timeout — concurrent callers wait their turn.
    # 15 sec is enough for stop()'s 3-sec thread-wait + start()'s init.
    if not _engine_swap_lock.acquire(timeout=15):
        return False, "engine swap in progress — try again"
    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        session["api_key"] = api_key
        session["api_secret"] = api_secret
        session["access_token"] = access_token
        session["kite"] = kite

        try:
            from trade_logger import save_nse_holidays_from_kite
            save_nse_holidays_from_kite(kite)
        except Exception:
            pass

        # Tear down old engine BEFORE creating a new one. stop() now
        # blocks ~3 sec waiting for background threads to notice
        # self.running=False and exit — no more dual-engine overlap.
        if engine is not None:
            try:
                engine.stop(wait_sec=3.0)
            except Exception as _e:
                print(f"[ENGINE-RESTART] stop() of old engine failed: {_e}")
            engine = None  # release reference so old engine can be GC'd

        engine = MarketEngine(api_key=api_key, access_token=access_token, loop=event_loop)
        engine.start()

        try:
            from trinity import api_routes as _tr
            _tr.attach_engine(engine)
        except Exception as _e:
            print(f"[TRINITY] attach_engine failed: {_e}")

        # ── WS CONTRACT SMOKE TEST (every engine swap) ──
        # Re-armed for every new engine instance so login + self-heal +
        # manual /api/auto-login/force all get verified 30s after start.
        try:
            import ws_contract as _wsc
            _wsc.schedule_startup_smoke_test(lambda: engine)
        except Exception as _wse:
            print(f"[ENGINE-RESTART] ws_contract schedule failed: {_wse}")

        return True, f"Engine started with access_token {access_token[:8]}..."
    except Exception as e:
        return False, str(e)
    finally:
        _engine_swap_lock.release()


def _stale_token_reaper():
    """Reap the stale Kite access token at 06:00 IST daily.

    Kite revokes the access token at 6 AM IST sharp. Past that moment
    the cached /data/access_token.json file holds a dead string — but
    any consumer (lifespan auto-resume on a container restart, the
    /api/status check, position_watcher reads, etc.) could still pick
    it up and silently treat the dashboard as "logged in" while the
    ticker is in fact dead. The user sees stale data without warning.

    This thread fires at 06:00 IST (window 06:00-06:10 to cover sleep
    drift) and performs three clean-up actions:
      1. Deletes /data/access_token.json so the file is gone.
      2. Stops the engine (its internal access_token is dead anyway —
         no ticks flow pre-market, monitoring is moot).
      3. Clears the in-memory session dict so /api/status correctly
         shows logged-out.

    The 08:50 in-process daemon then performs a fresh Kite login and
    starts a new engine with the new token. Between 06:00 and 08:50
    the dashboard displays a truthful "logged out, waiting for
    auto-login" state instead of a zombie one.

    Crash safety: outer try/except, never propagates errors.
    """
    global engine
    import time as _t
    from datetime import datetime as _dt
    import pytz as _pytz

    _IST = _pytz.timezone("Asia/Kolkata")
    token_file = _data_dir / "access_token.json"
    last_reap_date = None

    print("[TOKEN-REAPER] Started — will wipe stale Kite token at 06:00 IST daily")

    while True:
        try:
            now = _dt.now(_IST)
            today_str = now.strftime("%Y-%m-%d")

            # Already reaped today? Check again later.
            if last_reap_date == today_str:
                _t.sleep(300)
                continue

            # In the 06:00 - 06:10 IST reap window?
            t_min = now.hour * 60 + now.minute
            if 6 * 60 <= t_min <= 6 * 60 + 10:
                # 1. Delete cached token file
                if token_file.exists():
                    try:
                        token_file.unlink()
                        print(f"[TOKEN-REAPER] Deleted {token_file.name} at "
                              f"{now.strftime('%H:%M:%S')} IST — Kite revoked at 6 AM")
                    except Exception as e:
                        print(f"[TOKEN-REAPER] delete failed: {e}")
                else:
                    print(f"[TOKEN-REAPER] {token_file.name} already absent — nothing to reap")

                # 2. Stop the engine — internal token is dead
                if engine is not None:
                    try:
                        engine.stop()
                        engine = None
                        print("[TOKEN-REAPER] Stopped engine — 08:50 daemon will restart it fresh")
                    except Exception as e:
                        print(f"[TOKEN-REAPER] engine stop failed: {e}")

                # 3. Clear in-memory session
                session["api_key"] = None
                session["api_secret"] = None
                session["access_token"] = None
                session["kite"] = None

                last_reap_date = today_str
                _t.sleep(60)
            else:
                _t.sleep(60)
        except Exception as e:
            print(f"[TOKEN-REAPER] loop error: {e}")
            _t.sleep(60)


def _engine_selfheal_monitor():
    """Background thread — auto-recover engine during market hours.

    Layer 3 of the bulletproof auto-login system. Catches the case
    where the engine dies AFTER the morning auto-login window has
    passed (mid-day container crash, OOM, etc).

    Cycle (every 5 minutes):
      1. Only act between 09:15 - 15:30 IST weekdays
      2. Check if engine is None OR engine.running is False
      3. If yes, attempt recovery:
         a. Try resuming from cached /data/access_token.json
         b. If that fails (token expired), do full Kite login
         c. Either way, call _start_engine_with_token(...)
      4. Telegram alerts:
         - Success → "💚 Engine Recovered"
         - All paths failed → "🚨 Engine Down"
      5. Cooldown 5 min between recovery attempts to avoid hammer

    Crash safety: outer try/except, never propagates errors.
    """
    import time as _t

    # Lazy imports — daemon-style robust
    try:
        from council import storage as council_storage
    except Exception:
        council_storage = None
    try:
        import telegram_alerts
    except Exception:
        telegram_alerts = None

    def _ist_now():
        try:
            import pytz
            from datetime import datetime
            return datetime.now(pytz.timezone("Asia/Kolkata"))
        except Exception:
            from datetime import datetime
            return datetime.now()

    def _is_market_hours(now) -> bool:
        if now.weekday() >= 5:  # Sat/Sun
            return False
        t = now.hour * 60 + now.minute
        return 9 * 60 + 15 <= t <= 15 * 60 + 30

    # Adaptive cooldown — 60s in the first hour after market open (the
    # window where a stale token from yesterday matters most) and 300s
    # the rest of the day. Prevents hammering Kite all afternoon while
    # keeping morning bootstrap recovery snappy (≤1 min instead of ≤5).
    SELFHEAL_INTERVAL_OFF_HOURS = 300
    SELFHEAL_INTERVAL_FAST = 60       # 09:15-10:15 IST
    SELFHEAL_INTERVAL_DEFAULT = 300   # rest of market hours

    def _adaptive_interval(now) -> int:
        t = now.hour * 60 + now.minute
        if 9 * 60 + 15 <= t <= 10 * 60 + 15:
            return SELFHEAL_INTERVAL_FAST
        return SELFHEAL_INTERVAL_DEFAULT

    print("[SELFHEAL] Started — 60s checks in first market hour, 300s thereafter")

    last_recovery_attempt = 0
    last_alert_for_engine_down = 0

    while True:
        try:
            now = _ist_now()
            if not _is_market_hours(now):
                _t.sleep(SELFHEAL_INTERVAL_OFF_HOURS)
                continue

            interval = _adaptive_interval(now)

            # Engine state check
            engine_alive = (
                engine is not None
                and getattr(engine, "running", False)
            )
            if engine_alive:
                _t.sleep(interval)
                continue

            # Engine DOWN during market hours — attempt recovery
            now_ts = time.time()
            if (now_ts - last_recovery_attempt) < interval:
                _t.sleep(15)  # short sleep, cooldown not elapsed
                continue
            last_recovery_attempt = now_ts

            print(f"[SELFHEAL] Engine DOWN at {now.strftime('%H:%M:%S')} IST — attempting recovery")

            # First Telegram WARN (throttled by alerts module)
            if telegram_alerts and (now_ts - last_alert_for_engine_down) > 600:
                last_alert_for_engine_down = now_ts
                try:
                    telegram_alerts.alert_engine_down(
                        "Detected during market hours — attempting auto-recovery"
                    )
                except Exception:
                    pass

            recovered = False
            recovery_method = None
            cached_err = None       # NEW: capture cached-path failure
            fresh_err = None        # NEW: capture fresh-path failure
            attempt_start = time.time()

            # ── Path 1: Try cached token ──
            try:
                token_file = _data_dir / "access_token.json"
                if token_file.exists():
                    token_data = json.loads(token_file.read_text())
                    api_key = token_data.get("api_key", "")
                    access_token = token_data.get("access_token", "")
                    api_secret = token_data.get("api_secret", "")
                    if api_key and access_token:
                        ok, msg = _start_engine_with_token(api_key, access_token, api_secret)
                        if ok:
                            recovered = True
                            recovery_method = "cached_token"
                            print(f"[SELFHEAL] Recovered via cached token: {msg}")
                        else:
                            cached_err = f"engine start failed: {msg}"
                    else:
                        cached_err = "cached file missing api_key/access_token fields"
                else:
                    cached_err = "no access_token.json file"
            except Exception as e:
                cached_err = f"exception: {str(e)[:150]}"
                print(f"[SELFHEAL] cached-token path failed: {e}")

            # ── Path 2: Full Kite login (if cached failed) ──
            if not recovered:
                try:
                    import auto_login as al
                    required = ["KITE_USER_ID", "KITE_PASSWORD", "KITE_TOTP_SECRET",
                                "KITE_API_KEY", "KITE_API_SECRET"]
                    missing = [k for k in required if not os.environ.get(k)]
                    if missing:
                        fresh_err = f"missing env vars: {missing}"
                        print(f"[SELFHEAL] fresh-login skipped — {fresh_err}")
                    else:
                        access_token = al.kite_login()
                        api_key = os.environ["KITE_API_KEY"]
                        api_secret = os.environ.get("KITE_API_SECRET", "")
                        ok, msg = _start_engine_with_token(api_key, access_token, api_secret)
                        if ok:
                            recovered = True
                            recovery_method = "fresh_kite_login"
                            print(f"[SELFHEAL] Recovered via fresh login: {msg}")
                        else:
                            fresh_err = f"engine start failed after kite_login: {msg}"
                except Exception as e:
                    # Capture FULL exception including type
                    fresh_err = f"{type(e).__name__}: {str(e)[:200]}"
                    print(f"[SELFHEAL] fresh-login path failed: {fresh_err}")

            duration_ms = int((time.time() - attempt_start) * 1000)

            # ── Log + notify ──
            if council_storage:
                try:
                    # NEW: capture SPECIFIC error messages so we can debug
                    # without reading Render logs
                    if recovered:
                        err_msg = None
                    else:
                        err_msg = (
                            f"CACHED[{cached_err or 'unknown'}] | "
                            f"FRESH[{fresh_err or 'unknown'}]"
                        )[:500]
                    council_storage.log_autologin_attempt(
                        trigger_source="self_heal",
                        status="success" if recovered else "failed",
                        error=err_msg,
                        duration_ms=duration_ms,
                        extra={"method": recovery_method} if recovered else None,
                    )
                except Exception:
                    pass

            if telegram_alerts:
                try:
                    if recovered:
                        telegram_alerts.alert_engine_recovered()
                    else:
                        telegram_alerts.alert_engine_down(
                            f"Self-heal FAILED — cached + fresh both failed. Manual login required."
                        )
                except Exception:
                    pass

            _t.sleep(_adaptive_interval(_ist_now()))

        except Exception as e:
            print(f"[SELFHEAL] Outer loop error: {e}")
            _t.sleep(60)


def _eod_telegram_daemon():
    """Background thread — sends daily EOD report at 15:30 IST (after market close).

    2026-06-11: User requested "3pm report har din ki date wise ho".
    Logic:
      - Sleep until next 15:30 IST
      - On weekdays: fetch today's report, format, send to Telegram
      - On weekends: skip
      - Loop forever
    """
    import time as _t
    from datetime import datetime as _dt, timedelta as _td
    import pytz as _pytz

    IST = _pytz.timezone("Asia/Kolkata")

    def _next_run_time():
        """Returns next 15:30 IST (today if before, tomorrow if after)."""
        now = _dt.now(IST)
        target = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now >= target:
            target = target + _td(days=1)
        return target

    def _format_summary(report):
        """Builds telegram-friendly markdown summary from daily-report data."""
        try:
            date = report.get("date", "?")
            s = report.get("scalper", {})
            m = report.get("main", {})
            c = report.get("combined", {})

            def _emj(pnl):
                if pnl > 5000: return "🟢"
                if pnl > 0: return "🟢"
                if pnl > -5000: return "🟡"
                return "🔴"

            tot_pnl = c.get("total_pnl", 0)
            lines = [
                f"📊 *EOD Report — {date}*",
                f"",
                f"{_emj(tot_pnl)} *Combined: ₹{tot_pnl:+,.0f}*",
                f"  • Scalper: ₹{c.get('scalper_pnl', 0):+,.0f} ({s.get('n', 0)} trades, WR {s.get('win_rate', 0):.0f}%)",
                f"  • Main:    ₹{c.get('main_pnl', 0):+,.0f} ({m.get('n', 0)} trades, WR {m.get('win_rate', 0):.0f}%)",
                f"",
            ]

            # Top trades
            tw = s.get("top_winner") or m.get("top_winner")
            tl = s.get("top_loser") or m.get("top_loser")

            # Pick the bigger overall winner/loser
            all_winners = [x for x in [s.get("top_winner"), m.get("top_winner")] if x]
            all_losers = [x for x in [s.get("top_loser"), m.get("top_loser")] if x]
            if all_winners:
                tw = max(all_winners, key=lambda x: x["pnl"])
                lines.append(f"⭐ *Top Win:* {tw['idx']} {tw['action']} {tw['strike']}")
                lines.append(f"   ₹{tw['pnl']:+,.0f} (peak {tw['peak_pct']:+.1f}%, {tw['time']})")
            if all_losers:
                tl = min(all_losers, key=lambda x: x["pnl"])
                lines.append(f"💀 *Top Loss:* {tl['idx']} {tl['action']} {tl['strike']}")
                lines.append(f"   ₹{tl['pnl']:+,.0f} (peak {tl['peak_pct']:+.1f}%, {tl['time']})")

            lines.append("")

            # Status breakdown (top 4)
            all_status = {}
            for tab_data in [s, m]:
                for status, sd in (tab_data.get("trades_by_status") or {}).items():
                    if status not in all_status:
                        all_status[status] = {"n": 0, "pnl": 0}
                    all_status[status]["n"] += sd["n"]
                    all_status[status]["pnl"] += sd["pnl"]

            if all_status:
                lines.append("*Exits:*")
                sorted_st = sorted(all_status.items(), key=lambda x: -abs(x[1]["pnl"]))[:5]
                for status, sd in sorted_st:
                    pnl_str = f"₹{sd['pnl']:+,.0f}"
                    lines.append(f"  • {status}: {sd['n']}× → {pnl_str}")

            # New rules effectiveness
            rules_total = {}
            for tab_data in [s, m]:
                for r, count in (tab_data.get("new_rules_fired") or {}).items():
                    rules_total[r] = rules_total.get(r, 0) + count
            fired = {k: v for k, v in rules_total.items() if v > 0}
            if fired:
                lines.append("")
                lines.append("*Damage Control Fired:*")
                for r, count in sorted(fired.items(), key=lambda x: -x[1]):
                    lines.append(f"  • {r}: {count}×")

            return "\n".join(lines)
        except Exception as e:
            return f"📊 EOD Report — error formatting: {e}"

    def _is_market_day():
        """Mon-Fri only."""
        return _dt.now(IST).weekday() < 5

    print("[EOD-TG] daemon started, will fire at 15:30 IST each market day")
    while True:
        try:
            next_run = _next_run_time()
            sleep_sec = (next_run - _dt.now(IST)).total_seconds()
            sleep_sec = max(sleep_sec, 60)  # min 60s
            print(f"[EOD-TG] sleeping {sleep_sec:.0f}s until {next_run.strftime('%Y-%m-%d %H:%M')} IST")
            _t.sleep(sleep_sec)

            if not _is_market_day():
                print("[EOD-TG] weekend, skipping")
                continue

            # Fetch today's report
            from datetime import datetime as _dt2
            today = _dt2.now(IST).strftime("%Y-%m-%d")

            # Call the daily-report endpoint logic directly
            # (avoids HTTP self-call)
            try:
                # Use the local function we built
                import asyncio
                report = asyncio.run(admin_daily_report(date=today))
            except Exception as ee:
                print(f"[EOD-TG] report fetch error: {ee}")
                continue

            # Format + send
            try:
                import telegram_alerts as _tg
                if _tg.is_enabled():
                    msg = _format_summary(report)
                    _tg.send(msg, key=f"eod_{today}")
                    print(f"[EOD-TG] sent for {today}")
                else:
                    print(f"[EOD-TG] telegram not configured, skipped")
            except Exception as ee:
                print(f"[EOD-TG] send error: {ee}")

        except Exception as e:
            print(f"[EOD-TG] loop error: {e}")
            _t.sleep(60)


def _autologin_daemon():
    """Background thread — refreshes Kite token at 08:50-09:00 IST weekdays.

    DESIGN (hardened — 2026-05-17 final):
      Window: 08:50 - 09:00 IST (10 min). 30s retry = ~20 attempts.
      Tight window chosen over wide because:
        • 20 attempts is plenty for transient Kite blip recovery.
        • TOTP rotates every 30s — wider window doesn't add value.
        • Excessive login attempts risk Kite anti-bot detection.
        • Fail-fast + Telegram alert + manual login is better than
          spamming Kite for hours.

      Success: log to DB + Telegram alert + sleep 30 min past window.

      Failure mode escalation:
        attempt 1-2 failures → silent (transient blip likely)
        attempt 3-6 failures → Telegram WARN per attempt
        attempt = 7         → Telegram CRITICAL ("manual login NOW")

      EVERY attempt logged to council.db `auto_login_attempts` table
      so we can audit success rates without reading Render logs.

      Crash safety: outer try/except never lets the loop die.

      Defense in depth — this is just ONE of 6 reliability layers:
        Layer 2: GitHub Actions cron fires independently
        Layer 3: Self-heal monitor during market hours
        Layer 6: Emergency endpoint for one-click recovery
    """
    import time as _t

    required = [
        "KITE_USER_ID", "KITE_PASSWORD", "KITE_TOTP_SECRET",
        "KITE_API_KEY", "KITE_API_SECRET",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"[AUTOLOGIN-DAEMON] DISABLED — missing env vars: {missing}")
        print(f"[AUTOLOGIN-DAEMON] Set them on Render to enable in-process auto-login.")
        # CRITICAL Telegram alert — without env vars the daemon is silently
        # dead. Used to manifest as "engine never started" with no clear
        # cause until reading Render logs. Now you get a Telegram ping
        # immediately on container start.
        try:
            import telegram_alerts
            if telegram_alerts.is_enabled():
                telegram_alerts.send(
                    f"🚨 AUTOLOGIN DISABLED — missing env vars on Render:\n"
                    f"  {', '.join(missing)}\n\n"
                    f"Set these in Render dashboard → Settings → Environment\n"
                    f"Then restart container to activate daemon.",
                    key="autologin_env_missing",  # throttled — 1/hour
                )
        except Exception as _e:
            print(f"[AUTOLOGIN-DAEMON] env-missing telegram alert failed: {_e}")
        return

    try:
        import auto_login as al
    except Exception as e:
        print(f"[AUTOLOGIN-DAEMON] DISABLED — import auto_login failed: {e}")
        return

    # Lazy imports — keep daemon importable even if these aren't installed
    try:
        from council import storage as council_storage
    except Exception:
        council_storage = None
    try:
        import telegram_alerts
    except Exception:
        telegram_alerts = None

    # Window: 08:50 - 09:15 IST. 25 min covers daemon retries + buffer
    # before market open (09:15). At 30s retry = ~50 attempts max,
    # which gives the system extra room to recover from transient
    # Kite blips while still being short enough to avoid anti-bot
    # detection. Last attempt fires right at market open as final
    # chance for engine to come up before user actually needs to trade.
    WIN_START_HOUR_MIN = (8, 50)   # 08:50 IST inclusive
    WIN_END_HOUR_MIN = (9, 15)     # 09:15 IST inclusive (market open)

    RETRY_INTERVAL_SEC = 30        # within-window retry cadence
    CRITICAL_AFTER_ATTEMPTS = 10   # send 🆘 alert after this many fails

    def _in_window(now) -> bool:
        n = now.hour * 60 + now.minute
        a = WIN_START_HOUR_MIN[0] * 60 + WIN_START_HOUR_MIN[1]
        b = WIN_END_HOUR_MIN[0] * 60 + WIN_END_HOUR_MIN[1]
        return a <= n <= b

    print(
        f"[AUTOLOGIN-DAEMON] Started — will refresh Kite token "
        f"{WIN_START_HOUR_MIN[0]:02d}:{WIN_START_HOUR_MIN[1]:02d}-"
        f"{WIN_END_HOUR_MIN[0]:02d}:{WIN_END_HOUR_MIN[1]:02d} IST, Mon-Fri. "
        f"Retry every {RETRY_INTERVAL_SEC}s."
    )
    token_cache = _data_dir / "access_token.json"

    # Per-day attempt counter — resets when we successfully log in
    daily_attempt_count = 0
    last_attempt_date = None

    while True:
        try:
            now = al.ist_now()
            today_str = now.strftime("%Y-%m-%d")

            # New day → reset attempt counter
            if last_attempt_date != today_str:
                daily_attempt_count = 0
                last_attempt_date = today_str

            # Weekend — no markets, no need to refresh
            if now.weekday() >= 5:
                _t.sleep(3600)
                continue

            # Outside login window — check if container just restarted mid-day
            # with no live token. In that case fire login NOW (don't wait till
            # tomorrow's 8:50 window). Common when Render restarts container
            # at 11 AM or 2 PM — daemon would otherwise sleep until tomorrow.
            if not _in_window(now):
                # Market-hours check: 9:15 to 15:30 IST
                market_hours = (
                    (now.hour == 9 and now.minute >= 15) or
                    (10 <= now.hour <= 14) or
                    (now.hour == 15 and now.minute <= 30)
                )
                # Force-fire condition: in market hours + engine dead + no fresh token
                force_fire = False
                if market_hours and (engine is None or not getattr(engine, "running", False)):
                    if token_cache.exists():
                        try:
                            cached = json.loads(token_cache.read_text())
                            if cached.get("date") != today_str:
                                force_fire = True  # token from previous day
                        except Exception:
                            force_fire = True
                    else:
                        force_fire = True  # no token at all
                if not force_fire:
                    _t.sleep(20)
                    continue
                # Fall through to attempt (outside normal window)
                print(
                    f"[AUTOLOGIN-DAEMON] FORCE-FIRE: outside 8:50 window but "
                    f"market-hours + engine dead + no fresh token. Attempting login."
                )

            # Already logged in today? Verify engine is actually running.
            # OLD BUG: daemon skipped retry if cache.date == today, even
            # when engine had failed to start — leaving a "logged in but
            # dead" zombie state till self-heal. Now we ALSO check that
            # the engine is alive; a dead engine forces a fresh attempt.
            if token_cache.exists():
                try:
                    cached = json.loads(token_cache.read_text())
                    if cached.get("date") == today_str:
                        if engine is not None and getattr(engine, "running", False):
                            _t.sleep(60)
                            continue
                        # Today's token but no live engine — retry.
                        print(
                            f"[AUTOLOGIN-DAEMON] Today's token cached but engine NOT "
                            f"running — forcing fresh attempt #{daily_attempt_count + 1}"
                        )
                except Exception:
                    pass

            # ── Attempt ──
            daily_attempt_count += 1
            attempt_start = time.time()
            print(
                f"[AUTOLOGIN-DAEMON] Attempt #{daily_attempt_count} at "
                f"{now.strftime('%H:%M:%S')} IST"
            )

            try:
                access_token = al.kite_login()
                api_key = os.environ["KITE_API_KEY"]
                api_secret = os.environ.get("KITE_API_SECRET", "")
                ok, msg = _start_engine_with_token(api_key, access_token, api_secret)
                duration_ms = int((time.time() - attempt_start) * 1000)

                if ok:
                    print(f"[AUTOLOGIN-DAEMON] SUCCESS — {msg} ({duration_ms}ms)")
                    if council_storage:
                        try:
                            council_storage.log_autologin_attempt(
                                trigger_source="daemon",
                                status="success",
                                access_token_preview=access_token[:8] if access_token else "",
                                duration_ms=duration_ms,
                                extra={"attempt": daily_attempt_count},
                            )
                        except Exception as e:
                            print(f"[AUTOLOGIN-DAEMON] status log failed: {e}")
                    if telegram_alerts:
                        try:
                            telegram_alerts.alert_engine_started(
                                source="daemon",
                                token_preview=access_token[:8] if access_token else "",
                            )
                        except Exception as e:
                            print(f"[AUTOLOGIN-DAEMON] telegram alert failed: {e}")
                    _t.sleep(1800)  # 30 min — skip past window + warm-up
                else:
                    print(f"[AUTOLOGIN-DAEMON] Engine start failed: {msg}")
                    if council_storage:
                        try:
                            council_storage.log_autologin_attempt(
                                trigger_source="daemon",
                                status="failed",
                                error=f"engine_start_failed: {msg}",
                                duration_ms=duration_ms,
                                extra={"attempt": daily_attempt_count},
                            )
                        except Exception:
                            pass
                    if telegram_alerts and daily_attempt_count >= 3:
                        try:
                            telegram_alerts.alert_autologin_failed(
                                error=f"engine_start: {msg}",
                                attempt=daily_attempt_count,
                            )
                        except Exception:
                            pass
                    _t.sleep(RETRY_INTERVAL_SEC)
            except Exception as e:
                err_str = str(e)
                duration_ms = int((time.time() - attempt_start) * 1000)
                print(f"[AUTOLOGIN-DAEMON] Login failed: {err_str} ({duration_ms}ms)")
                if council_storage:
                    try:
                        council_storage.log_autologin_attempt(
                            trigger_source="daemon",
                            status="failed",
                            error=err_str[:500],
                            duration_ms=duration_ms,
                            extra={"attempt": daily_attempt_count},
                        )
                    except Exception:
                        pass
                # Telegram escalation (tuned for 10-min window with 30s retry):
                #   1-2 fails: silent (transient blip)
                #   3-6 fails: WARN per attempt
                #   7+ fails:  CRITICAL once ("manual login needed NOW")
                if telegram_alerts:
                    try:
                        if daily_attempt_count == CRITICAL_AFTER_ATTEMPTS:
                            telegram_alerts.alert_autologin_critical()
                        elif 3 <= daily_attempt_count < CRITICAL_AFTER_ATTEMPTS:
                            telegram_alerts.alert_autologin_failed(
                                error=err_str,
                                attempt=daily_attempt_count,
                            )
                    except Exception:
                        pass
                _t.sleep(RETRY_INTERVAL_SEC)

        except Exception as e:
            print(f"[AUTOLOGIN-DAEMON] Outer loop error: {e}")
            _t.sleep(60)


app = FastAPI(title="UNIVERSE Backend", lifespan=lifespan)

# GZip compression — 60-80% bandwidth reduction on JSON responses
# Threshold 500 bytes — don't compress tiny responses
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=5)

# CORS — Phase 1 (Vercel migration prep):
# Frontend will be on Vercel (vercel.app domain) but API stays on Render.
# Need explicit origin allowlist + regex for vercel preview branches.
# allow_credentials=True + allow_origins=["*"] is invalid per spec —
# we use regex pattern instead for production safety.
import os as _os_cors
_extra_origins = _os_cors.getenv("ALLOWED_ORIGINS", "").split(",")
_extra_origins = [o.strip() for o in _extra_origins if o.strip()]

_allowed_origins = [
    "https://universe-dashboard.onrender.com",   # current prod
    "http://localhost:5173",                     # vite dev
    "http://localhost:3000",                     # alt dev
    "http://localhost:4173",                     # vite preview
    *_extra_origins,                             # custom domains
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    # Regex matches Vercel preview branches (any.vercel.app subdomain)
    # plus the user's eventual custom domain pattern
    allow_origin_regex=r"https://([a-z0-9-]+\.)?(vercel\.app|netlify\.app|onrender\.com)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "X-RateLimit-Remaining"],
)


# Cache-Control headers for static assets (1-year cache for hashed JS/CSS)
@app.middleware("http")
async def add_cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    # Hashed assets (Vite generates index-XXXX.js, index-XXXX.css with content hash)
    if path.startswith("/assets/") and ("-" in path.split("/")[-1]):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    # HTML — short cache, must revalidate (so users get updates)
    elif path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "public, max-age=60, must-revalidate"
    # API responses — no cache (FastAPI handles this internally per endpoint)
    elif path.startswith("/api/"):
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

# Trinity Engine routes
try:
    from trinity import api_routes as trinity_routes
    app.include_router(trinity_routes.router)
    app.include_router(trinity_routes.ws_router)
    print("[TRINITY] API routes mounted")
except Exception as _e:
    print(f"[TRINITY] route mount failed: {_e}")


def get_frontend_url(request: Request) -> str:
    """Get frontend URL — same origin in prod, localhost in dev."""
    if FRONTEND_URL:
        return FRONTEND_URL
    if IS_PROD:
        return str(request.base_url).rstrip("/")
    return "http://localhost:5174"


# ── Auth Routes ──────────────────────────────────────────────────────────

@app.post("/api/login")
async def login(body: dict):
    api_key = body.get("api_key", "").strip()
    api_secret = body.get("api_secret", "").strip()

    if not api_key or not api_secret:
        return JSONResponse({"error": "API key and secret required"}, status_code=400)

    session["api_key"] = api_key
    session["api_secret"] = api_secret

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    return {"login_url": login_url}


@app.post("/api/auto-login")
async def auto_login(request: Request):
    """Auto-login using cached access_token from auto_login.py daemon.
    Accepts either:
    - POST body with {api_key, access_token, api_secret} — for remote AWS daemon
    - OR reads from cached token file on Render filesystem
    """
    global engine
    api_key = ""
    access_token = ""
    api_secret = ""

    # Try body first (remote daemon pushes credentials directly)
    try:
        body = await request.json()
        api_key = body.get("api_key", "")
        access_token = body.get("access_token", "")
        api_secret = body.get("api_secret", "")
    except Exception:
        body = {}

    # Fallback to cached token file
    token_file = _data_dir / "access_token.json"
    if not api_key and token_file.exists():
        try:
            token_data = json.loads(token_file.read_text())
            api_key = token_data.get("api_key", "")
            access_token = token_data.get("access_token", "")
            api_secret = token_data.get("api_secret", "")
        except Exception as e:
            return JSONResponse({"error": f"Invalid token cache: {e}"}, status_code=400)

    if not api_key or not access_token:
        return JSONResponse({"error": "No credentials provided (body or cache)"}, status_code=400)

    ok, msg = _start_engine_with_token(api_key, access_token, api_secret)
    if ok:
        print(f"[AUTO-LOGIN] {msg}")
        return {"status": "success", "message": "Auto-login successful, engine started"}
    print(f"[AUTO-LOGIN] Failed: {msg}")
    return JSONResponse({"error": msg}, status_code=500)


@app.get("/api/callback")
async def callback(request: Request, request_token: str = Query(...), status: str = Query("success")):
    global engine
    fe_url = get_frontend_url(request)

    if status != "success":
        return RedirectResponse(f"{fe_url}/?auth=failed")

    if not session["api_key"] or not session["api_secret"]:
        return RedirectResponse(f"{fe_url}/?auth=failed&reason=no_credentials")

    try:
        kite = KiteConnect(api_key=session["api_key"])
        data = kite.generate_session(request_token, api_secret=session["api_secret"])
        access_token = data["access_token"]

        session["access_token"] = access_token
        session["kite"] = kite
        kite.set_access_token(access_token)

        print(f"[AUTH] Login successful. Access token: {access_token[:8]}...")

        # Persist access token to /data so engine auto-resumes after redeploys
        try:
            token_file = _data_dir / "access_token.json"
            token_file.write_text(json.dumps({
                "api_key": session["api_key"],
                "access_token": access_token,
                "api_secret": session["api_secret"],
                "saved_at": __import__("time").time(),
            }))
            print(f"[AUTH] Saved access_token.json for auto-resume on next deploy")
        except Exception as e:
            print(f"[AUTH] Could not save token cache (auto-resume disabled): {e}")

        # Fetch NSE holidays from Kite API (auto-cache for the year)
        try:
            from trade_logger import save_nse_holidays_from_kite
            save_nse_holidays_from_kite(kite)
        except Exception as e:
            print(f"[AUTH] Holiday fetch failed (using fallback): {e}")

        engine = MarketEngine(
            api_key=session["api_key"],
            access_token=access_token,
            loop=event_loop,
        )
        engine.start()
        try:
            from trinity import api_routes as _tr
            _tr.attach_engine(engine)
        except Exception as _e:
            print(f"[TRINITY] attach_engine failed: {_e}")

        return RedirectResponse(f"{fe_url}/?auth=success")

    except Exception as e:
        print(f"[AUTH] Login failed: {e}")
        return RedirectResponse(f"{fe_url}/?auth=failed&reason={str(e)}")


@app.get("/api/status")
async def get_status():
    has_cache = get_cached("live") is not None
    return {
        "authenticated": session["access_token"] is not None or has_cache,
        "engine_running": engine is not None and engine.running,
        "has_cached_data": has_cache,
        "api_key": session["api_key"][:4] + "****" if session["api_key"] else None,
    }


@app.get("/api/ws/health")
async def ws_health():
    """WebSocket health snapshot — used by watchdog telemetry + external
    monitoring (e.g., Uptime Robot can alert on `is_stale: true`).

    Returns:
      running               engine.running flag
      is_market_hours       9:15-15:30 IST weekday?
      ticker_exists         WS ticker object alive?
      last_tick_age_sec     seconds since last tick (None if never)
      is_stale              true if no tick in 60+ sec during market hours
      watchdog_active       watchdog thread running?
      now_ist               current IST timestamp
    """
    if engine is None:
        return {
            "running": False,
            "is_market_hours": None,
            "ticker_exists": False,
            "last_tick_age_sec": None,
            "is_stale": True,
            "watchdog_active": False,
            "now_ist": None,
            "error": "engine not initialized",
        }
    try:
        return engine.get_ws_health()
    except Exception as e:
        return {"error": str(e), "running": False, "is_stale": True}


@app.post("/api/ws/force-reconnect")
async def ws_force_reconnect():
    """Manually trigger WebSocket reconnect (admin/debug endpoint).
    Same logic as watchdog auto-reconnect but on-demand.
    """
    if engine is None:
        return JSONResponse({"error": "engine not initialized"}, status_code=400)
    try:
        engine._restart_ticker()
        return {"status": "success", "message": "Ticker restart triggered"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/ws/contract")
async def ws_contract_snapshot():
    """WS health contract — runs all invariants and returns pass/fail per check.

    Used by:
      • Manual debugging ("kya thik hai abhi?")
      • External uptime monitors (alert on ok=false)
      • Pre-deploy sanity checks (CI smoke test)

    The contract layer (ws_contract.py) is immutable infrastructure: it
    defines what "WS healthy" means in ONE place so future upgrades can
    never silently regress the tick path.
    """
    try:
        import ws_contract as _wsc
        return _wsc.snapshot(engine)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/ws/contract/run")
async def ws_contract_run_now():
    """Force a contract check NOW (also fires Telegram alert on failure).
    Useful after a manual change to verify nothing broke without waiting
    for the 30s scheduled smoke test.
    """
    if engine is None:
        return JSONResponse({"ok": False, "error": "engine is None"}, status_code=400)
    try:
        import ws_contract as _wsc
        return _wsc.assert_healthy_or_alert(engine, context="manual_run")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/cache/stats")
async def cache_stats_endpoint():
    """API cache snapshot — used to verify the populator is working.
    Returns total keys + age of each cache entry. If populator is healthy,
    most ages should be <5 seconds.
    """
    try:
        return cache_stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Council API (Phase 1 — observe-only) ─────────────────────────────
#
# Read-only endpoints exposing the new "smart mind" decision layer.
# In Phase 1, council observes but does NOT influence trades. These
# endpoints let the operator + frontend inspect council reasoning.

@app.get("/api/council/health")
async def council_health():
    """Council module health snapshot — is it enabled? DB initialized?
    When was the last verdict?"""
    try:
        from council.observer import get_observer_health
        return get_observer_health()
    except Exception as e:
        return JSONResponse({"error": str(e), "enabled": False}, status_code=500)


@app.get("/api/council/current")
async def council_current():
    """Latest council verdict with full vote breakdown.

    Response:
      {
        "pulse_id": "nifty_1715634000123",
        "timestamp": "2026-05-13T09:15:23",
        "direction": "STRONG_BEARISH",
        "confidence": 0.78,
        "action": "ALLOW_ENTRY",
        "bull_strength": 8.0,
        "bear_strength": 42.0,
        "neutral_count": 1,
        "dissent_pct": 0.11,
        "reasoning": "Strong bearish — bear 42.0 vs bull 8.0 (5.2x dominance).",
        "votes": [ {engine, direction, conviction, reasoning, ...}, ... ]
      }
    """
    try:
        from council import storage
        latest = storage.get_latest_verdict()
        if not latest:
            return {"verdict": None, "message": "No council verdicts recorded yet"}
        return {"verdict": latest}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/council/history")
async def council_history(limit: int = 100):
    """Recent N council verdicts (without vote details — use /current for that).

    Limit clamped 1-500. Default 100.
    """
    try:
        from council import storage
        verdicts = storage.get_recent_verdicts(limit=limit)
        return {"count": len(verdicts), "verdicts": verdicts}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/council/stats")
async def council_stats(days: int = 1):
    """Aggregated council stats over last N days.

    Returns counts by direction (STRONG_BULLISH, LEANING_BEAR, MIXED, etc).
    Useful for "today the council leaned mostly X" type observations.
    """
    try:
        from council import storage
        return storage.summary_stats(days=days)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/council/engines")
async def council_engines(engine: Optional[str] = None):
    """Per-engine vote distribution. If `engine` query param supplied,
    returns just that engine's stats; else all engines.
    """
    try:
        from council import storage
        rows = storage.get_engine_stats(engine=engine)
        return {"count": len(rows), "stats": rows}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Auto-login status + diagnostics ──────────────────────────────────

@app.get("/api/auto-login/status")
async def autologin_status(limit: int = 50):
    """Recent auto-login attempts (daemon + manual + cron + self-heal).

    Use this to see, without reading Render logs, exactly when each
    morning's auto-login fired, which source succeeded, and what
    failed.
    """
    try:
        from council import storage
        attempts = storage.get_recent_autologin_attempts(limit=limit)
        return {"count": len(attempts), "attempts": attempts}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/auto-login/diagnostics")
async def autologin_diagnostics():
    """Live, comprehensive auto-login health snapshot.

    Use this to debug "why is login failing today" without reading Render
    logs. Returns:
      • Required env vars: which are set vs missing (values masked)
      • Daemon thread: alive or dead
      • Login window: currently inside, next window time
      • Token cache: file exists, age, today vs stale
      • Engine: running or not, since when
      • Last attempt: when, source, status, error
      • Recommendations: actionable next steps
    """
    import threading as _th
    from datetime import datetime as _dt
    import pytz as _pytz

    _IST = _pytz.timezone("Asia/Kolkata")
    now = _dt.now(_IST)
    today = now.strftime("%Y-%m-%d")

    # ── Env vars ──
    required = ["KITE_USER_ID", "KITE_PASSWORD", "KITE_TOTP_SECRET",
                "KITE_API_KEY", "KITE_API_SECRET"]
    optional = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    env_vars = {}
    for k in required + optional:
        v = os.environ.get(k, "")
        if v:
            mask = v[:4] + "..." + v[-2:] if len(v) > 8 else "***"
            env_vars[k] = {"set": True, "preview": mask}
        else:
            env_vars[k] = {"set": False}
    missing_required = [k for k in required if not os.environ.get(k)]

    # ── Daemon thread ──
    daemon_alive = False
    for t in _th.enumerate():
        if t.name == "autologin-daemon" and t.is_alive():
            daemon_alive = True
            break

    # ── Login window ──
    now_minutes = now.hour * 60 + now.minute
    win_start = 8 * 60 + 50
    win_end = 9 * 60 + 15
    in_window = win_start <= now_minutes <= win_end
    if in_window:
        next_window_in_sec = 0
    elif now_minutes < win_start:
        next_window_in_sec = (win_start - now_minutes) * 60
    else:
        # Past window today — next window is tomorrow 08:50
        tomorrow_window = (24 * 60 - now_minutes) + win_start
        next_window_in_sec = tomorrow_window * 60

    # ── Token cache file ──
    token_file = _data_dir / "access_token.json"
    cache_info = {"exists": False}
    if token_file.exists():
        try:
            data = json.loads(token_file.read_text())
            cache_info = {
                "exists": True,
                "date": data.get("date"),
                "is_today": data.get("date") == today,
                "login_time": data.get("login_time"),
                "token_preview": (data.get("access_token", "")[:8] + "...") if data.get("access_token") else "",
            }
        except Exception as e:
            cache_info = {"exists": True, "error": str(e)}

    # ── Engine state ──
    engine_info = {"running": False}
    if engine is not None:
        engine_info = {
            "running": bool(getattr(engine, "running", False)),
            "has_ticker": bool(getattr(engine, "ticker", None)),
            "spot_tokens": list(getattr(engine, "spot_tokens", {}).keys()),
        }

    # ── Last attempts (last 5) ──
    last_attempts = []
    try:
        from council import storage
        last_attempts = storage.get_recent_autologin_attempts(limit=5)
    except Exception as e:
        last_attempts = [{"error": str(e)}]

    # ── Recommendations ──
    recommendations = []
    if missing_required:
        recommendations.append({
            "severity": "critical",
            "message": f"Required env vars missing: {missing_required}. Set them on Render → daemon will activate.",
        })
    if not daemon_alive and not missing_required:
        recommendations.append({
            "severity": "critical",
            "message": "Daemon thread not running despite env vars set. Container may need restart.",
        })
    if cache_info.get("exists") and not cache_info.get("is_today"):
        recommendations.append({
            "severity": "info",
            "message": "Cached token is from a previous day. 6 AM reaper or 8:50 daemon will refresh.",
        })
    if cache_info.get("is_today") and not engine_info.get("running"):
        recommendations.append({
            "severity": "warning",
            "message": "Today's token cached but engine NOT running. Engine-start failure — daemon should retry on next cycle.",
        })
    if engine_info.get("running") and cache_info.get("is_today"):
        recommendations.append({
            "severity": "ok",
            "message": "Healthy — today's token + engine running.",
        })
    if not env_vars.get("TELEGRAM_BOT_TOKEN", {}).get("set"):
        recommendations.append({
            "severity": "warning",
            "message": "Telegram alerts disabled — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to receive failure notifications.",
        })

    return {
        "now_ist": now.isoformat(),
        "env_vars": env_vars,
        "missing_required": missing_required,
        "daemon": {"alive": daemon_alive},
        "login_window": {
            "in_window_now": in_window,
            "window": "08:50-09:15 IST",
            "next_window_in_sec": next_window_in_sec,
        },
        "token_cache": cache_info,
        "engine": engine_info,
        "last_attempts": last_attempts,
        "recommendations": recommendations,
    }


@app.get("/api/engine/deep-state")
async def engine_deep_state():
    """Deep engine internal state — for diagnosing 'running but no ticks' bugs.

    Exposes:
      - WebSocket ticker connection state
      - Subscribe tokens count + sample
      - Spot prices loaded (REST initial fetch)
      - Chain data populated counts
      - Last tick timestamp + age
      - Initial data fetch errors if any
    """
    if engine is None:
        return JSONResponse({"error": "engine is None"}, status_code=503)

    try:
        import time as _t
        out = {
            "engine_running": getattr(engine, "running", False),
            "ticker_exists": bool(getattr(engine, "ticker", None)),
            "ticker_is_connected": None,
            "spot_tokens_count": len(getattr(engine, "spot_tokens", {})),
            "spot_tokens_sample": dict(getattr(engine, "spot_tokens", {})),
            "subscribe_tokens_count": len(getattr(engine, "_subscribe_tokens", []) or []),
            "subscribe_tokens_sample": (getattr(engine, "_subscribe_tokens", []) or [])[:5],
            "prices_count": len(getattr(engine, "prices", {})),
            "prices_sample": {
                str(tok): {"ltp": v.get("ltp"), "oi": v.get("oi")}
                for tok, v in list(getattr(engine, "prices", {}).items())[:5]
            },
            "chain_strikes_count": {
                idx: len(getattr(engine, "chains", {}).get(idx, {}))
                for idx in ("NIFTY", "BANKNIFTY")
            },
            "last_tick_time": getattr(engine, "_last_tick_time", None),
            "last_tick_age_sec": None,
            "nearest_expiry": {
                idx: str(getattr(engine, "nearest_expiry", {}).get(idx, ""))
                for idx in ("NIFTY", "BANKNIFTY")
            },
        }
        # Ticker connection check
        try:
            t = getattr(engine, "ticker", None)
            if t is not None:
                # KiteTicker has is_connected() method
                try:
                    out["ticker_is_connected"] = t.is_connected()
                except Exception:
                    # Fallback: check the ws object directly
                    ws = getattr(t, "ws", None)
                    out["ticker_is_connected"] = (
                        ws is not None and getattr(ws, "state", None) == 1
                    )
        except Exception as e:
            out["ticker_check_error"] = str(e)

        # Tick age
        if out["last_tick_time"]:
            out["last_tick_age_sec"] = round(_t.time() - out["last_tick_time"], 1)

        # Spot prices for NIFTY / BANKNIFTY (the key debug data)
        spot_prices = {}
        for idx in ("NIFTY", "BANKNIFTY"):
            tok = getattr(engine, "spot_tokens", {}).get(idx)
            if tok:
                p = getattr(engine, "prices", {}).get(tok, {})
                spot_prices[idx] = {
                    "token": tok,
                    "ltp": p.get("ltp", 0),
                    "has_data": tok in getattr(engine, "prices", {}),
                }
        out["spot_prices"] = spot_prices

        # Try fetching a fresh quote via REST to verify Kite session works
        try:
            kite = getattr(engine, "kite", None)
            if kite is not None:
                test_symbols = ["NSE:NIFTY 50", "NSE:NIFTY BANK"]
                t_start = _t.time()
                test_quotes = kite.quote(test_symbols)
                out["rest_quote_test"] = {
                    "ok": True,
                    "duration_ms": int((_t.time() - t_start) * 1000),
                    "results": {
                        sym: {
                            "last_price": q.get("last_price", 0),
                            "instrument_token": q.get("instrument_token"),
                        }
                        for sym, q in test_quotes.items()
                    },
                }
        except Exception as e:
            out["rest_quote_test"] = {"ok": False, "error": str(e)[:300]}

        return out
    except Exception as e:
        import traceback
        return JSONResponse({
            "error": str(e),
            "traceback": traceback.format_exc()[:1500],
        }, status_code=500)


@app.post("/api/engine/reconnect-ticker")
async def engine_reconnect_ticker():
    """Force the ticker to reconnect + re-subscribe. Use when ticks are
    stale or stuck."""
    if engine is None:
        return JSONResponse({"error": "engine is None"}, status_code=503)
    try:
        # Close existing ticker
        old_ticker = getattr(engine, "ticker", None)
        if old_ticker is not None:
            try:
                old_ticker.close()
            except Exception:
                pass
            engine.ticker = None

        # Re-build subscriptions (in case tokens changed)
        try:
            engine._build_subscriptions()
        except Exception as e:
            return JSONResponse({
                "ok": False,
                "stage": "rebuild_subscriptions",
                "error": str(e),
            }, status_code=500)

        # Re-fetch initial data
        try:
            engine._fetch_initial_data()
        except Exception as e:
            print(f"[RECONNECT] initial fetch failed: {e}")

        # Reconnect ticker
        try:
            engine._connect_ticker()
        except Exception as e:
            return JSONResponse({
                "ok": False,
                "stage": "connect_ticker",
                "error": str(e),
            }, status_code=500)

        return {
            "ok": True,
            "message": "Ticker reconnect initiated",
            "subscribe_tokens": len(getattr(engine, "_subscribe_tokens", []) or []),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/auto-login/force")
async def autologin_force():
    """Force Kite login NOW — bypasses all timing/window checks.

    Use cases:
      • Container restarted mid-day with no fresh token
      • Daemon's 8:50 window passed but engine still dead
      • Manually testing the login flow
      • Recovery after Kite session blip

    Returns detailed result so you can SEE the actual error if it fails
    (vs the generic 'Both paths failed' in self-heal logs).
    """
    import time as _time
    try:
        import auto_login as al
        required = ["KITE_USER_ID", "KITE_PASSWORD", "KITE_TOTP_SECRET",
                    "KITE_API_KEY", "KITE_API_SECRET"]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            return JSONResponse({
                "ok": False,
                "error": f"missing env vars: {missing}",
                "stage": "validation",
            }, status_code=400)

        start = _time.time()
        try:
            access_token = al.kite_login()
        except Exception as e:
            return JSONResponse({
                "ok": False,
                "error": f"{type(e).__name__}: {str(e)}",
                "stage": "kite_login",
                "duration_ms": int((_time.time() - start) * 1000),
            }, status_code=500)

        api_key = os.environ["KITE_API_KEY"]
        api_secret = os.environ.get("KITE_API_SECRET", "")
        ok, msg = _start_engine_with_token(api_key, access_token, api_secret)
        duration_ms = int((_time.time() - start) * 1000)

        if not ok:
            return JSONResponse({
                "ok": False,
                "error": msg,
                "stage": "engine_start",
                "duration_ms": duration_ms,
                "access_token_preview": access_token[:8] if access_token else None,
            }, status_code=500)

        # Log success
        try:
            from council import storage
            storage.log_autologin_attempt(
                trigger_source="force_manual",
                status="success",
                access_token_preview=access_token[:8] if access_token else "",
                duration_ms=duration_ms,
            )
        except Exception:
            pass

        return {
            "ok": True,
            "message": f"Engine started with fresh token {access_token[:8]}…",
            "duration_ms": duration_ms,
            "stage": "complete",
        }
    except Exception as e:
        return JSONResponse({
            "ok": False,
            "error": f"unexpected: {type(e).__name__}: {str(e)}",
            "stage": "outer",
        }, status_code=500)


@app.get("/api/auto-login/summary")
async def autologin_summary(days: int = 7):
    """Aggregated auto-login stats per day per source over last N days.

    Returns a clear picture of:
      - Which days had auto-login attempts at all
      - Which trigger succeeded (daemon / external_cron / manual)
      - Failure counts per day
    """
    try:
        from council import storage
        return storage.get_autologin_summary(days=days)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/auto-login/test-alert")
async def autologin_test_alert():
    """Trigger a Telegram test alert. Use to verify env vars are set
    correctly and the bot can reach you.
    """
    try:
        import telegram_alerts
        if not telegram_alerts.is_enabled():
            return JSONResponse({
                "ok": False,
                "error": "Telegram disabled — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env var missing",
            }, status_code=400)
        ok = telegram_alerts.test_alert()
        return {"ok": ok, "message": "Test alert sent" if ok else "Send failed (see logs)"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/auto-login/telegram-health")
async def telegram_health():
    """Is Telegram alerts wired up? Useful for diagnostics."""
    try:
        import telegram_alerts
        return {
            "enabled": telegram_alerts.is_enabled(),
            "bot_token_set": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
            "chat_id_set": bool(os.getenv("TELEGRAM_CHAT_ID")),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/perf-stats")
async def perf_stats():
    """Latest performance sample. Useful for live monitoring."""
    try:
        from perf_monitor import get_latest_sample
        return get_latest_sample()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/perf-history")
async def perf_history(hours: int = 24, limit: int = 500):
    """Recent perf samples in last N hours.

    Use this when investigating a crash: query the time range around
    the failure to see CPU/memory/threads/disk trends.
    """
    try:
        from perf_monitor import get_history
        rows = get_history(hours=hours, limit=limit)
        return {"count": len(rows), "samples": rows}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/perf-sample-now")
async def perf_sample_now():
    """Force an immediate perf sample (don't wait for 5-min cycle).
    Useful for debugging."""
    try:
        from perf_monitor import take_sample
        return take_sample(lambda: engine)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Probability calibration (empirical historical winrate model) ─────

@app.get("/api/calibration")
async def calibration_table():
    """Return the probability calibration table — historical winrate by
    raw_prob bucket × engine × action.

    Use to see WHEN trades historically win/lose at each probability level.
    The audit on 2026-05-19 found severe non-monotone calibration:
    higher raw_prob frequently = LOWER actual WR. This endpoint exposes
    that data so the frontend can show a "calibrated WR" alongside the
    raw probability for every trade.
    """
    try:
        from calibration import get_table, diagnostics
        return {"diagnostics": diagnostics(), "table": get_table()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/calibration/lookup")
async def calibration_lookup(prob: int, engine: str = "main", action: str = "ALL"):
    """Lookup calibrated winrate + warnings for a specific (prob, engine, action).

    Example: GET /api/calibration/lookup?prob=85&engine=scalper&action=BUY%20PE

    Returns:
      - raw_prob: input echo
      - calibrated_wr: historical smoothed WR (or null if no data)
      - is_inverted: True if a lower raw_prob bucket has higher WR
      - warning: human-readable warning if expectancy negative
    """
    try:
        from calibration import calibrated_wr, expectancy_warning, is_inverted
        return {
            "raw_prob": prob,
            "engine": engine,
            "action": action,
            "calibrated_wr": calibrated_wr(prob, engine, action),
            "is_inverted": is_inverted(prob, engine),
            "warning": expectancy_warning(prob, engine, action),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/journal/trade/{trade_id}")
async def journal_trade_endpoint(trade_id: int):
    """Get complete decision timeline for a single trade.

    Use to debug "why did this trade do what it did?". Returns ENTRY,
    every SL update, partial exits, pyramids, and final EXIT — all in
    chronological order with reasoning.
    """
    try:
        from trade_journal import get_trade_timeline, explain_trade
        return {
            "trade_id": trade_id,
            "timeline": get_trade_timeline(trade_id),
            "explanation": explain_trade(trade_id),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/journal/recent")
async def journal_recent_endpoint(n: int = 50, event_type: Optional[str] = None,
                                  tab: Optional[str] = None):
    """Recent journal events (with optional filters).

    event_type values: ENTRY, SL_UPDATE, PARTIAL_EXIT, PYRAMID_ADD, EXIT,
                       GATE_BLOCKED, REGIME_CHANGE, ALERT
    tab values: MAIN, SCALPER
    """
    try:
        from trade_journal import get_recent_events
        return {
            "limit": n,
            "filters": {"event_type": event_type, "tab": tab},
            "events": get_recent_events(limit=n, event_type=event_type, tab=tab),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/journal/stats")
async def journal_stats_endpoint(days: int = 7):
    """Aggregate journal stats — event counts by type."""
    try:
        from trade_journal import get_stats
        return get_stats(days=days)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/early-move/scan")
async def early_move_scan():
    """Scan ALL early-move detectors right now. Returns active signals.

    Built 2026-05-21 to solve: "system har chiz late kyu samjhta hai?"
    Uses LEADING indicators (premium velocity, cross-asset lead-lag, etc)
    instead of lagging confluence engines.
    """
    try:
        from early_move import premium_velocity, cross_asset
        signals = []

        # Cross-asset signal
        try:
            sig = cross_asset.check_and_log(source="api_scan")
            if sig:
                signals.append(sig)
        except Exception as e:
            signals.append({"error": "cross_asset", "msg": str(e)})

        # Premium velocity — scan all tracked strikes
        try:
            history_sizes = premium_velocity.get_history_size()
            # For each tracked strike, try to detect
            for k in list(history_sizes.keys()):
                parts = k.split("|")
                if len(parts) != 3:
                    continue
                idx, strike_s, side = parts
                try:
                    sig = premium_velocity.check_and_log(
                        idx=idx, strike=int(strike_s), side=side,
                        delta=0.5,  # default ATM delta — caller can refine
                        source="api_scan",
                    )
                    if sig:
                        signals.append(sig)
                except Exception:
                    continue
        except Exception as e:
            signals.append({"error": "premium_velocity", "msg": str(e)})

        return {
            "signals": signals,
            "n_active": len(signals),
            "detectors_status": {
                "premium_velocity_enabled": premium_velocity.is_enabled(),
                "cross_asset_enabled": cross_asset.is_enabled(),
            },
            "tracked_strikes": len(premium_velocity.get_history_size()),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/early-move/status")
async def early_move_status():
    """Detector tracking state."""
    try:
        from early_move import premium_velocity, cross_asset
        return {
            "premium_velocity": {
                "enabled": premium_velocity.is_enabled(),
                "shadow": premium_velocity.is_shadow_enabled(),
                "tracked_keys": list(premium_velocity.get_history_size().keys()),
                "n_keys": len(premium_velocity.get_history_size()),
            },
            "cross_asset": {
                "enabled": cross_asset.is_enabled(),
                "shadow": cross_asset.is_shadow_enabled(),
            },
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/early-move/oi-rotation")
async def early_move_oi_rotation(idx: str = "BANKNIFTY"):
    """OI rotation scan — smart money positioning detector.

    Runs the 5 OI sub-detectors (WALL_BUILD, WALL_COLLAPSE,
    STRIKE_MIGRATION, WRITER_FLIP, UNUSUAL_VELOCITY) on the live
    option chain for the given index.

    Detects institutional positioning 30-45 min before confluence
    engines align — the "OI rotation" early-warning signal.
    """
    try:
        from early_move import oi_rotation
        global engine
        if engine is None:
            return {"error": "engine not running", "signals": []}

        spot_token = engine.spot_tokens.get(idx)
        spot = engine.prices.get(spot_token, {}).get("ltp", 0) if spot_token else 0
        if spot <= 0:
            return {"error": "no spot price", "idx": idx, "signals": []}

        try:
            from engine import INDEX_CONFIG as _IDX_CFG
            cfg = _IDX_CFG.get(idx)
        except Exception:
            cfg = None
        gap = cfg["strike_gap"] if cfg else (100 if idx == "BANKNIFTY" else 50)
        atm = round(spot / gap) * gap
        chain = engine.chains.get(idx, {})

        # Build strikes_data using the helper (uses stored snapshots for change)
        strikes_data = []
        for off in range(-10, 11):
            s = atm + off * gap
            cinfo = chain.get(s, {})
            ce_oi = cinfo.get("ce_oi", 0) or 0
            pe_oi = cinfo.get("pe_oi", 0) or 0
            ce_chg = cinfo.get("ce_oi_change", 0) or 0
            pe_chg = cinfo.get("pe_oi_change", 0) or 0
            strikes_data.append({
                "strike": s, "ce_oi": ce_oi, "pe_oi": pe_oi,
                "ce_change": ce_chg, "pe_change": pe_chg,
            })

        result = oi_rotation.check_and_log(
            idx=idx, spot=spot, strikes_data=strikes_data, source="api",
        )
        result["enabled"] = oi_rotation.is_enabled()
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/early-move/iv-term")
async def early_move_iv_term(idx: str = "BANKNIFTY"):
    """IV term-structure scan — volatility-timing detector.

    Detects IV expansion (move coming), IV crush (don't buy — vega
    against you), and term-structure inversion (imminent volatility).
    """
    try:
        from early_move import iv_term_structure
        result = iv_term_structure.check_and_log(idx=idx, source="api")
        result["enabled"] = iv_term_structure.is_enabled()
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/early-move/volume-profile")
async def early_move_volume_profile(idx: str = "BANKNIFTY"):
    """Volume-profile scan — breakout confirmation detector.

    Detects volume breakouts (real momentum), fakeout warnings (no
    volume behind move), volume exhaustion (move ending), and the
    session's high-volume price nodes (real support/resistance).
    """
    try:
        from early_move import volume_profile
        result = volume_profile.check_and_log(idx=idx, source="api")
        result["enabled"] = volume_profile.is_enabled()
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/early-move/entry-gate")
async def early_move_entry_gate_status():
    """Entry-gate status — how the aggregator is wired into trade firing.

    Modes:
      off   — shadow only (aggregator never affects trades)
      veto  — aggregator can BLOCK trades (crush/fakeout/conflict)
      full  — veto + confirm

    Set via env EARLY_MOVE_ENTRY_MODE.
    """
    try:
        from early_move.entry_gate import diagnostics
        return diagnostics()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/early-move/verdict")
async def early_move_verdict(idx: str = "BANKNIFTY", min_agree: int = 2):
    """AGGREGATOR verdict — combines all 5 leading detectors into ONE decision.

    The aggregator is the "jury": it reads premium_velocity, cross_asset,
    oi_rotation, iv_term_structure, volume_profile signals and produces:

      FIRE      — 2+ detectors agree on direction (early entry)
      NO_TRADE  — not enough agreement
      BLOCKED   — IV crush / fakeout / exhaustion veto

    This is the heart of the leading-indicator system. When 2+ LEADING
    detectors agree, the move is just starting — fire EARLY, ~30-40 min
    before the lagging confluence engines align.
    """
    try:
        from early_move import aggregator
        global engine
        if engine is None:
            return {"verdict": "NO_TRADE", "reason": "engine not running"}
        return aggregator.get_verdict(engine=engine, idx=idx, min_agree=min_agree)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/market-context")
async def market_context_snapshot():
    """200-day chart structure snapshot (per index).
    Used by verdict layer as ADDITIVE bonus (never blocks).
    Returns: trend_200d, S/R zones, ATR, swing counts."""
    try:
        import market_context as _mc
        return _mc.diagnostics()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/market-context/refresh")
async def market_context_refresh():
    """Force-refresh 200-day market context for both indexes.
    Useful after a deploy or when you want fresh data without waiting
    for the 6hr cycle."""
    if engine is None or not session.get("kite"):
        return JSONResponse({"error": "engine/kite not ready"}, status_code=400)
    try:
        import market_context as _mc
        kite = session["kite"]
        out = {}
        for idx in ("NIFTY", "BANKNIFTY"):
            out[idx] = _mc.refresh_context(kite, idx)
        return {"ok": True, "refreshed": out}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/market-context/score")
async def market_context_score(idx: str = "NIFTY", action: str = "BUY CE"):
    """Get the alignment bonus the verdict would receive for a proposed
    (idx, action) right now. Negative bonus = chart contradicts (still
    allowed to trade, just adjusted score). Positive = chart tailwind.
    """
    if engine is None:
        return JSONResponse({"error": "engine not ready"}, status_code=400)
    try:
        import market_context as _mc
        spot = engine.prices.get(engine.spot_tokens.get(idx), {}).get("ltp", 0)
        return {
            "idx": idx,
            "action": action,
            "spot": spot,
            "context": _mc.get_context(idx, spot, action),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── F&O SCANNER ENDPOINTS ──────────────────────────────────────────
@app.get("/api/fno/watchlist")
async def fno_watchlist(top: int = 15):
    """Ranked F&O watchlist: top bullish + top bearish setups for next
    1-3 sessions. Built from latest 08:00 IST scan of ~190 F&O stocks."""
    try:
        import fno_scanner as _fno
        ranked = _fno.ranked_watchlist(top_n=top)
        meta = _fno.latest_scan_meta()
        return {"ok": True, "meta": meta, **ranked}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/fno/stock/{symbol}")
async def fno_stock_detail(symbol: str):
    """Full scan detail for one F&O stock — includes deep analysis."""
    try:
        import fno_scanner as _fno
        detail = _fno.get_stock_detail(symbol)
        if not detail:
            return JSONResponse({"ok": False, "error": f"no data for {symbol}"}, status_code=404)
        # If deep analysis attached, return both summary + deep
        deep = detail.pop("_deep", None) if isinstance(detail, dict) else None
        return {"ok": True, "stock": detail, "deep": deep}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/fno/analyze/{symbol}")
async def fno_analyze_live(symbol: str):
    """Force fresh comprehensive analysis for one symbol (bypasses scan cache).
    Calls stock_analyzer directly. Useful for on-demand deep dive.
    """
    if engine is None or not session.get("kite"):
        return JSONResponse({"ok": False, "error": "engine/kite not ready"}, status_code=400)
    try:
        import fno_universe as _u
        import stock_analyzer as _sa
        kite = session["kite"]
        universe = _u.get_fno_symbols(kite)
        match = next((s for s in universe if s["symbol"].upper() == symbol.upper()), None)
        if not match:
            return JSONResponse({"ok": False, "error": f"symbol {symbol} not in F&O universe"}, status_code=404)
        # Live click-to-detail = full analysis (intraday TFs + futures)
        result = _sa.analyze(kite, match, fast=False)
        if not result:
            return JSONResponse({"ok": False, "error": "analysis failed"}, status_code=500)
        return {"ok": True, "deep": result}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/fno/scan")
async def fno_scan_force(max_symbols: int = 0):
    """Force a full F&O scan now. Useful after deploy or for manual refresh.
    Pass max_symbols=N to limit (for testing). 0 = full universe."""
    if engine is None or not session.get("kite"):
        return JSONResponse({"ok": False, "error": "engine/kite not ready"}, status_code=400)
    try:
        import fno_scanner as _fno
        kite = session["kite"]
        # Run in background so request doesn't block
        import threading
        def _run():
            try:
                _fno.run_full_scan(kite, max_symbols=max_symbols or None)
            except Exception as e:
                print(f"[FNO-SCAN] background scan err: {e}")
        threading.Thread(target=_run, daemon=True, name="fno-scan-manual").start()
        return {"ok": True, "message": "scan started in background — check /api/fno/watchlist in 60-90s"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/admin/check-lot-sizes")
async def check_lot_sizes():
    """Check current NIFTY/BANKNIFTY/FINNIFTY futures lot_size DIRECTLY from
    Kite NFO instruments. Source of truth.
    """
    if not session.get("kite"):
        return JSONResponse({"error": "kite not ready"}, status_code=400)
    try:
        kite = session["kite"]
        instruments = kite.instruments("NFO")
        from datetime import datetime
        import pytz
        IST = pytz.timezone("Asia/Kolkata")
        today = datetime.now(IST).date()

        out = {}
        for name in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"):
            futures = []
            for inst in instruments:
                if (inst.get("instrument_type") == "FUT"
                        and inst.get("name", "").upper() == name):
                    exp = inst.get("expiry")
                    exp_date = exp.date() if hasattr(exp, "date") else exp
                    if exp_date and exp_date >= today:
                        futures.append({
                            "tradingsymbol": inst.get("tradingsymbol"),
                            "expiry": str(exp_date),
                            "lot_size": inst.get("lot_size"),
                            "tick_size": inst.get("tick_size"),
                        })
            futures.sort(key=lambda f: f["expiry"])
            out[name] = futures[:3] if futures else "no_active_futures"

        # Hardcoded values in scalper_mode.log_scalp_trade (line ~1319)
        out["_hardcoded_in_scalper"] = {"NIFTY": 65, "BANKNIFTY": 30}
        out["_now"] = datetime.now(IST).isoformat()
        return out
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()[-800:]}, status_code=500)


@app.get("/api/fno/universe")
async def fno_universe_status():
    """Diagnostic for F&O universe cache."""
    try:
        import fno_universe as _u
        return _u.diagnostics()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/structure/state")
async def structure_state(idx: str = "NIFTY"):
    """Live market-structure verdict per timeframe (Phase 1 — shadow mode).

    Returns HH/HL/LH/LL structure analysis for the given index across
    5-min, 15-min, and 1-hour timeframes, plus Elder's Triple Screen
    alignment verdict. Pulls historical candles from Kite REST API
    (cached 30 min) — needs valid Kite session.

    This is SHADOW endpoint — for visibility only. No trade logic uses
    the verdict yet. Will be wired in Phase 2.

    Query params:
        idx: NIFTY | BANKNIFTY (default NIFTY)
    """
    if not engine or not session.get("kite"):
        return JSONResponse(
            {"error": "Engine or Kite session not ready"}, status_code=400
        )
    if idx not in ("NIFTY", "BANKNIFTY"):
        return JSONResponse(
            {"error": f"unsupported index: {idx}"}, status_code=400
        )
    try:
        import asyncio
        import price_structure as ps
        import historical_loader as hl

        # Offload sync Kite REST calls to thread executor
        def _fetch_all():
            kite = session["kite"]
            return {
                "5m": hl.load_index_history(kite, idx, "5minute", days=2),
                "15m": hl.load_index_history(kite, idx, "15minute", days=2),
                "1h": hl.load_index_history(kite, idx, "60minute", days=5),
            }

        candles_by_tf = await asyncio.to_thread(_fetch_all)

        # Detect structure on each timeframe
        structures = {
            tf: ps.detect_structure(candles)
            for tf, candles in candles_by_tf.items()
        }

        # Multi-TF alignment (Elder's Triple Screen)
        alignment = ps.align_timeframes(structures)

        # Compact response — full swings only on request for payload size
        def _slim(s):
            return {
                "verdict": s["verdict"],
                "confidence": s["confidence"],
                "reason": s["reason"],
                "last_high": s["last_high"],
                "last_low": s["last_low"],
                "prev_high": s["prev_high"],
                "prev_low": s["prev_low"],
                "swing_high_count": len(s["swing_highs"]),
                "swing_low_count": len(s["swing_lows"]),
            }

        result = {
            "idx": idx,
            "structures": {tf: _slim(s) for tf, s in structures.items()},
            "alignment": alignment,
            "candle_counts": {tf: len(c) for tf, c in candles_by_tf.items()},
        }

        # Shadow log — phase 1 visibility
        print(
            f"[STRUCTURE_SHADOW] {idx} "
            f"5m={structures['5m']['verdict']}({structures['5m']['confidence']}) "
            f"15m={structures['15m']['verdict']}({structures['15m']['confidence']}) "
            f"1h={structures['1h']['verdict']}({structures['1h']['confidence']}) "
            f"→ alignment={alignment['direction']}/{alignment['conviction']} "
            f"({alignment['reason']})"
        )

        return result
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/premium-swing/diagnostics")
async def premium_swing_diagnostics():
    """Premium swing detector config snapshot — Phase 5."""
    try:
        import premium_swing_detector as psd
        return psd.diagnostics()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/premium-swing/analyze")
async def premium_swing_analyze(payload: dict):
    """Analyze a list of candles for the day's-first-bottom-reversal pattern.

    POST body:
      {
        "candles": [{"ts":..., "open":..., "high":..., "low":..., "close":..., "volume":...}, ...],
        "side": "bottom" | "top"  (default "bottom")
      }

    Returns the pattern verdict + entry zone + suggested SL/target.
    Useful for off-line backtesting and for the dashboard to query
    "is this strike showing a reversal right now?" given fetched data.
    """
    try:
        import premium_swing_detector as psd
        candles = payload.get("candles") or []
        side = (payload.get("side") or "bottom").lower()
        today_only = bool(payload.get("today_only", False))
        if side == "top":
            return psd.detect_first_top_reversal(candles, today_only=today_only)
        return psd.detect_first_bottom_reversal(candles, today_only=today_only)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/structure/diagnostics")
async def structure_diagnostics():
    """Module config + gate state snapshot.

    Includes: price_structure config, historical_loader cache state, and
    (Phase 2+) structure_gate master mode + per-index cached verdicts.
    """
    try:
        import price_structure as ps
        import historical_loader as hl
        out = {
            "price_structure": ps.diagnostics(),
            "historical_loader": hl.diagnostics(),
        }
        try:
            import structure_gate as sg
            out["structure_gate"] = sg.diagnostics()
        except Exception as e:
            out["structure_gate"] = {"error": str(e)}
        return out
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/scalper/health")
async def scalper_health_status():
    """Adaptive market-health → scalper aggression level.

    The scalper reads market health (regime, VIX, ATR, expiry day,
    recent W/L streak) and picks its own aggression:

      AGGRESSIVE — conditions favour scalping → looser gates, higher cap
      BALANCED   — normal → default settings
      DEFENSIVE  — dead/expiry/spike/losing-streak → tighter, smaller

    Mode via env SCALPER_ADAPTIVE_HEALTH (off / shadow / live). In
    'shadow' the level is computed + logged but not applied; 'live'
    applies the tuning inside should_enter_scalp().
    """
    try:
        import scalper_health
        out = scalper_health.diagnostics()
        global engine
        if engine is not None:
            out["live"] = {
                "NIFTY": scalper_health.assess(engine, "NIFTY"),
                "BANKNIFTY": scalper_health.assess(engine, "BANKNIFTY"),
            }
        else:
            out["live"] = {"note": "engine not running"}
        return out
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/profit-floor/diagnose")
async def profit_floor_diagnose(entry: float, peak: float, current_sl: float):
    """Diagnose what profit floor would set for given entry/peak/SL.

    Use to verify floor logic for any trade scenario:
      GET /api/profit-floor/diagnose?entry=100&peak=110&current_sl=85
    """
    try:
        from profit_floor import diagnose
        return diagnose(entry, peak, current_sl)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/profit-floor/bands")
async def profit_floor_bands():
    """Show the peak-threshold → SL-floor bands."""
    try:
        from profit_floor import PROFIT_FLOOR_BANDS, is_enabled
        return {
            "enabled": is_enabled(),
            "bands": [
                {
                    "peak_threshold_pct": t,
                    "floor_multiplier": m,
                    "locked_pct": round((m - 1) * 100, 1),
                    "rule": f"Peak ≥ +{t}% → SL ≥ entry × {m} ({(m - 1) * 100:+.1f}% locked)",
                }
                for t, m in PROFIT_FLOOR_BANDS
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/aggressive-trail/status")
async def aggressive_trail_status():
    """Current aggressive trail configuration + flag state."""
    try:
        from aggressive_trail import is_enabled, is_shadow_enabled, PEAK_TRAIL_BANDS
        return {
            "enabled": is_enabled(),
            "shadow_logging": is_shadow_enabled(),
            "trail_bands": [
                {"peak_threshold_pct": t, "giveback_pct_from_peak": gb}
                for t, gb in PEAK_TRAIL_BANDS
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/regime-monitor")
async def regime_monitor_endpoint(tab: str = "BOTH", current_days: int = 7, baseline_days: int = 30):
    """Phase 1 DETECTION: regime health check.

    Compares last 7 days of trading vs last 30 days baseline across
    10 KPIs. Fires CRITICAL when 4+ metrics deviate >2σ.

    Use this to spot "today is different" BEFORE losses cascade.
    """
    try:
        from regime_monitor import assess
        return assess(tab=tab, current_days=current_days, baseline_days=baseline_days)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/regime-monitor/quick")
async def regime_monitor_quick():
    """Compact regime health for dashboard widget."""
    try:
        from regime_monitor import quick_status
        return quick_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/pattern-shift")
async def pattern_shift_endpoint(tab: str = "BOTH"):
    """Phase 1 DETECTION: intra-session pattern shift.

    Checks if today's exit-status distribution + losing streak
    indicate regime breakdown happening NOW.
    """
    try:
        from pattern_shift_detector import detect_shifts
        return detect_shifts(tab=tab)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/daily-diagnostic")
async def daily_diagnostic_endpoint(date: Optional[str] = None):
    """Phase 1 DETECTION: full EOD diagnostic report.

    Generates plain-English analysis of the day's trading:
    P&L vs baseline, best/worst trades, what worked, verdict.
    """
    try:
        from daily_diagnostic import generate_report
        return generate_report(date_iso=date)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/anomaly-alerts/check-now")
async def anomaly_check_now():
    """Force an immediate anomaly check + fire alerts if conditions met.

    Useful for manual sanity check during trading. Normally called
    every 15 min by background scheduler.
    """
    try:
        from anomaly_alerts import run_periodic_checks
        return run_periodic_checks()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/anomaly-alerts/eod-report")
async def anomaly_eod_now():
    """Trigger EOD diagnostic + Telegram send NOW (without waiting for 15:35)."""
    try:
        from anomaly_alerts import run_eod_diagnostic
        return run_eod_diagnostic()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/anomaly-alerts/status")
async def anomaly_status():
    """Current state of alert throttling + history."""
    try:
        from anomaly_alerts import get_status
        return get_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/profit-target/status")
async def profit_target_status():
    """Daily profit-target status per tab.

    Returns current P&L vs target, whether target hit, % completion.
    Use for "book win + walk away" dashboard widget.
    """
    try:
        from profit_target import status, is_enabled
        return {
            "enabled": is_enabled(),
            "main": status("MAIN"),
            "scalper": status("SCALPER"),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/circuit-breaker/status")
async def circuit_breaker_status():
    """Current circuit-breaker state per tab (P&L vs limit, streak status).

    Use for the dashboard daily P&L pace bar — shows how close each tab
    is to triggering the breaker.
    """
    try:
        from circuit_breaker import status, is_enabled
        return {
            "enabled": is_enabled(),
            "main": status("MAIN"),
            "scalper": status("SCALPER"),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/engine-correlation")
async def engine_correlation(hours: int = 336, threshold: float = 0.70):
    """Pairwise engine vote agreement analysis (Level-2 refactor data).

    Built 2026-05-21 to answer: which engines actually measure independent
    signals, and which are voting together as correlated noise?

    Returns observed clusters (engines voting together >= threshold) +
    independent engines + a hypothesis comparison.

    Use cases:
      • Identify which engines should be merged into meta-engines
      • Quantify the "11 engines but really 4-5 unique signals" hypothesis
      • Validate consolidation decisions before refactoring

    Args:
      hours:     lookback window (default 14 days)
      threshold: correlation cutoff to be considered "highly correlated" (0.0-1.0)
    """
    try:
        from engine_correlation_analyzer import suggest_consolidation
        return suggest_consolidation(hours=hours, threshold=threshold)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/engine-correlation/raw")
async def engine_correlation_raw(hours: int = 336):
    """Raw pairwise correlation matrix (no clustering).

    Returns every (engine_a, engine_b) pair with agreement_rate +
    counts. Use for detailed inspection of correlations.
    """
    try:
        from engine_correlation_analyzer import compute_pairwise_correlation
        return compute_pairwise_correlation(hours=hours)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/engine-bias")
async def engine_bias(hours: int = 168):
    """Per-engine bull/bear/neutral bias over last N hours.

    Built 2026-05-19 to make the structural bias of oi_flow, price_action,
    and seller_positioning visible to the dashboard. These three engines
    measure correlated aspects of Indian-market PE-write-dominance —
    treat them as ONE signal when all three vote bull, not three.

    Returns:
      • Per-engine: bull / bear / neutral counts, bias_pct, fire_rate
      • flag: STRUCTURAL_BULL | STRUCTURAL_BEAR | BALANCED | RARE | DEAD
      • correlated_bull_cluster: list of engines audit found to overlap
    """
    try:
        from engine_bias_analyzer import compute_engine_bias
        return compute_engine_bias(hours=hours)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/calibration/rebuild")
async def calibration_rebuild():
    """Rebuild the calibration table from current closed-trade history.

    Reads ALL closed trades from trades.db + scalper_trades.db, computes
    smoothed winrates per (engine, action, raw_prob bucket), and writes
    the table to /data/calibration_table.json (which then supersedes the
    built-in v1 fallback).

    Run this:
      • Weekly (more data = better calibration)
      • After regime changes (different market behaviour)
      • Never DURING trading (writes to disk; brief lock)
    """
    try:
        from calibration_builder import rebuild_from_db
        return rebuild_from_db()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/trinity/db-stats")
async def trinity_db_stats():
    """Trinity DB stats — file size, row counts per table, oldest/newest data.
    Use to verify pruning is keeping the DB lean.
    """
    try:
        from trinity.prune import get_trinity_db_stats
        return get_trinity_db_stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/trinity/prune-now")
async def trinity_prune_now(
    raw_ticks_keep_days: int = 7,
    strike_data_keep_days: int = 14,
):
    """Manually run trinity.db prune. Useful for emergency disk cleanup
    OR for first-time run to reclaim space immediately.

    Query params:
      raw_ticks_keep_days     (default 7) — older trinity_ticks deleted
      strike_data_keep_days   (default 14) — older trinity_strike_data deleted

    Returns size before/after, rows deleted, duration.
    """
    try:
        from trinity.prune import prune_trinity_db
        result = prune_trinity_db(
            raw_ticks_keep_days=raw_ticks_keep_days,
            strike_data_keep_days=strike_data_keep_days,
        )
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/health/send-report")
async def health_send_report():
    """Trigger the health monitor's 30-min report immediately.
    Useful for testing the Telegram message format without waiting.
    Returns the message text + Telegram send result.
    """
    try:
        from health_monitor import build_health_report
        import telegram_alerts
        msg = build_health_report(engine)
        ok = False
        if telegram_alerts.is_enabled():
            ok = telegram_alerts._send_sync(msg)
        return {"ok": ok, "telegram_enabled": telegram_alerts.is_enabled(),
                "message_preview": msg[:1000]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/health/send-eod")
async def health_send_eod():
    """Trigger the EOD summary right now (instead of waiting for 15:35 IST)."""
    try:
        from health_monitor import build_eod_summary
        import telegram_alerts
        msg = build_eod_summary(engine)
        ok = False
        if telegram_alerts.is_enabled():
            ok = telegram_alerts._send_sync(msg)
        return {"ok": ok, "telegram_enabled": telegram_alerts.is_enabled(),
                "message_preview": msg[:1000]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/auto-login/emergency")
async def emergency_login():
    """ONE-CLICK EMERGENCY LOGIN (Layer 6 of bulletproof auto-login).

    Trigger the full Kite login flow server-side using env vars.
    Used when auto-login daemon has failed and user needs the engine
    UP as fast as possible (e.g. market opening in 2 min).

    Returns within 10-30 seconds. No browser OAuth dance needed.

    Logs the attempt with trigger_source="manual" so it shows up in
    /api/auto-login/status alongside daemon attempts.
    """
    import time as _t

    required = ["KITE_USER_ID", "KITE_PASSWORD", "KITE_TOTP_SECRET",
                "KITE_API_KEY", "KITE_API_SECRET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        return JSONResponse({
            "ok": False,
            "error": f"Missing env vars: {missing}",
        }, status_code=400)

    started = _t.time()
    try:
        import auto_login as al
        access_token = al.kite_login()
        api_key = os.environ["KITE_API_KEY"]
        api_secret = os.environ.get("KITE_API_SECRET", "")
        ok, msg = _start_engine_with_token(api_key, access_token, api_secret)
        duration_ms = int((_t.time() - started) * 1000)

        # Log to council DB
        try:
            from council import storage as council_storage
            council_storage.log_autologin_attempt(
                trigger_source="manual",
                status="success" if ok else "failed",
                error=None if ok else f"engine_start: {msg}",
                access_token_preview=access_token[:8] if (ok and access_token) else None,
                duration_ms=duration_ms,
                extra={"path": "emergency_button"},
            )
        except Exception:
            pass

        # Telegram alert
        try:
            import telegram_alerts
            if ok:
                telegram_alerts.alert_engine_started(
                    source="emergency_button",
                    token_preview=access_token[:8] if access_token else "",
                )
            else:
                telegram_alerts.alert_autologin_failed(
                    error=f"emergency button: {msg}", attempt=1,
                )
        except Exception:
            pass

        if ok:
            return {
                "ok": True,
                "message": "Engine started",
                "duration_ms": duration_ms,
                "token_preview": access_token[:8] if access_token else "",
            }
        return JSONResponse({
            "ok": False,
            "error": msg,
            "duration_ms": duration_ms,
        }, status_code=500)

    except Exception as e:
        duration_ms = int((_t.time() - started) * 1000)
        err_str = str(e)
        # Log failure
        try:
            from council import storage as council_storage
            council_storage.log_autologin_attempt(
                trigger_source="manual",
                status="failed",
                error=err_str[:500],
                duration_ms=duration_ms,
                extra={"path": "emergency_button"},
            )
        except Exception:
            pass
        try:
            import telegram_alerts
            telegram_alerts.alert_autologin_failed(
                error=f"emergency button: {err_str}", attempt=1,
            )
        except Exception:
            pass
        return JSONResponse({
            "ok": False,
            "error": err_str,
            "duration_ms": duration_ms,
        }, status_code=500)


@app.post("/api/cache/invalidate/{prefix}")
async def cache_invalidate_endpoint(prefix: str):
    """Force-invalidate cache entries by prefix. Admin/debug only.
    Examples:
      POST /api/cache/invalidate/trades   → drops all trade caches
      POST /api/cache/invalidate/chain    → drops all chain caches
      POST /api/cache/invalidate/         → drops everything (dangerous)
    """
    try:
        count = cache_invalidate_prefix(prefix)
        return {"removed": count, "prefix": prefix}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/db/status")
async def db_migrations_status():
    """Per-database schema-migration status. Used to verify that all DBs
    are at expected schema version after deploy."""
    try:
        from db_migrations import status as _ms
        return _ms()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── EMERGENCY DISK ADMIN (2026-06-04) ─────────────────────────────────
# Root-cause finding: Telegram alerts "Self-heal FAILED — cached + fresh
# both failed" trace to OSError [Errno 28] No space left on device.
# Render persistent disk filled with months of SQLite + WAL + structured
# logs + backups. This blocks ALL writes including token cache writes →
# auto-login can't persist new token → engine never starts.
# These endpoints give visibility + safe targeted cleanup.

@app.get("/api/admin/disk")
async def admin_disk_usage():
    """Per-file breakdown of /data — what's eating the persistent disk.
    Returns total, free, per-file size (top 50 largest).
    """
    import shutil
    from pathlib import Path as _P
    try:
        usage = shutil.disk_usage(str(_data_dir))
        total_mb = usage.total / 1024 / 1024
        free_mb = usage.free / 1024 / 1024
        used_mb = total_mb - free_mb
        pct = (used_mb / total_mb * 100) if total_mb > 0 else 0

        # Recursive size scan
        files = []
        for p in _P(_data_dir).rglob("*"):
            try:
                if p.is_file():
                    files.append({
                        "path": str(p.relative_to(_data_dir)),
                        "size_mb": round(p.stat().st_size / 1024 / 1024, 2),
                        "modified_iso": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                    })
            except Exception:
                continue
        files.sort(key=lambda x: -x["size_mb"])
        return {
            "total_mb": round(total_mb, 1),
            "used_mb": round(used_mb, 1),
            "free_mb": round(free_mb, 1),
            "used_pct": round(pct, 1),
            "is_critical": pct > 95,
            "file_count": len(files),
            "top_50_files": files[:50],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/admin/calibration-audit")
async def admin_calibration_audit(days: int = 60):
    """Pull data to inform the BULL vs BEAR debate (2026-06-11).

    Answers two specific questions:
      1. How many days had loss ≥ ₹15k? (informs DAILY_LOSS_CAP debate)
      2. What's the WR by verdict probability bucket? (informs
         INSTANT_NEW_TRADE_PROB 55 vs 65 debate)

    Run on Render with:
      curl https://<your-app>.onrender.com/api/admin/calibration-audit?days=60
    """
    import sqlite3 as _sql
    from pathlib import Path as _P
    out = {"days_window": days, "scalper": {}, "main": {}, "errors": [], "diagnostics": {}}
    _data_dir = _P("/data") if _P("/data").is_dir() else _P(__file__).parent
    out["diagnostics"]["data_dir"] = str(_data_dir)

    def _diag_db(db_path: str, table: str):
        """Returns {exists, total_rows, closed_rows, columns, status_breakdown}."""
        info = {"path": db_path, "exists": _P(db_path).exists()}
        if not info["exists"]:
            return info
        try:
            c = _sql.connect(db_path)
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
            info["columns"] = cols
            info["total_rows"] = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            info["closed_rows"] = c.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status != 'OPEN'"
            ).fetchone()[0]
            # Status breakdown (KEY DEBUG — status != 'CLOSED' is the bug)
            status_rows = c.execute(
                f"SELECT status, COUNT(*) FROM {table} GROUP BY status ORDER BY 2 DESC"
            ).fetchall()
            info["status_breakdown"] = {r[0]: r[1] for r in status_rows}
            # Sample entry_time format
            time_col = "entry_time" if "entry_time" in cols else (
                "open_time" if "open_time" in cols else None)
            if time_col:
                row = c.execute(
                    f"SELECT {time_col} FROM {table} WHERE status != 'OPEN' "
                    f"ORDER BY id DESC LIMIT 1"
                ).fetchone()
                info["latest_entry_time"] = row[0] if row else None
                info["time_col_used"] = time_col
            # Probability column detection
            for pc in ("probability", "entry_probability", "win_probability"):
                if pc in cols:
                    info["prob_col"] = pc
                    break
            c.close()
        except Exception as e:
            info["error"] = str(e)
        return info

    out["diagnostics"]["scalper_db"] = _diag_db(
        str(_data_dir / "scalper_trades.db"), "scalper_trades")
    out["diagnostics"]["main_db"] = _diag_db(
        str(_data_dir / "trades.db"), "trades")

    def _bucket_query(rows):
        result = []
        for r in rows:
            n = r[1]; wins = r[2]; total = r[3] or 0
            wr = round(wins * 100 / n, 1) if n > 0 else 0
            avg = round(total / n, 0) if n > 0 else 0
            result.append({
                "bucket": r[0], "n": n, "wins": wins,
                "wr_pct": wr, "avg_rupees": avg, "total_rupees": round(total, 0)
            })
        return result

    # ── SCALPER side ──
    try:
        db = str(_data_dir / "scalper_trades.db")
        if not _P(db).exists():
            db = "scalper_trades.db"
        c = _sql.connect(db)
        # Days with loss ≥ 15k
        rows = c.execute(f"""
            SELECT DATE(entry_time) d, SUM(pnl_rupees) pnl, COUNT(*) n
            FROM scalper_trades
            WHERE status != 'OPEN' AND entry_time >= date('now','-{days} days')
            GROUP BY d HAVING pnl <= -15000 ORDER BY pnl ASC
        """).fetchall()
        out["scalper"]["loss_15k_days"] = [
            {"date": r[0], "pnl": round(r[1], 0), "n_trades": r[2]} for r in rows
        ]
        out["scalper"]["loss_15k_days_count"] = len(rows)
        # WR by bucket
        rows = c.execute(f"""
            SELECT
                CASE
                    WHEN probability < 50 THEN '<50'
                    WHEN probability < 55 THEN '50-54'
                    WHEN probability < 60 THEN '55-59'
                    WHEN probability < 65 THEN '60-64'
                    WHEN probability < 70 THEN '65-69'
                    WHEN probability < 80 THEN '70-79'
                    ELSE '80+'
                END as bucket,
                COUNT(*) as n,
                SUM(CASE WHEN pnl_rupees > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl_rupees) as total
            FROM scalper_trades
            WHERE status != 'OPEN' AND probability > 0
              AND entry_time >= date('now','-30 days')
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        out["scalper"]["wr_by_bucket_30d"] = _bucket_query(rows)
        # Worst 10 days
        rows = c.execute(f"""
            SELECT DATE(entry_time) d, SUM(pnl_rupees) pnl, COUNT(*) n
            FROM scalper_trades
            WHERE status != 'OPEN' AND entry_time >= date('now','-{days} days')
            GROUP BY d ORDER BY pnl ASC LIMIT 10
        """).fetchall()
        out["scalper"]["worst_10_days"] = [
            {"date": r[0], "pnl": round(r[1], 0), "n_trades": r[2]} for r in rows
        ]
        c.close()
    except Exception as e:
        out["errors"].append(f"scalper: {e}")

    # ── MAIN side (column-name-aware) ──
    try:
        db = str(_data_dir / "trades.db")
        if not _P(db).exists():
            db = "trades.db"
        c = _sql.connect(db)
        m_diag = out["diagnostics"]["main_db"]
        time_col = m_diag.get("time_col_used", "entry_time")
        prob_col = m_diag.get("prob_col", "probability")
        rows = c.execute(f"""
            SELECT DATE({time_col}) d, SUM(pnl_rupees) pnl, COUNT(*) n
            FROM trades
            WHERE status != 'OPEN' AND {time_col} >= date('now','-{days} days')
            GROUP BY d HAVING pnl <= -15000 ORDER BY pnl ASC
        """).fetchall()
        out["main"]["loss_15k_days"] = [
            {"date": r[0], "pnl": round(r[1], 0), "n_trades": r[2]} for r in rows
        ]
        out["main"]["loss_15k_days_count"] = len(rows)
        rows = c.execute(f"""
            SELECT
                CASE
                    WHEN {prob_col} < 50 THEN '<50'
                    WHEN {prob_col} < 55 THEN '50-54'
                    WHEN {prob_col} < 60 THEN '55-59'
                    WHEN {prob_col} < 65 THEN '60-64'
                    WHEN {prob_col} < 70 THEN '65-69'
                    WHEN {prob_col} < 80 THEN '70-79'
                    ELSE '80+'
                END as bucket,
                COUNT(*) as n,
                SUM(CASE WHEN pnl_rupees > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl_rupees) as total
            FROM trades
            WHERE status != 'OPEN' AND {prob_col} > 0
              AND {time_col} >= date('now','-30 days')
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        out["main"]["wr_by_bucket_30d"] = _bucket_query(rows)
        # Worst 10 days
        rows = c.execute(f"""
            SELECT DATE({time_col}) d, SUM(pnl_rupees) pnl, COUNT(*) n
            FROM trades
            WHERE status != 'OPEN' AND {time_col} >= date('now','-{days} days')
            GROUP BY d ORDER BY pnl ASC LIMIT 10
        """).fetchall()
        out["main"]["worst_10_days"] = [
            {"date": r[0], "pnl": round(r[1], 0), "n_trades": r[2]} for r in rows
        ]
        c.close()
    except Exception as e:
        out["errors"].append(f"main: {e}")

    # ── Decision hints ──
    scalper_bad = out.get("scalper", {}).get("loss_15k_days_count", 0)
    main_bad = out.get("main", {}).get("loss_15k_days_count", 0)
    total_bad = scalper_bad + main_bad
    out["decision_hints"] = {
        "daily_loss_cap": (
            "RESTORE ON" if total_bad >= 3 else
            "KEEP OFF" if total_bad <= 1 else
            "MARGINAL — your call"
        ),
        "daily_loss_cap_reason": f"{total_bad} days with loss ≥ ₹15k in last {days}d "
                                 f"(scalper={scalper_bad}, main={main_bad})",
    }
    # WR-based decision for 55% threshold
    for tab in ("scalper", "main"):
        buckets = out.get(tab, {}).get("wr_by_bucket_30d", [])
        b55 = next((b for b in buckets if b["bucket"] == "55-59"), None)
        if b55 and b55["n"] >= 5:
            out["decision_hints"][f"{tab}_55_bucket"] = {
                "wr_pct": b55["wr_pct"], "n": b55["n"],
                "avg_pnl": b55["avg_rupees"],
                "verdict": "KEEP at 55" if b55["wr_pct"] >= 50 else "RESTORE 65"
            }
    return out


@app.get("/api/admin/daily-report")
async def admin_daily_report(date: str = None):
    """Date-wise EOD report — works for any past day.

    Default: today.
    Returns per-tab P&L, top winner/loser, new rule firings, time-of-day breakdown.

    Examples:
      /api/admin/daily-report                  → today
      /api/admin/daily-report?date=2026-06-11  → specific date
      /api/admin/daily-report?date=2026-06-10  → past date
    """
    import sqlite3 as _sql
    from pathlib import Path as _P
    from datetime import datetime as _dt

    # Date handling
    if not date:
        date = _dt.now().strftime("%Y-%m-%d")

    _data_dir = _P("/data") if _P("/data").is_dir() else _P(__file__).parent
    out = {
        "date": date,
        "scalper": {},
        "main": {},
        "combined": {},
        "errors": [],
    }

    def _analyze_tab(db_path, table, prob_col="probability"):
        if not _P(db_path).exists():
            return {"error": f"DB not found: {db_path}"}
        c = _sql.connect(db_path)
        c.row_factory = _sql.Row
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
        hold_expr = "hold_seconds" if "hold_seconds" in cols else "(strftime('%s', exit_time) - strftime('%s', entry_time))"

        # All trades for the date
        rows = c.execute(f"""
            SELECT id, entry_time, exit_time, idx, action, strike, entry_price,
                   peak_ltp, exit_price, pnl_rupees, status, exit_reason,
                   {prob_col} as prob, qty
            FROM {table}
            WHERE status != 'OPEN' AND DATE(entry_time) = ?
            ORDER BY entry_time ASC
        """, (date,)).fetchall()
        trades = [dict(r) for r in rows]

        if not trades:
            c.close()
            return {"n": 0, "pnl": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "trades_by_status": {}, "top_winner": None, "top_loser": None,
                    "by_hour": {}, "new_rules_fired": {}}

        # Aggregates
        wins = [t for t in trades if t["pnl_rupees"] > 0]
        losses = [t for t in trades if t["pnl_rupees"] < 0]
        scratches = [t for t in trades if t["pnl_rupees"] == 0]
        total_pnl = sum(t["pnl_rupees"] for t in trades)

        # By status
        from collections import Counter
        status_counts = Counter(t["status"] for t in trades)
        status_pnl = {}
        for s in status_counts:
            status_pnl[s] = {
                "n": status_counts[s],
                "pnl": round(sum(t["pnl_rupees"] for t in trades if t["status"] == s), 0)
            }

        # Top winner/loser
        top_winner = max(trades, key=lambda t: t["pnl_rupees"]) if trades else None
        top_loser = min(trades, key=lambda t: t["pnl_rupees"]) if trades else None

        # By hour
        by_hour = {}
        for t in trades:
            try:
                h = int(t["entry_time"][11:13])
                key = f"{h:02d}:00"
                if key not in by_hour:
                    by_hour[key] = {"n": 0, "pnl": 0, "wins": 0}
                by_hour[key]["n"] += 1
                by_hour[key]["pnl"] += t["pnl_rupees"]
                if t["pnl_rupees"] > 0:
                    by_hour[key]["wins"] += 1
            except Exception:
                pass

        # NEW rules firings count (today's deployed protections)
        new_rules = {
            "INSTANT_REJECT":  status_counts.get("INSTANT_REJECT", 0),
            "EARLY_CUT":       status_counts.get("EARLY_CUT", 0),
            "ZOMBIE_KILL":     status_counts.get("ZOMBIE_KILL", 0),
            "PEAK_GIVEBACK":   status_counts.get("PEAK_GIVEBACK", 0),
            "BREAKEVEN_EXIT":  status_counts.get("BREAKEVEN_EXIT", 0),
            "STOP_HUNTED":     status_counts.get("STOP_HUNTED", 0),
        }

        c.close()

        # Build clean trade summary
        def _trade_brief(t):
            if not t: return None
            return {
                "id": t["id"],
                "time": t["entry_time"][11:19] if t.get("entry_time") else "",
                "idx": t["idx"],
                "action": t["action"],
                "strike": t["strike"],
                "entry": t["entry_price"],
                "peak_pct": round((t["peak_ltp"] - t["entry_price"]) / t["entry_price"] * 100, 2) if t["entry_price"] > 0 else 0,
                "exit": t["exit_price"],
                "pnl": round(t["pnl_rupees"], 0),
                "status": t["status"],
                "prob": t.get("prob", 0),
            }

        return {
            "n": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "scratches": len(scratches),
            "win_rate": round(len(wins) * 100 / len(trades), 1) if trades else 0,
            "pnl": round(total_pnl, 0),
            "trades_by_status": status_pnl,
            "top_winner": _trade_brief(top_winner) if top_winner and top_winner["pnl_rupees"] > 0 else None,
            "top_loser": _trade_brief(top_loser) if top_loser and top_loser["pnl_rupees"] < 0 else None,
            "by_hour": {k: {"n": v["n"], "pnl": round(v["pnl"], 0), "wins": v["wins"]} for k, v in sorted(by_hour.items())},
            "new_rules_fired": new_rules,
        }

    try:
        out["scalper"] = _analyze_tab(str(_data_dir / "scalper_trades.db"), "scalper_trades")
    except Exception as e:
        out["errors"].append(f"scalper: {e}")
    try:
        out["main"] = _analyze_tab(str(_data_dir / "trades.db"), "trades")
    except Exception as e:
        out["errors"].append(f"main: {e}")

    # Combined
    s = out.get("scalper", {})
    m = out.get("main", {})
    if "n" in s and "n" in m:
        s_pnl = s.get("pnl", 0)
        m_pnl = m.get("pnl", 0)
        s_n = s.get("n", 0)
        m_n = m.get("n", 0)
        total_n = s_n + m_n
        total_wins = s.get("wins", 0) + m.get("wins", 0)
        out["combined"] = {
            "total_trades": total_n,
            "total_pnl": round(s_pnl + m_pnl, 0),
            "total_wins": total_wins,
            "win_rate": round(total_wins * 100 / total_n, 1) if total_n > 0 else 0,
            "scalper_pnl": round(s_pnl, 0),
            "main_pnl": round(m_pnl, 0),
        }

    return out


@app.get("/api/admin/recent-days-report")
async def admin_recent_days_report(days: int = 7):
    """Roll-up: last N days summary. Returns date-wise P&L."""
    import sqlite3 as _sql
    from pathlib import Path as _P
    _data_dir = _P("/data") if _P("/data").is_dir() else _P(__file__).parent
    out = {"days": days, "by_date": {}, "totals": {}, "errors": []}

    try:
        for tab, db_name, table in [("scalper", "scalper_trades.db", "scalper_trades"),
                                      ("main", "trades.db", "trades")]:
            db = str(_data_dir / db_name)
            if not _P(db).exists():
                continue
            c = _sql.connect(db)
            rows = c.execute(f"""
                SELECT DATE(entry_time) d, COUNT(*) n,
                       SUM(CASE WHEN pnl_rupees > 0 THEN 1 ELSE 0 END) wins,
                       SUM(pnl_rupees) pnl
                FROM {table}
                WHERE status != 'OPEN'
                  AND entry_time >= date('now','-{days} days')
                GROUP BY d ORDER BY d DESC
            """).fetchall()
            for r in rows:
                d, n, w, pnl = r
                if d not in out["by_date"]:
                    out["by_date"][d] = {}
                out["by_date"][d][tab] = {
                    "n": n, "wins": w,
                    "win_rate": round(w * 100 / n, 1) if n > 0 else 0,
                    "pnl": round(pnl or 0, 0)
                }
            c.close()
    except Exception as e:
        out["errors"].append(str(e))

    # Build totals
    grand_total = 0
    for d, tabs in out["by_date"].items():
        s_pnl = tabs.get("scalper", {}).get("pnl", 0)
        m_pnl = tabs.get("main", {}).get("pnl", 0)
        tabs["combined_pnl"] = round(s_pnl + m_pnl, 0)
        grand_total += s_pnl + m_pnl

    out["totals"] = {
        "grand_total_pnl": round(grand_total, 0),
        "days_with_data": len(out["by_date"]),
        "avg_per_day": round(grand_total / len(out["by_date"]), 0) if out["by_date"] else 0,
    }
    return out


@app.get("/api/admin/exit-pattern-audit")
async def admin_exit_pattern_audit(days: int = 60, status: str = None):
    """Deep dive into EXIT PATTERNS — what kills trades?

    Returns per-exit-status breakdown with:
      - count, avg_pnl, avg_hold_min, avg_peak_pct
      - BNF vs NIFTY split
      - time-of-day distribution
      - worst 5 examples
      - prob bucket distribution

    Use cases:
      - REVERSAL_EXIT happens 108x on scalper. Why? Is it peak<0 trades
        or peak>2% then crash? What's avg verdict prob?
      - STOP_HUNTED 45x on main. Are SL ranges too tight? Wrong direction?

    Run:
      curl https://<app>/api/admin/exit-pattern-audit?days=60
      curl https://<app>/api/admin/exit-pattern-audit?days=60&status=REVERSAL_EXIT
    """
    import sqlite3 as _sql
    from pathlib import Path as _P
    out = {"days_window": days, "scalper": {}, "main": {}, "errors": []}
    _data_dir = _P("/data") if _P("/data").is_dir() else _P(__file__).parent

    def _analyze(db_path: str, table: str, prob_col: str, status_filter: str = None):
        if not _P(db_path).exists():
            return {}
        c = _sql.connect(db_path)
        c.row_factory = _sql.Row

        # Detect if hold_seconds column exists; if not, compute from times
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if "hold_seconds" in cols:
            hold_expr = "hold_seconds"
        else:
            hold_expr = "(strftime('%s', exit_time) - strftime('%s', entry_time))"

        # Build status filter
        where_status = f"status = '{status_filter}'" if status_filter else "status != 'OPEN'"

        # Also: SL distance metric for STOP_HUNTED analysis
        # original_sl column exists in main DB — measures initial SL distance
        sl_distance_expr = ""
        if "original_sl" in cols:
            sl_distance_expr = ", ROUND(AVG((entry_price - original_sl)*100.0/entry_price), 2) as avg_sl_distance_pct"

        # Per-status aggregate
        rows = c.execute(f"""
            SELECT
                status,
                COUNT(*) as n,
                ROUND(AVG(pnl_rupees), 0) as avg_pnl,
                ROUND(SUM(pnl_rupees), 0) as total_pnl,
                ROUND(AVG((peak_ltp - entry_price)*100.0/entry_price), 2) as avg_peak_pct,
                ROUND(AVG({hold_expr})/60.0, 1) as avg_hold_min,
                ROUND(AVG({prob_col}), 1) as avg_prob,
                SUM(CASE WHEN pnl_rupees > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN idx='BANKNIFTY' THEN 1 ELSE 0 END) as bnf,
                SUM(CASE WHEN idx='NIFTY' THEN 1 ELSE 0 END) as nifty
                {sl_distance_expr}
            FROM {table}
            WHERE status != 'OPEN'
              AND entry_time >= date('now','-{days} days')
              AND entry_price > 0
            GROUP BY status
            ORDER BY n DESC
        """).fetchall()
        per_status = []
        for r in rows:
            d = dict(r)
            d["wr_pct"] = round(d["wins"]*100/d["n"], 1) if d["n"] > 0 else 0
            per_status.append(d)

        # Time-of-day distribution per status
        time_dist = {}
        if status_filter:
            tod_rows = c.execute(f"""
                SELECT
                    CASE
                        WHEN CAST(substr(entry_time,12,2) AS INT) < 10 THEN '09:15-10:00'
                        WHEN CAST(substr(entry_time,12,2) AS INT) < 11 THEN '10:00-11:00'
                        WHEN CAST(substr(entry_time,12,2) AS INT) < 12 THEN '11:00-12:00'
                        WHEN CAST(substr(entry_time,12,2) AS INT) < 13 THEN '12:00-13:00'
                        WHEN CAST(substr(entry_time,12,2) AS INT) < 14 THEN '13:00-14:00'
                        WHEN CAST(substr(entry_time,12,2) AS INT) < 15 THEN '14:00-15:00'
                        ELSE '15:00+'
                    END as tod,
                    COUNT(*) as n,
                    ROUND(SUM(pnl_rupees), 0) as pnl
                FROM {table}
                WHERE {where_status}
                  AND entry_time >= date('now','-{days} days')
                GROUP BY tod ORDER BY tod
            """).fetchall()
            time_dist = [dict(r) for r in tod_rows]

        # Worst 10 examples of filtered status (if specified)
        worst_examples = []
        if status_filter:
            # Conditionally include original_sl if it exists (Main DB has it)
            sl_col = ", original_sl, sl_price" if "original_sl" in cols else ", sl_price"
            wrows = c.execute(f"""
                SELECT id, entry_time, idx, action, strike, entry_price, peak_ltp,
                       exit_price, pnl_rupees, {hold_expr} as hold_seconds,
                       {prob_col} as prob, exit_reason {sl_col}
                FROM {table}
                WHERE status = '{status_filter}'
                  AND entry_time >= date('now','-{days} days')
                ORDER BY pnl_rupees ASC LIMIT 10
            """).fetchall()
            for r in wrows:
                d = dict(r)
                if d["entry_price"] > 0:
                    d["peak_pct"] = round((d["peak_ltp"] - d["entry_price"])*100/d["entry_price"], 2)
                else:
                    d["peak_pct"] = 0
                d["hold_min"] = round((d.get("hold_seconds") or 0)/60.0, 1)
                # SL distance from entry
                osl = d.get("original_sl") or d.get("sl_price") or 0
                if d["entry_price"] > 0 and osl > 0:
                    d["sl_distance_pct"] = round((d["entry_price"] - osl)*100/d["entry_price"], 2)
                worst_examples.append(d)

        # Peak distribution for filtered status — were these trades EVER positive?
        peak_dist = {}
        if status_filter:
            prows = c.execute(f"""
                SELECT
                    CASE
                        WHEN (peak_ltp - entry_price)/entry_price*100 < 0 THEN 'NEVER_+0%'
                        WHEN (peak_ltp - entry_price)/entry_price*100 < 0.5 THEN 'peak_+0_to_+0.5%'
                        WHEN (peak_ltp - entry_price)/entry_price*100 < 1.5 THEN 'peak_+0.5_to_+1.5%'
                        WHEN (peak_ltp - entry_price)/entry_price*100 < 3 THEN 'peak_+1.5_to_+3%'
                        WHEN (peak_ltp - entry_price)/entry_price*100 < 5 THEN 'peak_+3_to_+5%'
                        ELSE 'peak_+5%+'
                    END as peak_band,
                    COUNT(*) as n,
                    ROUND(AVG(pnl_rupees), 0) as avg_pnl,
                    ROUND(SUM(pnl_rupees), 0) as total_pnl
                FROM {table}
                WHERE status = '{status_filter}'
                  AND entry_time >= date('now','-{days} days')
                  AND entry_price > 0
                GROUP BY peak_band
                ORDER BY n DESC
            """).fetchall()
            peak_dist = [dict(r) for r in prows]

        c.close()
        return {
            "per_status_aggregate": per_status,
            "time_of_day_dist": time_dist,
            "peak_distribution": peak_dist,
            "worst_10_examples": worst_examples,
        }

    try:
        out["scalper"] = _analyze(
            str(_data_dir / "scalper_trades.db"), "scalper_trades", "probability", status)
    except Exception as e:
        out["errors"].append(f"scalper: {e}")
    try:
        out["main"] = _analyze(
            str(_data_dir / "trades.db"), "trades", "probability", status)
    except Exception as e:
        out["errors"].append(f"main: {e}")

    # Decision hints based on what we find
    hints = []
    sa = out.get("scalper", {}).get("per_status_aggregate", [])
    for s in sa:
        if s["status"] == "REVERSAL_EXIT" and s["n"] >= 20:
            hints.append(f"SCALPER REVERSAL_EXIT: {s['n']} trades, avg_peak={s['avg_peak_pct']}%, "
                         f"avg_prob={s['avg_prob']}, total loss ₹{s['total_pnl']:,.0f}. "
                         f"{'NEVER_POSITIVE pattern (EARLY_CUT could catch)' if s['avg_peak_pct'] < 1 else 'PEAK_GIVEBACK pattern (profit_floor would help)'}")
        if s["status"] == "STOP_HUNTED" and s["n"] >= 10:
            hints.append(f"MAIN STOP_HUNTED: {s['n']} trades, avg_peak={s['avg_peak_pct']}%, "
                         f"avg_hold={s['avg_hold_min']}min. SL placement may be too tight.")
    out["decision_hints"] = hints
    return out


@app.get("/api/admin/trade-attribution")
async def admin_trade_attribution(days: int = 60, brokerage_per_trade: int = 1500):
    try:
        return await _do_admin_trade_attribution(days, brokerage_per_trade)
    except Exception as _ta_e:
        import traceback
        return JSONResponse(
            {"error": str(_ta_e), "trace": traceback.format_exc()[:2000]},
            status_code=500,
        )


async def _do_admin_trade_attribution(days: int, brokerage_per_trade: int):
    """Per-trade attribution: groups CLOSED trades by every dimension we
    capture at entry and computes win/loss + net P&L after brokerage.

    Dimensions returned:
      - probability bucket (50-60, 60-65, 65-70, 70-75, 75-80, 80-85, 85+)
      - source (verdict_momentum, verdict, counter, etc.)
      - regime_at_entry (CHOP/NORMAL/BREAKOUT/TRENDING)
      - structure combo (5m × 15m)
      - exit status × direction
      - probability × structure (cross-tab)

    Brokerage default ₹1500/trade — pass ?brokerage_per_trade=X to change.
    """
    import sqlite3 as _sql
    from collections import defaultdict
    from datetime import datetime, timedelta
    cutoff_dt = datetime.now() - timedelta(days=days)
    cutoff_iso = cutoff_dt.strftime("%Y-%m-%d")

    def _prob_bucket(p):
        if p is None or p == 0:
            return "unknown"
        if p < 60:  return "50-60"
        if p < 65:  return "60-65"
        if p < 70:  return "65-70"
        if p < 75:  return "70-75"
        if p < 80:  return "75-80"
        if p < 85:  return "80-85"
        return "85+"

    def _struct_label(s5, s15):
        if not s5 and not s15: return "no-structure-data"
        return f"5m={s5 or '?'} | 15m={s15 or '?'}"

    out = {"days_window": days, "brokerage_per_trade": brokerage_per_trade, "tables": {}}
    for path, table, label in (
        ("/data/trades.db", "trades", "main"),
        ("/data/scalper_trades.db", "scalper_trades", "scalper"),
    ):
        if not os.path.exists(path):
            continue
        c = _sql.connect(path)
        c.row_factory = _sql.Row
        rows = c.execute(
            f"SELECT idx, action, status, pnl_rupees, probability, source, "
            f"regime_at_entry, structure_5m, structure_15m, structure_1h, "
            f"range_pct_at_entry, entry_time "
            f"FROM {table} WHERE status NOT IN ('OPEN','PENDING') "
            f"AND substr(entry_time,1,10) >= ?",
            (cutoff_iso,)
        ).fetchall()
        c.close()

        total_n = len(rows)
        total_brokerage = total_n * brokerage_per_trade
        gross_pnl = sum(r["pnl_rupees"] or 0 for r in rows)
        net_pnl = gross_pnl - total_brokerage

        # ── Per-dimension aggregation helper ──
        def _agg(key_fn, sort_by="net_pnl"):
            buckets = defaultdict(lambda: {"n": 0, "wins": 0, "losses": 0, "gross": 0.0})
            for r in rows:
                key = key_fn(r)
                p = r["pnl_rupees"] or 0
                b = buckets[key]
                b["n"] += 1
                b["gross"] += p
                if p > 0: b["wins"] += 1
                elif p < 0: b["losses"] += 1
            result = []
            for key, b in buckets.items():
                brk = b["n"] * brokerage_per_trade
                net = b["gross"] - brk
                result.append({
                    "key": key,
                    "n": b["n"],
                    "wins": b["wins"],
                    "losses": b["losses"],
                    "wr_pct": round(b["wins"] / b["n"] * 100, 1) if b["n"] else 0,
                    "gross_pnl": round(b["gross"], 0),
                    "brokerage": brk,
                    "net_pnl": round(net, 0),
                    "avg_net": round(net / b["n"], 0) if b["n"] else 0,
                })
            result.sort(key=lambda x: -x[sort_by])
            return result

        out["tables"][label] = {
            "total_trades": total_n,
            "gross_pnl": round(gross_pnl, 0),
            "total_brokerage": total_brokerage,
            "net_pnl": round(net_pnl, 0),
            "by_probability": _agg(lambda r: _prob_bucket(r["probability"])),
            "by_source": _agg(lambda r: r["source"] or "unknown"),
            "by_regime": _agg(lambda r: r["regime_at_entry"] or "unknown"),
            "by_structure": _agg(lambda r: _struct_label(r["structure_5m"], r["structure_15m"])),
            "by_status": _agg(lambda r: r["status"] or "unknown"),
            "by_direction": _agg(
                lambda r: "BUY CE" if "CE" in (r["action"] or "") else "BUY PE"
            ),
            "by_prob_x_direction": _agg(
                lambda r: f"{_prob_bucket(r['probability'])} | "
                          f"{'CE' if 'CE' in (r['action'] or '') else 'PE'}"
            ),
        }
    return out


@app.get("/api/admin/regime-deep-audit")
async def admin_regime_deep_audit():
    """Returns a detailed regime × outcome breakdown for both modes.
    Use after regime_backfill has populated rows."""
    import sqlite3 as _sql
    from collections import Counter, defaultdict
    out = {"tables": {}}
    for path, table, label in (
        ("/data/trades.db", "trades", "main"),
        ("/data/scalper_trades.db", "scalper_trades", "scalper"),
    ):
        if not os.path.exists(path):
            continue
        c = _sql.connect(path)
        c.row_factory = _sql.Row
        rows = c.execute(
            f"SELECT idx, action, status, pnl_rupees, regime_at_entry, "
            f"range_pct_at_entry, candle_pct_at_entry, "
            f"structure_5m, structure_15m, structure_1h "
            f"FROM {table} WHERE status NOT IN ('OPEN','PENDING') "
            f"AND regime_at_entry IS NOT NULL AND regime_at_entry != ''"
        ).fetchall()
        c.close()
        # status × regime
        sxr_cnt = defaultdict(lambda: defaultdict(int))
        sxr_pnl = defaultdict(lambda: defaultdict(float))
        for r in rows:
            st = r["status"]
            rg = r["regime_at_entry"]
            sxr_cnt[st][rg] += 1
            sxr_pnl[st][rg] += r["pnl_rupees"] or 0
        status_regime = []
        for st in sorted(sxr_cnt.keys()):
            entry = {"status": st, "by_regime": {}}
            for rg, n in sxr_cnt[st].items():
                pnl = sxr_pnl[st][rg]
                entry["by_regime"][rg] = {"n": n, "pnl": round(pnl, 0)}
            status_regime.append(entry)
        # range histogram
        bands = [(0, 0.1), (0.1, 0.15), (0.15, 0.2), (0.2, 0.3),
                 (0.3, 0.4), (0.4, 0.6), (0.6, 1.0), (1.0, 99)]
        range_hist = []
        for lo, hi in bands:
            in_band = [r for r in rows if lo <= (r["range_pct_at_entry"] or 0) < hi]
            n = len(in_band)
            pnl = sum(r["pnl_rupees"] or 0 for r in in_band)
            w = sum(1 for r in in_band if (r["pnl_rupees"] or 0) > 0)
            l = sum(1 for r in in_band if (r["pnl_rupees"] or 0) < 0)
            range_hist.append({
                "band": f"[{lo}%-{hi}%)", "n": n,
                "pnl": round(pnl, 0), "wins": w, "losses": l,
                "wr_pct": round(w / n * 100, 1) if n else 0,
            })
        # structure alignment
        align = defaultdict(lambda: {"n": 0, "pnl": 0.0, "w": 0, "l": 0})
        for r in rows:
            is_ce = "CE" in (r["action"] or "")
            s5 = r["structure_5m"] or "UNKNOWN"
            s15 = r["structure_15m"] or "UNKNOWN"
            if is_ce and s5 == "UPTREND" and s15 == "UPTREND":
                cat = "CE aligned 5m+15m UP"
            elif is_ce and (s5 == "DOWNTREND" or s15 == "DOWNTREND"):
                cat = "CE counter-trend"
            elif not is_ce and s5 == "DOWNTREND" and s15 == "DOWNTREND":
                cat = "PE aligned 5m+15m DN"
            elif not is_ce and (s5 == "UPTREND" or s15 == "UPTREND"):
                cat = "PE counter-trend"
            elif s5 == "CHOP" or s15 == "CHOP":
                cat = "structure CHOP"
            else:
                cat = "mixed/other"
            b = align[cat]
            b["n"] += 1
            b["pnl"] += (r["pnl_rupees"] or 0)
            if (r["pnl_rupees"] or 0) > 0:
                b["w"] += 1
            elif (r["pnl_rupees"] or 0) < 0:
                b["l"] += 1
        alignment_buckets = []
        for cat, b in sorted(align.items(), key=lambda x: -x[1]["pnl"]):
            alignment_buckets.append({
                "bucket": cat, "n": b["n"],
                "pnl": round(b["pnl"], 0), "wins": b["w"], "losses": b["l"],
                "wr_pct": round(b["w"] / b["n"] * 100, 1) if b["n"] else 0,
            })
        out["tables"][label] = {
            "total": len(rows),
            "status_x_regime": status_regime,
            "range_histogram": range_hist,
            "structure_alignment": alignment_buckets,
        }
    return out


@app.post("/api/admin/regime-backfill")
async def admin_regime_backfill():
    """One-shot backfill: populate regime_at_entry + structure_5m/15m/1h
    for all historical trades in both trades.db and scalper_trades.db.

    Runs synchronously (1-3 min total for ~300 trades). Returns counts +
    summary distribution so the caller can immediately read the truth
    instead of waiting for an async job.
    """
    try:
        import regime_backfill as _rb
        _rb.main()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    # Quick summary read from the just-populated rows
    import sqlite3 as _sql
    out = {"status": "done", "summaries": {}}
    for path, table, label in (
        ("/data/trades.db", "trades", "main"),
        ("/data/scalper_trades.db", "scalper_trades", "scalper"),
    ):
        if not os.path.exists(path):
            continue
        c = _sql.connect(path)
        c.row_factory = _sql.Row
        rows = c.execute(
            f"SELECT regime_at_entry, pnl_rupees, status FROM {table} "
            f"WHERE status NOT IN ('OPEN','PENDING') "
            f"AND regime_at_entry IS NOT NULL AND regime_at_entry != ''"
        ).fetchall()
        c.close()
        from collections import Counter, defaultdict
        rg_cnt = Counter()
        rg_pnl = defaultdict(float)
        for r in rows:
            rg_cnt[r["regime_at_entry"]] += 1
            rg_pnl[r["regime_at_entry"]] += r["pnl_rupees"] or 0
        out["summaries"][label] = {
            "total": len(rows),
            "regime_distribution": {
                rg: {"n": rg_cnt[rg], "pnl_total": round(rg_pnl[rg], 2)}
                for rg in rg_cnt
            },
            "chop_total_pnl": round(rg_pnl.get("CHOP", 0), 2),
            "chop_trades": rg_cnt.get("CHOP", 0),
        }
    return out


@app.post("/api/admin/disk/cleanup")
async def admin_disk_cleanup(body: dict = None):
    """SAFE targeted cleanup of /data. Pass {"action": "..."} with one of:

      "wal_checkpoint"       — SQLite WAL/SHM truncation (no data loss).
                               Often recovers 50-200 MB instantly.
      "delete_backups_old"   — Delete any .backup / .bak / .sql.gz older
                               than 7 days.
      "delete_structured_logs_old" — Delete structured_logger output >7d.
      "vacuum_dbs"           — VACUUM all .db files (reclaims deleted rows).
                               SLOW but biggest recovery.
      "all_safe"             — runs wal_checkpoint + delete_backups_old +
                               delete_structured_logs_old (NO vacuum).

    Returns the action result + freed_mb estimate.
    """
    import shutil, sqlite3, os, time
    from pathlib import Path as _P
    action = (body or {}).get("action", "")
    if not action:
        return JSONResponse({"error": "action required"}, status_code=400)
    try:
        before = shutil.disk_usage(str(_data_dir)).free
        results = {"action": action, "steps": []}

        def _wal_checkpoint():
            count = 0
            for p in _P(_data_dir).glob("*.db"):
                try:
                    c = sqlite3.connect(str(p), timeout=5.0)
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    c.close()
                    count += 1
                except Exception:
                    pass
            return f"checkpointed {count} DBs"

        def _delete_old(patterns, days):
            cutoff = time.time() - days * 86400
            removed = 0
            for pat in patterns:
                for p in _P(_data_dir).rglob(pat):
                    try:
                        if p.is_file() and p.stat().st_mtime < cutoff:
                            p.unlink()
                            removed += 1
                    except Exception:
                        pass
            return f"removed {removed} files matching {patterns} older than {days}d"

        def _vacuum_dbs():
            count = 0
            for p in _P(_data_dir).glob("*.db"):
                try:
                    c = sqlite3.connect(str(p), timeout=30.0)
                    c.execute("VACUUM")
                    c.close()
                    count += 1
                except Exception:
                    pass
            return f"vacuumed {count} DBs"

        if action == "wal_checkpoint":
            results["steps"].append(_wal_checkpoint())
        elif action == "delete_backups_old":
            results["steps"].append(
                _delete_old(["*.backup", "*.bak", "*.sql.gz", "backups/*"], 7))
        elif action == "delete_structured_logs_old":
            results["steps"].append(
                _delete_old(["structured_log*", "*.log", "logs/*"], 7))
        elif action == "vacuum_dbs":
            results["steps"].append(_vacuum_dbs())
        elif action == "prune_council":
            # council.db growth audit (2026-06-04): 530 MB. Will be the next
            # disk bomb if not pruned. Contains:
            #   engine_votes        — 1 row per engine per verdict cycle
            #   council_verdicts    — 1 row per verdict
            #   perf_samples        — 1 row per 5-min sample
            #   auto_login_attempts — 1 row per login attempt
            #   engine_accuracy     — small, no prune needed
            # Strategy: keep 14 days of votes/verdicts/perf, 30 days of
            # login attempts (forensic value). VACUUM at end.
            try:
                from datetime import timedelta as _td
                import pytz as _pytz
                _IST = _pytz.timezone("Asia/Kolkata")
                now_ist = datetime.now(_IST)
                cutoff_14d = (now_ist - _td(days=14)).isoformat()
                cutoff_30d = (now_ist - _td(days=30)).isoformat()
                council_path = _P(_data_dir) / "council.db"
                if not council_path.exists():
                    results["steps"].append("council.db not found")
                else:
                    import sqlite3 as _sq
                    c = _sq.connect(str(council_path), timeout=30.0)
                    total_deleted = 0
                    for table, col, cutoff in [
                        ("engine_votes", "timestamp", cutoff_14d),
                        ("council_verdicts", "timestamp", cutoff_14d),
                        ("perf_samples", "iso", cutoff_14d),
                        ("auto_login_attempts", "timestamp", cutoff_30d),
                    ]:
                        try:
                            cur = c.execute(
                                f"DELETE FROM {table} WHERE {col} < ?",
                                (cutoff,))
                            total_deleted += cur.rowcount
                            results["steps"].append(
                                f"{table}: deleted {cur.rowcount} rows older than {cutoff[:10]}")
                        except Exception as _te:
                            results["steps"].append(f"{table}: {_te}")
                    c.commit()
                    try:
                        c.execute("VACUUM")
                        c.commit()
                        results["steps"].append("VACUUM complete")
                    except Exception as _ve:
                        results["steps"].append(f"VACUUM error: {_ve}")
                    c.close()
                    results["steps"].append(f"total deleted: {total_deleted}")
            except Exception as _e:
                results["steps"].append(f"prune_council error: {_e}")
        elif action == "prune_trap_data":
            # CORRECT fix: keep engine alive, prune old data + vacuum.
            # Trap engine is the BEST engine (61.9% WR, +₹6.4k avg) — must
            # stay running. Drops all snapshots older than 14 days +
            # reclaims disk space via VACUUM. Safe, idempotent, repeatable.
            try:
                from trap_engine import _purge_old, DB_PATH
                if DB_PATH is None:
                    # Trap engine not initialized yet — set path
                    import trap_engine
                    trap_engine.DB_PATH = str(_P(_data_dir) / "trap_data.db")
                _purge_old(14)
                results["steps"].append("trap_data: purged >14d rows + VACUUM")
            except Exception as _e:
                results["steps"].append(f"prune_trap_data error: {_e}")
        elif action == "drop_trap_data":
            # Trap engine data — shadow-only, not used in production trades.
            # Engine rebuilds from live ticks. Safe to delete.
            # As of 2026-06-04: this single file = 4.3 GB / 86% of disk.
            removed = []
            for name in ("trap_data.db", "trap_data.db-wal",
                         "trap_data.db-shm", "trap_data.db-journal"):
                p = _P(_data_dir) / name
                try:
                    if p.exists():
                        size = p.stat().st_size
                        p.unlink()
                        removed.append(f"{name} ({size/1024/1024:.1f} MB)")
                except Exception as _e:
                    removed.append(f"{name}: ERROR {_e}")
            results["steps"].append(f"dropped: {removed}")
        elif action == "all_safe":
            results["steps"].append(_wal_checkpoint())
            results["steps"].append(
                _delete_old(["*.backup", "*.bak", "*.sql.gz", "backups/*"], 7))
            results["steps"].append(
                _delete_old(["structured_log*", "*.log", "logs/*"], 7))
        else:
            return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)

        after = shutil.disk_usage(str(_data_dir)).free
        results["freed_mb"] = round((after - before) / 1024 / 1024, 1)
        results["free_after_mb"] = round(after / 1024 / 1024, 1)
        return results
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/backup/status")
async def backup_status():
    """Backup daemon status — config, last run, DB count.
    Use to verify backup is configured + running.
    """
    try:
        from backup_manager import get_status
        return get_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/backup/run-now")
async def backup_run_now():
    """Trigger manual backup immediately. Admin/debug endpoint.
    Returns archive size, DB count, success/failure.
    """
    try:
        from backup_manager import _run_backup_now
        return _run_backup_now()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/dashboard/snapshot")
async def dashboard_snapshot():
    """ONE call returns everything the Dashboard tab needs.

    Replaces ~40 individual API calls per page-load with a single bundled
    response. Frontend SWR can hydrate multiple components from this.

    All values come from the background-populated api_cache (sub-ms).
    Stale data is OK — the cache is at most 3-10 seconds behind.

    Reduces:
      - Tab open: 40 round-trips × 200ms = 8s  →  1 round-trip × 200ms = 200ms
      - Server CPU: 40 endpoints × compute   →  1 endpoint × dict assembly
      - Network bandwidth: 40 TCP/TLS handshakes → 1
    """
    snap = {
        "ts": time.time(),
        "engine_running": bool(engine and engine.running),
    }

    # Pull each cached key. Returns None if not yet populated.
    keys_to_include = [
        "live", "chain_NIFTY", "chain_BANKNIFTY",
        "oi_summary", "unusual",
        "trades_open",
        "forecast_live",
        "watcher_status", "positions_health",
        "trap_verdict", "smart_money_live", "reversal_live",
        "signals", "seller_summary", "trade_analysis",
        "hidden_shift", "intraday", "nextday", "multi_tf",
    ]

    for key in keys_to_include:
        try:
            val = cache_get_stale(key)
            if val is not None:
                # Use friendlier camelCase keys for frontend convenience
                friendly_key = {
                    "chain_NIFTY":     "chainNifty",
                    "chain_BANKNIFTY": "chainBanknifty",
                    "oi_summary":      "oiSummary",
                    "trades_open":     "tradesOpen",
                    "forecast_live":   "forecast",
                    "watcher_status":  "watcherStatus",
                    "positions_health": "positionsHealth",
                    "trap_verdict":    "trapVerdict",
                    "smart_money_live": "smartMoney",
                    "reversal_live":   "reversal",
                    "seller_summary":  "sellerData",
                    "trade_analysis":  "tradeAnalysis",
                    "hidden_shift":    "hiddenShift",
                    "multi_tf":        "multiTimeframe",
                }.get(key, key)
                snap[friendly_key] = val
        except Exception as e:
            print(f"[SNAPSHOT] err on {key}: {e}")

    return snap


@app.post("/api/logout")
async def logout():
    global engine
    if engine:
        engine.stop()
        engine = None
    session.update({"api_key": None, "api_secret": None, "access_token": None, "kite": None})
    return {"status": "logged_out"}


# ── Data Routes ──────────────────────────────────────────────────────────

# NEW (2026-05-08): Two-tier caching for fast tab loads.
#
# Tier 1: api_cache (background-populated, sub-ms reads)
#   - Engine's _start_cache_populator runs every 3s, pre-computes hot
#     endpoints, stores in api_cache._memory_cache.
#   - Endpoints read directly via cache_get() — no compute on hit.
#   - 50-200x faster tab load, same correctness (3s staleness max).
#
# Tier 2: legacy _get_or_cache fallback (lazy compute on cache miss)
#   - Used when populator hasn't run yet (cold start) or for non-hot endpoints.
#   - 5-second TTL with on-demand fetch.

from api_cache import cache_get, cache_set, cache_get_stale, cache_invalidate, cache_invalidate_prefix, cache_stats

_cache_timestamps = {}  # legacy timestamps (kept for backward compat)
_memory_cache = {}      # legacy local cache (kept for backward compat)


def _fast_cache_or_compute(key: str, fetcher, populator_max_age: float = 10.0,
                            fallback_ttl: float = 5.0):
    """Read from background-populated cache first (sub-ms).
    Falls back to lazy compute if cache is empty/stale.

    populator_max_age: how stale is OK from the engine's populator?
                      (3s populate cycle → 10s allows for 2-3 cycles slip)
    fallback_ttl:     when computing on miss, cache result for this long
    """
    # Tier 1: try background-populated cache
    fresh = cache_get(key, max_age_sec=populator_max_age)
    if fresh is not None:
        return fresh

    # Tier 2: lazy compute (legacy path)
    return _get_or_cache(key, fetcher, ttl=fallback_ttl)


def _get_or_cache(key, fetcher, ttl=5):
    """Get live data with TTL-based caching. ttl=seconds before refresh.

    UPGRADED 2026-05-09: Now checks api_cache (background populator) FIRST
    before computing. This makes ALL legacy callers automatically fast
    when populator has populated the key.

    Tier order:
      1. api_cache (background populator, sub-ms reads if fresh enough)
      2. local _memory_cache (per-process, ttl-based)
      3. fetcher() — actual compute (slowest)
    """
    now = time.time()

    # ── Tier 1: api_cache populator (NEW) ──
    # Allow staleness up to 2x ttl since populator runs every 3s and
    # we'd rather serve slightly-stale-but-fast than block on compute.
    populator_max_age = max(ttl * 2, 10)
    fresh_from_populator = cache_get(key, max_age_sec=populator_max_age)
    if fresh_from_populator is not None:
        return fresh_from_populator

    # ── Tier 2: local memory cache (legacy fast path) ──
    if key in _memory_cache and key in _cache_timestamps:
        if now - _cache_timestamps[key] < ttl:
            return _memory_cache[key]

    # ── Tier 3: compute on demand ──
    if engine and engine.running:
        try:
            data = fetcher()
            _memory_cache[key] = data
            _cache_timestamps[key] = now
            cache_set(key, data)  # also write to api_cache for next time
            save_cache(key, data)
            return data
        except Exception as e:
            print(f"[CACHE] Fetch error for {key}: {e}")
            # Return stale memory cache if available
            if key in _memory_cache:
                return _memory_cache[key]
            # Try api_cache stale value
            stale = cache_get_stale(key)
            if stale is not None:
                return stale

    # Engine not running — serve last saved data (file cache)
    if key in _memory_cache:
        return _memory_cache[key]
    stale = cache_get_stale(key)
    if stale is not None:
        return stale
    cached = get_cached(key)
    if cached:
        _memory_cache[key] = cached
        return cached
    # Return empty data structure instead of 503 — frontend handles gracefully
    return {}


@app.get("/api/live")
async def live_data():
    # Hot endpoint — polled every 5s by frontend dashboard.
    # Fast cache: read from background populator (10s max age).
    return _fast_cache_or_compute("live", lambda: engine.get_live_data())


@app.get("/api/option-chain/{index}")
async def option_chain(index: str):
    # Hot endpoint — multiple tabs request, heavy compute (41 strikes × greeks).
    # Background populator updates every 3s.
    idx = index.upper()
    return _fast_cache_or_compute(
        f"chain_{idx}",
        lambda: engine.get_option_chain(idx),
    )


@app.get("/api/historical/{token}/{interval}")
async def historical(token: str, interval: str = "5minute", days: int = 5):
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_historical(token, interval, days)


@app.get("/api/unusual")
async def unusual():
    return _fast_cache_or_compute("unusual", lambda: engine.get_unusual())


@app.get("/api/oi-summary")
async def oi_summary():
    return _fast_cache_or_compute("oi_summary", lambda: engine.get_oi_change_summary())


@app.get("/api/oi-insight/{index}")
async def oi_insight(index: str):
    """Combined Today's OI Change + Total OI with buyer interpretation.
    Single endpoint = easy fetch for UI."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        idx = index.upper()
        summary = engine.get_oi_change_summary()
        idx_data = summary.get(idx.lower(), {})
        strikes = idx_data.get("strikes", [])
        ltp = idx_data.get("ltp", 0)
        atm = idx_data.get("atm", 0)

        # ─── TODAY'S NET OI CHANGE (aaj 9:15 AM se ab tak) ───
        ce_added = sum(s["ceOIChange"] for s in strikes if s["ceOIChange"] > 0)
        ce_removed = sum(s["ceOIChange"] for s in strikes if s["ceOIChange"] < 0)
        pe_added = sum(s["peOIChange"] for s in strikes if s["peOIChange"] > 0)
        pe_removed = sum(s["peOIChange"] for s in strikes if s["peOIChange"] < 0)

        ce_net = ce_added + ce_removed   # net build-up
        pe_net = pe_added + pe_removed

        # PCR Change (today's flow)
        pcr_change = round(pe_net / ce_net, 2) if ce_net not in (0,) else 0
        if ce_net == 0 and pe_net != 0:
            pcr_change = 99.0 if pe_net > 0 else -99.0

        # Top strikes where OI built up TODAY
        top_ce_writing = sorted(strikes, key=lambda s: s["ceOIChange"], reverse=True)[:3]
        top_pe_writing = sorted(strikes, key=lambda s: s["peOIChange"], reverse=True)[:3]
        top_ce_covering = sorted(strikes, key=lambda s: s["ceOIChange"])[:3]
        top_pe_covering = sorted(strikes, key=lambda s: s["peOIChange"])[:3]

        # ─── TODAY'S INTERPRETATION (BUYER PERSPECTIVE) ───
        # CE writing > PE writing = bearish (sellers expect price down → BUY PE)
        # PE writing > CE writing = bullish (sellers expect price up/support → BUY CE)
        # Both writing heavy = range
        # Both covering = squeeze building
        today_signal = "WAIT"
        today_reason = "OI activity neutral"
        today_bias = "NEUTRAL"

        if ce_net > 0 and pe_net > 0:
            ratio = pe_net / max(ce_net, 1)
            if ratio > 1.5:
                today_signal = "BUY CE"
                today_bias = "BULLISH"
                today_reason = f"PE writers dominant ({pe_net/100000:.1f}L PE vs {ce_net/100000:.1f}L CE) — support building, expect upside"
            elif ratio < 0.66:
                today_signal = "BUY PE"
                today_bias = "BEARISH"
                today_reason = f"CE writers dominant ({ce_net/100000:.1f}L CE vs {pe_net/100000:.1f}L PE) — resistance building, expect downside"
            else:
                today_signal = "RANGE"
                today_bias = "NEUTRAL"
                today_reason = f"Both sides writing equally ({ce_net/100000:.1f}L CE / {pe_net/100000:.1f}L PE) — range bound"
        elif ce_net < 0 and pe_net > 0:
            today_signal = "BUY CE"
            today_bias = "BULLISH"
            today_reason = f"CE covering ({abs(ce_net)/100000:.1f}L) + PE writing ({pe_net/100000:.1f}L) — strong bullish setup"
        elif pe_net < 0 and ce_net > 0:
            today_signal = "BUY PE"
            today_bias = "BEARISH"
            today_reason = f"PE covering ({abs(pe_net)/100000:.1f}L) + CE writing ({ce_net/100000:.1f}L) — strong bearish setup"
        elif ce_net < 0 and pe_net < 0:
            today_signal = "WAIT"
            today_bias = "UNCERTAIN"
            today_reason = f"Both unwinding ({abs(ce_net)/100000:.1f}L CE / {abs(pe_net)/100000:.1f}L PE) — uncertainty, wait"

        # ─── TOTAL OI (cumulative — old + new combined) ───
        total_ce_oi = sum(s["ceOI"] for s in strikes)
        total_pe_oi = sum(s["peOI"] for s in strikes)
        total_pcr = round(total_pe_oi / max(total_ce_oi, 1), 2)

        # Walls (highest cumulative OI strikes)
        top_ce_wall = max(strikes, key=lambda s: s["ceOI"]) if strikes else None
        top_pe_wall = max(strikes, key=lambda s: s["peOI"]) if strikes else None

        # Total OI interpretation (where positions ALREADY parked)
        total_signal = "RANGE"
        total_reason = "Positions evenly distributed"
        if total_pcr > 1.3:
            total_signal = "BULL BIAS"
            total_reason = f"PCR {total_pcr} — heavy PE positions = strong support, market expects upside"
        elif total_pcr < 0.75:
            total_signal = "BEAR BIAS"
            total_reason = f"PCR {total_pcr} — heavy CE positions = strong resistance, market expects downside"
        else:
            total_signal = "RANGE"
            total_reason = f"PCR {total_pcr} — balanced positions"

        if top_ce_wall and top_pe_wall:
            total_reason += f" · Range {top_pe_wall['strike']}–{top_ce_wall['strike']}"

        return {
            "index": idx,
            "ltp": ltp,
            "atm": atm,
            # ─── TODAY (PRIMARY — what's happening NOW) ───
            "today": {
                "ce_oi_added": int(ce_added),
                "ce_oi_removed": int(ce_removed),
                "ce_oi_net": int(ce_net),
                "pe_oi_added": int(pe_added),
                "pe_oi_removed": int(pe_removed),
                "pe_oi_net": int(pe_net),
                "pcr_change": pcr_change,
                "signal": today_signal,
                "bias": today_bias,
                "reason": today_reason,
                "explain": {
                    "ce_oi_net": "CE OI added today (writers entering = resistance)",
                    "pe_oi_net": "PE OI added today (writers entering = support)",
                    "logic": "PE writing > CE writing = BULLISH (support stronger). CE writing > PE writing = BEARISH (resistance stronger).",
                },
                "top_ce_writing": [{"strike": s["strike"], "added": int(s["ceOIChange"])} for s in top_ce_writing if s["ceOIChange"] > 0],
                "top_pe_writing": [{"strike": s["strike"], "added": int(s["peOIChange"])} for s in top_pe_writing if s["peOIChange"] > 0],
                "top_ce_covering": [{"strike": s["strike"], "removed": int(s["ceOIChange"])} for s in top_ce_covering if s["ceOIChange"] < 0],
                "top_pe_covering": [{"strike": s["strike"], "removed": int(s["peOIChange"])} for s in top_pe_covering if s["peOIChange"] < 0],
            },
            # ─── TOTAL (SECONDARY — historical context) ───
            "total": {
                "ce_oi": int(total_ce_oi),
                "pe_oi": int(total_pe_oi),
                "pcr": total_pcr,
                "ce_wall_strike": top_ce_wall["strike"] if top_ce_wall else 0,
                "ce_wall_oi": int(top_ce_wall["ceOI"]) if top_ce_wall else 0,
                "pe_wall_strike": top_pe_wall["strike"] if top_pe_wall else 0,
                "pe_wall_oi": int(top_pe_wall["peOI"]) if top_pe_wall else 0,
                "signal": total_signal,
                "reason": total_reason,
                "explain": {
                    "ce_oi": "Total CE positions parked (cumulative — old + new)",
                    "pe_oi": "Total PE positions parked (cumulative)",
                    "pcr": "Put-Call Ratio = PE OI / CE OI. >1.3 = bullish lean, <0.75 = bearish.",
                    "walls": "Highest OI strikes act as support (PE wall) / resistance (CE wall)",
                },
            },
            # ─── PRIMARY VERDICT (combined view) ───
            "primary_signal": today_signal,
            "primary_reason": today_reason,
            "data_source": "today_oi_change",
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/backtest-stats")
async def backtest_stats():
    if not engine or not hasattr(engine, 'backtest_tracker') or not engine.backtest_tracker:
        return {"total": 0, "message": "Backtest tracker not running"}
    return engine.backtest_tracker.get_stats()


@app.get("/api/oi-timeline")
async def oi_timeline():
    return _get_or_cache("oi_timeline", lambda: engine.get_oi_timeline(), ttl=30)


@app.get("/api/fii-dii")
async def fii_dii():
    return _get_or_cache("fii_dii", lambda: engine.get_fii_dii(), ttl=3600)

@app.get("/api/global-cues")
async def global_cues():
    return _get_or_cache("global_cues", lambda: engine.get_global_cues(), ttl=900)

@app.get("/api/multi-timeframe")
async def multi_timeframe():
    return _get_or_cache("multi_tf", lambda: engine.get_multi_timeframe(), ttl=60)


@app.get("/api/seller-summary")
async def seller_summary():
    return _get_or_cache("seller_summary", lambda: engine.get_seller_summary(), ttl=15)


@app.get("/api/trade-analysis")
async def trade_analysis():
    return _get_or_cache("trade_analysis", lambda: engine.get_trade_analysis(), ttl=15)


@app.get("/api/hidden-shift")
async def hidden_shift():
    return _get_or_cache("hidden_shift", lambda: engine.get_hidden_shift(), ttl=30)


@app.get("/api/signals")
async def signals():
    return _get_or_cache("signals", lambda: engine.get_signals(), ttl=30)


@app.get("/api/price-action")
async def price_action(expiry: str = None):
    if expiry:
        # Don't cache expiry-specific requests
        if not engine or not engine.running:
            return JSONResponse({"error": "Engine not running"}, status_code=503)
        return engine.get_price_action(expiry_str=expiry)
    return _get_or_cache("price_action", lambda: engine.get_price_action())


@app.get("/api/intraday")
async def intraday():
    return _get_or_cache("intraday", lambda: engine.get_intraday())


@app.get("/api/nextday")
async def nextday():
    return _get_or_cache("nextday", lambda: engine.get_nextday())


@app.get("/api/weekly")
async def weekly():
    return _get_or_cache("weekly", lambda: engine.get_weekly())


@app.get("/api/expiries/{index}")
async def expiries(index: str):
    if not engine or not engine.running:
        cached = get_cached(f"expiries_{index}")
        if cached:
            return cached
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    data = engine.get_available_expiries(index)
    save_cache(f"expiries_{index}", data)
    return data


@app.get("/api/expiry-chain/{index}/{expiry}")
async def expiry_chain(index: str, expiry: str):
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_expiry_chain(index, expiry)


@app.get("/api/trap/scan")
async def trap_scan():
    """Run full trap fingerprint scan now."""
    if not engine or not engine.running or not hasattr(engine, 'trap_scanner') or not engine.trap_scanner:
        return JSONResponse({"error": "Trap scanner not running"}, status_code=503)
    result = engine.trap_scanner.run_scan()
    save_cache("trap_scan", result)
    return result


@app.get("/api/trap/alerts")
async def trap_alerts():
    """Get all active fingerprints from latest scan."""
    if not engine or not hasattr(engine, 'trap_scanner') or not engine.trap_scanner:
        cached = get_cached("trap_alerts")
        return cached if cached else []
    alerts = engine.trap_scanner.get_alerts()
    save_cache("trap_alerts", alerts)
    return alerts


@app.get("/api/trap/verdict")
async def trap_verdict():
    """Cross-engine trap verdict — combines all engines."""
    return _get_or_cache("trap_verdict", lambda: engine.get_trap_verdict(), ttl=60)


@app.get("/api/debug/verdict-compute")
async def debug_verdict_compute():
    """DEBUG (2026-06-08): force a fresh _compute_trap_verdict() call
    and surface any exception. /api/trap/verdict returns stale cached
    value when fresh compute raises — this endpoint bypasses the cache
    so we can SEE the actual failure.
    """
    if engine is None:
        return JSONResponse({"error": "engine is None"}, status_code=400)
    import traceback as _tb
    try:
        # Use force_recompute=True to bypass the engine's 15s cache
        result = engine.get_trap_verdict(force_recompute=True)
        return {
            "ok": True,
            "result_keys": list(result.keys()) if isinstance(result, dict) else None,
            "nifty_action": (result.get("nifty", {}) or {}).get("action"),
            "nifty_prob": (result.get("nifty", {}) or {}).get("winProbability"),
            "banknifty_action": (result.get("banknifty", {}) or {}).get("action"),
            "banknifty_prob": (result.get("banknifty", {}) or {}).get("winProbability"),
            "result": result,
        }
    except Exception as e:
        return JSONResponse({
            "ok": False,
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback": _tb.format_exc().split("\n")[-15:],
        }, status_code=500)


@app.get("/api/trap/history")
async def trap_history():
    """Get fingerprint history (last 7 days)."""
    if not engine or not hasattr(engine, 'trap_scanner') or not engine.trap_scanner:
        return []
    return engine.trap_scanner.get_history(days=7)


@app.get("/api/trap/today")
async def trap_today():
    """Get all signals from today — stays visible all day."""
    if not engine or not hasattr(engine, 'trap_scanner') or not engine.trap_scanner:
        cached = get_cached("trap_today")
        return cached if cached else []
    signals = engine.trap_scanner.get_today_signals()
    save_cache("trap_today", signals)
    return signals


@app.get("/api/trap/clusters")
async def trap_clusters():
    """Get active cluster alerts."""
    if not engine or not hasattr(engine, 'trap_scanner') or not engine.trap_scanner:
        return []
    return engine.trap_scanner.get_clusters()


@app.get("/api/trades/open")
async def trades_open():
    """Open trades — read from cache (background-populated, ~3s fresh).
    Cache invalidated when log_trade fires (so new trades appear instantly).
    """
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return _fast_cache_or_compute(
        "trades_open",
        lambda: engine.trade_manager.get_open_trades(),
        populator_max_age=8.0,  # background populator updates every 3s
        fallback_ttl=2.0,
    )

@app.get("/api/trades/alerts")
async def trades_alerts():
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return engine.trade_manager.get_position_alerts()

@app.get("/api/trades/closed")
async def trades_closed(days: int = 365):
    """Default 365 days so users see full history (was 7-day window)."""
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return engine.trade_manager.get_closed_trades(days=days)

@app.get("/api/trades/stats")
async def trades_stats(days: int = 365):
    """Default 365 days so stats reflect full history (was 30-day window)."""
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return {"total": 0, "open": 0, "wins": 0, "losses": 0, "winRate": 0, "totalPnl": 0}
    return engine.trade_manager.get_stats(days=days)

@app.get("/api/trades/date/{date}")
async def trades_by_date(date: str):
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return engine.trade_manager.get_trades_by_date(date)

@app.get("/api/trades/monthly/{year}/{month}")
async def trades_monthly(year: int, month: int):
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return {"month": f"{year}-{month:02d}", "trades": [], "stats": {"total": 0}}
    return engine.trade_manager.get_monthly_report(year, month)

@app.get("/api/trades/dates")
async def trades_dates():
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return engine.trade_manager.get_all_dates()

@app.get("/api/trades/stop-hunts")
async def trades_stop_hunts():
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return engine.trade_manager.get_stop_hunts()


@app.get("/api/trades/alerts-feed")
async def trades_alert_feed():
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return engine.trade_manager.get_trade_alerts()


# ── Buyer Suites endpoints (20 engines across 4 modules) ──

@app.get("/api/buyer/greeks/{index}")
async def buyer_greeks(index: str):
    """6 Greeks engines: GEX, IVR, IV Skew, Vol Term, Theta, VIX Term."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from options_greeks import score_all_greeks_buyer
        return score_all_greeks_buyer(engine, index.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/buyer/oi-intel/{index}")
async def buyer_oi_intel(index: str):
    """5 OI intelligence engines: Max Pain Drift, Strike Rotation, Delta-Adj, Fresh/Rolled, OTM/ITM."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from oi_intelligence import score_all_oi_intel_buyer
        from datetime import datetime, timedelta
        import pytz
        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        idx = index.upper()
        is_exp = (idx == "NIFTY" and now.weekday() == 1) or \
                 (idx == "BANKNIFTY" and now.weekday() == 3 and (now + timedelta(days=7)).month != now.month)
        return score_all_oi_intel_buyer(engine, idx, is_exp)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/buyer/time-patterns/{index}")
async def buyer_time_patterns(index: str):
    """4 time engines: ORB, Power Hour, Pre-market Gap, 0DTE."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from time_patterns import score_all_time_patterns_buyer
        return score_all_time_patterns_buyer(engine, index.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/buyer/market-structure/{index}")
async def buyer_market_structure(index: str):
    """5 structure engines: Sweep, Pin Risk, Stop Hunt, Cross-Asset, Sectoral."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from market_structure import score_all_market_structure_buyer
        from datetime import datetime, timedelta
        import pytz
        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        idx = index.upper()
        is_exp = (idx == "NIFTY" and now.weekday() == 1) or \
                 (idx == "BANKNIFTY" and now.weekday() == 3 and (now + timedelta(days=7)).month != now.month)
        return score_all_market_structure_buyer(engine, idx, is_exp)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/buyer/full/{index}")
async def buyer_full_suite(index: str):
    """All 20 engines combined summary."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from options_greeks import score_all_greeks_buyer
        from oi_intelligence import score_all_oi_intel_buyer
        from time_patterns import score_all_time_patterns_buyer
        from market_structure import score_all_market_structure_buyer
        from datetime import datetime, timedelta
        import pytz
        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        idx = index.upper()
        is_exp = (idx == "NIFTY" and now.weekday() == 1) or \
                 (idx == "BANKNIFTY" and now.weekday() == 3 and (now + timedelta(days=7)).month != now.month)
        g = score_all_greeks_buyer(engine, idx)
        o = score_all_oi_intel_buyer(engine, idx, is_exp)
        t = score_all_time_patterns_buyer(engine, idx)
        s = score_all_market_structure_buyer(engine, idx, is_exp)
        total_bull = g.get("bull", 0) + o.get("bull", 0) + t.get("bull", 0) + s.get("bull", 0)
        total_bear = g.get("bear", 0) + o.get("bear", 0) + t.get("bear", 0) + s.get("bear", 0)
        return {
            "index": idx,
            "is_expiry_day": is_exp,
            "total_bull": total_bull,
            "total_bear": total_bear,
            "suites": {
                "greeks": g,
                "oi_intel": o,
                "time_patterns": t,
                "market_structure": s,
            },
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Smart Money endpoints ──
# IMPORTANT: STATIC paths MUST be declared BEFORE the dynamic
# `/{index}` catch-all. FastAPI matches in declaration order; if
# `/{index}` came first, requests for `/live` would be captured as
# index="live" and return 400. Today's bug: Smart Money panel was
# broken because /api/smart-money/live & /history were shadowed.


@app.get("/api/smart-money/live")
async def smart_money_live_v2():
    """Latest smart money classification per index. Cached 10s."""
    try:
        from smart_money_detector import get_live_state
        return _get_or_cache("smart_money_live", get_live_state, ttl=10)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/smart-money/history")
async def smart_money_history_v2(idx: str = "", limit: int = 50):
    """Today's logged strong findings (score ≥ 6)."""
    try:
        from smart_money_detector import get_strike_history_log
        return {"events": get_strike_history_log(idx.upper() if idx else None, limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/smart-money/pulse-now")
async def smart_money_pulse_now_v2():
    """Force an immediate smart money analysis (bypass 2-min cycle).
    Captures OI minute snapshot first, then analyzes. Matches frontend
    SmartMoneyPanel's pulse button."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from oi_minute_capture import capture_pulse
        capture_pulse(engine)
        from smart_money_detector import analyze_pulse
        return analyze_pulse()
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.get("/api/smart-money/strike/{strike}")
async def smart_money_strike_v2(strike: int, idx: str = "NIFTY", minutes: int = 60):
    """Per-strike per-minute OI history (drill-down chart). Frontend
    SmartMoneyPanel calls this with idx + minutes."""
    try:
        from oi_minute_capture import get_strike_history
        return {
            "idx": idx.upper(), "strike": strike, "minutes": minutes,
            "history": get_strike_history(idx.upper(), strike, minutes),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/smart-money/{index}")
async def smart_money_data(index: str):
    """Get smart money signals for a specific index (NIFTY/BANKNIFTY).
    Subpaths /live, /history, /strike, /pulse-now are matched first by
    declarations above; this catches only true index queries."""
    global engine
    if not engine or not hasattr(engine, 'smart_money_state') or not engine.smart_money_state:
        return {"error": "Engine not running or smart_money state unavailable"}
    try:
        from smart_money import score_smart_money
        idx = index.upper()
        if idx not in ("NIFTY", "BANKNIFTY"):
            return JSONResponse({"error": "Invalid index"}, status_code=400)
        result = score_smart_money(engine.smart_money_state, engine, idx)
        snap_keys = [k for k in engine.smart_money_state.snapshots.keys() if k[0] == idx]
        result["tracked_strikes"] = len(snap_keys)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Scalper Mode endpoints (aggressive paper trader) ──

@app.get("/api/scalper/status")
async def scalper_status():
    try:
        import scalper_mode
        return {
            "enabled": scalper_mode.is_scalper_enabled(),
            # Auto-trade kill switch (2026-05-18 Phase 1 audit fix).
            # When False: signals computed but trades NOT fired.
            # Set env var SCALPER_AUTO_TRADE=on to re-enable.
            "auto_trade_enabled": scalper_mode.SCALPER_AUTO_TRADE_ENABLED,
            "auto_trade_pause_reason": (
                None if scalper_mode.SCALPER_AUTO_TRADE_ENABLED
                else "Paused 2026-05-18 — 4-session audit: 38% winrate, -₹119k loss. "
                     "Re-enable after directional-gate + theta-protect fixes."
            ),
            "config": {
                "threshold": scalper_mode.SCALPER_THRESHOLD,
                "dailyCap": scalper_mode.SCALPER_DAILY_CAP,
                "slPct": scalper_mode.SCALPER_SL_PCT * 100,
                "t1Pct": scalper_mode.SCALPER_T1_PCT * 100,
                "t2Pct": scalper_mode.SCALPER_T2_PCT * 100,
                "riskPct": scalper_mode.SCALPER_RISK_PCT,
                "maxHoldMin": scalper_mode.SCALPER_MAX_HOLD_MIN,
            },
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/scalper/enable")
async def scalper_enable():
    try:
        import scalper_mode
        scalper_mode.enable_scalper()
        return {"status": "enabled"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/scalper/disable")
async def scalper_disable():
    try:
        import scalper_mode
        scalper_mode.disable_scalper()
        return {"status": "disabled"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/scalper/trades/open")
async def scalper_open_trades():
    try:
        import scalper_mode
        return scalper_mode.get_scalper_open_trades()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/scalper/trades/closed")
async def scalper_closed_trades(days: int = 7):
    try:
        import scalper_mode
        return scalper_mode.get_scalper_closed_trades(days)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/scalper/stats")
async def scalper_stats():
    try:
        import scalper_mode
        return scalper_mode.get_scalper_stats()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/scalper/smart-sl")
async def scalper_smart_sl_get():
    """Get Smart SL config (enabled, spot_anchor_pct) + ladder structure."""
    try:
        import scalper_mode
        cfg = scalper_mode.get_smart_sl_config()
        return {
            **cfg,
            "ladder": [
                {"stage": i, "trigger_pct": t, "sl_offset_pct": s, "label": l}
                for i, (t, s, l) in enumerate(scalper_mode.SMART_SL_LADDER)
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/scalper/smart-sl/toggle")
async def scalper_smart_sl_toggle():
    """Flip Smart SL ON/OFF. Takes effect on next tick — no trade disruption."""
    try:
        import scalper_mode
        cur = scalper_mode.get_smart_sl_config()
        new = scalper_mode.set_smart_sl_config(enabled=(not cur["enabled"]))
        return {"ok": True, "enabled": new["enabled"], "config": new}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/scalper/smart-sl/config")
async def scalper_smart_sl_set(body: dict):
    """Set Smart SL fields explicitly: {enabled, spot_anchor_pct}."""
    try:
        import scalper_mode
        new = scalper_mode.set_smart_sl_config(
            enabled=body.get("enabled"),
            spot_anchor_pct=body.get("spot_anchor_pct"),
        )
        return {"ok": True, "config": new}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/scalper/trades/{trade_id}/ladder")
async def scalper_trade_ladder(trade_id: int):
    """Live ladder progress for one open trade — for UI rendering."""
    try:
        import scalper_mode
        conn = scalper_mode._conn()
        row = conn.execute(
            "SELECT entry_price, current_ltp, smart_sl_stage, smart_sl_value, entry_spot, idx, action FROM scalper_trades WHERE id=?",
            (trade_id,)
        ).fetchone()
        conn.close()
        if not row:
            return JSONResponse({"error": "Trade not found"}, status_code=404)
        cfg = scalper_mode.get_smart_sl_config()
        ladder = scalper_mode.get_ladder_progress(
            row["entry_price"],
            row["current_ltp"] or row["entry_price"],
            current_stage_saved=row["smart_sl_stage"] or 0,
        )
        return {
            "trade_id": trade_id,
            "entry_price": row["entry_price"],
            "current_ltp": row["current_ltp"],
            "current_stage": row["smart_sl_stage"] or 0,
            "active_sl": row["smart_sl_value"] or row["entry_price"] * 0.85,
            "entry_spot": row["entry_spot"],
            "spot_anchor_pct": cfg["spot_anchor_pct"],
            "smart_sl_enabled": cfg["enabled"],
            "ladder": ladder,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Reversal Cockpit endpoints (B4.11-14) ──
# All 4 cockpit panels in one composable view: OI delta (writer/buyer
# pressure), per-strike minute history, capitulation score, smart money.
# Cached briefly so a tab open with N components shares 1 fetch per panel.

@app.get("/api/scalper/oi-context")
async def scalper_oi_context(idx: str = "NIFTY"):
    """Writer/buyer pressure breakdown for an index (B4.12 gauge data).
    Returns: ce/pe 15m delta %, signals (writer adding/covering), PCR drift,
    max-pain shift. Cached 10s — 60s pulse anyway."""
    try:
        from oi_delta_tracker import assess as _oi_assess
        cache_key = f"scalper_oi_context:{idx.upper()}"
        return _get_or_cache(cache_key, lambda: _oi_assess(idx.upper()), ttl=5)  # C1
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/scalper/strike-history")
async def scalper_strike_history(idx: str = "NIFTY", strike: int = 0,
                                  minutes: int = 30):
    """Per-strike per-minute OI history (B4.11 chart data).
    Returns rows of {ts, ce_oi, ce_ltp, pe_oi, pe_ltp, spot}."""
    try:
        from oi_minute_capture import get_strike_history
        if not strike:
            return {"history": []}
        history = get_strike_history(idx.upper(), int(strike), minutes=minutes)
        return {"idx": idx.upper(), "strike": int(strike),
                "minutes": minutes, "history": history}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/scalper/trades/{trade_id}")
async def scalper_delete_trade(trade_id: int):
    """USER-ONLY manual delete. No auto-deletion happens system-wide."""
    try:
        import scalper_mode
        conn = scalper_mode._conn()
        conn.execute("DELETE FROM scalper_ticks WHERE trade_id=?", (trade_id,))
        cur = conn.execute("DELETE FROM scalper_trades WHERE id=?", (trade_id,))
        conn.commit()
        conn.close()
        return {"ok": True, "deleted": cur.rowcount}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/scalper/capital-usage")
async def scalper_capital_usage():
    """Live capital usage breakdown — committed, available, unrealized P&L.
    Reads CURRENT chain LTP for zero-latency live values (no DB lag)."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        import scalper_mode
        usage = scalper_mode.get_capital_usage()
        # Patch open trades with REAL-TIME chain LTP (engine.prices in-memory)
        for ot in usage.get("open_trades", []):
            chain = engine.chains.get(ot["idx"], {})
            strike_data = chain.get(ot["strike"], {})
            side_key = "ce_ltp" if "CE" in ot.get("action", "") else "pe_ltp"
            live_ltp = strike_data.get(side_key) or 0
            if live_ltp > 0:
                ot["current"] = live_ltp
                ot["live_value"] = round(live_ltp * ot["qty"], 2)
                ot["unrealized"] = round((live_ltp - ot["entry"]) * ot["qty"], 2)
        # Recompute aggregates
        usage["live_value"] = round(sum(ot["live_value"] for ot in usage["open_trades"]), 2)
        usage["unrealized_pnl"] = round(sum(ot["unrealized"] for ot in usage["open_trades"]), 2)
        usage["total_today_pnl"] = round(usage.get("realized_today", 0) + usage["unrealized_pnl"], 2)
        return usage
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _fetch_scalper_open_trades_sync():
    """Sync DB read for scalper live-prices — runs in thread executor.

    Pulled out so the async endpoint can offload via asyncio.to_thread()
    and not block the FastAPI event loop. With ~10 users polling at 2s,
    inline sync sqlite was eating 75-750ms of event loop per second,
    causing dashboard freeze under load.
    """
    import scalper_mode
    conn = scalper_mode._conn()
    try:
        rows = conn.execute(
            "SELECT id, idx, strike, action, entry_price, qty FROM scalper_trades WHERE status='OPEN'"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/scalper/live-prices")
async def scalper_live_prices():
    """Zero-latency live LTP per open scalper trade. Pulls direct from engine.chains
    (in-memory, no DB hit on the hot path). Returns {trade_id: ltp, pnl_rupees, pnl_pct}.
    Frontend polls every 2s. The DB read is offloaded to a thread so the
    event loop stays free for other concurrent requests."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        import asyncio
        rows = await asyncio.to_thread(_fetch_scalper_open_trades_sync)
        out = []
        import time as _time
        ts = int(_time.time() * 1000)
        for r in rows:
            chain = engine.chains.get(r["idx"], {})
            strike_data = chain.get(r["strike"], {})
            side_key = "ce_ltp" if "CE" in r["action"] else "pe_ltp"
            ltp = strike_data.get(side_key) or 0
            entry = r["entry_price"]
            qty = r["qty"]
            pnl_rupees = round((ltp - entry) * qty, 2) if ltp > 0 else 0
            pnl_pct = round((ltp - entry) / entry * 100, 2) if ltp > 0 and entry > 0 else 0
            out.append({
                "id": r["id"], "ltp": ltp, "entry": entry, "qty": qty,
                "pnl_rupees": pnl_rupees, "pnl_pct": pnl_pct, "ts": ts,
            })
        return {"prices": out, "ts": ts}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/trades/live-prices")
async def trades_live_prices():
    """Zero-latency live LTP per open MAIN trade — reads engine.chains
    in-memory (no DB hit, no cache). The PnL tab polls this ~1s so it
    shows real-time premium + P&L instead of the 15s full-refresh lag.
    Mirror of /api/scalper/live-prices for the swing/PnL side."""
    if not engine or not hasattr(engine, "trade_manager") or not engine.trade_manager:
        return {"prices": [], "ts": 0}
    try:
        import asyncio
        import time as _time
        ts = int(_time.time() * 1000)
        # Offload sync sqlite read to thread executor — keeps FastAPI
        # event loop free for other concurrent requests under load.
        open_trades = await asyncio.to_thread(engine.trade_manager.get_open_trades)
        out = []
        for t in open_trades:
            chain = engine.chains.get(t.get("idx"), {})
            strike_data = chain.get(t.get("strike"), {})
            side_key = "ce_ltp" if "CE" in (t.get("action") or "") else "pe_ltp"
            ltp = strike_data.get(side_key) or 0
            entry = t.get("entry_price") or 0
            qty = t.get("qty") or 0
            pnl_rupees = round((ltp - entry) * qty, 2) if ltp > 0 else 0
            pnl_pct = round((ltp - entry) / entry * 100, 2) if ltp > 0 and entry > 0 else 0
            out.append({
                "id": t.get("id"), "ltp": ltp, "entry": entry, "qty": qty,
                "pnl_rupees": pnl_rupees, "pnl_pct": pnl_pct, "ts": ts,
            })
        return {"prices": out, "ts": ts}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/scalper/trades/{trade_id}/ticks")
async def scalper_trade_ticks(trade_id: int, limit: int = 500):
    """Tick history for one scalper trade (live LTP samples)."""
    try:
        import scalper_mode
        return {"trade_id": trade_id, "ticks": scalper_mode.get_trade_ticks(trade_id, limit=limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/scalper/trades/{trade_id}/exit")
async def scalper_manual_exit(trade_id: int, bg: BackgroundTasks, body: dict = None):
    """Manually exit an open scalper trade at current/given LTP.
    N1: capital tracker write deferred to bg task — endpoint returns
    PnL/exit data ~30ms faster."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        import scalper_mode
        body = body or {}
        # Resolve current LTP from chain if not provided
        ltp = body.get("ltp")
        if not ltp or ltp <= 0:
            # Look up trade to find strike + side
            conn = scalper_mode._conn()
            row = conn.execute(
                "SELECT idx, strike, action, current_ltp FROM scalper_trades WHERE id=?",
                (trade_id,)
            ).fetchone()
            conn.close()
            if not row:
                return JSONResponse({"error": "Trade not found"}, status_code=404)
            chain = engine.chains.get(row["idx"], {})
            strike_data = chain.get(row["strike"], {})
            side_key = "ce_ltp" if "CE" in row["action"] else "pe_ltp"
            ltp = strike_data.get(side_key) or row["current_ltp"] or 0
        # Sync exit (critical UPDATE), capital tracker as bg task
        result = scalper_mode.manual_exit(trade_id, current_ltp=float(ltp),
                                          defer_capital_track=True)
        if result.get("ok"):
            bg.add_task(scalper_mode.record_capital_after_exit,
                        "SCALPER", result["pnl_rupees"], trade_id,
                        f"Manual exit @ ₹{result['exit_price']:.2f}")
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/scalper/config")
async def scalper_config_get():
    """Get user-configurable scalper settings (capital, qty, SL/T1/T2, threshold)."""
    try:
        import scalper_mode
        return scalper_mode.get_scalper_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/scalper/config")
async def scalper_config_set(body: dict):
    """Update scalper settings. Body can contain any of:
      capital (₹), nifty_qty, banknifty_qty, sl_pct, t1_pct, t2_pct, threshold, daily_cap.
    Only provided fields are updated."""
    try:
        import scalper_mode
        updated = scalper_mode.set_scalper_config(
            capital=body.get("capital"),
            nifty_qty=body.get("nifty_qty"),
            banknifty_qty=body.get("banknifty_qty"),
            sl_pct=body.get("sl_pct"),
            t1_pct=body.get("t1_pct"),
            t2_pct=body.get("t2_pct"),
            threshold=body.get("threshold"),
            daily_cap=body.get("daily_cap"),
        )
        return {"ok": True, "config": updated}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Rejection Zone Engine endpoints ──
# IMPORTANT: specific routes MUST come before /api/zones/{index} catch-all

@app.get("/api/zones/hidden-events")
async def zones_hidden_events(idx: str = None, hours: int = 2, limit: int = 30):
    """Recent hidden activity feed (mass buys, stealth builds, smart exits)."""
    try:
        import rejection_engine
        events = rejection_engine.get_recent_hidden_events(idx=idx, hours=hours, limit=limit)
        return events  # always a list
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/zones/capture-now")
async def zones_capture_now():
    """Manual trigger — force capture price + OI snapshot now (testing)."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        import rejection_engine
        rejection_engine.capture_price_sample(engine)
        rejection_engine.capture_oi_snapshot(engine)
        return {"ok": True, "captured_at": datetime.now().isoformat()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/zones/chart/{index}")
async def zones_chart(index: str, days: int = 5):
    """Light chart data: price series + zone overlay levels."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        import rejection_engine
        return rejection_engine.get_chart_data(engine, index.upper(), days=days)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/zones/{index}")
async def zones_analysis(index: str):
    """Rejection zones (upside + downside) with deep OI + hidden activity + verdict."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    # Reject reserved paths that should have matched specific routes
    if index.lower() in ("hidden-events", "capture-now", "chart"):
        return JSONResponse({"error": f"Invalid index: {index}"}, status_code=400)
    try:
        import rejection_engine
        return rejection_engine.get_zones_analysis(engine, index.upper())
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Backtest Validator endpoints ──

@app.get("/api/backtest/full")
async def backtest_full():
    """Replay all closed trades through 18 filters. Compare system vs reality."""
    try:
        from backtest_validator import run_full_backtest
        return run_full_backtest()
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/backtest/filter-stats")
async def backtest_filter_stats():
    """Quick filter performance summary (no full trade list)."""
    try:
        from backtest_validator import get_filter_stats_only
        return get_filter_stats_only()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/backtest/trade/{trade_id}")
async def backtest_one_trade(trade_id: int, source: str = "MAIN"):
    """Backtest analysis for a specific trade."""
    try:
        from backtest_validator import get_trade_analysis
        return get_trade_analysis(trade_id, source=source.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Position Watcher endpoints (Active Position Manager) ──

@app.get("/api/positions/health")
async def positions_health():
    """Latest health snapshots for all open trades (both PnL + Scalper).
    Cached 5s — multiple components polling shouldn't recompute every time."""
    try:
        from position_watcher import get_last_health
        return _get_or_cache("positions_health",
                             lambda: {"positions": get_last_health()}, ttl=2)  # C1: 5→2s
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/positions/health/{trade_id}")
async def position_health_one(trade_id: int, source: str = "MAIN"):
    """Latest health for one trade + history."""
    try:
        from position_watcher import get_health_for_trade, get_health_history
        cur = get_health_for_trade(source.upper(), trade_id)
        hist = get_health_history(source.upper(), trade_id, limit=200)
        return {"current": cur, "history": hist}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/admin/main-gates-trace")
async def admin_main_gates_trace():
    """Trace every gate for current Main mode verdict.

    Shows which specific gate is blocking Main from trading.
    Run this when Main mode has 0 trades despite high verdict.
    """
    out = {"timestamp": None, "verdict_summary": {}, "gate_results": {}}
    try:
        from datetime import datetime as _dt
        import pytz as _pytz
        out["timestamp"] = _dt.now(_pytz.timezone("Asia/Kolkata")).isoformat()

        global engine
        eng = engine
        if not eng:
            return {"error": "engine not running"}

        try:
            if hasattr(eng, "get_trap_verdict"):
                verdict = eng.get_trap_verdict() or {}
            elif hasattr(eng, "get_full_verdict"):
                verdict = eng.get_full_verdict() or {}
            else:
                verdict = {}
        except Exception:
            verdict = {}
        out["verdict_summary"] = {
            idx: {
                "action": v.get("action", "?") if isinstance(v, dict) else "?",
                "prob": v.get("winProbability", 0) if isinstance(v, dict) else 0,
            } for idx, v in verdict.items() if isinstance(v, dict)
        }

        # Trace each idx — verdict keys are lowercase
        for idx in ("NIFTY", "BANKNIFTY"):
            v = verdict.get(idx.lower(), {})
            if not isinstance(v, dict) or not v:
                out["gate_results"][idx] = {"error": f"no verdict for {idx}"}
                continue
            gates = []
            action = v.get("action", "")
            prob = v.get("winProbability", 0)
            try:
                from expiry_day_guard import should_skip
                blocked = should_skip(source="trace")
                gates.append({"gate": "G0a expiry_day", "blocked": blocked,
                              "detail": "Tuesday morning block" if blocked else "OK"})
            except Exception as e:
                gates.append({"gate": "G0a expiry_day", "error": str(e)})

            try:
                from circuit_breaker import should_block
                blocked = should_block(tab="MAIN", source="trace")
                gates.append({"gate": "G0b circuit_breaker", "blocked": blocked,
                              "detail": "daily cap or streak" if blocked else "OK"})
            except Exception as e:
                gates.append({"gate": "G0b circuit_breaker", "error": str(e)})

            try:
                from profit_target import should_block as _pt_block
                blocked = _pt_block(tab="MAIN", source="trace")
                gates.append({"gate": "G0d profit_target", "blocked": blocked,
                              "detail": "profit booked" if blocked else "OK"})
            except Exception as e:
                gates.append({"gate": "G0d profit_target", "error": str(e)})

            try:
                import os as _os
                if _os.environ.get("CALIBRATION_GATE_ENABLED", "on").lower() == "on":
                    from calibration import calibrated_wr as _cal_fn
                    cal_min = float(_os.environ.get("MAIN_CALIBRATION_MIN_WR", "35"))
                    cal_wr = _cal_fn(int(prob), engine_type="main", action=action)
                    blocked = cal_wr is not None and cal_wr < cal_min
                    gates.append({"gate": "G0c calibration", "blocked": blocked,
                                  "detail": f"cal_wr={cal_wr} threshold={cal_min}"})
                else:
                    gates.append({"gate": "G0c calibration", "blocked": False,
                                  "detail": "disabled"})
            except Exception as e:
                gates.append({"gate": "G0c calibration", "error": str(e)})

            try:
                import structure_gate as _sg
                if _sg.master_mode() != "off" and _sg.main_enabled():
                    sg_dec = _sg.evaluate_entry(engine=eng, idx=idx,
                                                  proposed_action=action,
                                                  source="trace")
                    blocked = not sg_dec.get("allow", True)
                    gates.append({"gate": "G0f structure", "blocked": blocked,
                                  "detail": sg_dec.get("reason", "OK")})
                else:
                    gates.append({"gate": "G0f structure", "blocked": False,
                                  "detail": "disabled"})
            except Exception as e:
                gates.append({"gate": "G0f structure", "error": str(e)})

            try:
                from early_move.entry_gate import evaluate_entry as _em_eval
                em = _em_eval(engine=eng, idx=idx,
                               proposed_action=action, source="trace")
                blocked = not em.get("allow", True)
                gates.append({"gate": "G0e early_move", "blocked": blocked,
                              "detail": em.get("reason", "OK")})
            except Exception as e:
                gates.append({"gate": "G0e early_move", "error": str(e)})

            # OI Shift (2026-06-13 fix: correct function name + signature)
            try:
                from oi_shift_detector import is_trade_against_shift
                spot = eng.prices.get(eng.spot_tokens.get(idx), {}).get("ltp", 0) if hasattr(eng, 'prices') else 0
                blocked, reason = is_trade_against_shift(idx, action, spot)
                gates.append({"gate": "A2 oi_shift", "blocked": blocked,
                              "detail": reason or "OK"})
            except Exception as e:
                gates.append({"gate": "A2 oi_shift", "error": str(e)[:80]})

            # Divergence (2026-06-13 fix: correct function name + signature)
            try:
                from divergence_filter import check_divergence
                spot = eng.prices.get(eng.spot_tokens.get(idx), {}).get("ltp", 0) if hasattr(eng, 'prices') else 0
                # check_divergence may need strike + premium — gracefully handle
                result = check_divergence(eng, idx, action, 0, 0)
                blocked = result.get("block", False) if isinstance(result, dict) else False
                reason = result.get("reason", "OK") if isinstance(result, dict) else "OK"
                gates.append({"gate": "A4 divergence", "blocked": blocked,
                              "detail": reason})
            except Exception as e:
                gates.append({"gate": "A4 divergence", "error": str(e)[:80]})

            # Truth/Lie (2026-06-13 fix: correct function name + signature)
            try:
                from truth_lie_detector import check_pattern
                # Need top_engine and vix — fetch from engine if available
                vix = 15  # default
                try:
                    vix = eng.prices.get('VIX', {}).get('ltp', 15) if hasattr(eng, 'prices') else 15
                except Exception:
                    pass
                tl = check_pattern(action, prob, "trap", vix) or {}
                blocked = tl.get("block", False) if isinstance(tl, dict) else False
                gates.append({"gate": "A6 truth_lie", "blocked": blocked,
                              "detail": (tl.get("message", "OK") if isinstance(tl, dict) else "OK")[:80]})
            except Exception as e:
                gates.append({"gate": "A6 truth_lie", "error": str(e)[:80]})

            # Quality (2026-06-13 fix: correct module + function name)
            try:
                # Try a few candidate modules — entry_filters may expose differently
                q = None
                try:
                    from quality_score import calculate_quality
                    verdict_data = {"action": action, "winProbability": prob}
                    q = calculate_quality(verdict_data, action, idx, eng)
                except Exception:
                    try:
                        from entry_filters import quality_score
                        q = quality_score(verdict_data={"action": action, "winProbability": prob}, idx=idx)
                    except Exception:
                        q = None
                if q:
                    score = q.get("score", 0) if isinstance(q, dict) else 0
                    blocked = score < 5
                    grade = q.get("grade", "?") if isinstance(q, dict) else "?"
                    gates.append({"gate": "A8 quality", "blocked": blocked,
                                  "detail": f"score={score}/10 grade={grade}"})
                else:
                    gates.append({"gate": "A8 quality", "blocked": False,
                                  "detail": "module not exporting expected name"})
            except Exception as e:
                gates.append({"gate": "A8 quality", "error": str(e)[:80]})

            # Buyer filter (2026-06-13 fix: correct function name)
            try:
                from buyer_filters import check_buyer_filters
                result = check_buyer_filters(eng, idx, action)
                if isinstance(result, dict):
                    blocked = result.get("block", False)
                    reason = result.get("reason", "OK")
                elif isinstance(result, tuple) and len(result) == 2:
                    blocked, reason = result
                else:
                    blocked, reason = False, "OK"
                gates.append({"gate": "A11 buyer_filter", "blocked": blocked,
                              "detail": reason or "OK"})
            except Exception as e:
                gates.append({"gate": "A11 buyer_filter", "error": str(e)[:80]})

            out["gate_results"][idx] = {
                "action": action,
                "prob": prob,
                "gates": gates,
                "first_blocker": next((g["gate"] for g in gates if g.get("blocked")), None),
            }
    except Exception as e:
        out["error"] = str(e)
    return out


@app.get("/api/trades/why-no-trade")
async def why_no_trade():
    """Diagnostic: explain why auto-trader is not entering trades right now.
    Walks through every gate (volatility, probability, caps, filters, momentum)
    and returns pass/fail for each + the current verdict. PnL tab uses this."""
    try:
        if not engine:
            return {"engine_alive": False, "error": "engine not started"}

        from datetime import datetime, timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)

        # Volatility regime
        # FIX: function is `build_recommendations` (not `get_recommendations`)
        # AND classify_regime returns dict with 'recommend' nested already.
        try:
            from volatility_detector import classify_regime
            regime_data = classify_regime(engine)
            # classify_regime() already builds recommendations as `recommend`
            vol_rec = regime_data.get("recommend", {
                "main_pnl_allowed": True, "min_probability": 50, "warnings": [],
            })
        except Exception as e:
            regime_data = {"regime": "UNKNOWN", "error": str(e)}
            vol_rec = {"main_pnl_allowed": True, "min_probability": 50, "warnings": []}

        # Get latest verdict for both indices
        # FIX: method is `get_trap_verdict` (not `get_full_verdict`)
        verdict = {}
        try:
            if hasattr(engine, "get_trap_verdict"):
                verdict = engine.get_trap_verdict() or {}
            elif hasattr(engine, "get_full_verdict"):
                verdict = engine.get_full_verdict() or {}
        except Exception as e:
            verdict = {"error": str(e)}

        # Today + open counts
        import sqlite3
        from position_watcher import _trades_db_path
        today_iso = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        try:
            conn = sqlite3.connect(_trades_db_path())
            today_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE entry_time > ?", (today_iso,)
            ).fetchone()[0]
            open_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status='OPEN'"
            ).fetchone()[0]
            today_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl_rupees),0) FROM trades WHERE entry_time > ? AND status != 'OPEN'",
                (today_iso,)
            ).fetchone()[0]
            today_trades_summary = conn.execute(
                "SELECT id, idx, action, strike, status, pnl_rupees FROM trades WHERE entry_time > ? ORDER BY entry_time DESC LIMIT 10",
                (today_iso,)
            ).fetchall()
            conn.close()
        except Exception as e:
            today_count = -1; open_count = -1; today_pnl = 0; today_trades_summary = []

        # Market hours check
        market_open = ((now.hour == 9 and now.minute >= 20)
                       or (10 <= now.hour <= 14)
                       or (now.hour == 15 and now.minute <= 15))

        # Trade manager state
        tm = getattr(engine, "trade_manager", None)
        pending_entries = {}
        if tm and hasattr(tm, "_pending_entry"):
            for k, v in tm._pending_entry.items():
                pending_entries[k] = {
                    "action": v.get("action"),
                    "strike": v.get("strike"),
                    "entry_price": v.get("entry_price"),
                    "probability": v.get("probability"),
                    "age_sec": round(__import__("time").time() - tm._pending_entry_time.get(k, 0), 1)
                              if k in (tm._pending_entry_time or {}) else None,
                }

        # Per-index gate analysis
        idx_analysis = {}
        for idx in ("NIFTY", "BANKNIFTY"):
            v = verdict.get(idx.lower(), {}) if isinstance(verdict, dict) else {}
            action = v.get("action", "NO_DATA")
            win_prob = v.get("winProbability", 0)
            min_prob = vol_rec.get("min_probability", 50)
            gates = []
            gates.append({"name": "Volatility Allowed",
                          "pass": vol_rec.get("main_pnl_allowed", True),
                          "detail": f"regime={regime_data.get('regime')}"})
            gates.append({"name": "Action != NO TRADE",
                          "pass": action and action != "NO TRADE",
                          "detail": f"action='{action}'"})
            gates.append({"name": "Probability >= base 50%",
                          "pass": win_prob >= 50,
                          "detail": f"prob={win_prob}%"})
            gates.append({"name": f"Probability >= regime min {min_prob}%",
                          "pass": win_prob >= min_prob,
                          "detail": f"prob={win_prob}% vs need {min_prob}%"})
            gates.append({"name": "Market hours",
                          "pass": market_open,
                          "detail": f"now={now.strftime('%H:%M')} IST"})
            gates.append({"name": "Daily cap (<15)",
                          "pass": today_count < 15,
                          "detail": f"today={today_count}"})
            gates.append({"name": "Concurrent cap (<10)",
                          "pass": open_count < 10,
                          "detail": f"open={open_count}"})

            # Duplicate open same idx+action
            try:
                conn = sqlite3.connect(_trades_db_path())
                dup = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE status='OPEN' AND idx=? AND action=?",
                    (idx, action)
                ).fetchone()[0]
                conn.close()
            except Exception:
                dup = 0
            gates.append({"name": "No duplicate open",
                          "pass": dup == 0,
                          "detail": f"existing open same direction={dup}"})

            # Pending entry status
            pe = pending_entries.get(idx)
            if pe:
                gates.append({"name": "Pending momentum confirmation",
                              "pass": False,  # waiting
                              "detail": f"pending {pe['action']} {pe['strike']} @ ₹{pe['entry_price']} for {pe['age_sec']}s (max 120s)"})

            all_pass = all(g["pass"] for g in gates if "Pending" not in g["name"])
            idx_analysis[idx] = {
                "verdict_action": action,
                "win_probability": win_prob,
                "would_take_trade": all_pass,
                "blocking_gates": [g for g in gates if not g["pass"]],
                "all_gates": gates,
                "pending_entry": pe,
                # FIX: surface smartBias in per_index so frontend can render
                # range position, exhaustion warnings, capitulation boosts.
                "smartBias": v.get("smartBias", {}) if isinstance(v, dict) else {},
                "bullPct": v.get("bullPct") if isinstance(v, dict) else None,
                "bearPct": v.get("bearPct") if isinstance(v, dict) else None,
            }

        # Recent block log (best-effort)
        block_log_hint = ("Server logs (Render dashboard) will show "
                          "[TRADE] BLOCKED ... lines from OI shift / divergence / "
                          "truth-lie / regime gates if any of these are firing.")

        return {
            "now_ist": now.strftime("%Y-%m-%d %H:%M:%S"),
            "market_open": market_open,
            "regime": regime_data,
            "vol_recommendations": vol_rec,
            "today_trade_count": today_count,
            "open_trade_count": open_count,
            "today_realised_pnl": today_pnl,
            "today_recent_trades": [
                {"id": r[0], "idx": r[1], "action": r[2], "strike": r[3],
                 "status": r[4], "pnl": r[5]} for r in today_trades_summary
            ],
            "trade_manager_alive": tm is not None,
            "pending_entries": pending_entries,
            "verdict_snapshot": {
                k: {"action": (v.get("action") if isinstance(v, dict) else None),
                    "winProbability": (v.get("winProbability") if isinstance(v, dict) else None),
                    "topReasons": (v.get("topReasons", [])[:3] if isinstance(v, dict) else []),
                    "smartBias": (v.get("smartBias", {}) if isinstance(v, dict) else {}),
                    "bullPct": (v.get("bullPct") if isinstance(v, dict) else None),
                    "bearPct": (v.get("bearPct") if isinstance(v, dict) else None)}
                for k, v in (verdict.items() if isinstance(verdict, dict) else [])
                if k in ("nifty", "banknifty")
            },
            "per_index": idx_analysis,
            "block_log_hint": block_log_hint,
        }
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.get("/api/system/health-check")
async def system_health_check():
    """Comprehensive end-to-end diagnostic across the entire dashboard:
    engine, all 12 databases, every trading intelligence engine, position
    watcher, capitulation engine, velocity trackers, today's activity,
    background threads, disk usage. Returns category-grouped PASS/WARN/FAIL
    with overall verdict (HEALTHY / OK_WITH_WARNINGS / DEGRADED / BROKEN)."""
    try:
        from system_health import run_full_check
        return run_full_check(engine)
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


# ── Forecast Engine endpoints (predictive narrative builder) ──
@app.get("/api/forecast/live")
async def forecast_live():
    """Live forecast: bias + key levels + expected path + buyer action plan.
    Cache: read from background populator (5s max age), fallback computes
    every 30s. Forecast pulse internally runs every 60s in engine."""
    try:
        from forecast_engine import get_live_state
        return _fast_cache_or_compute(
            "forecast_live",
            get_live_state,
            populator_max_age=20.0,
            fallback_ttl=30.0,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/forecast/pulse-now")
async def forecast_pulse_now():
    """Force an immediate forecast pulse."""
    try:
        if not engine:
            return JSONResponse({"error": "Engine not started"}, status_code=503)
        from forecast_engine import pulse
        snap = pulse(engine)
        return snap
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.get("/api/forecast/{index}")
async def forecast_index(index: str):
    """Forecast for a single index (NIFTY or BANKNIFTY)."""
    try:
        from forecast_engine import get_forecast
        idx = index.upper()
        if idx not in ("NIFTY", "BANKNIFTY"):
            return JSONResponse({"error": "Invalid index"}, status_code=400)
        f = get_forecast(idx)
        if not f:
            return JSONResponse({"error": "No forecast yet — wait for first pulse"},
                                status_code=503)
        return f
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/reversal/live")
async def reversal_live():
    """Live capitulation state for both NIFTY and BANKNIFTY. Cached 10s."""
    try:
        from capitulation_engine import get_live_state
        return _get_or_cache("reversal_live", get_live_state, ttl=5)  # C1: 10→5s
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/reversal/history")
async def reversal_history(idx: str = "", limit: int = 50):
    """Today's capitulation events log."""
    try:
        from capitulation_engine import get_history
        return {"events": get_history(idx.upper() if idx else None, limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/reversal/pulse-now")
async def reversal_pulse_now():
    """Force an immediate capitulation pulse."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from capitulation_engine import pulse, set_live_state
        snap = pulse(engine)
        set_live_state(snap)
        return snap
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


# ── Polarity Flip Detector endpoints (S/R role tracking) ──
@app.get("/api/structure/levels")
async def structure_levels(idx: str = "NIFTY"):
    """All tracked S/R levels with full state. Cached 15s."""
    try:
        from polarity_flip_detector import get_current_levels
        return _get_or_cache(f"structure_levels_{idx}",
                             lambda: get_current_levels(idx.upper()), ttl=10)  # C1: 15→10s
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/structure/flips")
async def structure_flips(idx: str = "", limit: int = 50):
    """Today's confirmed polarity flip events (R→S breakouts, S→R breakdowns)."""
    try:
        from polarity_flip_detector import get_flip_events
        return {"events": get_flip_events(idx.upper() if idx else None, limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/structure/timeline")
async def structure_timeline(idx: str = "NIFTY"):
    """Hourly + event-tagged S/R snapshots showing 'pehle kya tha vs ab kya hai'.
    Returns chronological list — open / hourly / capitulation / trend-change anchors."""
    try:
        from polarity_flip_detector import get_timeline
        return get_timeline(idx.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/structure/snapshot")
async def structure_snapshot(tag: str = "MANUAL"):
    """Force-capture current S/R structure as a named snapshot."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from polarity_flip_detector import trigger_snapshot
        trigger_snapshot(engine, tag=tag)
        return {"status": "snapshot captured", "tag": tag}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── IV Rank Engine endpoints ──
@app.get("/api/iv-rank/strike/{strike}")
async def iv_rank_strike(strike: int, idx: str = "NIFTY"):
    """IV Rank for one strike (CE + PE) with 60-day stats."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from iv_rank_engine import get_strike_iv_rank
        return get_strike_iv_rank(engine, idx.upper(), strike)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/iv-rank/chain")
async def iv_rank_chain(idx: str = "NIFTY"):
    """IV Rank for ATM ±10 strikes."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from iv_rank_engine import get_chain_iv_ranks
        return get_chain_iv_ranks(engine, idx.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/iv-rank/stats")
async def iv_rank_stats():
    """Capture stats — days of history, last capture, provisional flag."""
    try:
        from iv_rank_engine import get_capture_stats
        return get_capture_stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/iv-rank/capture-now")
async def iv_rank_capture_now():
    """Manual trigger for IV snapshot."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from iv_rank_engine import capture_iv_snapshot
        capture_iv_snapshot(engine)
        return {"status": "captured"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Buyer Filters endpoints (Pump + Max Pain + Vega/Theta) ──
@app.get("/api/buyer-filters/check")
async def buyer_filters_check(idx: str, strike: int, action: str = "BUY_CE"):
    """Pre-trade check — runs all 3 buyer filters and returns combined verdict.
    Used by Buyer Cockpit + manual entry confirm."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from buyer_filters import check_buyer_filters
        chain = engine.chains.get(idx.upper(), {})
        sd = chain.get(strike) or chain.get(str(strike)) or {}
        side = "ce_ltp" if "CE" in action.upper() else "pe_ltp"
        current_premium = sd.get(side, 0) or 0
        allowed, reason, qty_mult, details = check_buyer_filters(
            engine, idx.upper(), action, strike, current_premium
        )
        return {
            "allowed": allowed,
            "reason": reason,
            "qty_multiplier": qty_mult,
            "details": details,
            "current_premium": current_premium,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/buyer-filters/capture-day-open")
async def buyer_filters_capture():
    """Manual trigger for day-open capture (debug)."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from buyer_filters import capture_day_open
        capture_day_open(engine)
        return {"status": "captured"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Spread + Liquidity Filter endpoints ──
@app.get("/api/spread/strike/{strike}")
async def spread_strike(strike: int, idx: str = "NIFTY"):
    """Live spread + depth for one strike (both CE and PE)."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from spread_filter import get_strike_liquidity
        return get_strike_liquidity(engine, idx.upper(), strike)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/spread/chain")
async def spread_chain(idx: str = "NIFTY"):
    """Liquidity scan of all NTM ±10 strikes."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from spread_filter import get_chain_liquidity
        return get_chain_liquidity(engine, idx.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/spread/blocks")
async def spread_blocks(idx: str = "", limit: int = 50):
    """Today's spread-blocked entry attempts."""
    try:
        from spread_filter import get_blocks_today
        return {"blocks": get_blocks_today(idx.upper() if idx else None, limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Time-Decay SL endpoints ──
@app.get("/api/time-decay/status/{trade_id}")
async def time_decay_status(trade_id: int, source: str = "MAIN"):
    try:
        from time_decay_sl import get_decay_status
        import sqlite3
        src = source.upper()
        if src == "SCALPER":
            import scalper_mode
            conn = scalper_mode._conn()
            row = conn.execute(
                "SELECT id, entry_price, sl_price, current_ltp, entry_time, idx, action, strike "
                "FROM scalper_trades WHERE id=?", (trade_id,)
            ).fetchone()
            conn.close()
        else:
            from trade_logger import _conn as _tconn
            conn = _tconn()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, entry_price, sl_price, current_ltp, entry_time, idx, action, strike "
                "FROM trades WHERE id=?", (trade_id,)
            ).fetchone()
            conn.close()
        if not row:
            return JSONResponse({"error": "trade not found"}, status_code=404)
        trade = dict(row) if hasattr(row, "keys") else {
            "id": row[0], "entry_price": row[1], "sl_price": row[2],
            "current_ltp": row[3], "entry_time": row[4], "idx": row[5],
            "action": row[6], "strike": row[7],
        }
        current_premium = trade.get("current_ltp") or trade.get("entry_price") or 0
        return get_decay_status(trade, current_premium, src)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/time-decay/log")
async def time_decay_log(source: str = "", limit: int = 100):
    try:
        from time_decay_sl import get_decay_log_today
        return {"events": get_decay_log_today(source.upper() if source else None, limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/time-decay/ladder")
async def time_decay_ladder(mode: str = "MAIN"):
    try:
        from time_decay_sl import get_ladder_config
        return {"mode": mode.upper(), "ladder": get_ladder_config(mode.upper())}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Profit-Lock Trailing SL endpoints ──
@app.get("/api/profit-trail/status/{trade_id}")
async def profit_trail_status(trade_id: int, source: str = "MAIN"):
    """Live trail status for a trade — current stage, profit %, locked %,
    next stage threshold + premium needed. Used by trade card UI."""
    try:
        from profit_trailing_sl import get_trail_status
        import sqlite3
        src = source.upper()
        if src == "SCALPER":
            import scalper_mode
            conn = scalper_mode._conn()
            row = conn.execute(
                "SELECT id, entry_price, sl_price, current_ltp, idx, action, strike "
                "FROM scalper_trades WHERE id=?", (trade_id,)
            ).fetchone()
            conn.close()
        else:
            from trade_logger import _conn as _tconn
            conn = _tconn()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, entry_price, sl_price, current_ltp, idx, action, strike "
                "FROM trades WHERE id=?", (trade_id,)
            ).fetchone()
            conn.close()
        if not row:
            return JSONResponse({"error": "trade not found"}, status_code=404)
        trade = dict(row) if hasattr(row, "keys") else {
            "id": row[0], "entry_price": row[1], "sl_price": row[2],
            "current_ltp": row[3], "idx": row[4], "action": row[5], "strike": row[6],
        }
        current_premium = trade.get("current_ltp") or trade.get("entry_price") or 0
        return get_trail_status(trade, current_premium, src)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/profit-trail/log")
async def profit_trail_log(source: str = "", limit: int = 100):
    """Today's trail-raise events for audit panel."""
    try:
        from profit_trailing_sl import get_trail_log_today
        return {"events": get_trail_log_today(source.upper() if source else None, limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/profit-trail/ladder")
async def profit_trail_ladder(mode: str = "MAIN"):
    """Active ladder configuration (for UI display)."""
    try:
        from profit_trailing_sl import get_ladder_config
        return {"mode": mode.upper(), "ladder": get_ladder_config(mode.upper())}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# Smart Money endpoints moved earlier in the file (before /{index}
# catch-all) to fix routing shadow — see lines ~890-960.


@app.post("/api/structure/pulse-now")
async def structure_pulse_now():
    """Force a polarity-flip detection pulse immediately (skip 60s wait).
    Discovers levels, updates registry, detects any pending flips."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from polarity_flip_detector import pulse
        return pulse(engine)
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.get("/api/market/close-status")
async def market_close_status():
    """Market close countdown for the UI banner (3:20 PM warning → 3:25 PM auto-close)."""
    try:
        from scalper_mode import get_market_close_status
        return get_market_close_status()
    except Exception as e:
        return JSONResponse({"error": str(e), "state": "NORMAL"}, status_code=500)


@app.get("/api/positions/watcher-debug")
async def positions_watcher_debug():
    """Full diagnostic: DB paths, trades found in DB vs trades cached
    by the watcher. Use this to debug 'open trades zero' issues."""
    try:
        from position_watcher import (
            _get_open_main_trades, _get_open_scalper_trades,
            _trades_db_path, _scalper_db_path, _last_health_cache,
            WATCHER_DB, DATA_DIR
        )
        import os, time as _time
        main = _get_open_main_trades()
        scalper = _get_open_scalper_trades()
        cache = _last_health_cache
        last_ts = max([h.get("ts", 0) for h in cache.values()], default=0)
        return {
            "data_dir": DATA_DIR,
            "data_dir_is_data": DATA_DIR == "/data",
            "trades_db_path": _trades_db_path(),
            "trades_db_exists": os.path.exists(_trades_db_path()),
            "trades_db_size": os.path.getsize(_trades_db_path()) if os.path.exists(_trades_db_path()) else 0,
            "scalper_db_path": _scalper_db_path(),
            "scalper_db_exists": os.path.exists(_scalper_db_path()),
            "scalper_db_size": os.path.getsize(_scalper_db_path()) if os.path.exists(_scalper_db_path()) else 0,
            "watcher_db_path": WATCHER_DB,
            "open_main_in_db": len(main),
            "open_scalper_in_db": len(scalper),
            "main_trade_ids": [t.get("id") for t in main],
            "main_trade_summary": [
                {"id": t.get("id"), "idx": t.get("idx"), "action": t.get("action"),
                 "strike": t.get("strike"), "entry": t.get("entry_price"),
                 "current_ltp": t.get("current_ltp"), "status": t.get("status")}
                for t in main[:5]
            ],
            "scalper_trade_ids": [t.get("id") for t in scalper],
            "scalper_trade_summary": [
                {"id": t.get("id"), "idx": t.get("idx"), "action": t.get("action"),
                 "strike": t.get("strike"), "entry": t.get("entry_price"),
                 "current_ltp": t.get("current_ltp"), "status": t.get("status")}
                for t in scalper[:5]
            ],
            "cached_count": len(cache),
            "cached_keys": list(cache.keys()),
            "last_pulse_age_sec": round(_time.time() - last_ts, 1) if last_ts else None,
            "engine_alive": engine is not None,
            "engine_has_chains": hasattr(engine, "chains") if engine else False,
            "engine_has_spot_tokens": hasattr(engine, "spot_tokens") if engine else False,
            "spot_tokens_keys": list(engine.spot_tokens.keys())[:10] if (engine and hasattr(engine, "spot_tokens")) else [],
            "chains_keys": list(engine.chains.keys()) if (engine and hasattr(engine, "chains")) else [],
        }
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.post("/api/positions/watcher-pulse-now")
async def positions_watcher_pulse_now():
    """Force a watcher pulse immediately (don't wait for the 30s tick).
    Returns the snapshot of what happened. Useful right after deploy."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        from position_watcher import watcher_pulse
        snap = watcher_pulse(engine)
        return snap
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.get("/api/positions/watcher-status")
async def positions_watcher_status():
    """Liveness signal for the position watcher loop. Cached 5s."""
    def _compute():
        from position_watcher import get_last_health, _last_health_cache
        cached = list(_last_health_cache.values())
        last_pulse_ts = max([h.get("ts", 0) for h in cached], default=0)
        now = __import__("time").time()
        age = (now - last_pulse_ts) if last_pulse_ts else None
        is_live = age is not None and age < 90
        return {
            "live": bool(is_live),
            "last_pulse_age_sec": round(age, 1) if age is not None else None,
            "cached_positions": len(cached),
            "main_count": len([h for h in cached if h.get("source") == "MAIN"]),
            "scalper_count": len([h for h in cached if h.get("source") == "SCALPER"]),
            "stub_count": len([h for h in cached if h.get("stub")]),
        }
    try:
        return _fast_cache_or_compute(
            "watcher_status", _compute,
            populator_max_age=8.0, fallback_ttl=3.0
        )
    except Exception as e:
        return JSONResponse({"error": str(e), "live": False}, status_code=500)


@app.get("/api/positions/ticks/{trade_id}")
async def position_ticks(trade_id: int, source: str = "MAIN", limit: int = 500):
    """Live LTP tick history for a specific trade (used for the live chart).
    Both MAIN and SCALPER ticks come from watcher's position_ticks table
    so the chart format is identical regardless of mode.
    """
    try:
        src = source.upper()
        from position_watcher import get_position_ticks
        ticks = get_position_ticks(src, trade_id, limit=limit)
        return {"trade_id": trade_id, "source": src, "ticks": ticks}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/positions/exit/{trade_id}")
async def position_force_exit(trade_id: int, bg: BackgroundTasks, source: str = "MAIN"):
    """Manually exit a trade from any tab — uses watcher's force-close machinery.
    N1: capital tracker writes deferred to background tasks."""
    try:
        src = source.upper()
        if src == "SCALPER":
            import scalper_mode
            import sqlite3
            conn = sqlite3.connect(str(scalper_mode.SCALPER_DB))
            row = conn.execute("SELECT current_ltp, entry_price FROM scalper_trades WHERE id=? AND status='OPEN'",
                               (trade_id,)).fetchone()
            conn.close()
            if not row:
                return {"status": "not_found"}
            ltp = row[0] or row[1]
            res = scalper_mode.manual_exit(trade_id, ltp, reason="USER_MANUAL_EXIT",
                                           defer_capital_track=True)
            if res.get("ok"):
                bg.add_task(scalper_mode.record_capital_after_exit,
                            "SCALPER", res["pnl_rupees"], trade_id,
                            f"User manual exit @ ₹{res['exit_price']:.2f}")
            return {"status": "closed", **(res or {})}
        else:
            from position_watcher import _force_close_main, _trades_db_path
            import sqlite3
            conn = sqlite3.connect(_trades_db_path())
            row = conn.execute("SELECT current_ltp, entry_price FROM trades WHERE id=? AND status='OPEN'",
                               (trade_id,)).fetchone()
            conn.close()
            if not row:
                return {"status": "not_found"}
            ltp = row[0] or row[1]
            ok = _force_close_main(trade_id, ltp, "USER_MANUAL_EXIT")
            return {"status": "closed" if ok else "failed", "exit_price": ltp}
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/positions/exits")
async def positions_exits(limit: int = 50):
    """Recent watcher-triggered exits with full reason chains."""
    try:
        from position_watcher import get_recent_exits
        return {"exits": get_recent_exits(limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/positions/config")
async def positions_config_get():
    try:
        from position_watcher import get_config
        return get_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/positions/config")
async def positions_config_set(payload: dict):
    """Update watcher config: auto_exit_main, auto_exit_scalper, tight_sl_*, thresholds."""
    try:
        from position_watcher import set_config
        return set_config(**payload)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Times Tab Real Engine endpoints (Phase 1) ──

@app.get("/api/times/events")
async def times_events(idx: str = "NIFTY"):
    """Chronological event timeline for today with maths + logic + traps."""
    try:
        from times_tab_engine import get_today_events
        return {"events": get_today_events(idx.upper())}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/times/story")
async def times_story(idx: str = "NIFTY"):
    """Today's story summary + bias."""
    try:
        from times_tab_engine import get_today_story
        return get_today_story(idx.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── AI Brain endpoints (Phase 2-3) ──

@app.post("/api/ai/ask")
async def ai_ask(body: dict):
    """User asks AI a question. AI fetches all dashboard data + responds."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        question = (body or {}).get("question", "").strip()
        session = (body or {}).get("session_id", "default")
        if not question:
            return JSONResponse({"error": "question required"}, status_code=400)
        from ai_brain import ask
        return ask(question, engine, session_id=session)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/ai/eod-forecast")
async def ai_eod_forecast_get():
    """Get latest EOD forecast (today's tomorrow prediction)."""
    try:
        from ai_brain import get_latest_eod_forecast
        f = get_latest_eod_forecast()
        return f or {"error": "No forecast yet — generated daily at 3:20 PM IST"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ai/eod-forecast/generate")
async def ai_eod_forecast_generate():
    """Manual trigger — force AI EOD forecast generation now."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from ai_brain import generate_eod_forecast
        return generate_eod_forecast(engine)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/ai/chat-history")
async def ai_chat_history(session_id: str = "default", limit: int = 30):
    """Get chat history for a session."""
    try:
        from ai_brain import get_chat_history
        return {"messages": get_chat_history(session_id, limit=limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Volatility Detector endpoints (A1) ──

@app.get("/api/volatility/regime")
async def volatility_regime():
    """Current market volatility regime + recommendations."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from volatility_detector import classify_regime, log_regime
        result = classify_regime(engine)
        log_regime(result)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/volatility/history")
async def volatility_history(hours: int = 4):
    """Recent regime changes."""
    try:
        from volatility_detector import get_regime_history
        return {"history": get_regime_history(hours)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Risk Tier endpoints (A5) ──

@app.get("/api/risk-tier/current")
async def risk_tier_current():
    """Current adaptive risk tier + win/loss streaks."""
    try:
        import risk_tier_manager
        return risk_tier_manager.get_summary()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/risk-tier/history")
async def risk_tier_history(limit: int = 50):
    """Tier transition history."""
    try:
        import risk_tier_manager
        return {"history": risk_tier_manager.get_history(limit=limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/risk-tier/reset")
async def risk_tier_reset():
    """Manual reset to Tier 1 (use with caution)."""
    try:
        import risk_tier_manager
        risk_tier_manager.reset_for_new_day()
        return {"ok": True, "state": risk_tier_manager.get_summary()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Daily Training endpoints (B1+B2+B3) ──

@app.get("/api/daily/profile/today")
async def daily_profile_today():
    """Today's profile (live updates throughout day)."""
    try:
        from daily_training import get_today_profile
        return get_today_profile() or {"error": "No profile yet — capture at 3:30 PM"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/daily/profile/{day_name}")
async def daily_profile_day(day_name: str):
    """Past 12 profiles for a weekday (MON, TUE, WED...)."""
    try:
        from daily_training import get_profile_for_day
        return {"profiles": get_profile_for_day(day_name)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/daily/comparison")
async def daily_comparison(weeks: int = 4):
    """Find similar past same-weekday profiles for today."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from daily_training import find_similar_past_days
        return {"matches": find_similar_past_days(engine, days_back=weeks)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/daily/weights/today")
async def daily_weights_today():
    """Day-specific engine weights for today (Mon/Tue/etc)."""
    try:
        from daily_training import get_day_weights
        result = get_day_weights()
        return result or {"error": "No day-specific weights yet"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/daily/predict")
async def daily_predict():
    """Morning prediction (B10) — full forecast for today's session."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from morning_prediction import predict_today
        return predict_today(engine)
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/daily/capture-now")
async def daily_capture_now():
    """Manual trigger — force EOD profile capture."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from daily_training import capture_today_profile
        return capture_today_profile(engine)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Truth/Lie Detector endpoints (A3) ──

@app.get("/api/truth-lie/patterns")
async def truth_lie_patterns(days: int = 30):
    """Aggregate pattern analysis (top 50 patterns)."""
    try:
        from truth_lie_detector import get_pattern_summary
        return {"patterns": get_pattern_summary(days=days)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/truth-lie/check")
async def truth_lie_check(action: str, probability: int, top_engine: str = "unknown", vix: float = 18):
    """Check if a hypothetical trade would be blocked by pattern matching."""
    try:
        from truth_lie_detector import check_pattern
        is_lie, conf, win_rate, samples, msg = check_pattern(
            action, probability, top_engine, vix
        )
        return {
            "is_lie": is_lie,
            "confidence": conf,
            "win_rate": win_rate,
            "samples": samples,
            "message": msg,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Quality Score endpoints (A8) ──

@app.get("/api/quality/current/{index}")
async def quality_current(index: str):
    """Live quality score for current verdict on this index."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from quality_score import calculate_quality
        idx = index.upper()
        verdict = engine.get_trap_verdict()
        v = verdict.get(idx.lower(), {})
        action = v.get("action", "")
        if not action or action == "NO TRADE":
            return {"score": 0, "grade": "NO_TRADE", "passes": False, "reasons": ["No active signal"]}
        return calculate_quality(v, action, idx, engine=engine)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── OI Shift Detector endpoints (A2) ──

@app.get("/api/oi-shifts/recent")
async def oi_shifts_recent(idx: str = None, hours: int = 2):
    """Recent wall shifts."""
    try:
        from oi_shift_detector import get_recent_shifts
        return {"shifts": get_recent_shifts(idx, hours=hours)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/oi-shifts/capture-now")
async def oi_shifts_capture_now():
    """Manual snapshot trigger."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from oi_shift_detector import capture_wall_snapshot
        capture_wall_snapshot(engine)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Capital Tracker endpoints ──
# Independent per-system tracker (SCALPER + MAIN). Auto-adjusts on
# trade close. Profit Bank stores excess over base. Loss reduces capital.

@app.get("/api/capital/{system}")
async def capital_get(system: str):
    """Get capital state for SCALPER or MAIN system."""
    try:
        import capital_tracker
        sys_upper = system.upper()
        if sys_upper not in ("SCALPER", "MAIN"):
            return JSONResponse({"error": "system must be SCALPER or MAIN"}, status_code=400)
        return capital_tracker.get_summary(sys_upper)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/capital/{system}/history")
async def capital_history(system: str, limit: int = 100):
    """Full capital adjustment history for a system."""
    try:
        import capital_tracker
        sys_upper = system.upper()
        if sys_upper not in ("SCALPER", "MAIN"):
            return JSONResponse({"error": "system must be SCALPER or MAIN"}, status_code=400)
        return {"history": capital_tracker.get_history(sys_upper, limit=limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/capital/{system}/withdraw")
async def capital_withdraw(system: str, body: dict = None):
    """Manual withdraw from Profit Bank. body={amount: float} or omit for full."""
    try:
        import capital_tracker
        sys_upper = system.upper()
        if sys_upper not in ("SCALPER", "MAIN"):
            return JSONResponse({"error": "system must be SCALPER or MAIN"}, status_code=400)
        amount = (body or {}).get("amount")
        return capital_tracker.withdraw_profit_bank(sys_upper, amount=amount)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/capital/{system}/base")
async def capital_set_base(system: str, body: dict):
    """Set base capital target (e.g. ₹10L)."""
    try:
        import capital_tracker
        sys_upper = system.upper()
        if sys_upper not in ("SCALPER", "MAIN"):
            return JSONResponse({"error": "system must be SCALPER or MAIN"}, status_code=400)
        new_base = body.get("base_capital")
        if not new_base or new_base <= 0:
            return JSONResponse({"error": "base_capital must be > 0"}, status_code=400)
        return capital_tracker.set_base_capital(sys_upper, float(new_base))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/capital/{system}/reset")
async def capital_reset(system: str, body: dict = None):
    """Reset current capital to base. body={to_base: bool} default True."""
    try:
        import capital_tracker
        sys_upper = system.upper()
        if sys_upper not in ("SCALPER", "MAIN"):
            return JSONResponse({"error": "system must be SCALPER or MAIN"}, status_code=400)
        to_base = (body or {}).get("to_base", True)
        return capital_tracker.reset_capital(sys_upper, to_base=bool(to_base))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/capital/{system}/account")
async def capital_account(system: str):
    """Professional account summary — realized/unrealized P&L, drawdown,
    daily/weekly/monthly performance. Direct from trade DB."""
    try:
        import capital_tracker
        sys_upper = system.upper()
        if sys_upper not in ("SCALPER", "MAIN"):
            return JSONResponse({"error": "system must be SCALPER or MAIN"}, status_code=400)
        return capital_tracker.get_account_summary(sys_upper)
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/capital/{system}/backfill")
async def capital_backfill(system: str):
    """Replay all existing closed trades through tracker — builds full
    historical state from past trades (P&L, profit bank, loss recovery)."""
    try:
        import capital_tracker
        sys_upper = system.upper()
        if sys_upper not in ("SCALPER", "MAIN"):
            return JSONResponse({"error": "system must be SCALPER or MAIN"}, status_code=400)
        return capital_tracker.backfill_from_trades(sys_upper)
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# ── BUYER MODE endpoints ──

@app.get("/api/buyer-mode")
async def buyer_mode_get():
    """Current mode (HEDGER/BUYER) + active thresholds + comparison."""
    try:
        import buyer_mode
        return buyer_mode.get_summary()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/buyer-mode/toggle")
async def buyer_mode_toggle():
    """Flip between HEDGER and BUYER modes (one-click toggle)."""
    try:
        import buyer_mode
        cur = buyer_mode.get_mode()
        new_mode = "BUYER" if cur == "HEDGER" else "HEDGER"
        buyer_mode.set_mode(new_mode)
        return {"ok": True, "mode": new_mode, "thresholds": buyer_mode.get_thresholds()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/buyer-mode/set")
async def buyer_mode_set(body: dict):
    """Explicitly set mode to HEDGER or BUYER."""
    try:
        import buyer_mode
        mode = (body.get("mode") or "").upper()
        if mode not in ("HEDGER", "BUYER"):
            return JSONResponse({"error": "mode must be HEDGER or BUYER"}, status_code=400)
        buyer_mode.set_mode(mode)
        return {"ok": True, "mode": mode, "thresholds": buyer_mode.get_thresholds()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/buyer-mode/overrides")
async def buyer_mode_overrides_set(body: dict):
    """Set custom threshold overrides (advanced users)."""
    try:
        import buyer_mode
        overrides = body.get("overrides", {})
        buyer_mode.set_overrides(overrides)
        return {"ok": True, "thresholds": buyer_mode.get_thresholds()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/buyer-mode/overrides/reset")
async def buyer_mode_overrides_reset():
    try:
        import buyer_mode
        buyer_mode.reset_overrides()
        return {"ok": True, "thresholds": buyer_mode.get_thresholds()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Smart Autopsy Mind endpoints ──

@app.get("/api/mind/predict/{index}")
async def mind_predict(index: str):
    """Today's pattern matched against history → predictive outcome."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from smart_autopsy_mind import predict_today
        return predict_today(engine, index.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/mind/similar/{index}")
async def mind_similar(index: str, top_n: int = 5):
    """Find past days similar to today."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from smart_autopsy_mind import find_similar_days
        return {"matches": find_similar_days(engine, index.upper(), top_n=top_n)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/mind/narrate/{index}")
async def mind_narrate(index: str):
    """Explain WHY market moved today."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from smart_autopsy_mind import explain_move
        return explain_move(engine, index.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/mind/summary/{index}")
async def mind_summary(index: str):
    """All mind insights combined (prediction + similar + narrative)."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from smart_autopsy_mind import get_mind_summary
        return get_mind_summary(engine, index.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/mind/recorded-days")
async def mind_recorded_days():
    """List all days the mind has learned from."""
    try:
        from smart_autopsy_mind import get_recorded_days
        return {"days": get_recorded_days()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/mind/record-now/{index}")
async def mind_record_now(index: str):
    """Manual trigger to record today's pattern (for testing)."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from smart_autopsy_mind import record_day_pattern
        result = record_day_pattern(engine, index.upper())
        return {"status": "success" if result else "failed", "date": datetime.now().strftime("%Y-%m-%d")}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Shadow Autopsy endpoints ──

@app.get("/api/shadow/today")
async def shadow_today():
    """Today's shadow autopsy — 52 paper trades on ATM±6 CE+PE, which won/lost."""
    try:
        from shadow_autopsy import get_today_summary
        return get_today_summary()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/shadow/history")
async def shadow_history(days: int = 7):
    """Historical shadow autopsy performance."""
    try:
        from shadow_autopsy import get_history
        return get_history(days)
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/shadow/trigger-open")
async def shadow_trigger_open():
    """Manual trigger — force-open shadow trades NOW (for testing or missed 9:20 AM)."""
    global engine
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        from shadow_autopsy import take_snapshot_open
        count = take_snapshot_open(engine)
        return {"status": "success", "created": count}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/trades/exit/{trade_id}")
async def manual_exit_trade(trade_id: int):
    """Manual exit — user clicks EXIT button on a position."""
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    try:
        from trade_logger import _conn, ist_now
        conn = _conn()
        conn.row_factory = sqlite3.Row
        trade = conn.execute("SELECT * FROM trades WHERE id=? AND status='OPEN'", (trade_id,)).fetchone()
        if not trade:
            conn.close()
            return {"error": "Trade not found or already closed"}

        t = dict(trade)
        # Get current LTP
        chain = engine.chains.get(t["idx"], {})
        strike_data = chain.get(t["strike"], {})
        opt = "ce" if "CE" in t["action"] else "pe"
        current_ltp = strike_data.get(f"{opt}_ltp", 0)
        if current_ltp <= 0:
            current_ltp = t.get("current_ltp", t["entry_price"])

        exit_price = current_ltp
        pnl_pts = round(exit_price - t["entry_price"], 2)
        existing_pnl = t.get("pnl_rupees", 0) or 0
        current_qty = t.get("qty", 0)
        pnl_rupees = round(existing_pnl + pnl_pts * current_qty, 2)

        now = ist_now()
        cursor = conn.execute("""
            UPDATE trades SET status='MANUAL_EXIT', exit_price=?, exit_time=?,
                pnl_pts=?, pnl_rupees=?, exit_reason=?
            WHERE id=? AND status='OPEN'
        """, (exit_price, now.isoformat(), pnl_pts, pnl_rupees,
              f"Manual exit by user at ₹{exit_price}. PnL: ₹{pnl_rupees:+,.0f}", trade_id))
        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()
        if rows_affected == 0:
            # TOCTOU: another process closed this trade between our SELECT and UPDATE
            return {"error": "Trade was closed by another process before manual exit could complete"}
        print(f"[TRADE] MANUAL EXIT: {t['action']} {t['idx']} {t['strike']} — PnL: ₹{pnl_rupees:+,.0f}")
        return {"status": "closed", "pnl": pnl_rupees, "exitPrice": exit_price}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/ai-analysis")
async def ai_analysis():
    """Run Claude AI analysis on ALL dashboard data."""
    from ai_analysis import run_ai_analysis
    # Collect all data
    all_data = {
        "live": _get_or_cache("live", lambda: engine.get_live_data() if engine else None),
        "signals": _get_or_cache("signals", lambda: engine.get_signals() if engine else []),
        "oiSummary": _get_or_cache("oi_summary", lambda: engine.get_oi_change_summary() if engine else {}),
        "sellerData": _get_or_cache("seller_summary", lambda: engine.get_seller_summary() if engine else {}),
        "unusual": _get_or_cache("unusual", lambda: engine.get_unusual() if engine else []),
        "tradeAnalysis": _get_or_cache("trade_analysis", lambda: engine.get_trade_analysis() if engine else {}),
        "hiddenShift": _get_or_cache("hidden_shift", lambda: engine.get_hidden_shift() if engine else {}),
        "trapScan": get_cached("trap_scan"),
        "intraday": _get_or_cache("intraday", lambda: engine.get_intraday() if engine else {}),
    }
    result = run_ai_analysis(all_data)
    save_cache("ai_analysis", result)
    return result


@app.get("/api/export-daily")
async def export_daily():
    """Collects ALL data types for full A-Z PDF export."""
    return {
        "date": ist_now().strftime("%Y-%m-%d"),
        "generated": ist_now().strftime("%I:%M:%S %p IST"),
        "live": _get_or_cache("live", lambda: engine.get_live_data() if engine else None),
        "unusual": _get_or_cache("unusual", lambda: engine.get_unusual() if engine else []),
        "signals": _get_or_cache("signals", lambda: engine.get_signals() if engine else []),
        "oiSummary": _get_or_cache("oi_summary", lambda: engine.get_oi_change_summary() if engine else {}),
        "sellerData": _get_or_cache("seller_summary", lambda: engine.get_seller_summary() if engine else {}),
        "tradeAnalysis": _get_or_cache("trade_analysis", lambda: engine.get_trade_analysis() if engine else {}),
        "intraday": _get_or_cache("intraday", lambda: engine.get_intraday() if engine else {}),
        "nextday": _get_or_cache("nextday", lambda: engine.get_nextday() if engine else {}),
        "weekly": _get_or_cache("weekly", lambda: engine.get_weekly() if engine else {}),
    }


# ── Reports & ML Feedback Routes ─────────────────────────────────────────

@app.get("/api/reports/engine-accuracy")
async def report_engine_accuracy(days: int = 30):
    return get_engine_accuracy(days)

@app.get("/api/reports/weekly")
async def report_weekly():
    return get_weekly_report()

@app.get("/api/reports/hourly")
async def report_hourly(days: int = 30):
    return get_hourly_analysis(days)

@app.get("/api/reports/patterns")
async def report_patterns(days: int = 30):
    return get_pattern_analysis(days)

@app.get("/api/reports/weights")
async def report_weights():
    return get_weights_info()

@app.post("/api/reports/apply-weights")
async def report_apply_weights():
    return apply_recommended_weights()

@app.post("/api/reports/reset-weights")
async def report_reset_weights():
    return reset_weights()

@app.get("/api/reports/trading-windows")
async def report_trading_windows(days: int = 30):
    return get_trading_windows(days)

@app.post("/api/reports/run-train")
async def report_run_train():
    return run_auto_train()

@app.get("/api/reports/training-history")
async def report_training_history(limit: int = 20):
    return get_training_history(limit)

@app.get("/api/reports/auto-train-status")
async def report_auto_train_status():
    return get_auto_train_status()

@app.get("/api/reports/backtest-simulation")
async def report_backtest_sim(days: int = 30):
    return run_validation(days)

@app.get("/api/reports/real-trade-analysis")
async def report_real_trades():
    return get_real_trade_analysis()


# ── Trade Autopsy & Gap Prediction Routes ────────────────────────────────

@app.get("/api/autopsy/trade/{trade_id}")
async def autopsy_trade(trade_id: int):
    autopsy_init_db()
    return get_trade_autopsy(trade_id)

@app.get("/api/autopsy/patterns")
async def autopsy_patterns():
    autopsy_init_db()
    return get_win_loss_patterns()

@app.get("/api/autopsy/gap-prediction/{index}")
async def autopsy_gap_pred(index: str):
    autopsy_init_db()
    if not engine:
        return {"prediction": "NEED DATA", "confidence": 0, "message": "Engine not running"}
    return get_gap_prediction(engine, index.upper())

@app.get("/api/autopsy/gap-history/{index}")
async def autopsy_gap_hist(index: str, limit: int = 30):
    autopsy_init_db()
    return get_gap_history(index.upper(), limit)


# ── Alerts Routes ────────────────────────────────────────────────────────

@app.get("/api/alerts")
async def alerts_list(limit: int = 100, offset: int = 0, severity: Optional[str] = None,
                      type: Optional[str] = None, unread: bool = False):
    alerts_init_db()
    rows = list_alerts(limit=limit, offset=offset, severity=severity,
                       alert_type=type, unread_only=unread)
    return {"alerts": rows}

@app.get("/api/alerts/counts")
async def alerts_counts():
    alerts_init_db()
    return get_unread_counts()

@app.post("/api/alerts/mark-read")
async def alerts_mark_read(payload: dict):
    alerts_init_db()
    mark_read(
        alert_ids=payload.get("ids"),
        tab=payload.get("tab"),
        all_=bool(payload.get("all")),
    )
    return {"ok": True}

@app.post("/api/alerts/{alert_id}/dismiss")
async def alerts_dismiss_one(alert_id: int):
    alerts_init_db()
    alerts_dismiss(alert_id)
    return {"ok": True}

@app.post("/api/alerts/{alert_id}/pin")
async def alerts_pin_one(alert_id: int, payload: dict):
    alerts_init_db()
    alerts_pin(alert_id, bool(payload.get("pinned", True)))
    return {"ok": True}

@app.post("/api/alerts/push")
async def alerts_push(payload: dict):
    """Internal endpoint — for testing + engine to push alerts."""
    alerts_init_db()
    alert = push_alert(
        alert_type=payload.get("alert_type", "AI_INSIGHT"),
        title=payload.get("title", ""),
        message=payload.get("message", ""),
        meta=payload.get("meta"),
    )
    return alert


# ── Replay Mode Route ────────────────────────────────────────────────────

@app.get("/api/replay/snapshots")
async def replay_snapshots(index: str, date: str):
    """Return time-ordered market snapshots for a given date + index.
    Used by ReplayMode component to scrub through the trading day."""
    idx = index.upper()
    if idx not in ("NIFTY", "BANKNIFTY"):
        return {"snapshots": []}
    tt_init_db()
    _data = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
    db = _data / "trading_times.db"
    if not db.exists():
        return {"snapshots": []}
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT timestamp, spot, pcr, max_pain, top_ce_wall, top_pe_wall, confidence, "
            "blast_direction, conviction, hedge_trend, ce_volume_total, pe_volume_total, "
            "ce_oi_net_change, pe_oi_net_change, vwap "
            "FROM market_snapshots WHERE idx=? AND timestamp LIKE ? ORDER BY timestamp ASC",
            (idx, f"{date}%")
        ).fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            # Format time for display
            try:
                t_iso = d.get("timestamp", "")
                d["time"] = t_iso[11:16] if len(t_iso) >= 16 else ""
            except Exception:
                d["time"] = ""
            # Friendly keys for frontend
            d["ceWall"] = d.pop("top_ce_wall", None)
            d["peWall"] = d.pop("top_pe_wall", None)
            d["maxPain"] = d.pop("max_pain", None)
            d["signalScore"] = d.pop("confidence", 0)
            # Verdict narrative
            direction = d.get("blast_direction") or ""
            conv = d.get("conviction") or ""
            d["verdict"] = f"{direction} · {conv}" if direction or conv else ""
            out.append(d)
        return {"snapshots": out, "date": date, "index": idx, "count": len(out)}
    except Exception as e:
        print(f"[REPLAY] Error: {e}")
        return {"snapshots": [], "error": str(e)}


# ── Strike Detail Route ──────────────────────────────────────────────────

@app.get("/api/strike-detail")
async def strike_detail(index: str, strike: int, expiry: Optional[str] = None):
    """Aggregate all A-Z info for a single strike. Supports any expiry."""
    idx = index.upper()
    if not engine or idx not in ("NIFTY", "BANKNIFTY"):
        return {"error": "Engine not ready or invalid index"}

    cfg = {"NIFTY": {"strike_gap": 50}, "BANKNIFTY": {"strike_gap": 100}}.get(idx, {"strike_gap": 50})
    spot_token = engine.spot_tokens.get(idx)
    spot = engine.prices.get(spot_token, {}).get("ltp", 0)
    atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"] if spot else 0
    nearest = str(engine.nearest_expiry.get(idx, ""))
    target_expiry = expiry or nearest

    # Use live chain for current expiry, fetch for others
    if target_expiry == nearest:
        chain = engine.chains.get(idx, {})
        d = chain.get(int(strike), {})
    else:
        ec = engine.get_expiry_chain(idx, target_expiry)
        if ec.get("error"):
            return ec
        strikes_list = ec.get("strikes", [])
        match = next((s for s in strikes_list if int(s.get("strike", 0)) == int(strike)), None)
        d = {
            "ce_ltp": match.get("ceLTP", 0) if match else 0,
            "pe_ltp": match.get("peLTP", 0) if match else 0,
            "ce_oi": match.get("ceOI", 0) if match else 0,
            "pe_oi": match.get("peOI", 0) if match else 0,
            "ce_volume": match.get("ceVol", 0) if match else 0,
            "pe_volume": match.get("peVol", 0) if match else 0,
        } if match else {}
        chain = {int(s.get("strike", 0)): {
            "ce_oi": s.get("ceOI", 0), "pe_oi": s.get("peOI", 0),
            "ce_ltp": s.get("ceLTP", 0), "pe_ltp": s.get("peLTP", 0),
        } for s in strikes_list}

    # If still no data, try on-demand Kite REST fetch (for far OTM strikes not in chain)
    if not d or (not d.get("ce_ltp") and not d.get("pe_ltp")):
        fetched = engine.fetch_single_strike(idx, int(strike), target_expiry)
        if fetched and not fetched.get("error"):
            d = {
                "ce_ltp": fetched.get("ce_ltp", 0),
                "pe_ltp": fetched.get("pe_ltp", 0),
                "ce_oi": fetched.get("ce_oi", 0),
                "pe_oi": fetched.get("pe_oi", 0),
                "ce_volume": fetched.get("ce_volume", 0),
                "pe_volume": fetched.get("pe_volume", 0),
            }
            print(f"[STRIKE-DETAIL] On-demand fetch {idx} {strike} {target_expiry}: CE={d['ce_ltp']}, PE={d['pe_ltp']}")
        elif fetched and fetched.get("error"):
            # Return the error to frontend
            return {
                "index": idx, "strike": strike, "spot": spot, "atm": atm,
                "atmDistance": int(strike) - atm, "expiry": target_expiry,
                "error": fetched["error"],
                "ceLTP": 0, "peLTP": 0, "ceOI": 0, "peOI": 0, "ceVol": 0, "peVol": 0, "pcr": 0,
                "trades": [],
            }

    if not d:
        # Graceful fallback: return strike info even if not available anywhere
        return {
            "index": idx, "strike": strike, "spot": spot, "atm": atm,
            "atmDistance": int(strike) - atm, "expiry": target_expiry,
            "error": f"Strike {strike} not in chain for expiry {target_expiry}",
            "ceLTP": 0, "peLTP": 0, "ceOI": 0, "peOI": 0, "ceVol": 0, "peVol": 0, "pcr": 0,
            "trades": [],
        }

    total_ce = sum(x.get("ce_oi", 0) for x in chain.values())
    total_pe = sum(x.get("pe_oi", 0) for x in chain.values())
    pcr = round(total_pe / max(total_ce, 1), 2) if total_ce else 0

    # Approximate Greeks (simplified Black-Scholes style using moneyness)
    ce_ltp = d.get("ce_ltp", 0)
    pe_ltp = d.get("pe_ltp", 0)
    moneyness = (int(strike) - spot) / max(spot, 1) if spot else 0
    # Delta approx: ATM ~0.5, decreases by 0.1 per 1% OTM
    delta_ce = max(0.05, min(0.95, 0.5 - moneyness * 10)) if spot else 0
    delta_pe = -max(0.05, min(0.95, 0.5 + moneyness * 10)) if spot else 0
    # IV approx via LTP and moneyness
    iv_est = 15.0  # default baseline
    theta_est = -(ce_ltp + pe_ltp) * 0.02 if (ce_ltp or pe_ltp) else 0
    gamma_est = 0.018 if abs(moneyness) < 0.01 else max(0.005, 0.018 - abs(moneyness) * 0.1)
    vega_est = (ce_ltp + pe_ltp) * 0.03 if (ce_ltp or pe_ltp) else 0

    # trades on this strike
    trades = []
    try:
        trades_db = _data_dir / "trades.db"
        if trades_db.exists():
            tconn = sqlite3.connect(str(trades_db))
            tconn.row_factory = sqlite3.Row
            rows = tconn.execute(
                "SELECT entry_time, action, entry_price, exit_price, pnl_rupees, exit_reason FROM trades WHERE idx=? AND strike=? ORDER BY entry_time DESC LIMIT 30",
                (idx, int(strike))
            ).fetchall()
            tconn.close()
            trades = [{
                "date": r["entry_time"][:10] if r["entry_time"] else "",
                "action": r["action"],
                "entry": r["entry_price"],
                "exit": r["exit_price"],
                "pnl": r["pnl_rupees"],
                "reason": r["exit_reason"] or "",
            } for r in rows]
    except Exception as e:
        print(f"[STRIKE-DETAIL] trades query failed: {e}")

    return {
        "index": idx,
        "strike": strike,
        "spot": spot,
        "atm": atm,
        "atmDistance": int(strike) - atm,
        "moneyness": round(moneyness * 100, 2),
        "expiry": target_expiry,
        "isCurrentExpiry": target_expiry == nearest,
        "ceLTP": ce_ltp,
        "peLTP": pe_ltp,
        "ceOI": d.get("ce_oi", 0),
        "peOI": d.get("pe_oi", 0),
        "ceVol": d.get("ce_volume", 0),
        "peVol": d.get("pe_volume", 0),
        "pcr": pcr,
        "greeks": {
            "deltaCE": round(delta_ce, 3),
            "deltaPE": round(delta_pe, 3),
            "gammaCE": round(gamma_est, 4),
            "gammaPE": round(gamma_est, 4),
            "thetaCE": round(theta_est, 2),
            "thetaPE": round(theta_est, 2),
            "vegaCE": round(vega_est, 2),
            "vegaPE": round(vega_est, 2),
            "rhoCE": 0.08, "rhoPE": -0.12,
        },
        "iv": iv_est,
        "ivRank": 42,
        "trades": trades,
    }


# ── Battle Station — Strike Comparison + AI Verdict ──────────────────────

@app.post("/api/battle/verdict")
async def battle_verdict(payload: dict):
    """Takes a list of strikes, computes strategies, returns Claude AI verdict."""
    strikes = payload.get("strikes", [])
    if not strikes or len(strikes) < 2:
        return {"error": "Need at least 2 strikes to compare"}

    # Fetch detail for each strike
    enriched = []
    for s in strikes[:4]:  # max 4
        idx = s.get("index", "NIFTY").upper()
        strike_val = int(s.get("strike", 0))
        expiry_val = s.get("expiry")
        detail = await strike_detail(idx, strike_val, expiry_val)
        detail["_original"] = s
        enriched.append(detail)

    # Compute strategies
    strategies = _compute_strategies(enriched)

    # Build prompt for Claude
    try:
        from ai_analysis import run_ai_analysis
        ai_data = {
            "live": engine.get_live_data() if engine else {},
            "battleStrikes": enriched,
            "strategies": strategies,
        }
        # Custom prompt for battle verdict
        import os, anthropic, json as _json
        key = os.environ.get("CLAUDE_API_KEY", "")
        if not key:
            return {
                "strikes": enriched, "strategies": strategies,
                "verdict": {"recommendation": "NO AI KEY", "reasoning": "Claude API key not configured"},
            }
        client = anthropic.Anthropic(api_key=key)
        prompt = f"""Analyze these {len(enriched)} option strikes and recommend the best trade.

STRIKES:
{_json.dumps(enriched, default=str)[:3000]}

STRATEGIES AVAILABLE:
{_json.dumps(strategies, default=str)[:2000]}

Return JSON with this structure:
{{
  "winner": "BUY 24400 CE" or similar,
  "confidence": 75,
  "reasoning": ["reason 1", "reason 2", "reason 3"],
  "entry": "155-160",
  "target1": "195",
  "target2": "230",
  "sl": "132",
  "riskReward": "1:2.5",
  "holdTime": "30min-2hrs",
  "avoid": ["strategy name — why avoid"],
  "dangers": ["IV crush", "Theta decay"],
  "dangerScore": 35
}}

Only JSON, no markdown."""
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        verdict = _json.loads(text)
        return {"strikes": enriched, "strategies": strategies, "verdict": verdict}
    except Exception as e:
        print(f"[BATTLE] AI error: {e}")
        return {
            "strikes": enriched, "strategies": strategies,
            "verdict": {"recommendation": "AI_ERROR", "reasoning": [str(e)[:200]]},
        }


def _compute_strategies(strikes: list) -> list:
    """Compute payoff for common strategies across pinned strikes."""
    out = []
    if not strikes:
        return out

    # BUY CE only (cheapest CE)
    ces = [s for s in strikes if s.get("ceLTP", 0) > 0]
    if ces:
        cheapest_ce = min(ces, key=lambda x: x["ceLTP"])
        out.append({
            "name": f"BUY {cheapest_ce['strike']} CE",
            "type": "LONG_CALL",
            "cost": cheapest_ce["ceLTP"],
            "maxLoss": cheapest_ce["ceLTP"],
            "maxProfit": "unlimited",
            "breakeven": cheapest_ce["strike"] + cheapest_ce["ceLTP"],
            "bestWhen": "Bullish breakout",
            "strike": cheapest_ce["strike"],
        })

    # BUY PE only
    pes = [s for s in strikes if s.get("peLTP", 0) > 0]
    if pes:
        cheapest_pe = min(pes, key=lambda x: x["peLTP"])
        out.append({
            "name": f"BUY {cheapest_pe['strike']} PE",
            "type": "LONG_PUT",
            "cost": cheapest_pe["peLTP"],
            "maxLoss": cheapest_pe["peLTP"],
            "maxProfit": cheapest_pe["strike"] - cheapest_pe["peLTP"],
            "breakeven": cheapest_pe["strike"] - cheapest_pe["peLTP"],
            "bestWhen": "Bearish breakdown",
            "strike": cheapest_pe["strike"],
        })

    # Straddle — same strike CE + PE
    for s in strikes:
        if s.get("ceLTP", 0) > 0 and s.get("peLTP", 0) > 0:
            total = s["ceLTP"] + s["peLTP"]
            out.append({
                "name": f"STRADDLE @ {s['strike']}",
                "type": "LONG_STRADDLE",
                "cost": total,
                "maxLoss": total,
                "maxProfit": "unlimited",
                "breakeven": f"{s['strike'] - total} / {s['strike'] + total}",
                "bestWhen": "BIG move either direction",
                "strike": s["strike"],
            })
            break  # just one straddle

    # Strangle — different strikes CE + PE
    if len(strikes) >= 2:
        sorted_s = sorted(strikes, key=lambda x: x["strike"])
        low = sorted_s[0]
        high = sorted_s[-1]
        if low.get("peLTP", 0) > 0 and high.get("ceLTP", 0) > 0:
            total = low["peLTP"] + high["ceLTP"]
            out.append({
                "name": f"STRANGLE {low['strike']}PE + {high['strike']}CE",
                "type": "LONG_STRANGLE",
                "cost": total,
                "maxLoss": total,
                "maxProfit": "unlimited",
                "breakeven": f"{low['strike'] - total} / {high['strike'] + total}",
                "bestWhen": "Very large move",
                "strikes": [low["strike"], high["strike"]],
            })

    return out


@app.post("/api/battle/compare")
async def battle_compare(payload: dict):
    """Lightweight comparison — no AI, just metrics + strategies. Fast."""
    strikes = payload.get("strikes", [])
    if not strikes:
        return {"error": "No strikes provided"}
    enriched = []
    for s in strikes[:4]:
        idx = s.get("index", "NIFTY").upper()
        strike_val = int(s.get("strike", 0))
        expiry_val = s.get("expiry")
        detail = await strike_detail(idx, strike_val, expiry_val)
        enriched.append(detail)
    strategies = _compute_strategies(enriched)
    return {"strikes": enriched, "strategies": strategies}


# ── AI Assistant — Floating 🧠 button (Option 3 full scale) ────────────

def _claude_client():
    """Build Claude client, returns None if not configured."""
    try:
        import os, anthropic
        key = os.environ.get("CLAUDE_API_KEY", "")
        if not key:
            return None
        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None


def _build_context_summary():
    """Build a compact context summary for Claude — current market state."""
    if not engine:
        return {"error": "Engine not running"}
    try:
        live = engine.get_live_data()
        signals = engine.get_signals()[:3] if hasattr(engine, "get_signals") else []
        trap = engine.get_trap_verdict() if hasattr(engine, "get_trap_verdict") else {}
        return {
            "live": live,
            "topSignals": signals if isinstance(signals, list) else [],
            "verdict": trap,
            "timestamp": str(__import__("datetime").datetime.now()),
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/chat")
async def ai_chat(payload: dict):
    """General chat endpoint — user sends message + optional context,
    returns Claude response. Context auto-augmented with live dashboard state."""
    import json as _json
    user_msg = payload.get("message", "").strip()
    frontend_context = payload.get("context", {})
    chat_history = payload.get("history", [])[-6:]  # last 6 turns

    if not user_msg:
        return {"error": "Empty message"}

    client = _claude_client()
    if not client:
        return {"error": "Claude API key not configured"}

    backend_context = _build_context_summary()

    # Build system prompt
    system_prompt = """You are UNIVERSE AI, a trading assistant for an options BUYER on NSE (NIFTY / BANKNIFTY).
You have access to LIVE market data + the user's pinned strikes + current tab context.
Be direct, specific with numbers, use Hinglish when it helps clarity.
Never suggest selling options (user is buyer only). Always respect risk management.
Max response: 4-6 short sentences unless deep analysis requested."""

    # Build messages
    messages = []
    for turn in chat_history:
        if turn.get("role") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})

    # Add live context + user message
    context_snippet = f"\n\nCURRENT CONTEXT:\nTab: {frontend_context.get('activeTab', '?')}\nPinned: {frontend_context.get('pinnedStrikes', [])}\nLive data: {_json.dumps(backend_context, default=str)[:1500]}"
    messages.append({"role": "user", "content": user_msg + context_snippet})

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=system_prompt,
            messages=messages,
        )
        return {"reply": msg.content[0].text.strip(), "tokensUsed": msg.usage.input_tokens + msg.usage.output_tokens}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/morning-brief")
async def ai_morning_brief(payload: dict = None):
    """Morning brief — global cues + expected day setup. Cached 3 hours."""
    import json as _json, time
    global _morning_brief_cache, _morning_brief_time
    now_t = time.time()
    if '_morning_brief_cache' not in globals():
        _morning_brief_cache = None
        _morning_brief_time = 0
    if _morning_brief_cache and (now_t - _morning_brief_time) < 10800:
        return {"brief": _morning_brief_cache, "cached": True}

    client = _claude_client()
    if not client:
        return {"error": "Claude API key not configured"}

    backend_context = _build_context_summary()

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system="You are a morning market brief generator for an NSE options buyer. Output 5-6 sentences covering: overnight global markets, SGX NIFTY futures direction, key levels for today, and ONE specific trading insight. Be specific with numbers. Hinglish OK.",
            messages=[{"role": "user", "content": f"Generate morning brief. Current data: {_json.dumps(backend_context, default=str)[:2000]}"}],
        )
        brief = msg.content[0].text.strip()
        _morning_brief_cache = brief
        _morning_brief_time = now_t
        return {"brief": brief, "cached": False}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/risk-calc")
async def ai_risk_calc(payload: dict):
    """Position sizing calculator — deterministic math, no Claude call."""
    try:
        capital = float(payload.get("capital", 500000))
        risk_pct = float(payload.get("riskPct", 2)) / 100
        entry = float(payload.get("entry", 0))
        sl = float(payload.get("sl", 0))
        lot_size = int(payload.get("lotSize", 65))

        if entry <= 0 or sl <= 0 or sl >= entry:
            return {"error": "Invalid entry/SL. SL must be below entry."}

        max_risk = capital * risk_pct
        risk_per_lot = (entry - sl) * lot_size
        max_lots = int(max_risk / risk_per_lot) if risk_per_lot > 0 else 0
        total_position = entry * lot_size * max_lots
        actual_risk = risk_per_lot * max_lots

        return {
            "maxRisk": round(max_risk),
            "riskPerLot": round(risk_per_lot),
            "recommendedLots": max_lots,
            "positionSize": round(total_position),
            "actualRisk": round(actual_risk),
            "actualRiskPct": round((actual_risk / capital) * 100, 2),
            "advice": f"Risk {max_lots} lots = ₹{round(actual_risk):,} ({round((actual_risk/capital)*100, 2)}% of capital). Exit if price hits ₹{sl}.",
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/scenario")
async def ai_scenario(payload: dict):
    """Scenario explorer — Greeks-based P&L simulation for spot move."""
    try:
        spot = float(payload.get("spot", 0))
        spot_delta_pct = float(payload.get("spotDeltaPct", 0)) / 100
        strike_ltp = float(payload.get("strikeLTP", 0))
        delta = float(payload.get("delta", 0.5))
        gamma = float(payload.get("gamma", 0.018))
        theta = float(payload.get("theta", -3))
        hours_held = float(payload.get("hoursHeld", 1))
        lot_size = int(payload.get("lotSize", 65))
        lots = int(payload.get("lots", 1))

        spot_change = spot * spot_delta_pct
        # New price = current + delta * spot_change + 0.5 * gamma * spot_change^2 + theta * time
        price_change = (delta * spot_change) + (0.5 * gamma * (spot_change ** 2)) + (theta * hours_held / 6.25)
        new_ltp = strike_ltp + price_change
        pnl_per_lot = price_change * lot_size
        total_pnl = pnl_per_lot * lots

        return {
            "newSpot": round(spot + spot_change, 2),
            "newLTP": round(new_ltp, 2),
            "pnlPerLot": round(pnl_per_lot),
            "totalPnL": round(total_pnl),
            "advice": f"If spot moves {spot_delta_pct*100:+.2f}% in {hours_held}h, your P&L would be ₹{round(total_pnl):,}",
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/trade-decision")
async def ai_trade_decision(payload: dict):
    """Claude-based: Should I buy this strike now? Full analysis + verdict."""
    import json as _json
    strike = payload.get("strike")
    client = _claude_client()
    if not client:
        return {"error": "Claude API key not configured"}
    backend_context = _build_context_summary()
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system="You are UNIVERSE AI helping an options buyer decide a trade. Given the strike + live data, return: 1) Decision (YES/NO/WAIT), 2) Confidence %, 3) 3 reasons, 4) Entry/SL/T1/T2 if YES. Be specific with numbers. Max 6 sentences.",
            messages=[{"role": "user", "content": f"Should I buy this strike?\nStrike: {_json.dumps(strike, default=str)}\nMarket: {_json.dumps(backend_context, default=str)[:2000]}"}],
        )
        return {"decision": msg.content[0].text.strip()}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/emergency")
async def ai_emergency(payload: dict):
    """Emergency mid-trade help — 'I'm losing/panicking, what do I do?'"""
    import json as _json
    trade_info = payload.get("trade", {})
    user_concern = payload.get("concern", "My trade is going against me")
    client = _claude_client()
    if not client:
        return {"error": "Claude API key not configured"}
    backend_context = _build_context_summary()
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system="You are UNIVERSE AI helping a panicking trader mid-trade. Be CALM and DIRECT. Analyze: 1) Is exit justified? 2) What's the current momentum direction? 3) Exit or hold recommendation. Be brutally honest. No hedging. 4-5 sentences max.",
            messages=[{"role": "user", "content": f"EMERGENCY: {user_concern}\nMy trade: {_json.dumps(trade_info, default=str)}\nMarket right now: {_json.dumps(backend_context, default=str)[:1500]}"}],
        )
        return {"advice": msg.content[0].text.strip()}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/psychology")
async def ai_psychology_check(payload: dict):
    """Psychology coach — are you overtrading / revenge trading?"""
    import json as _json
    try:
        # Analyze recent trade pattern from DB
        trades_db = _data_dir / "trades.db"
        recent_summary = {}
        if trades_db.exists():
            tconn = sqlite3.connect(str(trades_db))
            tconn.row_factory = sqlite3.Row
            from datetime import timedelta as _td
            _one_day_ago = (ist_now() - _td(days=1)).isoformat()
            rows = tconn.execute(
                "SELECT entry_time, pnl_rupees, qty, probability, status FROM trades WHERE entry_time > ? ORDER BY entry_time",
                (_one_day_ago,)
            ).fetchall()
            tconn.close()
            trades = [dict(r) for r in rows]
            wins = sum(1 for t in trades if (t.get("pnl_rupees") or 0) > 0)
            losses = len(trades) - wins
            total_pnl = sum((t.get("pnl_rupees") or 0) for t in trades)
            recent_summary = {
                "todayTrades": len(trades),
                "wins": wins,
                "losses": losses,
                "todayPnL": round(total_pnl),
                "avgProbability": round(sum((t.get("probability") or 0) for t in trades) / max(len(trades), 1)),
            }
        client = _claude_client()
        if not client:
            return {"analysis": f"Today: {recent_summary.get('todayTrades', 0)} trades, P&L ₹{recent_summary.get('todayPnL', 0):+,}"}
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system="You are a trading psychology coach. Given today's trade pattern, honestly assess: is the trader overtrading, revenge trading, or disciplined? Be direct. 3-4 sentences. End with clear advice (STOP / SLOW DOWN / KEEP GOING).",
            messages=[{"role": "user", "content": f"Today's pattern: {_json.dumps(recent_summary, default=str)}"}],
        )
        return {"analysis": msg.content[0].text.strip(), "data": recent_summary}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ai/pattern-explain")
async def ai_pattern_explain(payload: dict):
    """Teach the user what a pattern means."""
    import json as _json
    pattern_desc = payload.get("pattern", "")
    client = _claude_client()
    if not client:
        return {"error": "Claude API key not configured"}
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system="You are UNIVERSE AI teaching an options buyer. Explain the market pattern in simple Hinglish. Cover: 1) What it means, 2) Trade implication for option buyer, 3) What to avoid. Max 5 sentences.",
            messages=[{"role": "user", "content": f"Explain this pattern: {pattern_desc}"}],
        )
        return {"explanation": msg.content[0].text.strip()}
    except Exception as e:
        return {"error": str(e)}


# ── Beast Mode Training — 11 upgrades over basic weekly Bayesian ───────

@app.post("/api/training/run-now")
async def training_run_now():
    """Manually trigger a beast-mode training cycle. Returns full report."""
    try:
        from ml_beast import run_beast_training
        live = engine.get_live_data() if engine else {}
        nifty = live.get("nifty", {}) if isinstance(live, dict) else {}
        vix = live.get("vix") or nifty.get("vix")
        nifty_pct = nifty.get("changePct") or nifty.get("change_pct") or 0
        pcr = (live.get("oiSummary", {}).get("pcr") if isinstance(live, dict) else None) or 1.0
        report = run_beast_training(current_vix=vix, current_nifty_pct=nifty_pct, current_pcr=pcr)
        return report
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/training/history")
async def training_history(limit: int = 30):
    try:
        from ml_beast import get_training_history
        return {"runs": get_training_history(limit=limit)}
    except Exception as e:
        return {"error": str(e), "runs": []}


@app.get("/api/training/engine-health")
async def training_engine_health():
    try:
        from ml_beast import get_engine_health_report
        return {"engines": get_engine_health_report()}
    except Exception as e:
        return {"error": str(e), "engines": []}


@app.get("/api/training/ab-status")
async def training_ab_status():
    try:
        from ml_beast import get_ab_status
        return get_ab_status()
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/training/ab-start")
async def training_ab_start(payload: dict):
    """Start new A/B test with two weight sets."""
    try:
        from ml_beast import start_ab_test
        wa = payload.get("weights_a")
        wb = payload.get("weights_b")
        if not wa or not wb:
            return {"error": "Need weights_a and weights_b"}
        return start_ab_test(wa, wb)
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/training/ab-finalize")
async def training_ab_finalize():
    """Check if running A/B has enough data, promote winner if yes."""
    try:
        from ml_beast import check_ab_winner
        return check_ab_winner()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/training/online-status")
async def training_online_status():
    try:
        from ml_beast import get_online_learning_status
        return get_online_learning_status()
    except Exception as e:
        return {"error": str(e)}


# ── Engine Toggles — User control which engines contribute to verdict ──

@app.get("/api/engine-toggles")
async def engine_toggles_get():
    """Return current ON/OFF state + weight of each engine."""
    try:
        from engine import _load_engine_toggles, _load_dynamic_weights, _WEIGHT_DEFAULTS, _TOGGLE_DEFAULTS
        toggles = _load_engine_toggles()
        weights = _load_dynamic_weights()
        # Show max weight too (what it would be if ON)
        return {
            "engines": [
                {
                    "key": k,
                    "active": toggles.get(k, True),
                    "weight": weights.get(k, 0),
                    "maxWeight": _WEIGHT_DEFAULTS.get(k, 0),
                } for k in _WEIGHT_DEFAULTS
            ],
        }
    except Exception as e:
        return {"error": str(e), "engines": []}


@app.post("/api/engine-toggles")
async def engine_toggles_set(payload: dict):
    """Persist user's toggle preferences. Verdict uses immediately."""
    try:
        from engine import _save_engine_toggles
        toggles = payload.get("toggles") or payload
        if not isinstance(toggles, dict):
            return {"error": "Invalid payload"}
        saved = _save_engine_toggles(toggles)
        return {"saved": saved, "ok": True}
    except Exception as e:
        return {"error": str(e)}


# ── Battle Station Bonus: strike history / spread / correlation ────────

@app.get("/api/strike-history")
async def strike_history(index: str, strike: int, minutes: int = 30):
    """Return time-ordered LTP + OI snapshots for a specific strike over
    the last N minutes. Used for inline sparklines in Battle Station."""
    idx = index.upper()
    if not engine or idx not in ("NIFTY", "BANKNIFTY"):
        return {"points": []}

    # Read from trading_times market_snapshots (already captures per-tick data)
    data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
    db = data_dir / "trading_times.db"
    if not db.exists():
        return {"points": []}

    try:
        from datetime import timedelta
        cutoff = (ist_now() - timedelta(minutes=minutes)).isoformat()
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        # market_snapshots has ATM strikes LTP, for specific strike we approximate
        # using spot + atm_ce_ltp / atm_pe_ltp. For exact strike history, we'd need
        # a dedicated per-strike table. For now, return spot + ATM premium trend.
        rows = conn.execute(
            "SELECT timestamp, spot, atm_ce_ltp, atm_pe_ltp FROM market_snapshots "
            "WHERE idx=? AND timestamp > ? ORDER BY timestamp ASC",
            (idx, cutoff)
        ).fetchall()
        conn.close()
        points = [{
            "t": r["timestamp"][11:16] if r["timestamp"] else "",
            "spot": r["spot"],
            "ceLTP": r["atm_ce_ltp"],
            "peLTP": r["atm_pe_ltp"],
        } for r in rows]
        return {"points": points, "strike": strike, "index": idx}
    except Exception as e:
        print(f"[STRIKE-HISTORY] {e}")
        return {"points": [], "error": str(e)}


@app.get("/api/spread")
async def spread_check(index: str, strike: int, type: str = "CE"):
    """Fetch Kite depth quote to compute bid-ask spread % for a strike.
    Used for 'wide spread' warning in Battle Station."""
    idx = index.upper()
    if not engine or idx not in ("NIFTY", "BANKNIFTY"):
        return {"error": "Engine not ready"}

    try:
        # Find instrument
        cfg = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY"}[idx]
        nearest = str(engine.nearest_expiry.get(idx, ""))
        from datetime import date as date_type
        target_date = date_type.fromisoformat(nearest) if nearest else None
        if not target_date:
            return {"error": "No expiry"}

        opts = [i for i in engine.nfo_instruments
                if i["name"] == cfg
                and i["instrument_type"] == type.upper()
                and i["expiry"] == target_date
                and int(i["strike"]) == int(strike)]
        if not opts:
            return {"error": f"Strike {strike} {type} not found"}

        sym = f"NFO:{opts[0]['tradingsymbol']}"
        q = engine.kite.quote([sym]).get(sym, {})
        depth = q.get("depth", {})
        buy = depth.get("buy", [{}])[0] if depth.get("buy") else {}
        sell = depth.get("sell", [{}])[0] if depth.get("sell") else {}
        bid = buy.get("price", 0)
        ask = sell.get("price", 0)
        ltp = q.get("last_price", 0)
        spread = ask - bid if (bid and ask) else 0
        spread_pct = (spread / ltp * 100) if ltp else 0
        status = "tight" if spread_pct < 0.5 else "moderate" if spread_pct < 1.5 else "wide"
        return {
            "bid": bid, "ask": ask, "ltp": ltp,
            "spread": spread, "spreadPct": round(spread_pct, 2),
            "bidQty": buy.get("quantity", 0),
            "askQty": sell.get("quantity", 0),
            "status": status,
        }
    except Exception as e:
        print(f"[SPREAD] {e}")
        return {"error": str(e)}


@app.get("/api/correlation")
async def correlation_check(minutes: int = 30):
    """Compute rolling correlation + leader-lag between NIFTY and BANKNIFTY
    using recent 1-min price changes from trading_times."""
    if not engine:
        return {"error": "Engine not ready"}
    try:
        data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
        db = data_dir / "trading_times.db"
        if not db.exists():
            return {"correlation": 0, "leader": "UNKNOWN"}
        from datetime import timedelta
        cutoff = (ist_now() - timedelta(minutes=minutes)).isoformat()
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        n_rows = conn.execute(
            "SELECT timestamp, spot FROM market_snapshots WHERE idx='NIFTY' AND timestamp > ? ORDER BY timestamp",
            (cutoff,)
        ).fetchall()
        b_rows = conn.execute(
            "SELECT timestamp, spot FROM market_snapshots WHERE idx='BANKNIFTY' AND timestamp > ? ORDER BY timestamp",
            (cutoff,)
        ).fetchall()
        conn.close()

        if len(n_rows) < 5 or len(b_rows) < 5:
            return {"correlation": 0, "leader": "NOT_ENOUGH_DATA", "samples": min(len(n_rows), len(b_rows))}

        # Pair by nearest timestamp
        n_series = [r["spot"] for r in n_rows]
        b_series = [r["spot"] for r in b_rows]
        n = min(len(n_series), len(b_series))
        n_series, b_series = n_series[:n], b_series[:n]

        # Compute % returns
        n_ret = [(n_series[i] - n_series[i-1]) / n_series[i-1] for i in range(1, n) if n_series[i-1]]
        b_ret = [(b_series[i] - b_series[i-1]) / b_series[i-1] for i in range(1, n) if b_series[i-1]]
        if not n_ret or not b_ret:
            return {"correlation": 0, "leader": "UNKNOWN"}

        # Pearson correlation
        m = min(len(n_ret), len(b_ret))
        n_ret, b_ret = n_ret[:m], b_ret[:m]
        mean_n = sum(n_ret) / m
        mean_b = sum(b_ret) / m
        cov = sum((n_ret[i] - mean_n) * (b_ret[i] - mean_b) for i in range(m)) / m
        var_n = sum((x - mean_n)**2 for x in n_ret) / m
        var_b = sum((x - mean_b)**2 for x in b_ret) / m
        corr = cov / ((var_n * var_b)**0.5) if (var_n > 0 and var_b > 0) else 0

        # Leader detection: compare volatility / recent momentum
        # If BN moves happened slightly earlier or are bigger, BN is leading
        n_vol = (var_n ** 0.5) * 100
        b_vol = (var_b ** 0.5) * 100
        leader = "BANKNIFTY" if b_vol > n_vol * 1.1 else "NIFTY" if n_vol > b_vol * 1.1 else "MOVING_TOGETHER"

        return {
            "correlation": round(corr, 3),
            "niftyVol": round(n_vol, 3),
            "bnVol": round(b_vol, 3),
            "leader": leader,
            "samples": m,
            "window": minutes,
        }
    except Exception as e:
        print(f"[CORRELATION] {e}")
        return {"error": str(e)}


@app.post("/api/news/summary")
async def news_summary(payload: dict):
    """Claude-based summary of current market events relevant to the pinned strike(s).
    Uses all available engine data to synthesize 'what's happening right now'."""
    strikes = payload.get("strikes", [])
    try:
        import os, anthropic, json as _json
        key = os.environ.get("CLAUDE_API_KEY", "")
        if not key:
            return {"summary": "", "error": "Claude API key not configured"}

        # Gather context
        live_data = engine.get_live_data() if engine else {}
        signals = engine.get_signals() if engine else []
        unusual = engine.get_unusual() if engine else []

        context = {
            "live": live_data,
            "signals": signals[:5] if isinstance(signals, list) else [],
            "unusual": unusual[:10] if isinstance(unusual, list) else [],
            "pinnedStrikes": strikes,
        }

        client = anthropic.Anthropic(api_key=key)
        prompt = f"""You are a market news synthesizer. Given the CURRENT market data below,
produce a 3-sentence summary of what's moving the market right now that's relevant to
these specific strikes: {_json.dumps(strikes, default=str)[:500]}.

Market data: {_json.dumps(context, default=str)[:2500]}

Focus on: unusual activity, signals driving the direction, major OI shifts, expiry proximity.
Be specific with numbers. No hedging language. Max 3 sentences."""
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return {"summary": msg.content[0].text.strip()}
    except Exception as e:
        return {"summary": "", "error": str(e)}


# ── Replay Mode Route (keep existing) ────────────────────────────────────


# ── Trading Times Routes ─────────────────────────────────────────────────

@app.get("/api/trading-times/live/{index}")
async def trading_times_live(index: str):
    tt_init_db()
    idx = index.upper()
    if idx not in ("NIFTY", "BANKNIFTY"):
        return JSONResponse({"error": "Invalid index"}, status_code=400)
    if not engine:
        return {"signal": {"windowType": "NO_DATA", "blastDirection": "NONE", "confidence": 0, "message": "Engine not running"}}
    return tt_live(engine, idx)

@app.get("/api/trading-times/timeline/{index}")
async def trading_times_timeline(index: str):
    tt_init_db()
    return tt_timeline(index.upper())

@app.get("/api/trading-times/report/daily")
async def trading_times_daily(date: str = None):
    tt_init_db()
    return tt_daily(date)

@app.get("/api/trading-times/report/weekly")
async def trading_times_weekly():
    tt_init_db()
    return tt_weekly()

@app.get("/api/trading-times/report/monthly")
async def trading_times_monthly(year: int = None, month: int = None):
    tt_init_db()
    return tt_monthly(year, month)


# ── WebSocket Route ──────────────────────────────────────────────────────

@app.websocket("/ws/ticks")
async def websocket_ticks(ws: WebSocket):
    await ws.accept()
    print("[WS] Client connected")

    if engine:
        engine.register_ws(ws)

    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        print("[WS] Client disconnected")
    except Exception as e:
        print(f"[WS] Error: {e}")
    finally:
        if engine:
            engine.unregister_ws(ws)


# ── Serve React Frontend (Production) ────────────────────────────────────

# ── Diagnostic endpoint (always registered, even if dist missing) ─────
@app.get("/api/system/build-info")
async def build_info():
    """Diagnose why root URL might be 404 — checks DIST_DIR existence."""
    import os
    dist_path = str(DIST_DIR)
    dist_exists = DIST_DIR.exists()
    index_path = DIST_DIR / "index.html"
    index_exists = index_path.exists()
    assets_dir = DIST_DIR / "assets"

    # Walk a few levels up to see what's there
    parents_listing = {}
    try:
        cwd = os.getcwd()
        parent1 = Path(__file__).parent.parent
        parents_listing = {
            "cwd": cwd,
            "main.py_path": str(Path(__file__)),
            "parent1 (project root)": str(parent1),
            "parent1_listing": [p.name for p in parent1.iterdir()][:30] if parent1.exists() else [],
            "dist_listing": [p.name for p in DIST_DIR.iterdir()][:30] if dist_exists else [],
            "assets_listing": [p.name for p in assets_dir.iterdir()][:30] if assets_dir.exists() else [],
        }
    except Exception as e:
        parents_listing = {"error": str(e)}

    return {
        "DIST_DIR": dist_path,
        "DIST_DIR_exists": dist_exists,
        "index_html_exists": index_exists,
        "index_html_path": str(index_path),
        "static_routes_registered": dist_exists,
        "diagnosis": (
            "OK — frontend should serve" if (dist_exists and index_exists)
            else "BROKEN — dist/ folder not generated. Check Render build logs for npm errors."
        ),
        "filesystem": parents_listing,
    }


# ── Fallback root handler — runs ONLY if dist/ missing ────────────────
# Without this, GET / returns FastAPI default 404 with no info.
# This gives the user something useful when build fails.
if not DIST_DIR.exists():
    @app.get("/")
    async def fallback_root():
        return JSONResponse({
            "error": "Frontend build not found",
            "message": "The dist/ folder was not generated during deploy. "
                       "Check Render build logs.",
            "debug_endpoint": "/api/system/build-info",
            "api_status": "API endpoints work — only frontend is missing",
        }, status_code=503)


if DIST_DIR.exists():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="static-assets")

    # Serve other static files in dist root (favicon, icons, etc)
    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(str(DIST_DIR / "favicon.svg"))

    @app.get("/icons.svg")
    async def icons():
        return FileResponse(str(DIST_DIR / "icons.svg"))

    # PWA files — these MUST be served as their own files, not as
    # index.html. Catch-all SPA route below was returning HTML for
    # these → broke service worker registration + manifest parsing
    # → user saw "stale" frozen dashboard because SW never updated.
    @app.get("/manifest.json")
    async def manifest_json():
        return FileResponse(str(DIST_DIR / "manifest.json"),
                            media_type="application/json")

    @app.get("/sw.js")
    async def service_worker():
        # Service workers MUST be served with correct content-type
        # AND from same origin — extra cache-busting headers.
        return FileResponse(str(DIST_DIR / "sw.js"),
                            media_type="application/javascript",
                            headers={
                                "Service-Worker-Allowed": "/",
                                "Cache-Control": "no-cache, no-store, must-revalidate",
                            })

    # ROOT path explicitly — `/{full_path:path}` does not match empty string
    # so without this, GET / returns 404 (browsing dashboard.onrender.com fails).
    @app.get("/")
    async def serve_spa_root():
        return FileResponse(str(DIST_DIR / "index.html"))

    # SPA fallback — serve index.html for all non-API/WS routes
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Don't serve index.html for API/WS routes
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            return JSONResponse({"error": "Not found"}, status_code=404)
        # Files with extensions in dist root → serve as static (avoid
        # index.html for things like /robots.txt, /image.png etc.)
        if "." in full_path and not full_path.startswith("api/"):
            asset_path = DIST_DIR / full_path
            if asset_path.is_file() and asset_path.parent == DIST_DIR:
                return FileResponse(str(asset_path))
        return FileResponse(str(DIST_DIR / "index.html"))


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=not IS_PROD)
