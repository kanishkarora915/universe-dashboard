"""
UNIVERSE Backend — FastAPI server for Kite Connect integration.
Routes: OAuth login/callback, live data, option chain, historical, unusual activity, WebSocket.
Serves React frontend static build in production.
"""

import asyncio
import json
import os
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
async def trades_closed():
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return []
    return engine.trade_manager.get_closed_trades()

@app.get("/api/trades/stats")
async def trades_stats():
    if not engine or not hasattr(engine, 'trade_manager') or not engine.trade_manager:
        return {"total": 0, "open": 0, "wins": 0, "losses": 0, "winRate": 0, "totalPnl": 0}
    return engine.trade_manager.get_stats()

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
