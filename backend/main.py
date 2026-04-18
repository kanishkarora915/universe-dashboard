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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from kiteconnect import KiteConnect

from engine import MarketEngine
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
    global event_loop
    event_loop = asyncio.get_event_loop()
    yield
    if engine:
        engine.stop()


app = FastAPI(title="UNIVERSE Backend", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    """Auto-login using cached access_token from auto_login.py daemon."""
    global engine
    token_file = _data_dir / "access_token.json"
    if not token_file.exists():
        return JSONResponse({"error": "No cached token. Run auto_login.py first."}, status_code=400)

    try:
        token_data = json.loads(token_file.read_text())
        api_key = token_data.get("api_key", "")
        access_token = token_data.get("access_token", "")

        if not api_key or not access_token:
            return JSONResponse({"error": "Invalid token cache"}, status_code=400)

        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        session["api_key"] = api_key
        session["api_secret"] = token_data.get("api_secret", "")
        session["access_token"] = access_token
        session["kite"] = kite

        # Fetch holidays
        try:
            from trade_logger import save_nse_holidays_from_kite
            save_nse_holidays_from_kite(kite)
        except Exception:
            pass

        engine = MarketEngine(api_key=api_key, access_token=access_token, loop=event_loop)
        engine.start()

        print(f"[AUTO-LOGIN] Engine started with cached token from {token_data.get('login_time', 'unknown')}")
        return {"status": "success", "message": "Auto-login successful, engine started"}
    except Exception as e:
        print(f"[AUTO-LOGIN] Failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


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


@app.post("/api/logout")
async def logout():
    global engine
    if engine:
        engine.stop()
        engine = None
    session.update({"api_key": None, "api_secret": None, "access_token": None, "kite": None})
    return {"status": "logged_out"}


# ── Data Routes ──────────────────────────────────────────────────────────

_cache_timestamps = {}  # {key: timestamp}
_memory_cache = {}  # {key: data} — fast in-memory cache

def _get_or_cache(key, fetcher, ttl=5):
    """Get live data with TTL-based caching. ttl=seconds before refresh."""
    now = time.time()
    # Return memory cache if fresh
    if key in _memory_cache and key in _cache_timestamps:
        if now - _cache_timestamps[key] < ttl:
            return _memory_cache[key]

    if engine and engine.running:
        try:
            data = fetcher()
            _memory_cache[key] = data
            _cache_timestamps[key] = now
            save_cache(key, data)
            return data
        except Exception as e:
            print(f"[CACHE] Fetch error for {key}: {e}")
            # Return stale memory cache if available
            if key in _memory_cache:
                return _memory_cache[key]

    # Engine not running — serve last saved data (file cache)
    if key in _memory_cache:
        return _memory_cache[key]
    cached = get_cached(key)
    if cached:
        _memory_cache[key] = cached
        return cached
    # Return empty data structure instead of 503 — frontend handles gracefully
    return {}


@app.get("/api/live")
async def live_data():
    return _get_or_cache("live", lambda: engine.get_live_data())


@app.get("/api/option-chain/{index}")
async def option_chain(index: str):
    return _get_or_cache(f"chain_{index}", lambda: engine.get_option_chain(index.upper()))


@app.get("/api/historical/{token}/{interval}")
async def historical(token: str, interval: str = "5minute", days: int = 5):
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_historical(token, interval, days)


@app.get("/api/unusual")
async def unusual():
    return _get_or_cache("unusual", lambda: engine.get_unusual())


@app.get("/api/oi-summary")
async def oi_summary():
    return _get_or_cache("oi_summary", lambda: engine.get_oi_change_summary())


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
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return engine.trade_manager.get_open_trades()

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
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
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
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
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

    # SPA fallback — serve index.html for all non-API routes
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Don't serve index.html for API/WS routes
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(str(DIST_DIR / "index.html"))


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=not IS_PROD)
