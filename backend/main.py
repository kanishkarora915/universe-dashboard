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
                print(f"[STARTUP] Engine auto-resumed from cached token {access_token[:8]}…")
            else:
                print("[STARTUP] access_token.json present but missing fields — manual login needed")
        elif engine is None:
            print("[STARTUP] No access_token.json found — waiting for manual login")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[STARTUP] Auto-resume failed (will need manual login): {e}")

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

    try:

        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        session["api_key"] = api_key
        session["api_secret"] = api_secret
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
        try:
            from trinity import api_routes as _tr
            _tr.attach_engine(engine)
        except Exception as _e:
            print(f"[TRINITY] attach_engine failed: {_e}")

        print(f"[AUTO-LOGIN] Engine started with access_token {access_token[:8]}...")
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

@app.get("/api/smart-money/{index}")
async def smart_money_data(index: str):
    """Get smart money signals (slow cooking OI, block trades, iceberg) for an index."""
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


@app.get("/api/scalper/live-prices")
async def scalper_live_prices():
    """Zero-latency live LTP per open scalper trade. Pulls direct from engine.chains
    (in-memory, no DB hit). Returns just {trade_id: ltp, pnl_rupees, pnl_pct}.
    Frontend should poll this every 1s for real-trading-app feel."""
    if not engine:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    try:
        import scalper_mode
        conn = scalper_mode._conn()
        rows = conn.execute(
            "SELECT id, idx, strike, action, entry_price, qty FROM scalper_trades WHERE status='OPEN'"
        ).fetchall()
        conn.close()
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


@app.get("/api/scalper/trades/{trade_id}/ticks")
async def scalper_trade_ticks(trade_id: int, limit: int = 500):
    """Tick history for one scalper trade (live LTP samples)."""
    try:
        import scalper_mode
        return {"trade_id": trade_id, "ticks": scalper_mode.get_trade_ticks(trade_id, limit=limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/scalper/trades/{trade_id}/exit")
async def scalper_manual_exit(trade_id: int, body: dict = None):
    """Manually exit an open scalper trade at current/given LTP."""
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
        return scalper_mode.manual_exit(trade_id, current_ltp=float(ltp))
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
    """Latest health snapshots for all open trades (both PnL + Scalper)."""
    try:
        from position_watcher import get_last_health
        return {"positions": get_last_health()}
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
        try:
            from volatility_detector import classify_regime, get_recommendations
            regime_data = classify_regime(engine)
            vol_rec = get_recommendations(regime_data)
        except Exception as e:
            regime_data = {"regime": "UNKNOWN", "error": str(e)}
            vol_rec = {"main_pnl_allowed": True, "min_probability": 50, "warnings": []}

        # Get latest verdict for both indices
        verdict = {}
        try:
            verdict = engine.get_full_verdict() if hasattr(engine, "get_full_verdict") else {}
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
                    "topReasons": (v.get("topReasons", [])[:3] if isinstance(v, dict) else [])}
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


@app.get("/api/reversal/live")
async def reversal_live():
    """Live capitulation state for both NIFTY and BANKNIFTY."""
    try:
        from capitulation_engine import get_live_state
        return get_live_state()
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
    """All currently tracked S/R levels with full state — initial role,
    current role, touches, OI evolution, last flip timestamp."""
    try:
        from polarity_flip_detector import get_current_levels
        return get_current_levels(idx.upper())
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


# ── Smart Money Detector endpoints (institutional flow tracking) ──
@app.get("/api/smart-money/live")
async def smart_money_live():
    """Latest smart money classification per index — WRITER_DRIP /
    BUYER_DRIP / WRITER_COVER / BUYER_EXIT for every active NTM strike,
    plus net institutional view + buyer recommendations."""
    try:
        from smart_money_detector import get_live_state
        return get_live_state()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/smart-money/history")
async def smart_money_history(idx: str = "", limit: int = 50):
    """Today's logged strong findings (score ≥ 6)."""
    try:
        from smart_money_detector import get_strike_history_log
        return {"events": get_strike_history_log(idx.upper() if idx else None, limit)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/smart-money/strike/{strike}")
async def smart_money_strike_history(strike: int, idx: str = "NIFTY", minutes: int = 60):
    """Per-strike per-minute history (for drill-down chart)."""
    try:
        from oi_minute_capture import get_strike_history
        return {
            "idx": idx.upper(), "strike": strike,
            "minutes": minutes,
            "history": get_strike_history(idx.upper(), strike, minutes),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/smart-money/pulse-now")
async def smart_money_pulse_now():
    """Force an immediate smart money analysis (bypass 2-min cycle)."""
    try:
        if not engine:
            return JSONResponse({"error": "engine not started"}, status_code=503)
        # Trigger an OI minute capture first to ensure latest data
        from oi_minute_capture import capture_pulse
        capture_pulse(engine)
        from smart_money_detector import analyze_pulse
        return analyze_pulse()
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


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
    """Liveness signal for the position watcher loop.
    Frontend uses this to draw the green/red 'WATCHER LIVE' badge."""
    try:
        from position_watcher import get_last_health, _last_health_cache
        cached = list(_last_health_cache.values())
        last_pulse_ts = max([h.get("ts", 0) for h in cached], default=0)
        now = __import__("time").time()
        age = (now - last_pulse_ts) if last_pulse_ts else None
        is_live = age is not None and age < 90  # 3× pulse interval
        return {
            "live": bool(is_live),
            "last_pulse_age_sec": round(age, 1) if age is not None else None,
            "cached_positions": len(cached),
            "main_count": len([h for h in cached if h.get("source") == "MAIN"]),
            "scalper_count": len([h for h in cached if h.get("source") == "SCALPER"]),
            "stub_count": len([h for h in cached if h.get("stub")]),
        }
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
async def position_force_exit(trade_id: int, source: str = "MAIN"):
    """Manually exit a trade from any tab — uses watcher's force-close machinery."""
    try:
        src = source.upper()
        if src == "SCALPER":
            import scalper_mode
            # use scalper's existing manual_exit
            import sqlite3
            conn = sqlite3.connect(str(scalper_mode.SCALPER_DB))
            row = conn.execute("SELECT current_ltp, entry_price FROM scalper_trades WHERE id=? AND status='OPEN'",
                               (trade_id,)).fetchone()
            conn.close()
            if not row:
                return {"status": "not_found"}
            ltp = row[0] or row[1]
            res = scalper_mode.manual_exit(trade_id, ltp, reason="USER_MANUAL_EXIT")
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
        lot_size = int(payload.get("lotSize", 75))

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
        lot_size = int(payload.get("lotSize", 75))
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
        return FileResponse(str(DIST_DIR / "index.html"))


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=not IS_PROD)
