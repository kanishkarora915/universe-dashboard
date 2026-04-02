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
    return {
        "authenticated": session["access_token"] is not None,
        "engine_running": engine is not None and engine.running,
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

@app.get("/api/live")
async def live_data():
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_live_data()


@app.get("/api/option-chain/{index}")
async def option_chain(index: str):
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_option_chain(index.upper())


@app.get("/api/historical/{token}/{interval}")
async def historical(token: str, interval: str = "5minute", days: int = 5):
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_historical(token, interval, days)


@app.get("/api/unusual")
async def unusual():
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_unusual()


@app.get("/api/oi-summary")
async def oi_summary():
    """Returns aggregated OI change data for OI Change tab."""
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_oi_change_summary()


@app.get("/api/signals")
async def signals():
    """Returns auto-generated signals with 9-point scoring."""
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_signals()


@app.get("/api/intraday")
async def intraday():
    """Returns real-time computed technical indicators for intraday tab."""
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_intraday()


@app.get("/api/nextday")
async def nextday():
    """Returns real support/resistance/walls/strategy for next day tab."""
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_nextday()


@app.get("/api/weekly")
async def weekly():
    """Returns real weekly analysis from option chain data."""
    if not engine or not engine.running:
        return JSONResponse({"error": "Engine not running"}, status_code=503)
    return engine.get_weekly()


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
