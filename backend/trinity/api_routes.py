"""
FastAPI endpoints — exact list from spec §7.2.

GET  /api/trinity/status           connection + subscription status
GET  /api/trinity/snapshot         current spot/future/synthetic
GET  /api/trinity/timeseries       historical 3-line data
GET  /api/trinity/regime           current regime + confidence
GET  /api/trinity/signals/active   live trade signals
GET  /api/trinity/strikes/heatmap  9-strike deviation map
GET  /api/trinity/trap-zones       upper/lower trap bounds
WS   /ws/trinity/live              real-time tick stream
GET  /api/trinity/config + POST    risk_capital config
"""

import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse

from . import orchestrator
from . import storage
from . import tick_processor as tp
from . import websocket_manager as wsm
from . import trap_detector as td
from . import synthetic_calculator as sc

router = APIRouter(prefix="/api/trinity", tags=["trinity"])

# Engine reference — set at app startup via attach_engine()
_engine_holder = {"engine": None}


def attach_engine(engine):
    _engine_holder["engine"] = engine


def _engine():
    return _engine_holder.get("engine")


@router.get("/status")
async def trinity_status():
    eng = _engine()
    if not eng:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    return orchestrator.get_status(eng)


@router.get("/snapshot")
async def trinity_snapshot():
    eng = _engine()
    if not eng:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    snap = orchestrator.get_snapshot()
    state = tp.get_state()
    fut_token = state.fut_token
    spot = orchestrator.get_spot_ltp(eng) or 0
    future = orchestrator.get_future_ltp(eng, fut_token) or 0
    atm = wsm.compute_atm_strike(spot, 50) if spot else 0
    per_strike = sc.compute_per_strike_synthetics(eng, atm) if atm else {}
    composite, used = sc.compute_composite_synthetic(per_strike)
    deviation = sc.compute_trinity_deviation(composite, spot) if composite else 0
    return {
        "ts": snap.get("ts"),
        "spot": spot,
        "future": future,
        "synthetic": round(composite or 0, 2),
        "deviation": round(deviation or 0, 2),
        "premium": round((future - spot) if future and spot else 0, 2),
        "atm": atm,
        "regime": state.current_regime,
        "regime_duration_secs": round(state.regime_duration_secs(), 1),
        "strikes_used": used,
        "degraded": state.degraded,
    }


@router.get("/timeseries")
async def trinity_timeseries(mins: int = 30):
    """Last N minutes of 1-sec bars for chart."""
    if mins < 1: mins = 1
    if mins > 360: mins = 360
    return {"data": storage.get_timeseries(mins=mins, limit=mins * 60 + 100)}


@router.get("/regime")
async def trinity_regime():
    eng = _engine()
    if not eng:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    state = tp.get_state()
    return {
        "regime": state.current_regime,
        "duration_secs": round(state.regime_duration_secs(), 1),
        "history": list(state.regime_history)[-20:],
    }


@router.get("/signals/active")
async def trinity_active_signals():
    return {"signals": storage.get_active_signals()}


@router.get("/signals/history")
async def trinity_signal_history(limit: int = 20):
    return {"signals": storage.get_recent_signals(limit=limit)}


@router.get("/strikes/heatmap")
async def trinity_strikes_heatmap():
    """9-strike deviation map for heatmap UI."""
    eng = _engine()
    if not eng:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    spot = orchestrator.get_spot_ltp(eng) or 0
    if not spot:
        return {"error": "No spot price"}
    atm = wsm.compute_atm_strike(spot, 50)
    per_strike = sc.compute_per_strike_synthetics(eng, atm)
    deviations = sc.compute_strike_deviations(per_strike, spot)
    return {
        "spot": spot,
        "atm": atm,
        "strikes": deviations,
    }


@router.get("/trap-zones")
async def trinity_trap_zones():
    eng = _engine()
    if not eng:
        return JSONResponse({"error": "Engine not running"}, status_code=400)
    spot = orchestrator.get_spot_ltp(eng) or 0
    if not spot:
        return {"error": "No spot price"}
    state = tp.get_state()
    return td.compute_trap_zones(eng, spot, state, state.current_regime)


@router.get("/config")
async def trinity_config_get():
    return {"risk_capital": orchestrator.RISK_CAPITAL}


@router.post("/config")
async def trinity_config_set(body: dict):
    rc = body.get("risk_capital")
    if rc is not None:
        new_val = orchestrator.set_risk_capital(rc)
        return {"risk_capital": new_val, "ok": True}
    return {"error": "no fields to update"}


@router.websocket("/live")
async def trinity_ws_live(websocket: WebSocket):
    """Push 1-sec snapshot updates to frontend."""
    await websocket.accept()
    try:
        last_ts = 0
        while True:
            state = tp.get_state()
            snap = state.bar_buffer.latest()
            if snap and snap.get("ts") and snap["ts"] != last_ts:
                last_ts = snap["ts"]
                payload = {
                    "type": "snapshot",
                    "data": snap,
                    "regime": state.current_regime,
                    "duration_secs": round(state.regime_duration_secs(), 1),
                }
                await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[TRINITY-WS] error: {e}")


# Top-level WebSocket route (per spec §7.2 path: /ws/trinity/live)
ws_router = APIRouter()


@ws_router.websocket("/ws/trinity/live")
async def trinity_ws_live_top(websocket: WebSocket):
    await trinity_ws_live(websocket)
