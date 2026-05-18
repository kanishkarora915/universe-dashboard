"""
Kite WS subscription manager for Trinity.

Resolves:
  - NIFTY 50 spot (token 256265)
  - NIFTY current month FUT
  - 9 option strikes around ATM (ATM, ±50, ±100, ±150, ±200)

Reuses the existing engine's WebSocket — adds FUT token to subscription list.
For options, reads from engine.chains (already pre-subscribed at ATM±10).
"""

import time
from datetime import datetime, date

NIFTY_SPOT_TOKEN = 256265

# Strike offsets from ATM (per spec §2): ATM, ±50, ±100, ±150, ±200
STRIKE_OFFSETS_PTS = [-200, -150, -100, -50, 0, 50, 100, 150, 200]


def _today():
    return date.today()


def find_nifty_fut_token(nfo_instruments):
    """Find current month NIFTY FUT token from kite NFO instruments dump."""
    today = _today()
    nifty_futs = [
        i for i in nfo_instruments
        if i.get("name") == "NIFTY"
        and i.get("instrument_type") == "FUT"
        and i.get("segment") in ("NFO-FUT", "NFO")
    ]
    # Filter expiries >= today, pick nearest
    upcoming = []
    for inst in nifty_futs:
        exp = inst.get("expiry")
        if isinstance(exp, str):
            try:
                exp = datetime.strptime(exp, "%Y-%m-%d").date()
            except Exception:
                continue
        if exp and exp >= today:
            upcoming.append((exp, inst))
    if not upcoming:
        return None, None
    upcoming.sort(key=lambda x: x[0])
    nearest_exp, inst = upcoming[0]
    return inst.get("instrument_token"), {
        "token": inst.get("instrument_token"),
        "tradingsymbol": inst.get("tradingsymbol"),
        "expiry": str(nearest_exp),
    }


def compute_atm_strike(spot, strike_gap=50):
    """Round spot to nearest 50 → ATM strike."""
    if spot <= 0:
        return 0
    return round(spot / strike_gap) * strike_gap


def get_nine_strikes(atm, strike_gap=50):
    """Return 9 strikes for synthetic computation (ATM, ±50, ±100, ±150, ±200)."""
    return [atm + (off // strike_gap) * strike_gap for off in STRIKE_OFFSETS_PTS]


def resolve_strike_tokens(engine, atm):
    """For each of 9 strikes, find CE+PE tokens from engine.token_to_info.
    Returns dict {strike: {"ce_token": int, "pe_token": int}}."""
    strikes = get_nine_strikes(atm)
    out = {}
    for s in strikes:
        out[s] = {"ce_token": None, "pe_token": None}
        for tok, info in engine.token_to_info.items():
            if info.get("index") == "NIFTY" and info.get("strike") == s:
                if info.get("opt_type") == "CE":
                    out[s]["ce_token"] = tok
                elif info.get("opt_type") == "PE":
                    out[s]["pe_token"] = tok
    return out


def add_fut_to_engine_subscription(engine, fut_token):
    """Append NIFTY-FUT token to engine's WS subscription list. Idempotent."""
    if not fut_token:
        return False
    if not hasattr(engine, "_subscribe_tokens"):
        engine._subscribe_tokens = []
    if fut_token in engine._subscribe_tokens:
        return False
    engine._subscribe_tokens.append(fut_token)
    # Try to subscribe live if WS already connected
    try:
        ws = getattr(engine, "_ticker", None) or getattr(engine, "ticker", None)
        if ws:
            ws.subscribe([fut_token])
            ws.set_mode(ws.MODE_FULL, [fut_token])
            print(f"[TRINITY] Live-subscribed NIFTY-FUT token {fut_token}")
    except Exception as e:
        print(f"[TRINITY] FUT live-subscribe failed (will join on next reconnect): {e}")
    return True


def get_subscription_status(engine, fut_token, atm):
    """Snapshot of current subscription state for /status endpoint."""
    spot_subscribed = NIFTY_SPOT_TOKEN in (engine._subscribe_tokens or []) \
        if hasattr(engine, "_subscribe_tokens") else False
    fut_subscribed = bool(fut_token) and fut_token in (engine._subscribe_tokens or [])

    strike_map = resolve_strike_tokens(engine, atm)
    strike_status = {}
    for s, toks in strike_map.items():
        strike_status[s] = {
            "ce_token": toks["ce_token"],
            "pe_token": toks["pe_token"],
            "ce_subscribed": toks["ce_token"] in (engine._subscribe_tokens or []) if toks["ce_token"] else False,
            "pe_subscribed": toks["pe_token"] in (engine._subscribe_tokens or []) if toks["pe_token"] else False,
        }
    return {
        "spot_subscribed": spot_subscribed,
        "fut_token": fut_token,
        "fut_subscribed": fut_subscribed,
        "atm": atm,
        "strikes": strike_status,
        "total_subscribed": len(engine._subscribe_tokens) if hasattr(engine, "_subscribe_tokens") else 0,
        "ts": int(time.time() * 1000),
    }
