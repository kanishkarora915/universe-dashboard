"""
UNIVERSE Backend — FastAPI server for Kite Connect integration.
Routes: OAuth login/callback, live data, option chain, historical, unusual activity, WebSocket.
Serves React frontend static build in production.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from kiteconnect import KiteConnect

from engine import MarketEngine

# ── Config ───────────────────────────────────────────────────────────────

PORT = int(os.getenv("PORT", 8000))
# In production (Render), frontend is served from same origin
# In dev, frontend runs on separate Vite port
IS_PROD = os.getenv("RENDER", "") == "true" or os.path.exists(Path(__file__).parent.parent / "dist")
FRONTEND_URL = os.getenv("FRONTEND_URL", "")  # Set on Render, e.g. https://universe-dashboard.onrender.com

# Build path for static files
DIST_DIR = Path(__file__).parent.parent / "dist"

# ── Data cache (persists across sessions) ────────────────────────────────

CACHE_FILE = Path(__file__).parent / "data_cache.json"

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

def _get_or_cache(key, fetcher):
    """Get live data from engine, cache it. If engine down, return cached."""
    if engine and engine.running:
        try:
            data = fetcher()
            save_cache(key, data)
            return data
        except Exception as e:
            print(f"[CACHE] Fetch error for {key}: {e}")
    # Engine not running — serve last cached data
    cached = get_cached(key)
    if cached:
        return cached
    return JSONResponse({"error": "No data available. Login to Kite."}, status_code=503)


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


@app.get("/api/seller-summary")
async def seller_summary():
    return _get_or_cache("seller_summary", lambda: engine.get_seller_summary())


@app.get("/api/trade-analysis")
async def trade_analysis():
    return _get_or_cache("trade_analysis", lambda: engine.get_trade_analysis())


@app.get("/api/hidden-shift")
async def hidden_shift():
    return _get_or_cache("hidden_shift", lambda: engine.get_hidden_shift())


@app.get("/api/signals")
async def signals():
    return _get_or_cache("signals", lambda: engine.get_signals())


@app.get("/api/price-action")
async def price_action():
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
