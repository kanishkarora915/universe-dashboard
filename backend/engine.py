"""
UNIVERSE Market Engine — Standalone Kite Connect integration.
Handles: KiteTicker WebSocket, option chain, Greeks, PCR, Max Pain, unusual activity.
ALL DATA IS REAL — fetched via Kite REST API at startup + live ticks via WebSocket.
"""

import math
import time
import threading
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Callable

import numpy as np
import pytz

IST = pytz.timezone("Asia/Kolkata")

def ist_now():
    """Always return IST time regardless of server timezone."""
    return datetime.now(IST)
import pandas as pd
from scipy.stats import norm
from kiteconnect import KiteConnect, KiteTicker

# ── Constants ────────────────────────────────────────────────────────────

RISK_FREE_RATE = 0.07
TRADING_DAYS = 252

NIFTY_SPOT_SYMBOL = "NSE:NIFTY 50"
BANKNIFTY_SPOT_SYMBOL = "NSE:NIFTY BANK"
VIX_SYMBOL = "NSE:INDIA VIX"

INDEX_CONFIG = {
    "NIFTY": {
        "name": "NIFTY",
        "exchange": "NFO",
        "spot_symbol": NIFTY_SPOT_SYMBOL,
        "strike_gap": 50,
        "atm_range": 10,
    },
    "BANKNIFTY": {
        "name": "BANKNIFTY",
        "exchange": "NFO",
        "spot_symbol": BANKNIFTY_SPOT_SYMBOL,
        "strike_gap": 100,
        "atm_range": 10,
    },
}


# ── Black-Scholes Greeks ─────────────────────────────────────────────────

def _bs_d1(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def bs_greeks(S, K, T, r, sigma, opt_type):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "iv": 0}
    d1 = _bs_d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    sqrt_T = math.sqrt(T)
    n_d1 = norm.pdf(d1)
    N_d1 = norm.cdf(d1)
    N_d2 = norm.cdf(d2)
    gamma = n_d1 / (S * sigma * sqrt_T)
    vega = S * n_d1 * sqrt_T / 100
    if opt_type == "CE":
        delta = N_d1
        theta = (-(S * n_d1 * sigma) / (2 * sqrt_T) - r * K * math.exp(-r * T) * N_d2) / TRADING_DAYS
    else:
        delta = N_d1 - 1
        theta = (-(S * n_d1 * sigma) / (2 * sqrt_T) + r * K * math.exp(-r * T) * norm.cdf(-d2)) / TRADING_DAYS
    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 2),
        "vega": round(vega, 2),
        "iv": round(sigma * 100, 2),
    }


def implied_vol(price, S, K, T, r, opt_type):
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    sigma = 0.3
    for _ in range(50):
        d1 = _bs_d1(S, K, T, r, sigma)
        d2 = d1 - sigma * math.sqrt(T)
        if opt_type == "CE":
            theo = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        else:
            theo = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        diff = theo - price
        vega = S * norm.pdf(d1) * math.sqrt(T)
        if vega < 1e-10:
            break
        sigma -= diff / vega
        sigma = max(0.01, min(sigma, 5.0))
        if abs(diff) < 0.01:
            break
    return max(0.0, sigma)


# ── Computation helpers ──────────────────────────────────────────────────

def compute_pcr(chain_data):
    total_ce = sum(s.get("ce_oi", 0) for s in chain_data.values())
    total_pe = sum(s.get("pe_oi", 0) for s in chain_data.values())
    if total_ce == 0:
        return 0.0
    return round(total_pe / total_ce, 2)


def compute_max_pain(chain_data, spot):
    strikes = sorted(chain_data.keys())
    if not strikes:
        return spot
    min_loss = float("inf")
    max_pain_strike = strikes[len(strikes) // 2]
    for settle_strike in strikes:
        total_loss = 0
        for strike, data in chain_data.items():
            ce_oi = data.get("ce_oi", 0)
            pe_oi = data.get("pe_oi", 0)
            if settle_strike > strike:
                total_loss += (settle_strike - strike) * ce_oi
            if settle_strike < strike:
                total_loss += (strike - settle_strike) * pe_oi
        if total_loss < min_loss:
            min_loss = total_loss
            max_pain_strike = settle_strike
    return max_pain_strike


def find_big_walls(chain_data):
    max_ce_oi, max_pe_oi = 0, 0
    big_ce_strike, big_pe_strike = 0, 0
    for strike, data in chain_data.items():
        ce_oi = data.get("ce_oi", 0)
        pe_oi = data.get("pe_oi", 0)
        if ce_oi > max_ce_oi:
            max_ce_oi = ce_oi
            big_ce_strike = strike
        if pe_oi > max_pe_oi:
            max_pe_oi = pe_oi
            big_pe_strike = strike
    return big_ce_strike, big_pe_strike


def compute_ivr(current_iv):
    iv_low = 10.0
    iv_high = 35.0
    if iv_high == iv_low:
        return 50
    ivr = (current_iv - iv_low) / (iv_high - iv_low) * 100
    return int(max(0, min(100, ivr)))


def derive_trend(ltp, prev_close):
    if prev_close <= 0:
        return "SIDEWAYS"
    if ltp > prev_close * 1.002:
        return "BULLISH"
    elif ltp < prev_close * 0.998:
        return "BEARISH"
    return "SIDEWAYS"


def derive_regime(change_pct):
    if change_pct > 0.5:
        return "TRENDING UP"
    elif change_pct < -0.5:
        return "TRENDING DOWN"
    return "RANGE BOUND"


# ── Market Engine ────────────────────────────────────────────────────────

class MarketEngine:
    def __init__(self, api_key: str, access_token: str, loop: asyncio.AbstractEventLoop = None):
        self.api_key = api_key
        self.access_token = access_token
        self.loop = loop

        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)

        self.ticker: Optional[KiteTicker] = None
        self.running = False

        # ── Caches ──
        self.prices = {}
        self.chains = {"NIFTY": {}, "BANKNIFTY": {}}
        self.unusual_alerts = []

        # ── Token maps ──
        self.token_to_info = {}
        self.spot_tokens = {}
        self.spot_prev_close = {}
        self.prev_oi = {}
        self.initial_oi = {}  # Stores OI at market open — never overwritten
        self.initial_ltp = {}  # Stores LTP at market open — for seller classification
        self.option_symbols = {}   # {token: "NFO:SYMBOL"} for quote fetching

        # ── Hourly OI snapshots for Hidden Shift detection ──
        self.oi_snapshots = []     # List of {"time": datetime, "chains": {index: {strike: {ce_oi, pe_oi, ce_ltp, pe_ltp}}}, "prices": {index: ltp}}
        self._last_snapshot_time = 0

        # ── Expiry tracking ──
        self.nearest_expiry = {}   # {"NIFTY": date, "BANKNIFTY": date}

        # ── WebSocket clients ──
        self._ws_clients = []
        self._ws_lock = threading.Lock()
        self._last_push = 0

    # ── Public API ───────────────────────────────────────────────────────

    def start(self):
        print("[ENGINE] Starting market engine...")
        self._build_subscriptions()
        self._fetch_initial_data()      # <-- NEW: Full REST fetch before ticks
        self._connect_ticker()
        self.running = True
        print("[ENGINE] Market engine started with REAL data.")

    def stop(self):
        self.running = False
        if self.ticker:
            try:
                self.ticker.close()
            except Exception:
                pass
        print("[ENGINE] Market engine stopped.")

    def register_ws(self, ws):
        with self._ws_lock:
            self._ws_clients.append(ws)

    def unregister_ws(self, ws):
        with self._ws_lock:
            self._ws_clients = [w for w in self._ws_clients if w is not ws]

    def get_live_data(self) -> dict:
        """Returns REAL data matching mockLive shape."""
        result = {}
        for index in ["NIFTY", "BANKNIFTY"]:
            key = index.lower() if index == "NIFTY" else "banknifty"
            spot_token = self.spot_tokens.get(index)
            vix_token = self.spot_tokens.get("VIX")
            chain = self.chains.get(index, {})

            spot = self.prices.get(spot_token, {})
            vix_data = self.prices.get(vix_token, {})

            ltp = spot.get("ltp", 0)
            prev_close = self.spot_prev_close.get(index, 0)
            if prev_close <= 0:
                prev_close = ltp
            change = round(ltp - prev_close, 2)
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0

            total_ce_oi = sum(s.get("ce_oi", 0) for s in chain.values())
            total_pe_oi = sum(s.get("pe_oi", 0) for s in chain.values())
            pcr = compute_pcr(chain)
            max_pain = compute_max_pain(chain, ltp)
            big_ce, big_pe = find_big_walls(chain)
            vix = vix_data.get("ltp", 0)

            atm_iv = self._get_atm_iv(index, ltp)
            ivr = compute_ivr(atm_iv) if atm_iv > 0 else 0

            result[key] = {
                "ltp": ltp,
                "change": change,
                "changePct": change_pct,
                "high": spot.get("high", 0),
                "low": spot.get("low", 0),
                "pcr": pcr,
                "ivr": ivr,
                "totalCE_OI": total_ce_oi,
                "totalPE_OI": total_pe_oi,
                "maxPain": max_pain,
                "bigCallStrike": big_ce,
                "bigPutStrike": big_pe,
                "vix": round(vix, 2),
                "trend": derive_trend(ltp, prev_close),
                "regime": derive_regime(change_pct),
            }

        return result

    def get_option_chain(self, index: str) -> list:
        chain = self.chains.get(index.upper(), {})
        spot_token = self.spot_tokens.get(index.upper())
        spot_ltp = self.prices.get(spot_token, {}).get("ltp", 0)
        rows = []
        for strike, data in sorted(chain.items()):
            T = max(self._days_to_expiry(index.upper()) / 365, 1 / 365)
            ce_iv, pe_iv, ce_greeks, pe_greeks = 0, 0, {}, {}
            if data.get("ce_ltp", 0) > 0 and spot_ltp > 0:
                ce_iv = implied_vol(data["ce_ltp"], spot_ltp, strike, T, RISK_FREE_RATE, "CE")
                ce_greeks = bs_greeks(spot_ltp, strike, T, RISK_FREE_RATE, ce_iv, "CE")
            if data.get("pe_ltp", 0) > 0 and spot_ltp > 0:
                pe_iv = implied_vol(data["pe_ltp"], spot_ltp, strike, T, RISK_FREE_RATE, "PE")
                pe_greeks = bs_greeks(spot_ltp, strike, T, RISK_FREE_RATE, pe_iv, "PE")
            rows.append({
                "strike": strike,
                "ce_ltp": data.get("ce_ltp", 0), "ce_oi": data.get("ce_oi", 0),
                "ce_oi_change": data.get("ce_oi_change", 0), "ce_volume": data.get("ce_volume", 0),
                "ce_iv": round(ce_iv * 100, 2) if ce_iv else 0, "ce_greeks": ce_greeks,
                "pe_ltp": data.get("pe_ltp", 0), "pe_oi": data.get("pe_oi", 0),
                "pe_oi_change": data.get("pe_oi_change", 0), "pe_volume": data.get("pe_volume", 0),
                "pe_iv": round(pe_iv * 100, 2) if pe_iv else 0, "pe_greeks": pe_greeks,
            })
        return rows

    def get_unusual(self) -> list:
        return list(reversed(self.unusual_alerts[-50:]))

    def get_oi_change_summary(self) -> dict:
        """Aggregated OI change data for OI Change tab."""
        result = {}
        for index in ["NIFTY", "BANKNIFTY"]:
            chain = self.chains.get(index, {})
            spot_token = self.spot_tokens.get(index)
            ltp = self.prices.get(spot_token, {}).get("ltp", 0)
            cfg = INDEX_CONFIG[index]
            atm = round(ltp / cfg["strike_gap"]) * cfg["strike_gap"] if ltp > 0 else 0

            strikes_data = []
            total_ce_oi = 0
            total_pe_oi = 0
            total_ce_oi_change_pos = 0
            total_ce_oi_change_neg = 0
            total_pe_oi_change_pos = 0
            total_pe_oi_change_neg = 0

            for strike in sorted(chain.keys()):
                data = chain[strike]
                ce_oi = data.get("ce_oi", 0)
                pe_oi = data.get("pe_oi", 0)
                ce_ltp = data.get("ce_ltp", 0)
                pe_ltp = data.get("pe_ltp", 0)
                ce_vol = data.get("ce_volume", 0)
                pe_vol = data.get("pe_volume", 0)

                # OI change from initial fetch (stored in prev_oi at startup)
                ce_token = None
                pe_token = None
                for tok, info in self.token_to_info.items():
                    if info["index"] == index and info["strike"] == strike:
                        if info["opt_type"] == "CE":
                            ce_token = tok
                        else:
                            pe_token = tok

                ce_oi_initial = self.initial_oi.get(ce_token, ce_oi) if ce_token else ce_oi
                pe_oi_initial = self.initial_oi.get(pe_token, pe_oi) if pe_token else pe_oi
                ce_oi_change = ce_oi - ce_oi_initial
                pe_oi_change = pe_oi - pe_oi_initial

                total_ce_oi += ce_oi
                total_pe_oi += pe_oi
                if ce_oi_change > 0:
                    total_ce_oi_change_pos += ce_oi_change
                else:
                    total_ce_oi_change_neg += ce_oi_change
                if pe_oi_change > 0:
                    total_pe_oi_change_pos += pe_oi_change
                else:
                    total_pe_oi_change_neg += pe_oi_change

                strikes_data.append({
                    "strike": strike,
                    "isATM": strike == atm,
                    "ceOI": ce_oi,
                    "peOI": pe_oi,
                    "ceOIChange": ce_oi_change,
                    "peOIChange": pe_oi_change,
                    "ceLTP": ce_ltp,
                    "peLTP": pe_ltp,
                    "ceVol": ce_vol,
                    "peVol": pe_vol,
                })

            result[index.lower()] = {
                "strikes": strikes_data,
                "ltp": ltp,
                "atm": atm,
                "totalCEOI": total_ce_oi,
                "totalPEOI": total_pe_oi,
                "ceOIChangePos": total_ce_oi_change_pos,
                "ceOIChangeNeg": total_ce_oi_change_neg,
                "peOIChangePos": total_pe_oi_change_pos,
                "peOIChangeNeg": total_pe_oi_change_neg,
                "netOIChange": (total_ce_oi_change_pos + total_ce_oi_change_neg +
                                total_pe_oi_change_pos + total_pe_oi_change_neg),
                "pcr": round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0,
                "timestamp": ist_now().strftime("%I:%M:%S %p IST"),
            }

        return result

    # ── SELLER ACTIVITY SUMMARY ───────────────────────────────────────

    def get_seller_summary(self) -> dict:
        """Per-strike seller (writer) activity: Writing / Short Covering / Buying / Long Unwinding."""
        result = {}
        for index in ["NIFTY", "BANKNIFTY"]:
            chain = self.chains.get(index, {})
            spot_token = self.spot_tokens.get(index)
            ltp = self.prices.get(spot_token, {}).get("ltp", 0)
            cfg = INDEX_CONFIG[index]
            atm = round(ltp / cfg["strike_gap"]) * cfg["strike_gap"] if ltp > 0 else 0

            strikes_data = []
            # Seller aggregates
            total_ce_writing = 0
            total_pe_writing = 0
            total_ce_shortcover = 0
            total_pe_shortcover = 0
            # Buyer aggregates
            total_ce_buying = 0
            total_pe_buying = 0
            total_ce_longunwind = 0
            total_pe_longunwind = 0

            for strike in sorted(chain.keys()):
                data = chain[strike]
                ce_oi = data.get("ce_oi", 0)
                pe_oi = data.get("pe_oi", 0)
                ce_ltp = data.get("ce_ltp", 0)
                pe_ltp = data.get("pe_ltp", 0)

                # Find tokens
                ce_token = None
                pe_token = None
                for tok, info in self.token_to_info.items():
                    if info["index"] == index and info["strike"] == strike:
                        if info["opt_type"] == "CE":
                            ce_token = tok
                        else:
                            pe_token = tok

                # OI change from open
                ce_oi_initial = self.initial_oi.get(ce_token, ce_oi) if ce_token else ce_oi
                pe_oi_initial = self.initial_oi.get(pe_token, pe_oi) if pe_token else pe_oi
                ce_oi_change = ce_oi - ce_oi_initial
                pe_oi_change = pe_oi - pe_oi_initial

                # Premium change from open
                ce_ltp_initial = self.initial_ltp.get(ce_token, ce_ltp) if ce_token else ce_ltp
                pe_ltp_initial = self.initial_ltp.get(pe_token, pe_ltp) if pe_token else pe_ltp
                ce_prem_change = round(ce_ltp - ce_ltp_initial, 2)
                pe_prem_change = round(pe_ltp - pe_ltp_initial, 2)

                # Classify CE activity
                ce_activity = "NEUTRAL"
                if ce_oi_change > 0 and ce_prem_change <= 0:
                    ce_activity = "WRITING"
                    total_ce_writing += ce_oi_change
                elif ce_oi_change > 0 and ce_prem_change > 0:
                    ce_activity = "BUYING"
                    total_ce_buying += ce_oi_change
                elif ce_oi_change < 0 and ce_prem_change >= 0:
                    ce_activity = "SHORT_COVER"
                    total_ce_shortcover += abs(ce_oi_change)
                elif ce_oi_change < 0 and ce_prem_change < 0:
                    ce_activity = "LONG_UNWIND"
                    total_ce_longunwind += abs(ce_oi_change)

                # Classify PE activity
                pe_activity = "NEUTRAL"
                if pe_oi_change > 0 and pe_prem_change <= 0:
                    pe_activity = "WRITING"
                    total_pe_writing += pe_oi_change
                elif pe_oi_change > 0 and pe_prem_change > 0:
                    pe_activity = "BUYING"
                    total_pe_buying += pe_oi_change
                elif pe_oi_change < 0 and pe_prem_change >= 0:
                    pe_activity = "SHORT_COVER"
                    total_pe_shortcover += abs(pe_oi_change)
                elif pe_oi_change < 0 and pe_prem_change < 0:
                    pe_activity = "LONG_UNWIND"
                    total_pe_longunwind += abs(pe_oi_change)

                # Only include strikes with non-zero activity
                ce_oi_change_pct = round((ce_oi_change / ce_oi_initial) * 100, 1) if ce_oi_initial > 0 else 0
                pe_oi_change_pct = round((pe_oi_change / pe_oi_initial) * 100, 1) if pe_oi_initial > 0 else 0

                # Classify magnitude: MAJOR (>2L or >20%), MINOR (<2L), NEUTRAL
                ce_magnitude = "MAJOR" if (abs(ce_oi_change) > 200000 or abs(ce_oi_change_pct) > 20) else "MINOR" if ce_oi_change != 0 else "NEUTRAL"
                pe_magnitude = "MAJOR" if (abs(pe_oi_change) > 200000 or abs(pe_oi_change_pct) > 20) else "MINOR" if pe_oi_change != 0 else "NEUTRAL"

                if ce_oi_change != 0 or pe_oi_change != 0:
                    strikes_data.append({
                        "strike": strike,
                        "isATM": strike == atm,
                        "ceOI": ce_oi,
                        "peOI": pe_oi,
                        "ceOIInitial": ce_oi_initial,
                        "peOIInitial": pe_oi_initial,
                        "ceOIChange": ce_oi_change,
                        "peOIChange": pe_oi_change,
                        "ceOIChangePct": ce_oi_change_pct,
                        "peOIChangePct": pe_oi_change_pct,
                        "ceLTP": ce_ltp,
                        "peLTP": pe_ltp,
                        "cePremChange": ce_prem_change,
                        "pePremChange": pe_prem_change,
                        "ceActivity": ce_activity,
                        "peActivity": pe_activity,
                        "ceMagnitude": ce_magnitude,
                        "peMagnitude": pe_magnitude,
                    })

            # Seller bias
            net_seller_oi = total_ce_writing + total_pe_writing
            if total_ce_writing > total_pe_writing * 1.2:
                seller_bias = "BEARISH"
            elif total_pe_writing > total_ce_writing * 1.2:
                seller_bias = "BULLISH"
            else:
                seller_bias = "NEUTRAL"

            # +OI / -OI totals
            total_plus_oi = total_ce_writing + total_ce_buying + total_pe_writing + total_pe_buying
            total_minus_oi = total_ce_shortcover + total_ce_longunwind + total_pe_shortcover + total_pe_longunwind
            net_oi_change = total_plus_oi - total_minus_oi

            # Major changes (>2L OI change)
            major_changes = [s for s in strikes_data if s["ceMagnitude"] == "MAJOR" or s["peMagnitude"] == "MAJOR"]
            minor_changes = [s for s in strikes_data if s not in major_changes]

            # Detect shifts: OI leaving one strike and appearing at another
            ce_losing = sorted([s for s in strikes_data if s["ceOIChange"] < -100000], key=lambda x: x["ceOIChange"])
            ce_gaining = sorted([s for s in strikes_data if s["ceOIChange"] > 100000], key=lambda x: x["ceOIChange"], reverse=True)
            pe_losing = sorted([s for s in strikes_data if s["peOIChange"] < -100000], key=lambda x: x["peOIChange"])
            pe_gaining = sorted([s for s in strikes_data if s["peOIChange"] > 100000], key=lambda x: x["peOIChange"], reverse=True)

            shifts = []
            if ce_losing and ce_gaining:
                shifts.append({
                    "side": "CE",
                    "from": [{"strike": int(s["strike"]), "change": s["ceOIChange"]} for s in ce_losing[:3]],
                    "to": [{"strike": int(s["strike"]), "change": s["ceOIChange"]} for s in ce_gaining[:3]],
                    "meaning": "Resistance shifting " + ("UP" if ce_gaining[0]["strike"] > ce_losing[0]["strike"] else "DOWN"),
                })
            if pe_losing and pe_gaining:
                shifts.append({
                    "side": "PE",
                    "from": [{"strike": int(s["strike"]), "change": s["peOIChange"]} for s in pe_losing[:3]],
                    "to": [{"strike": int(s["strike"]), "change": s["peOIChange"]} for s in pe_gaining[:3]],
                    "meaning": "Support shifting " + ("UP" if pe_gaining[0]["strike"] > pe_losing[0]["strike"] else "DOWN"),
                })

            result[index.lower()] = {
                "strikes": strikes_data,
                "ltp": ltp,
                "atm": atm,
                # +/- OI totals
                "totalPlusOI": total_plus_oi,
                "totalMinusOI": total_minus_oi,
                "netOIChange": net_oi_change,
                # Seller metrics
                "ceWritingOI": total_ce_writing,
                "peWritingOI": total_pe_writing,
                "ceShortCoverOI": total_ce_shortcover,
                "peShortCoverOI": total_pe_shortcover,
                "netSellerOI": net_seller_oi,
                "sellerBias": seller_bias,
                # Buyer metrics
                "ceBuyingOI": total_ce_buying,
                "peBuyingOI": total_pe_buying,
                "ceLongUnwindOI": total_ce_longunwind,
                "peLongUnwindOI": total_pe_longunwind,
                # Major/Minor/Shifts
                "majorCount": len(major_changes),
                "minorCount": len(minor_changes),
                "shifts": shifts,
                "timestamp": ist_now().strftime("%I:%M:%S %p IST"),
            }

        return result

    # ── TRADE ANALYSIS (combines unusual + seller data) ───────────────

    def get_trade_analysis(self) -> dict:
        """Combines seller activity + unusual alerts to generate trade recommendations."""
        seller = self.get_seller_summary()
        unusual = self.get_unusual()
        result = {}

        for index in ["NIFTY", "BANKNIFTY"]:
            key = index.lower()
            s = seller.get(key, {})
            ltp = s.get("ltp", 0)
            atm = s.get("atm", 0)
            cfg = INDEX_CONFIG[index]

            ce_writing = s.get("ceWritingOI", 0)
            pe_writing = s.get("peWritingOI", 0)
            ce_sc = s.get("ceShortCoverOI", 0)
            pe_sc = s.get("peShortCoverOI", 0)
            ce_buying = s.get("ceBuyingOI", 0)
            pe_buying = s.get("peBuyingOI", 0)
            bias = s.get("sellerBias", "NEUTRAL")

            # Find max writing strikes (resistance/support)
            strikes = s.get("strikes", [])
            ce_writing_strikes = sorted(
                [st for st in strikes if st["ceActivity"] == "WRITING"],
                key=lambda x: x["ceOIChange"], reverse=True
            )[:3]
            pe_writing_strikes = sorted(
                [st for st in strikes if st["peActivity"] == "WRITING"],
                key=lambda x: x["peOIChange"], reverse=True
            )[:3]

            # Filter unusual alerts for this index
            idx_unusual = [u for u in unusual if index in u.get("instrument", "")]
            writing_alerts = [u for u in idx_unusual if u.get("type") in ["BIG WRITING"]]
            sc_alerts = [u for u in idx_unusual if u.get("type") in ["SHORT COVERING"]]

            # Build reasons
            reasons = []
            recommendations = []

            # Determine market structure
            if bias == "BEARISH":
                reasons.append(f"CE writers dominating: {ce_writing/100000:.1f}L vs PE writers: {pe_writing/100000:.1f}L")
                if ce_writing_strikes:
                    resistance = ce_writing_strikes[0]["strike"]
                    reasons.append(f"Heavy CE writing at {int(resistance)} = strong resistance")
                    recommendations.append({
                        "action": "SELL CE" if ltp < resistance else "BUY PE",
                        "strike": int(resistance),
                        "reason": f"Sellers capping upside at {int(resistance)}. CE writing OI: {ce_writing_strikes[0]['ceOIChange']/100000:.1f}L",
                        "confidence": "HIGH" if ce_writing > pe_writing * 2 else "MEDIUM",
                    })
                if pe_sc > 0:
                    reasons.append(f"PE short covering: {pe_sc/100000:.1f}L = support weakening")

            elif bias == "BULLISH":
                reasons.append(f"PE writers dominating: {pe_writing/100000:.1f}L vs CE writers: {ce_writing/100000:.1f}L")
                if pe_writing_strikes:
                    support = pe_writing_strikes[0]["strike"]
                    reasons.append(f"Heavy PE writing at {int(support)} = strong support")
                    recommendations.append({
                        "action": "BUY CE" if ltp > support else "SELL PE",
                        "strike": int(support),
                        "reason": f"Sellers defending support at {int(support)}. PE writing OI: {pe_writing_strikes[0]['peOIChange']/100000:.1f}L",
                        "confidence": "HIGH" if pe_writing > ce_writing * 2 else "MEDIUM",
                    })
                if ce_sc > 0:
                    reasons.append(f"CE short covering: {ce_sc/100000:.1f}L = resistance weakening")

            else:
                reasons.append(f"CE writing: {ce_writing/100000:.1f}L, PE writing: {pe_writing/100000:.1f}L — balanced")
                if ce_writing_strikes and pe_writing_strikes:
                    resistance = ce_writing_strikes[0]["strike"]
                    support = pe_writing_strikes[0]["strike"]
                    reasons.append(f"Range: {int(support)}-{int(resistance)}")
                    recommendations.append({
                        "action": "SELL STRADDLE/STRANGLE",
                        "strike": int(atm),
                        "reason": f"Range bound between {int(support)}-{int(resistance)}. Both CE & PE sellers active.",
                        "confidence": "MEDIUM",
                    })

            # Add unusual alert context
            for wa in writing_alerts[:2]:
                reasons.append(f"Unusual: {wa['type']} on {wa['instrument']} ({wa['oiChange']})")

            # Identify key levels
            key_levels = {}
            if ce_writing_strikes:
                key_levels["resistance"] = [int(st["strike"]) for st in ce_writing_strikes]
            if pe_writing_strikes:
                key_levels["support"] = [int(st["strike"]) for st in pe_writing_strikes]

            result[key] = {
                "ltp": ltp,
                "atm": int(atm),
                "sellerBias": bias,
                "reasons": reasons,
                "recommendations": recommendations,
                "keyLevels": key_levels,
                "sellerStats": {
                    "ceWriting": ce_writing,
                    "peWriting": pe_writing,
                    "ceShortCover": ce_sc,
                    "peShortCover": pe_sc,
                    "ceBuying": ce_buying,
                    "peBuying": pe_buying,
                },
                "recentAlerts": idx_unusual[:5],
                "timestamp": ist_now().strftime("%I:%M:%S %p IST"),
            }

        return result

    # ── HIDDEN SHIFT — Institutional OI Cooking Detection ──────────────

    def get_hidden_shift(self) -> dict:
        """Detect institutional OI manipulation patterns before price moves.
        Compares current OI vs ~1hr ago snapshot. Detects 4 patterns:
        1. Silent Accumulation: OI build >15% while price flat
        2. Covering Trap: OI falling but price NOT responding
        3. Strike Migration: OI shifting between strikes while price flat
        4. PCR Divergence: Price-PCR direction mismatch
        """
        result = {}

        # Find best reference snapshot (~30-60 min ago, or earliest available)
        ref_snapshot = None
        if self.oi_snapshots:
            now = ist_now()
            for snap in reversed(self.oi_snapshots[:-1] if len(self.oi_snapshots) > 1 else self.oi_snapshots):
                age_min = (now - snap["time"]).total_seconds() / 60
                if age_min >= 10:  # At least ~10 min old (was 25, too strict)
                    ref_snapshot = snap
                    break
            if not ref_snapshot:
                ref_snapshot = self.oi_snapshots[0]  # Use earliest

        for index in ["NIFTY", "BANKNIFTY"]:
            chain = self.chains.get(index, {})
            spot_token = self.spot_tokens.get(index)
            current_price = self.prices.get(spot_token, {}).get("ltp", 0)
            cfg = INDEX_CONFIG[index]
            atm = round(current_price / cfg["strike_gap"]) * cfg["strike_gap"] if current_price > 0 else 0

            # Reference data — use snapshot if available, else fall back to initial_oi/initial_ltp
            ref_price = 0
            ref_chains = {}
            snapshot_age_min = 0
            use_initial_fallback = False

            if ref_snapshot:
                ref_price = ref_snapshot["prices"].get(index, current_price)
                ref_chains = ref_snapshot["chains"].get(index, {})
                snapshot_age_min = round((ist_now() - ref_snapshot["time"]).total_seconds() / 60)
            elif self.initial_oi:
                # No snapshots yet — use initial_oi from market open as reference
                use_initial_fallback = True
                prev_close = self.spot_prev_close.get(index, current_price)
                ref_price = prev_close
                snapshot_age_min = -1  # Flag: using market open data

            price_move = abs(current_price - ref_price) if ref_price else 0
            price_direction = "UP" if current_price > ref_price else "DOWN" if current_price < ref_price else "FLAT"
            price_flat = price_move < 50  # <50 points = flat

            patterns = []
            silent_acc = []
            covering_trap = []
            strike_migration_ce = {"from": [], "to": []}
            strike_migration_pe = {"from": [], "to": []}

            # Current totals for PCR
            total_ce_oi = 0
            total_pe_oi = 0
            ref_total_ce_oi = 0
            ref_total_pe_oi = 0

            # Analyze each strike
            strike_analysis = []
            for strike in sorted(chain.keys()):
                data = chain[strike]
                ce_oi = data.get("ce_oi", 0)
                pe_oi = data.get("pe_oi", 0)
                ce_ltp = data.get("ce_ltp", 0)
                pe_ltp = data.get("pe_ltp", 0)

                if use_initial_fallback:
                    # Use initial_oi from market open
                    ce_token = None
                    pe_token = None
                    for tok, inf in self.token_to_info.items():
                        if inf["index"] == index and inf["strike"] == strike:
                            if inf["opt_type"] == "CE":
                                ce_token = tok
                            else:
                                pe_token = tok
                    ref_ce_oi = self.initial_oi.get(ce_token, ce_oi) if ce_token else ce_oi
                    ref_pe_oi = self.initial_oi.get(pe_token, pe_oi) if pe_token else pe_oi
                    ref_ce_ltp = self.initial_ltp.get(ce_token, ce_ltp) if ce_token else ce_ltp
                    ref_pe_ltp = self.initial_ltp.get(pe_token, pe_ltp) if pe_token else pe_ltp
                else:
                    ref_data = ref_chains.get(strike, {})
                    ref_ce_oi = ref_data.get("ce_oi", ce_oi)
                    ref_pe_oi = ref_data.get("pe_oi", pe_oi)
                    ref_ce_ltp = ref_data.get("ce_ltp", ce_ltp)
                    ref_pe_ltp = ref_data.get("pe_ltp", pe_ltp)

                ce_oi_change = ce_oi - ref_ce_oi
                pe_oi_change = pe_oi - ref_pe_oi
                ce_oi_pct = round((ce_oi_change / ref_ce_oi) * 100, 1) if ref_ce_oi > 0 else 0
                pe_oi_pct = round((pe_oi_change / ref_pe_oi) * 100, 1) if ref_pe_oi > 0 else 0
                ce_prem_change = round(ce_ltp - ref_ce_ltp, 2)
                pe_prem_change = round(pe_ltp - ref_pe_ltp, 2)

                total_ce_oi += ce_oi
                total_pe_oi += pe_oi
                ref_total_ce_oi += ref_ce_oi
                ref_total_pe_oi += ref_pe_oi

                # ── PATTERN 1: SILENT ACCUMULATION ──
                if price_flat:
                    if ce_oi_pct > 15 and abs(ce_oi_change) > 50000:
                        silent_acc.append({
                            "strike": int(strike),
                            "side": "CE",
                            "oiChange": ce_oi_change,
                            "oiPct": ce_oi_pct,
                            "premChange": ce_prem_change,
                            "signal": f"CE OI at {int(strike)} jumped {ce_oi_pct}% ({ce_oi_change/100000:.1f}L) while price moved only {price_move:.0f} pts",
                        })
                    if pe_oi_pct > 15 and abs(pe_oi_change) > 50000:
                        silent_acc.append({
                            "strike": int(strike),
                            "side": "PE",
                            "oiChange": pe_oi_change,
                            "oiPct": pe_oi_pct,
                            "premChange": pe_prem_change,
                            "signal": f"PE OI at {int(strike)} jumped {pe_oi_pct}% ({pe_oi_change/100000:.1f}L) while price moved only {price_move:.0f} pts",
                        })

                # ── PATTERN 2: COVERING TRAP ──
                # OI falling sharply but price not moving in expected direction
                if ce_oi_change < -50000 and ce_oi_pct < -10:
                    # CE OI falling = shorts covering CE = should be bullish (price up)
                    if price_direction != "UP":
                        covering_trap.append({
                            "strike": int(strike),
                            "side": "CE",
                            "oiChange": ce_oi_change,
                            "oiPct": ce_oi_pct,
                            "expected": "UP (CE covering = bullish)",
                            "actual": price_direction,
                            "signal": f"CE shorts covering at {int(strike)} ({ce_oi_pct}%) but price going {price_direction} — TRAP",
                        })
                if pe_oi_change < -50000 and pe_oi_pct < -10:
                    # PE OI falling = shorts covering PE = should be bearish (price down)
                    if price_direction != "DOWN":
                        covering_trap.append({
                            "strike": int(strike),
                            "side": "PE",
                            "oiChange": pe_oi_change,
                            "oiPct": pe_oi_pct,
                            "expected": "DOWN (PE covering = bearish)",
                            "actual": price_direction,
                            "signal": f"PE shorts covering at {int(strike)} ({pe_oi_pct}%) but price going {price_direction} — TRAP",
                        })

                # ── PATTERN 3: STRIKE MIGRATION ──
                if price_flat:
                    if ce_oi_change < -50000 and ce_oi_pct < -10:
                        strike_migration_ce["from"].append({"strike": int(strike), "change": ce_oi_change, "pct": ce_oi_pct})
                    if ce_oi_change > 50000 and ce_oi_pct > 10:
                        strike_migration_ce["to"].append({"strike": int(strike), "change": ce_oi_change, "pct": ce_oi_pct})
                    if pe_oi_change < -50000 and pe_oi_pct < -10:
                        strike_migration_pe["from"].append({"strike": int(strike), "change": pe_oi_change, "pct": pe_oi_pct})
                    if pe_oi_change > 50000 and pe_oi_pct > 10:
                        strike_migration_pe["to"].append({"strike": int(strike), "change": pe_oi_change, "pct": pe_oi_pct})

                strike_analysis.append({
                    "strike": int(strike),
                    "isATM": strike == atm,
                    "ceOI": ce_oi,
                    "peOI": pe_oi,
                    "ceOIChange": ce_oi_change,
                    "peOIChange": pe_oi_change,
                    "ceOIPct": ce_oi_pct,
                    "peOIPct": pe_oi_pct,
                    "cePremChange": ce_prem_change,
                    "pePremChange": pe_prem_change,
                })

            # Build Pattern 1
            if silent_acc:
                # Sort by absolute OI change
                silent_acc.sort(key=lambda x: abs(x["oiChange"]), reverse=True)
                top = silent_acc[0]
                direction = "BUY PE" if top["side"] == "CE" else "BUY CE"
                patterns.append({
                    "id": 1,
                    "name": "SILENT ACCUMULATION",
                    "emoji": "🔇",
                    "detected": True,
                    "severity": "HIGH" if len(silent_acc) >= 3 or abs(top["oiChange"]) > 200000 else "MEDIUM",
                    "details": silent_acc[:5],
                    "direction": direction,
                    "targetStrike": top["strike"],
                    "insight": f"Institutions silently building {top['side']} positions at {top['strike']} — {top['oiPct']}% OI jump, price barely moved. Expect sharp move soon.",
                })

            # Build Pattern 2
            if covering_trap:
                covering_trap.sort(key=lambda x: abs(x["oiChange"]), reverse=True)
                top = covering_trap[0]
                # If CE covering but price not going up → price will eventually go up = BUY CE
                direction = "BUY CE" if top["side"] == "CE" else "BUY PE"
                patterns.append({
                    "id": 2,
                    "name": "COVERING TRAP",
                    "emoji": "🪤",
                    "detected": True,
                    "severity": "HIGH" if abs(top["oiChange"]) > 200000 else "MEDIUM",
                    "details": covering_trap[:5],
                    "direction": direction,
                    "targetStrike": top["strike"],
                    "insight": f"{top['side']} shorts covering at {top['strike']} but price going {top['actual']} instead of expected. Delayed move incoming — {direction}.",
                })

            # Build Pattern 3
            ce_migration = bool(strike_migration_ce["from"] and strike_migration_ce["to"])
            pe_migration = bool(strike_migration_pe["from"] and strike_migration_pe["to"])
            if ce_migration or pe_migration:
                details = []
                target = atm
                direction = "NEUTRAL"
                insight_parts = []
                if ce_migration:
                    from_strikes = [s["strike"] for s in strike_migration_ce["from"]]
                    to_strikes = [s["strike"] for s in strike_migration_ce["to"]]
                    avg_from = sum(from_strikes) / len(from_strikes)
                    avg_to = sum(to_strikes) / len(to_strikes)
                    if avg_to > avg_from:
                        direction = "BUY CE"
                        insight_parts.append(f"CE OI shifting UP ({from_strikes} → {to_strikes}) = resistance moving higher")
                    else:
                        direction = "BUY PE"
                        insight_parts.append(f"CE OI shifting DOWN ({from_strikes} → {to_strikes}) = resistance tightening")
                    target = to_strikes[0] if to_strikes else atm
                    details.append({"side": "CE", "from": from_strikes, "to": to_strikes})
                if pe_migration:
                    from_strikes = [s["strike"] for s in strike_migration_pe["from"]]
                    to_strikes = [s["strike"] for s in strike_migration_pe["to"]]
                    avg_from = sum(from_strikes) / len(from_strikes)
                    avg_to = sum(to_strikes) / len(to_strikes)
                    if avg_to < avg_from:
                        direction = "BUY PE" if direction != "BUY CE" else direction
                        insight_parts.append(f"PE OI shifting DOWN ({from_strikes} → {to_strikes}) = support dropping")
                    else:
                        direction = "BUY CE" if direction != "BUY PE" else direction
                        insight_parts.append(f"PE OI shifting UP ({from_strikes} → {to_strikes}) = support strengthening")
                    target = to_strikes[0] if to_strikes else target
                    details.append({"side": "PE", "from": from_strikes, "to": to_strikes})
                patterns.append({
                    "id": 3,
                    "name": "STRIKE MIGRATION",
                    "emoji": "🔀",
                    "detected": True,
                    "severity": "HIGH",
                    "details": details,
                    "direction": direction,
                    "targetStrike": int(target),
                    "insight": " | ".join(insight_parts) + f". Price flat at {current_price:.0f} — institutions repositioning.",
                })

            # ── PATTERN 4: PCR DIVERGENCE ──
            current_pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0
            ref_pcr = round(ref_total_pe_oi / ref_total_ce_oi, 2) if ref_total_ce_oi > 0 else 0
            pcr_change = round(current_pcr - ref_pcr, 3)

            pcr_divergence = False
            pcr_insight = ""
            pcr_direction = "NEUTRAL"
            if price_direction == "DOWN" and pcr_change < 0:
                # Price falling + PCR falling = institutions NOT scared = bounce
                pcr_divergence = True
                pcr_direction = "BUY CE"
                pcr_insight = f"Price down {price_move:.0f} pts but PCR also FALLING ({ref_pcr} → {current_pcr}). Institutions NOT adding puts = NOT scared. BOUNCE likely."
            elif price_direction == "UP" and pcr_change > 0:
                # Price rising + PCR rising = smart money hedging = fake move
                pcr_divergence = True
                pcr_direction = "BUY PE"
                pcr_insight = f"Price up {price_move:.0f} pts but PCR also RISING ({ref_pcr} → {current_pcr}). Smart money hedging with puts = move may be FAKE. Reversal likely."

            if pcr_divergence:
                patterns.append({
                    "id": 4,
                    "name": "PCR DIVERGENCE",
                    "emoji": "⚡",
                    "detected": True,
                    "severity": "HIGH" if abs(pcr_change) > 0.05 else "MEDIUM",
                    "details": [{"currentPCR": current_pcr, "refPCR": ref_pcr, "pcrChange": pcr_change, "priceDirection": price_direction, "priceMove": round(price_move, 1)}],
                    "direction": pcr_direction,
                    "targetStrike": int(atm),
                    "insight": pcr_insight,
                })

            # Overall verdict
            if patterns:
                ce_signals = sum(1 for p in patterns if "CE" in p["direction"])
                pe_signals = sum(1 for p in patterns if "PE" in p["direction"])
                if ce_signals > pe_signals:
                    overall = "BUY CE"
                elif pe_signals > ce_signals:
                    overall = "BUY PE"
                else:
                    overall = patterns[0]["direction"]
                confidence = "HIGH" if len(patterns) >= 3 else "MEDIUM" if len(patterns) >= 2 else "LOW"
                institution_doing = []
                for p in patterns:
                    if p["id"] == 1: institution_doing.append("silently accumulating positions")
                    elif p["id"] == 2: institution_doing.append("running a covering trap")
                    elif p["id"] == 3: institution_doing.append("migrating strikes to reposition")
                    elif p["id"] == 4: institution_doing.append("diverging from retail sentiment")
                verdict = f"Institutions are likely {', '.join(institution_doing)}. {overall} recommended."
            else:
                overall = "NO CLEAR SIGNAL"
                confidence = "LOW"
                verdict = "No institutional manipulation patterns detected right now. OI changes are organic."

            result[index.lower()] = {
                "ltp": current_price,
                "atm": int(atm),
                "refPrice": round(ref_price, 1),
                "priceMove": round(price_move, 1),
                "priceDirection": price_direction,
                "snapshotAge": snapshot_age_min,
                "patternsDetected": len(patterns),
                "patterns": patterns,
                "overallSignal": overall,
                "confidence": confidence,
                "verdict": verdict,
                "currentPCR": current_pcr if total_ce_oi > 0 else 0,
                "refPCR": ref_pcr if ref_total_ce_oi > 0 else 0,
                "strikes": strike_analysis,
                "timestamp": ist_now().strftime("%I:%M:%S %p IST"),
            }

        return result

    # ── SIGNAL SCORING ENGINE (9-point) ────────────────────────────────

    def get_signals(self) -> list:
        """Auto-generate trading signals with 9-point scoring for NIFTY & BANKNIFTY."""
        signals = []
        signal_id = 1

        for index in ["NIFTY", "BANKNIFTY"]:
            try:
                spot_token = self.spot_tokens.get(index)
                if not spot_token:
                    continue

                spot = self.prices.get(spot_token, {})
                ltp = spot.get("ltp", 0)
                if ltp <= 0:
                    continue

                prev_close = self.spot_prev_close.get(index, ltp)
                chain = self.chains.get(index, {})
                cfg = INDEX_CONFIG[index]
                change_pct = round((ltp - prev_close) / prev_close * 100, 2) if prev_close else 0

                # ── Fetch technicals (reuse intraday compute) ──
                try:
                    candles = self.kite.historical_data(
                        spot_token, ist_now() - timedelta(days=2), ist_now(), "5minute"
                    )
                except Exception as e:
                    print(f"[SIGNALS] Historical data fetch failed for {index}: {e}")
                    candles = []

                has_technicals = candles and len(candles) >= 30
                if has_technicals:
                    closes = [c["close"] for c in candles]
                    highs = [c["high"] for c in candles]
                    lows = [c["low"] for c in candles]
                    ema20 = self._ema(closes, 20)
                    ema50 = self._ema(closes, 50) if len(closes) >= 50 else ema20
                    rsi = self._compute_rsi(closes, 14)
                    macd_line, signal_line, histogram = self._compute_macd(closes)
                    supertrend, st_dir = self._compute_supertrend(highs, lows, closes, 10, 3)
                else:
                    # Fallback: use price action only
                    ema20 = prev_close
                    ema50 = prev_close
                    rsi = 50 + (change_pct * 5)  # Rough RSI estimate from price change
                    rsi = max(20, min(80, rsi))
                    histogram = change_pct
                    supertrend = prev_close
                    st_dir = -1 if change_pct < -0.3 else (1 if change_pct > 0.3 else 0)

                pcr = compute_pcr(chain)
                max_pain = compute_max_pain(chain, ltp)
                big_ce, big_pe = find_big_walls(chain)
                big_ce_oi = chain.get(big_ce, {}).get("ce_oi", 0)
                big_pe_oi = chain.get(big_pe, {}).get("pe_oi", 0)
                total_ce_oi = sum(s.get("ce_oi", 0) for s in chain.values())
                total_pe_oi = sum(s.get("pe_oi", 0) for s in chain.values())
                vix = self.prices.get(self.spot_tokens.get("VIX"), {}).get("ltp", 0)
                atm_iv = self._get_atm_iv(index, ltp)
                ivr = compute_ivr(atm_iv) if atm_iv > 0 else 50

                # ── Determine direction ──
                bearish_count = 0
                bullish_count = 0
                if ltp < ema20:
                    bearish_count += 1
                else:
                    bullish_count += 1
                if rsi < 45:
                    bearish_count += 1
                elif rsi > 55:
                    bullish_count += 1
                if histogram < 0:
                    bearish_count += 1
                else:
                    bullish_count += 1
                if pcr < 0.85:
                    bearish_count += 1
                elif pcr > 1.15:
                    bullish_count += 1

                is_bearish = bearish_count > bullish_count
                direction = "BEARISH" if is_bearish else "BULLISH"
                signal_type = "BUY PUT" if is_bearish else "BUY CALL"

                # ── Score 9 conditions ──
                reasoning = []
                score = 0

                # 1. EMA 20+50 confluence (1 pt)
                if is_bearish:
                    passed = ltp < ema20 and ltp < ema50
                    reasoning.append({
                        "pass": True if passed else ("warn" if ltp < ema20 else False),
                        "text": f"LTP {ltp:.0f} {'below' if ltp < ema20 else 'above'} EMA20 ({ema20:.0f}) and {'below' if ltp < ema50 else 'above'} EMA50 ({ema50:.0f})"
                    })
                else:
                    passed = ltp > ema20 and ltp > ema50
                    reasoning.append({
                        "pass": True if passed else ("warn" if ltp > ema20 else False),
                        "text": f"LTP {ltp:.0f} {'above' if ltp > ema20 else 'below'} EMA20 ({ema20:.0f}) and {'above' if ltp > ema50 else 'below'} EMA50 ({ema50:.0f})"
                    })
                if passed:
                    score += 1

                # 2. RSI momentum (1 pt)
                if is_bearish:
                    passed = rsi < 45
                    reasoning.append({
                        "pass": True if rsi < 40 else ("warn" if rsi < 50 else False),
                        "text": f"RSI at {rsi:.1f} — {'bearish momentum confirmed' if rsi < 40 else 'neutral zone' if rsi < 55 else 'bullish divergence risk'}"
                    })
                else:
                    passed = rsi > 55
                    reasoning.append({
                        "pass": True if rsi > 60 else ("warn" if rsi > 50 else False),
                        "text": f"RSI at {rsi:.1f} — {'bullish momentum confirmed' if rsi > 60 else 'neutral zone' if rsi > 45 else 'bearish divergence risk'}"
                    })
                if passed:
                    score += 1

                # 3. MACD histogram (1 pt)
                if is_bearish:
                    passed = histogram < 0
                    reasoning.append({
                        "pass": True if histogram < -1 else ("warn" if histogram < 0 else False),
                        "text": f"MACD histogram {histogram:.2f} — {'bearish confirmed' if histogram < 0 else 'bullish, against bias'}"
                    })
                else:
                    passed = histogram > 0
                    reasoning.append({
                        "pass": True if histogram > 1 else ("warn" if histogram > 0 else False),
                        "text": f"MACD histogram {histogram:.2f} — {'bullish confirmed' if histogram > 0 else 'bearish, against bias'}"
                    })
                if passed:
                    score += 1

                # 4. Supertrend / Price structure (1 pt)
                if is_bearish:
                    passed = st_dir < 0
                    reasoning.append({
                        "pass": True if passed else "warn",
                        "text": f"Supertrend {supertrend:.0f} {'SELL signal — bearish structure' if st_dir < 0 else 'BUY signal — conflicting'}"
                    })
                else:
                    passed = st_dir > 0
                    reasoning.append({
                        "pass": True if passed else "warn",
                        "text": f"Supertrend {supertrend:.0f} {'BUY signal — bullish structure' if st_dir > 0 else 'SELL signal — conflicting'}"
                    })
                if passed:
                    score += 1

                # 5. OI buildup at key strikes (1 pt)
                if is_bearish:
                    passed = big_ce_oi > 500000  # 5L+ OI at resistance
                    reasoning.append({
                        "pass": True if passed else "warn",
                        "text": f"Big CE OI wall {int(big_ce)} — {big_ce_oi/100000:.1f}L contracts — {'strong resistance cap' if passed else 'moderate resistance'}"
                    })
                else:
                    passed = big_pe_oi > 500000  # 5L+ OI at support
                    reasoning.append({
                        "pass": True if passed else "warn",
                        "text": f"Big PE OI wall {int(big_pe)} — {big_pe_oi/100000:.1f}L contracts — {'strong support zone' if passed else 'moderate support'}"
                    })
                if passed:
                    score += 1

                # 6. PCR extreme (1 pt)
                if is_bearish:
                    passed = pcr < 0.80
                    reasoning.append({
                        "pass": True if pcr < 0.75 else ("warn" if pcr < 0.90 else False),
                        "text": f"PCR {pcr} — {'bearish extreme, CE writers dominating' if pcr < 0.75 else 'mild bearish tilt' if pcr < 0.90 else 'neutral/bullish zone'}"
                    })
                else:
                    passed = pcr > 1.15
                    reasoning.append({
                        "pass": True if pcr > 1.25 else ("warn" if pcr > 1.0 else False),
                        "text": f"PCR {pcr} — {'bullish extreme, PE writers dominating' if pcr > 1.25 else 'mild bullish tilt' if pcr > 1.0 else 'neutral/bearish zone'}"
                    })
                if passed:
                    score += 1

                # 7. Big CE/PE writing at key strike (1 pt)
                if is_bearish:
                    nearest_ce_above = 0
                    for strike in sorted(chain.keys()):
                        if strike > ltp:
                            ce_oi = chain[strike].get("ce_oi", 0)
                            if ce_oi > 300000:  # 3L+
                                nearest_ce_above = strike
                                break
                    passed = nearest_ce_above > 0 and (nearest_ce_above - ltp) < cfg["strike_gap"] * 3
                    reasoning.append({
                        "pass": True if passed else "warn",
                        "text": f"CE writing at {int(nearest_ce_above) if nearest_ce_above else 'N/A'} — {'close overhead cap, bearish' if passed else 'no immediate cap'}"
                    })
                else:
                    nearest_pe_below = 0
                    for strike in sorted(chain.keys(), reverse=True):
                        if strike < ltp:
                            pe_oi = chain[strike].get("pe_oi", 0)
                            if pe_oi > 300000:  # 3L+
                                nearest_pe_below = strike
                                break
                    passed = nearest_pe_below > 0 and (ltp - nearest_pe_below) < cfg["strike_gap"] * 3
                    reasoning.append({
                        "pass": True if passed else "warn",
                        "text": f"PE writing at {int(nearest_pe_below) if nearest_pe_below else 'N/A'} — {'close floor support, bullish' if passed else 'no immediate support'}"
                    })
                if passed:
                    score += 1

                # 8. IVR safe zone 20-60 (1 pt)
                passed = 20 <= ivr <= 60
                reasoning.append({
                    "pass": True if passed else ("warn" if ivr < 75 else False),
                    "text": f"IVR {ivr}% — {'safe zone for option buying' if passed else 'too low, avoid' if ivr < 20 else 'expensive, premium crush risk'}"
                })
                if passed:
                    score += 1

                # 9. VIX / Market structure (1 pt)
                passed = vix < 20
                reasoning.append({
                    "pass": True if vix < 16 else ("warn" if vix < 22 else False),
                    "text": f"VIX {vix:.2f} — {'normal range, safe' if vix < 16 else 'elevated but manageable' if vix < 20 else 'HIGH — be cautious'}"
                })
                if passed:
                    score += 1

                # ── Skip if score too low ──
                if score < 3:
                    continue

                # ── Compute strike, entry, targets, SL ──
                atm = round(ltp / cfg["strike_gap"]) * cfg["strike_gap"]
                if is_bearish:
                    strike = atm  # ATM PE
                    opt_type_label = "PE"
                    strike_key = atm
                    prem = chain.get(strike_key, {}).get("pe_ltp", 0)
                else:
                    strike = atm  # ATM CE
                    opt_type_label = "CE"
                    strike_key = atm
                    prem = chain.get(strike_key, {}).get("ce_ltp", 0)

                if prem <= 0:
                    prem = 150  # default if no premium data

                entry_low = round(prem * 0.95)
                entry_high = round(prem * 1.05)
                sl = round(prem * 0.60)  # 40% SL
                t1 = round(prem * 1.30)
                t2 = round(prem * 1.65)
                rr = round((t1 - prem) / (prem - sl), 1) if (prem - sl) > 0 else 0

                # Expiry
                expiry_date = self.nearest_expiry.get(index)
                expiry_str = expiry_date.strftime("%d %b") if expiry_date else "This Week"

                # Status
                status = "ACTIVE"
                if score < 5:
                    status = "WATCHLIST"

                now = ist_now()

                signals.append({
                    "id": signal_id,
                    "time": now.strftime("%I:%M %p"),
                    "instrument": index,
                    "type": signal_type,
                    "strike": f"{int(strike)} {opt_type_label}",
                    "expiry": expiry_str,
                    "entry": f"{entry_low}\u2013{entry_high}",
                    "t1": str(t1),
                    "t2": str(t2),
                    "sl": str(sl),
                    "score": score,
                    "maxScore": 9,
                    "rr": f"1:{rr}",
                    "status": status,
                    "reasoning": reasoning,
                })
                signal_id += 1

            except Exception as e:
                print(f"[SIGNALS] Error computing signal for {index}: {e}")

        return signals

    def get_historical(self, symbol: str, interval: str = "5minute", days: int = 5) -> list:
        try:
            to_date = ist_now()
            from_date = to_date - timedelta(days=days)
            data = self.kite.historical_data(int(symbol), from_date, to_date, interval)
            return [{"date": str(d["date"]), "open": d["open"], "high": d["high"],
                     "low": d["low"], "close": d["close"], "volume": d["volume"]} for d in data]
        except Exception as e:
            print(f"[ENGINE] Historical fetch error: {e}")
            return []

    # ── INTRADAY — Real technicals from historical candles ──────────────

    def get_intraday(self) -> dict:
        """Compute REAL technical indicators from historical candle data."""
        result = {}
        for index in ["NIFTY", "BANKNIFTY"]:
            spot_token = self.spot_tokens.get(index)
            if not spot_token:
                continue
            try:
                # Fetch 5-min candles for today + yesterday
                candles = self.kite.historical_data(
                    spot_token, ist_now() - timedelta(days=2), ist_now(), "5minute"
                )
                if not candles or len(candles) < 10:
                    result[index] = self._empty_technicals(index)
                    continue

                closes = [c["close"] for c in candles]
                highs = [c["high"] for c in candles]
                lows = [c["low"] for c in candles]
                volumes = [c["volume"] for c in candles]

                # VWAP (today's candles only)
                today_candles = [c for c in candles if c["date"].date() == ist_now().date()]
                if today_candles:
                    cum_vol = 0
                    cum_tp_vol = 0
                    for c in today_candles:
                        tp = (c["high"] + c["low"] + c["close"]) / 3
                        cum_tp_vol += tp * c["volume"]
                        cum_vol += c["volume"]
                    vwap = round(cum_tp_vol / cum_vol, 2) if cum_vol else closes[-1]
                else:
                    vwap = closes[-1]

                # RSI 14
                rsi = self._compute_rsi(closes, 14)

                # MACD (12,26,9)
                macd_line, signal_line, histogram = self._compute_macd(closes)

                # Supertrend (10, 3)
                supertrend, st_direction = self._compute_supertrend(highs, lows, closes, 10, 3)

                # EMA 9, 20, 50
                ema9 = self._ema(closes, 9)
                ema20 = self._ema(closes, 20)
                ema50 = self._ema(closes, 50) if len(closes) >= 50 else 0

                # Bollinger Bands
                bb_mid = ema20
                if len(closes) >= 20:
                    std20 = float(np.std(closes[-20:]))
                    bb_upper = round(bb_mid + 2 * std20, 2)
                    bb_lower = round(bb_mid - 2 * std20, 2)
                else:
                    bb_upper = bb_mid
                    bb_lower = bb_mid

                # Pivot Points from yesterday's data
                yesterday_candles = [c for c in candles if c["date"].date() < ist_now().date()]
                if yesterday_candles:
                    yh = max(c["high"] for c in yesterday_candles)
                    yl = min(c["low"] for c in yesterday_candles)
                    yc = yesterday_candles[-1]["close"]
                    pivot = round((yh + yl + yc) / 3, 2)
                    r1 = round(2 * pivot - yl, 2)
                    r2 = round(pivot + (yh - yl), 2)
                    r3 = round(yh + 2 * (pivot - yl), 2)
                    s1 = round(2 * pivot - yh, 2)
                    s2 = round(pivot - (yh - yl), 2)
                    s3 = round(yl - 2 * (yh - pivot), 2)
                else:
                    pivot = closes[-1]
                    r1 = r2 = r3 = s1 = s2 = s3 = 0

                rsi_label = "Oversold" if rsi < 30 else "Overbought" if rsi > 70 else "Weak" if rsi < 45 else "Strong" if rsi > 55 else "Neutral"
                macd_label = "Bullish Cross" if histogram > 0 else "Bearish Cross"
                st_label = f"{round(supertrend)} {'↑ BUY' if st_direction > 0 else '↓ SELL'}"

                result[index] = {
                    "vwap": vwap,
                    "rsi": round(rsi, 1),
                    "rsiLabel": rsi_label,
                    "macd": round(macd_line, 2),
                    "macdSignal": round(signal_line, 2),
                    "macdHist": round(histogram, 2),
                    "macdLabel": macd_label,
                    "supertrend": round(supertrend, 2),
                    "supertrendLabel": st_label,
                    "ema9": round(ema9, 2),
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "bbUpper": bb_upper,
                    "bbLower": bb_lower,
                    "pivot": pivot,
                    "r1": r1, "r2": r2, "r3": r3,
                    "s1": s1, "s2": s2, "s3": s3,
                }
            except Exception as e:
                print(f"[ENGINE] Intraday compute error for {index}: {e}")
                result[index] = self._empty_technicals(index)

        return result

    def _empty_technicals(self, index):
        return {"vwap": 0, "rsi": 0, "rsiLabel": "N/A", "macd": 0, "macdSignal": 0,
                "macdHist": 0, "macdLabel": "N/A", "supertrend": 0, "supertrendLabel": "N/A",
                "ema9": 0, "ema20": 0, "ema50": 0, "bbUpper": 0, "bbLower": 0,
                "pivot": 0, "r1": 0, "r2": 0, "r3": 0, "s1": 0, "s2": 0, "s3": 0}

    def _compute_rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _compute_macd(self, closes, fast=12, slow=26, signal=9):
        if len(closes) < slow + signal:
            return 0, 0, 0
        ema_fast = self._ema(closes, fast)
        ema_slow = self._ema(closes, slow)
        macd_val = ema_fast - ema_slow
        # Simplified signal line
        macd_series = []
        fast_ema_series = self._ema_series(closes, fast)
        slow_ema_series = self._ema_series(closes, slow)
        for i in range(len(slow_ema_series)):
            macd_series.append(fast_ema_series[i + (len(fast_ema_series) - len(slow_ema_series))] - slow_ema_series[i])
        if len(macd_series) >= signal:
            signal_val = sum(macd_series[-signal:]) / signal
        else:
            signal_val = macd_val
        return macd_val, signal_val, macd_val - signal_val

    def _compute_supertrend(self, highs, lows, closes, period=10, multiplier=3):
        if len(closes) < period:
            return closes[-1] if closes else 0, 1
        atr_vals = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            atr_vals.append(tr)
        if len(atr_vals) < period:
            return closes[-1], 1
        atr = sum(atr_vals[-period:]) / period
        hl2 = (highs[-1] + lows[-1]) / 2
        upper = hl2 + multiplier * atr
        lower = hl2 - multiplier * atr
        direction = 1 if closes[-1] > lower else -1
        st = lower if direction > 0 else upper
        return st, direction

    def _ema(self, data, period):
        if len(data) < period:
            return data[-1] if data else 0
        k = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = val * k + ema * (1 - k)
        return ema

    def _ema_series(self, data, period):
        if len(data) < period:
            return data[:]
        k = 2 / (period + 1)
        result = []
        ema = sum(data[:period]) / period
        result.append(ema)
        for val in data[period:]:
            ema = val * k + ema * (1 - k)
            result.append(ema)
        return result

    # ── NEXT DAY — Real levels from option chain ─────────────────────────

    def get_nextday(self) -> dict:
        """Compute real next-day levels from option chain + technicals."""
        live = self.get_live_data()
        result = {"date": f"Tomorrow — {(ist_now() + timedelta(days=1)).strftime('%d %b %Y')}",
                  "generatedAt": ist_now().strftime("%I:%M %p IST")}

        for index in ["NIFTY", "BANKNIFTY"]:
            key = "nifty" if index == "NIFTY" else "banknifty"
            ld = live.get(key, {})
            chain = self.chains.get(index, {})
            ltp = ld.get("ltp", 0)
            pcr = ld.get("pcr", 0)
            big_ce = ld.get("bigCallStrike", 0)
            big_pe = ld.get("bigPutStrike", 0)
            max_pain = ld.get("maxPain", 0)
            vix = ld.get("vix", 0)
            cfg = INDEX_CONFIG[index]

            # Compute real pivot from today's OHLC
            spot_token = self.spot_tokens.get(index)
            spot = self.prices.get(spot_token, {})
            h = spot.get("high", ltp)
            l = spot.get("low", ltp)
            c = ltp
            pivot = round((h + l + c) / 3, 2)
            r1 = round(2 * pivot - l, 2)
            r2 = round(pivot + (h - l), 2)
            r3 = round(h + 2 * (pivot - l), 2)
            s1 = round(2 * pivot - h, 2)
            s2 = round(pivot - (h - l), 2)
            s3 = round(l - 2 * (h - pivot), 2)

            # Bias from PCR + price action
            if pcr < 0.75:
                bias = "BEARISH"
            elif pcr > 1.2:
                bias = "BULLISH"
            else:
                change_pct = ld.get("changePct", 0)
                bias = "BULLISH" if change_pct > 0.3 else "BEARISH" if change_pct < -0.3 else "NEUTRAL"

            # Big OI walls for context
            big_ce_oi = 0
            big_pe_oi = 0
            for strike, data in chain.items():
                if strike == big_ce:
                    big_ce_oi = data.get("ce_oi", 0)
                if strike == big_pe:
                    big_pe_oi = data.get("pe_oi", 0)

            # Range estimate from ATR-like calc
            day_range = h - l if h > l else cfg["strike_gap"] * 2
            range_high = round(ltp + day_range * 0.6, 0)
            range_low = round(ltp - day_range * 0.6, 0)

            # Opening bias
            if ld.get("changePct", 0) > 0.5:
                opening = "Gap up likely — bullish momentum from today's close"
            elif ld.get("changePct", 0) < -0.5:
                opening = "Gap down likely — selling pressure continued from today"
            else:
                opening = "Flat open expected — consolidation zone"

            # Strategy
            if bias == "BEARISH":
                strategy = f"Buy PE on pullback to {round(pivot)}–{round(r1)} range. Avoid CE buying until {int(big_ce)} reclaimed."
            elif bias == "BULLISH":
                strategy = f"Buy CE on dips to {round(s1)}–{round(pivot)} range. Avoid PE unless {int(big_pe)} breaks."
            else:
                strategy = f"Wait for directional breakout. Range-bound between {round(s1)} and {round(r1)}."

            result[key] = {
                "bias": bias, "pivot": pivot, "maxPain": max_pain,
                "rangeHigh": range_high, "rangeLow": range_low,
                "resistance": [
                    {"level": r1, "reason": f"R1 Pivot — first resistance from today's range"},
                    {"level": r2, "reason": f"R2 Pivot — extended resistance zone"},
                    {"level": int(big_ce), "reason": f"Big CE Wall — {round(big_ce_oi / 100000, 1)}L OI resistance cap"},
                ],
                "support": [
                    {"level": s1, "reason": f"S1 Pivot — first support from today's range"},
                    {"level": s2, "reason": f"S2 Pivot — deeper support zone"},
                    {"level": int(big_pe), "reason": f"Big PE Wall — {round(big_pe_oi / 100000, 1)}L OI support zone"},
                ],
                "bigCEWall": f"{int(big_ce)} CE — {round(big_ce_oi / 100000, 1)}L OI — resistance cap",
                "bigPEWall": f"{int(big_pe)} PE — {round(big_pe_oi / 100000, 1)}L OI — support zone",
                "unusual": f"Max Pain at {int(max_pain)} — watch for pin towards this level",
                "opening": opening,
                "strategy": strategy,
                "plan": [
                    f"9:15–9:30 AM → Watch opening direction, don't trade first 5 candles",
                    f"9:30–10:30 AM → If {index} {'breaks below ' + str(round(s1)) if bias == 'BEARISH' else 'holds above ' + str(round(s1))}, take directional trade",
                    f"10:30 AM–2:00 PM → Trail stop to entry after T1 hit, respect VWAP",
                    f"2:00–2:30 PM → VIX {'above 18 = caution' if vix > 15 else 'stable = safe to hold'}, last entry window",
                ],
            }

        return result

    # ── WEEKLY — Real analysis from option chain ─────────────────────────

    def get_weekly(self) -> dict:
        """Compute real weekly outlook from current option chain data."""
        live = self.get_live_data()
        nifty = live.get("nifty", {})
        bn = live.get("banknifty", {})

        nifty_ltp = nifty.get("ltp", 0)
        bn_ltp = bn.get("ltp", 0)
        vix = nifty.get("vix", 0)

        # Weekly ranges (estimate from current day range * 5)
        n_spot = self.prices.get(self.spot_tokens.get("NIFTY"), {})
        b_spot = self.prices.get(self.spot_tokens.get("BANKNIFTY"), {})
        n_day_range = (n_spot.get("high", nifty_ltp) - n_spot.get("low", nifty_ltp)) or 200
        b_day_range = (b_spot.get("high", bn_ltp) - b_spot.get("low", bn_ltp)) or 500

        # Bias from PCR
        n_bias = "BEARISH" if nifty.get("pcr", 1) < 0.8 else "BULLISH" if nifty.get("pcr", 1) > 1.2 else "SIDEWAYS"
        b_bias = "BEARISH" if bn.get("pcr", 1) < 0.8 else "BULLISH" if bn.get("pcr", 1) > 1.2 else "SIDEWAYS"

        # OI analysis from chains
        oi_analysis = []
        for idx in ["NIFTY", "BANKNIFTY"]:
            chain = self.chains.get(idx, {})
            big_ce, big_pe = find_big_walls(chain)
            pcr = compute_pcr(chain)
            total_ce = sum(s.get("ce_oi", 0) for s in chain.values())
            total_pe = sum(s.get("pe_oi", 0) for s in chain.values())
            max_pain = compute_max_pain(chain, self.prices.get(self.spot_tokens.get(idx), {}).get("ltp", 0))

            big_ce_oi = chain.get(big_ce, {}).get("ce_oi", 0)
            big_pe_oi = chain.get(big_pe, {}).get("pe_oi", 0)

            oi_analysis.append(f"{idx}: {int(big_pe)} PE highest OI ({round(big_pe_oi/100000,1)}L) — key support")
            oi_analysis.append(f"{idx}: {int(big_ce)} CE highest OI ({round(big_ce_oi/100000,1)}L) — resistance cap")

        oi_analysis.append(f"NIFTY PCR: {nifty.get('pcr', 0)} — {'bearish tilt' if nifty.get('pcr', 0) < 0.85 else 'bullish tilt' if nifty.get('pcr', 0) > 1.15 else 'neutral zone'}")
        oi_analysis.append(f"VIX: {vix} — {'HIGH caution' if vix > 18 else 'normal range, safe for buying' if vix < 16 else 'elevated, be careful'}")

        today = ist_now()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=4)

        # Make-or-Break levels from big PE walls
        n_mob = nifty.get("bigPutStrike", nifty_ltp - 200)
        b_mob = bn.get("bigPutStrike", bn_ltp - 500)

        return {
            "week": f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}",
            "niftyBias": n_bias, "bnBias": b_bias,
            "niftyRange": {"high": round(nifty_ltp + n_day_range * 2), "low": round(nifty_ltp - n_day_range * 2)},
            "bnRange": {"high": round(bn_ltp + b_day_range * 2), "low": round(bn_ltp - b_day_range * 2)},
            "oiAnalysis": oi_analysis,
            "fii": "Check NSE FII/DII data for latest flow",
            "dii": "Check NSE FII/DII data for latest flow",
            "verdict": f"PCR-based: {'Bears dominating' if nifty.get('pcr', 1) < 0.8 else 'Bulls in control' if nifty.get('pcr', 1) > 1.2 else 'Tug of war — wait for direction'}",
            "macro": [
                f"VIX at {vix} — {'HIGH volatility week expected' if vix > 18 else 'normal volatility'}",
                f"Nifty PCR {nifty.get('pcr', 0)} | BankNifty PCR {bn.get('pcr', 0)}",
                "Check economic calendar for RBI/Fed/NFP events this week",
            ],
            "plan": [
                {"day": "Monday", "col": "#0A84FF", "text": "Wait and watch — observe open + first 30 min before entry"},
                {"day": "Tuesday", "col": "#0A84FF", "text": "Core trade window — look for clean signal with strong OI confirmation"},
                {"day": "Wednesday", "col": "#30D158", "text": "Best momentum day — add to winning positions if trend clear"},
                {"day": "Thursday", "col": "#FF453A", "text": "⚠️ Theta decay aggressive — NO option buying after 2 PM"},
                {"day": "Friday", "col": "#FF9F0A", "text": "🚫 No new positions — weekend risk, exit all by 1 PM"},
            ],
            "niftyMoB": n_mob, "bnMoB": b_mob,
        }

    # ── INITIAL DATA FETCH (REST API) ────────────────────────────────────

    def _fetch_initial_data(self):
        """Fetch ALL data via REST API before ticks start flowing.
        This ensures dashboard shows real data immediately."""
        print("[ENGINE] Fetching initial data via REST API...")

        try:
            # 1. Fetch spot quotes (LTP, High, Low, Close, OHLC)
            spot_symbols = [NIFTY_SPOT_SYMBOL, BANKNIFTY_SPOT_SYMBOL, VIX_SYMBOL]
            spot_quotes = self.kite.quote(spot_symbols)

            for sym, q in spot_quotes.items():
                # Find token for this symbol
                for idx, tok in self.spot_tokens.items():
                    cfg_sym = INDEX_CONFIG.get(idx, {}).get("spot_symbol", "")
                    if cfg_sym == sym or (idx == "VIX" and "VIX" in sym):
                        self.prices[tok] = {
                            "ltp": q.get("last_price", 0),
                            "high": q.get("ohlc", {}).get("high", 0),
                            "low": q.get("ohlc", {}).get("low", 0),
                            "close": q.get("ohlc", {}).get("close", 0),
                            "oi": q.get("oi", 0),
                            "volume": q.get("volume", 0),
                            "buy_qty": q.get("total_buy_quantity", 0),
                            "sell_qty": q.get("total_sell_quantity", 0),
                        }
                        break

            # Map VIX token
            vix_tok = self.spot_tokens.get("VIX")
            if vix_tok and VIX_SYMBOL in spot_quotes:
                q = spot_quotes[VIX_SYMBOL]
                self.prices[vix_tok] = {
                    "ltp": q.get("last_price", 0),
                    "high": q.get("ohlc", {}).get("high", 0),
                    "low": q.get("ohlc", {}).get("low", 0),
                    "close": q.get("ohlc", {}).get("close", 0),
                    "oi": 0, "volume": 0, "buy_qty": 0, "sell_qty": 0,
                }

            # Store prev close
            for idx in ["NIFTY", "BANKNIFTY"]:
                sym = INDEX_CONFIG[idx]["spot_symbol"]
                if sym in spot_quotes:
                    self.spot_prev_close[idx] = spot_quotes[sym].get("ohlc", {}).get("close", 0)

            print(f"[ENGINE] Spot data loaded: NIFTY={self.prices.get(self.spot_tokens.get('NIFTY'), {}).get('ltp', 0)}, "
                  f"BN={self.prices.get(self.spot_tokens.get('BANKNIFTY'), {}).get('ltp', 0)}, "
                  f"VIX={self.prices.get(self.spot_tokens.get('VIX'), {}).get('ltp', 0)}")

            # 2. Fetch option chain quotes in batches (Kite max 500 per call)
            all_option_symbols = list(self.option_symbols.values())
            print(f"[ENGINE] Fetching quotes for {len(all_option_symbols)} option strikes...")

            for i in range(0, len(all_option_symbols), 200):
                batch = all_option_symbols[i:i + 200]
                try:
                    quotes = self.kite.quote(batch)
                    for sym, q in quotes.items():
                        # Find token for this symbol
                        for tok, s in self.option_symbols.items():
                            if s == sym:
                                info = self.token_to_info.get(tok, {})
                                if not info:
                                    continue

                                index = info["index"]
                                strike = info["strike"]
                                opt_type = info["opt_type"].lower()

                                if strike not in self.chains[index]:
                                    self.chains[index][strike] = {}

                                chain_entry = self.chains[index][strike]
                                chain_entry[f"{opt_type}_ltp"] = q.get("last_price", 0)
                                chain_entry[f"{opt_type}_oi"] = q.get("oi", 0)
                                chain_entry[f"{opt_type}_volume"] = q.get("volume", 0)
                                chain_entry[f"{opt_type}_oi_change"] = q.get("oi", 0)  # Will compute delta on ticks

                                # Store in prices cache too
                                self.prices[tok] = {
                                    "ltp": q.get("last_price", 0),
                                    "high": q.get("ohlc", {}).get("high", 0),
                                    "low": q.get("ohlc", {}).get("low", 0),
                                    "close": q.get("ohlc", {}).get("close", 0),
                                    "oi": q.get("oi", 0),
                                    "volume": q.get("volume", 0),
                                    "buy_qty": q.get("total_buy_quantity", 0),
                                    "sell_qty": q.get("total_sell_quantity", 0),
                                }

                                # Store initial OI and LTP for unusual detection + seller classification
                                self.prev_oi[tok] = q.get("oi", 0)
                                self.initial_oi[tok] = q.get("oi", 0)
                                self.initial_ltp[tok] = q.get("last_price", 0)
                                break

                    time.sleep(0.4)  # Rate limit
                except Exception as e:
                    print(f"[ENGINE] Batch quote error: {e}")

            # Log chain summary
            for idx in ["NIFTY", "BANKNIFTY"]:
                chain = self.chains[idx]
                total_ce = sum(s.get("ce_oi", 0) for s in chain.values())
                total_pe = sum(s.get("pe_oi", 0) for s in chain.values())
                pcr = round(total_pe / total_ce, 2) if total_ce else 0
                print(f"[ENGINE] {idx} chain: {len(chain)} strikes, CE_OI={total_ce}, PE_OI={total_pe}, PCR={pcr}")

        except Exception as e:
            print(f"[ENGINE] Initial data fetch error: {e}")

        print("[ENGINE] Initial data fetch complete.")
        self._take_oi_snapshot()  # First snapshot at market open

    def _take_oi_snapshot(self):
        """Capture current OI state for Hidden Shift pattern detection."""
        import copy
        snapshot = {
            "time": ist_now(),
            "chains": {},
            "prices": {},
        }
        for index in ["NIFTY", "BANKNIFTY"]:
            spot_token = self.spot_tokens.get(index)
            snapshot["prices"][index] = self.prices.get(spot_token, {}).get("ltp", 0)
            chain = self.chains.get(index, {})
            snapshot["chains"][index] = {}
            for strike, data in chain.items():
                snapshot["chains"][index][strike] = {
                    "ce_oi": data.get("ce_oi", 0),
                    "pe_oi": data.get("pe_oi", 0),
                    "ce_ltp": data.get("ce_ltp", 0),
                    "pe_ltp": data.get("pe_ltp", 0),
                }
        self.oi_snapshots.append(snapshot)
        # Keep max 12 snapshots (6 hours at 30min intervals)
        if len(self.oi_snapshots) > 12:
            self.oi_snapshots = self.oi_snapshots[-12:]
        self._last_snapshot_time = time.time()
        print(f"[ENGINE] OI snapshot taken. Total snapshots: {len(self.oi_snapshots)}")

    # ── Build subscriptions ──────────────────────────────────────────────

    def _build_subscriptions(self):
        print("[ENGINE] Fetching instruments...")
        nse_instruments = self.kite.instruments("NSE")
        nfo_instruments = self.kite.instruments("NFO")

        for inst in nse_instruments:
            ts = inst["tradingsymbol"]
            if ts == "NIFTY 50":
                self.spot_tokens["NIFTY"] = inst["instrument_token"]
            elif ts == "NIFTY BANK":
                self.spot_tokens["BANKNIFTY"] = inst["instrument_token"]
            elif ts == "INDIA VIX":
                self.spot_tokens["VIX"] = inst["instrument_token"]

        # Get spot prices for ATM calculation
        spot_data = self.kite.ltp([NIFTY_SPOT_SYMBOL, BANKNIFTY_SPOT_SYMBOL])
        nifty_spot = spot_data.get(NIFTY_SPOT_SYMBOL, {}).get("last_price", 23000)
        bn_spot = spot_data.get(BANKNIFTY_SPOT_SYMBOL, {}).get("last_price", 49000)

        spots = {"NIFTY": nifty_spot, "BANKNIFTY": bn_spot}
        subscribe_tokens = list(self.spot_tokens.values())

        for index, cfg in INDEX_CONFIG.items():
            spot = spots[index]
            atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"]
            strike_range = cfg["atm_range"]

            opts = [i for i in nfo_instruments
                    if i["name"] == cfg["name"]
                    and i["instrument_type"] in ("CE", "PE")]

            if not opts:
                print(f"[ENGINE] No options found for {index}")
                continue

            expiries = sorted(set(i["expiry"] for i in opts))
            today = ist_now().date()
            future_expiries = [e for e in expiries if e >= today]
            if not future_expiries:
                print(f"[ENGINE] No future expiries for {index}")
                continue
            nearest_expiry = future_expiries[0]
            self.nearest_expiry[index] = nearest_expiry
            print(f"[ENGINE] {index}: ATM={atm}, Expiry={nearest_expiry}")

            for i in opts:
                if i["expiry"] != nearest_expiry:
                    continue
                strike = i["strike"]
                if abs(strike - atm) > strike_range * cfg["strike_gap"]:
                    continue

                token = i["instrument_token"]
                opt_type = i["instrument_type"]

                self.token_to_info[token] = {
                    "index": index, "strike": strike,
                    "opt_type": opt_type, "symbol": i["tradingsymbol"],
                    "expiry": str(nearest_expiry),
                }
                self.option_symbols[token] = f"NFO:{i['tradingsymbol']}"
                subscribe_tokens.append(token)

                if strike not in self.chains[index]:
                    self.chains[index][strike] = {}

        self._subscribe_tokens = subscribe_tokens
        print(f"[ENGINE] Subscription: {len(subscribe_tokens)} tokens "
              f"(spots: {len(self.spot_tokens)}, options: {len(self.token_to_info)})")

    def _connect_ticker(self):
        self.ticker = KiteTicker(self.api_key, self.access_token)

        def on_ticks(ws, ticks):
            self._process_ticks(ticks)

        def on_connect(ws, response):
            print(f"[TICKER] Connected. Subscribing {len(self._subscribe_tokens)} tokens...")
            ws.subscribe(self._subscribe_tokens)
            ws.set_mode(ws.MODE_FULL, self._subscribe_tokens)

        def on_close(ws, code, reason):
            print(f"[TICKER] Closed: {code} — {reason}")

        def on_error(ws, code, reason):
            print(f"[TICKER] Error: {code} — {reason}")

        def on_reconnect(ws, attempts_count):
            print(f"[TICKER] Reconnecting... attempt {attempts_count}")

        self.ticker.on_ticks = on_ticks
        self.ticker.on_connect = on_connect
        self.ticker.on_close = on_close
        self.ticker.on_error = on_error
        self.ticker.on_reconnect = on_reconnect
        self.ticker.connect(threaded=True)

    def _process_ticks(self, ticks):
        for tick in ticks:
            token = tick.get("instrument_token")
            if not token:
                continue

            # Update price cache — handle both full and compact tick modes
            ohlc = tick.get("ohlc", {})
            self.prices[token] = {
                "ltp": tick.get("last_price", 0),
                "high": ohlc.get("high", tick.get("high", 0)),
                "low": ohlc.get("low", tick.get("low", 0)),
                "close": ohlc.get("close", tick.get("close", 0)),
                "oi": tick.get("oi", 0),
                "volume": tick.get("volume_traded", tick.get("volume", 0)),
                "buy_qty": tick.get("total_buy_quantity", 0),
                "sell_qty": tick.get("total_sell_quantity", 0),
            }

            # If option token, update chain
            info = self.token_to_info.get(token)
            if info:
                index = info["index"]
                strike = info["strike"]
                opt_type = info["opt_type"].lower()

                if strike not in self.chains[index]:
                    self.chains[index][strike] = {}

                chain_entry = self.chains[index][strike]
                chain_entry[f"{opt_type}_ltp"] = tick.get("last_price", 0)
                chain_entry[f"{opt_type}_oi"] = tick.get("oi", 0)
                chain_entry[f"{opt_type}_volume"] = tick.get("volume_traded", tick.get("volume", 0))

                # Backfill initial_oi/initial_ltp from first tick if not set during initial fetch
                if token not in self.initial_oi:
                    self.initial_oi[token] = tick.get("oi", 0)
                    self.initial_ltp[token] = tick.get("last_price", 0)
                    self.prev_oi[token] = tick.get("oi", 0)

                self._check_unusual(token, tick, info)

        # Throttled push (every 1s)
        now = time.time()
        if now - self._last_push >= 1.0:
            self._last_push = now
            self._push_to_clients()

        # OI snapshot every 30 minutes for Hidden Shift detection
        if now - self._last_snapshot_time >= 1800:
            self._take_oi_snapshot()

    def _check_unusual(self, token, tick, info):
        oi = tick.get("oi", 0)
        ltp = tick.get("last_price", 0)
        prev_close = tick.get("close", 0) or tick.get("ohlc", {}).get("close", 0)
        volume = tick.get("volume_traded", tick.get("volume", 0))

        # Use cumulative OI change from market open (initial_oi), not tick-to-tick
        open_oi = self.initial_oi.get(token, oi)
        open_ltp = self.initial_ltp.get(token, ltp)
        oi_change = oi - open_oi
        prem_change = round(ltp - open_ltp, 2) if open_ltp else 0

        # Track prev values for tick-level detection
        prev_oi_val = self.prev_oi.get(token, oi)
        tick_oi_change = oi - prev_oi_val
        self.prev_oi[token] = oi
        self.prev_oi[f"{token}_ltp"] = ltp

        # Alert on cumulative OI change > 1L from open, but only when tick moves OI
        # (to avoid re-alerting the same cumulative every tick)
        already_alerted_key = f"{token}_alerted_level"
        last_alerted_level = self.prev_oi.get(already_alerted_key, 0)
        abs_oi_change = abs(oi_change)

        # Alert at 1L, 2L, 5L milestones (avoid spam)
        alert_milestone = 0
        if abs_oi_change > 500000 and last_alerted_level < 500000:
            alert_milestone = 500000
        elif abs_oi_change > 200000 and last_alerted_level < 200000:
            alert_milestone = 200000
        elif abs_oi_change > 100000 and last_alerted_level < 100000:
            alert_milestone = 100000

        if alert_milestone > 0 and tick_oi_change != 0:
            self.prev_oi[already_alerted_key] = abs_oi_change
            now_ist = ist_now()
            now = now_ist.strftime("%I:%M %p IST")
            instrument = f"{info['index']} {int(info['strike'])} {info['opt_type']}"
            oi_change_lakhs = round(oi_change / 100000, 1)

            # Determine type from OI + premium direction
            if oi_change > 0 and prem_change <= 0:
                alert_type = "BIG WRITING"  # OI up + premium down = writing
            elif oi_change > 0 and prem_change > 0:
                alert_type = "BIG BUYING"   # OI up + premium up = fresh buying
            elif oi_change < 0 and prem_change >= 0:
                alert_type = "SHORT COVERING"  # OI down + premium up
            else:
                alert_type = "LONG UNWINDING"  # OI down + premium down

            alert_level = "CRITICAL" if abs(oi_change) > 500000 else "HIGH" if abs(oi_change) > 200000 else "MEDIUM"

            # Detailed signal
            total_oi_lakhs = round(oi / 100000, 1)
            signal = f"{alert_type}: {abs(oi_change_lakhs)}L contracts"
            if info["opt_type"] == "CE":
                if oi_change > 0 and prem_change <= 0:
                    signal = f"CE writing at {int(info['strike'])} ({abs(oi_change_lakhs)}L) - bearish, resistance cap. Total OI: {total_oi_lakhs}L"
                elif oi_change > 0 and prem_change > 0:
                    signal = f"Fresh CE buying at {int(info['strike'])} ({abs(oi_change_lakhs)}L) - bullish bet. Premium +{prem_change} pts"
                else:
                    signal = f"CE unwinding at {int(info['strike'])} ({abs(oi_change_lakhs)}L) - resistance weakening"
            else:
                if oi_change > 0 and prem_change <= 0:
                    signal = f"PE writing at {int(info['strike'])} ({abs(oi_change_lakhs)}L) - bullish, support building. Total OI: {total_oi_lakhs}L"
                elif oi_change > 0 and prem_change > 0:
                    signal = f"Fresh PE buying at {int(info['strike'])} ({abs(oi_change_lakhs)}L) - bearish directional bet. Premium +{prem_change} pts"
                else:
                    signal = f"PE unwinding at {int(info['strike'])} ({abs(oi_change_lakhs)}L) - support weakening"

            prem_str = f"{'+' if prem_change > 0 else ''}{prem_change} pts" if prem_change != 0 else f"LTP: {ltp}"

            alert = {
                "time": now,
                "instrument": instrument,
                "type": alert_type,
                "oiChange": f"{'+' if oi_change > 0 else ''}{oi_change_lakhs}L contracts (Total: {total_oi_lakhs}L)",
                "premChange": prem_str,
                "alert": alert_level,
                "signal": signal,
            }
            self.unusual_alerts.append(alert)
            print(f"[UNUSUAL] {now} {alert_level}: {instrument} - {alert_type} - {oi_change_lakhs}L OI, prem {prem_str}")

    def _get_atm_iv(self, index, spot):
        chain = self.chains.get(index, {})
        if not chain or spot <= 0:
            return 0
        cfg = INDEX_CONFIG[index]
        atm = round(spot / cfg["strike_gap"]) * cfg["strike_gap"]
        atm_data = chain.get(atm, {})
        T = max(self._days_to_expiry(index) / 365, 1 / 365)
        ce_ltp = atm_data.get("ce_ltp", 0)
        pe_ltp = atm_data.get("pe_ltp", 0)
        avg_prem = (ce_ltp + pe_ltp) / 2 if ce_ltp and pe_ltp else ce_ltp or pe_ltp
        if avg_prem > 0:
            iv = implied_vol(avg_prem, spot, atm, T, RISK_FREE_RATE, "CE")
            return iv * 100
        return 0

    def _days_to_expiry(self, index="NIFTY"):
        expiry = self.nearest_expiry.get(index)
        if expiry:
            delta = (expiry - ist_now().date()).days
            return max(delta, 1)
        today = ist_now()
        days_ahead = 3 - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return max(days_ahead, 1)

    def _push_to_clients(self):
        if not self._ws_clients or not self.loop:
            return

        live_data = self.get_live_data()
        unusual = self.get_unusual()

        message = {
            "channel": "live",
            "data": live_data,
            "unusual": unusual,
            "ts": ist_now().strftime("%H:%M:%S"),
        }

        with self._ws_lock:
            for ws in self._ws_clients[:]:
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_json(message), self.loop)
                except Exception:
                    self._ws_clients.remove(ws)
