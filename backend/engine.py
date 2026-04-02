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
        self.option_symbols = {}   # {token: "NFO:SYMBOL"} for quote fetching

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

    def get_historical(self, symbol: str, interval: str = "5minute", days: int = 5) -> list:
        try:
            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)
            data = self.kite.historical_data(int(symbol), from_date, to_date, interval)
            return [{"date": str(d["date"]), "open": d["open"], "high": d["high"],
                     "low": d["low"], "close": d["close"], "volume": d["volume"]} for d in data]
        except Exception as e:
            print(f"[ENGINE] Historical fetch error: {e}")
            return []

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

                                # Store initial OI for unusual detection
                                self.prev_oi[tok] = q.get("oi", 0)
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
            today = datetime.now().date()
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

                self._check_unusual(token, tick, info)

        # Throttled push (every 1s)
        now = time.time()
        if now - self._last_push >= 1.0:
            self._last_push = now
            self._push_to_clients()

    def _check_unusual(self, token, tick, info):
        oi = tick.get("oi", 0)
        prev_oi = self.prev_oi.get(token, oi)
        oi_change = oi - prev_oi
        self.prev_oi[token] = oi

        if abs(oi_change) > 500000:
            now = datetime.now().strftime("%I:%M %p")
            instrument = f"{info['index']} {int(info['strike'])} {info['opt_type']}"
            oi_change_lakhs = round(oi_change / 100000, 1)
            alert_type = "BIG WRITING" if oi_change > 0 else "BIG UNWINDING"
            alert_level = "CRITICAL" if abs(oi_change) > 1000000 else "HIGH"

            signal = f"{'OI buildup' if oi_change > 0 else 'OI unwinding'} of {abs(oi_change_lakhs)}L contracts"
            if info["opt_type"] == "CE" and oi_change > 0:
                signal += f" — bearish, resistance cap at {int(info['strike'])}"
            elif info["opt_type"] == "PE" and oi_change > 0:
                signal += f" — bullish, support building at {int(info['strike'])}"

            alert = {
                "time": now, "instrument": instrument, "type": alert_type,
                "oiChange": f"{'+' if oi_change > 0 else ''}{oi_change_lakhs}L contracts",
                "premChange": f"{round(tick.get('last_price', 0) - tick.get('close', 0), 1)} pts" if tick.get('close') else "0 pts",
                "alert": alert_level, "signal": signal,
            }
            self.unusual_alerts.append(alert)
            print(f"[UNUSUAL] {alert_level}: {instrument} — {alert_type} — {oi_change_lakhs}L")

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
            delta = (expiry - datetime.now().date()).days
            return max(delta, 1)
        today = datetime.now()
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
            "ts": datetime.now().strftime("%H:%M:%S"),
        }

        with self._ws_lock:
            for ws in self._ws_clients[:]:
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_json(message), self.loop)
                except Exception:
                    self._ws_clients.remove(ws)
