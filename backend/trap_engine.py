"""
Trap Fingerprint Engine — Detects institutional hidden positioning in far OTM strikes.
Identifies unusual OI + Volume divergence WITHOUT corresponding spot price movement.

TrapScore (0-10):
  OI_change_pct > 15% in one snapshot     → +3 points
  Volume > 2x average_daily_volume        → +3 points
  IV_change < 5% (IV flat = stealth buy)  → +2 points
  Spot moved < 0.3% in same window        → +2 points

  TrapScore >= 6 = FINGERPRINT DETECTED
  TrapScore 4-5  = WATCH ZONE
  TrapScore < 4  = Normal
"""

import sqlite3
import time
import threading
from datetime import datetime, timedelta, date as date_type
import pytz

IST = pytz.timezone("Asia/Kolkata")

def ist_now():
    return datetime.now(IST)

DB_PATH = None  # Set by init

# ── SQLite Setup ────────────────────────────────────────────────────────

def init_db(db_path):
    global DB_PATH
    DB_PATH = db_path
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trap_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strike REAL NOT NULL,
            option_type TEXT NOT NULL,
            expiry TEXT NOT NULL,
            oi INTEGER DEFAULT 0,
            oi_change INTEGER DEFAULT 0,
            oi_change_pct REAL DEFAULT 0,
            volume INTEGER DEFAULT 0,
            avg_volume INTEGER DEFAULT 0,
            volume_ratio REAL DEFAULT 0,
            iv REAL DEFAULT 0,
            iv_change REAL DEFAULT 0,
            ltp REAL DEFAULT 0,
            spot_price REAL DEFAULT 0,
            spot_change_pct REAL DEFAULT 0,
            trap_score INTEGER DEFAULT 0,
            is_cluster INTEGER DEFAULT 0,
            alert_level TEXT DEFAULT 'NORMAL'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON trap_snapshots(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON trap_snapshots(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON trap_snapshots(trap_score)")
    conn.commit()
    conn.close()
    # Purge old data (>30 days)
    _purge_old(30)
    print(f"[TRAP] Database initialized at {db_path}")


def _purge_old(days=30):
    cutoff = (ist_now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM trap_snapshots WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()


def _get_conn():
    return sqlite3.connect(DB_PATH)


# ── INDEX CONFIG (matches engine.py) ────────────────────────────────────

INDEX_CONFIG = {
    "NIFTY": {"name": "NIFTY", "strike_gap": 50, "spot_symbol": "NSE:NIFTY 50"},
    "BANKNIFTY": {"name": "BANKNIFTY", "strike_gap": 100, "spot_symbol": "NSE:NIFTY BANK"},
}


# ── TRAP SCANNER ────────────────────────────────────────────────────────

class TrapScanner:
    def __init__(self, kite, nfo_instruments):
        self.kite = kite
        self.nfo_instruments = nfo_instruments
        self.prev_snapshots = {}  # {(symbol, strike, opt_type, expiry): {oi, volume, iv, ltp}}
        self._scan_thread = None
        self._running = False

    def start_auto_scan(self, interval_sec=300):
        """Start background scanning every 5 minutes."""
        self._running = True
        self._scan_thread = threading.Thread(target=self._auto_scan_loop, args=(interval_sec,), daemon=True)
        self._scan_thread.start()
        print(f"[TRAP] Auto-scan started (every {interval_sec}s)")

    def stop(self):
        self._running = False

    def _auto_scan_loop(self, interval_sec):
        while self._running:
            now = ist_now()
            h, m = now.hour, now.minute
            # Only scan during market hours: 9:10 AM - 3:25 PM IST
            if (h == 9 and m >= 10) or (10 <= h <= 14) or (h == 15 and m <= 25):
                try:
                    self.run_scan()
                except Exception as e:
                    print(f"[TRAP] Scan error: {e}")
            time.sleep(interval_sec)

    def run_scan(self) -> dict:
        """Run a full trap scan for all indices. Returns scan results."""
        now = ist_now()
        timestamp = now.isoformat()
        all_results = {}

        for index, cfg in INDEX_CONFIG.items():
            try:
                results = self._scan_index(index, cfg, timestamp)
                all_results[index.lower()] = results
            except Exception as e:
                print(f"[TRAP] Scan error for {index}: {e}")
                all_results[index.lower()] = {"error": str(e)}

        return all_results

    def _scan_index(self, index, cfg, timestamp):
        """Scan a single index for trap fingerprints."""
        # Get spot price
        spot_data = self.kite.ltp([cfg["spot_symbol"]])
        spot_price = spot_data.get(cfg["spot_symbol"], {}).get("last_price", 0)
        if spot_price <= 0:
            return {"error": "No spot price"}

        # Find all future expiries
        today = ist_now().date()
        opts = [i for i in self.nfo_instruments
                if i["name"] == cfg["name"]
                and i["instrument_type"] in ("CE", "PE")
                and i["expiry"] >= today]

        expiries = sorted(set(i["expiry"] for i in opts))
        future_expiries = [e for e in expiries if e >= today]
        if not future_expiries:
            return {"error": "No expiries found"}

        # Current and next expiry
        current_expiry = future_expiries[0]
        next_expiry = future_expiries[1] if len(future_expiries) > 1 else None

        # Filter OTM strikes: 1% to 8% from spot
        otm_low = spot_price * 0.92   # 8% below
        otm_high = spot_price * 1.08  # 8% above
        near_low = spot_price * 0.99  # 1% below
        near_high = spot_price * 1.01 # 1% above

        # Get previous spot for spot_change_pct
        prev_spot_key = f"{index}_prev_spot"
        prev_spot = self.prev_snapshots.get(prev_spot_key, spot_price)

        spot_change_pct = abs((spot_price - prev_spot) / prev_spot * 100) if prev_spot > 0 else 0
        self.prev_snapshots[prev_spot_key] = spot_price

        # Select target expiries
        target_expiries = [current_expiry]
        if next_expiry:
            target_expiries.append(next_expiry)

        # Filter instruments
        scan_opts = []
        for i in opts:
            if i["expiry"] not in target_expiries:
                continue
            strike = i["strike"]
            opt_type = i["instrument_type"]
            # OTM filter: CE above spot, PE below spot (1%-8% range)
            if opt_type == "CE" and near_high < strike <= otm_high:
                scan_opts.append(i)
            elif opt_type == "PE" and otm_low <= strike < near_low:
                scan_opts.append(i)

        if not scan_opts:
            return {"strikes": [], "clusters": [], "spot": spot_price, "spotChangePct": round(spot_change_pct, 3)}

        # Batch fetch quotes
        symbols = {i["instrument_token"]: f"NFO:{i['tradingsymbol']}" for i in scan_opts}
        all_quotes = {}
        token_list = list(symbols.keys())

        for batch_start in range(0, len(token_list), 200):
            batch = token_list[batch_start:batch_start + 200]
            batch_syms = [symbols[t] for t in batch]
            try:
                quotes = self.kite.quote(batch_syms)
                for sym, q in quotes.items():
                    for t in batch:
                        if symbols[t] == sym:
                            all_quotes[t] = q
                            break
                time.sleep(0.3)
            except Exception as e:
                print(f"[TRAP] Quote batch error: {e}")

        # Process each strike
        scan_results = []
        db_rows = []

        for inst in scan_opts:
            token = inst["instrument_token"]
            strike = inst["strike"]
            opt_type = inst["instrument_type"]
            expiry = inst["expiry"]
            expiry_str = str(expiry)

            q = all_quotes.get(token, {})
            oi = q.get("oi", 0)
            volume = q.get("volume", 0)
            ltp = q.get("last_price", 0)

            # Skip illiquid
            if oi < 500:
                continue

            # Get previous snapshot
            snap_key = (index, strike, opt_type, expiry_str)
            prev = self.prev_snapshots.get(snap_key, {})
            prev_oi = prev.get("oi", oi)
            prev_volume = prev.get("volume", 0)
            prev_iv = prev.get("iv", 0)

            # OI change
            oi_change = oi - prev_oi
            oi_change_pct = round((oi_change / prev_oi) * 100, 1) if prev_oi > 0 else 0

            # Volume ratio (vs previous snapshot as proxy for avg)
            avg_vol = max(prev_volume, volume // 2, 1)
            volume_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 0

            # IV approximation from premium (simplified)
            # Using ATM straddle approximation: IV ~ (premium / spot) * sqrt(365/DTE) * 100
            dte = max((expiry - ist_now().date()).days, 1)
            iv_approx = round((ltp / spot_price) * (365 / dte) ** 0.5 * 100, 1) if spot_price > 0 else 0
            iv_change = round(abs(iv_approx - prev_iv), 1) if prev_iv > 0 else 0

            # Premium change (to classify BUYER vs SELLER)
            prev_ltp = prev.get("ltp", ltp)
            prem_change = round(ltp - prev_ltp, 2)
            prem_change_pct = round((prem_change / prev_ltp) * 100, 1) if prev_ltp > 0 else 0

            # Store current as prev for next scan
            self.prev_snapshots[snap_key] = {"oi": oi, "volume": volume, "iv": iv_approx, "ltp": ltp}

            # ── CLASSIFY: BUYER vs SELLER ──
            # OI ↑ + Premium ↑ = BUYERS entering (fresh longs)
            # OI ↑ + Premium ↓ = SELLERS writing (fresh shorts)
            # OI ↓ + Premium ↑ = SELLERS covering (shorts exiting)
            # OI ↓ + Premium ↓ = BUYERS exiting (longs unwinding)
            if oi_change > 0 and prem_change > 0:
                oi_actor = "BUYERS"
                oi_action = "FRESH BUYING"
            elif oi_change > 0 and prem_change <= 0:
                oi_actor = "SELLERS"
                oi_action = "FRESH WRITING"
            elif oi_change < 0 and prem_change >= 0:
                oi_actor = "SELLERS"
                oi_action = "SHORT COVERING"
            elif oi_change < 0 and prem_change < 0:
                oi_actor = "BUYERS"
                oi_action = "LONG UNWINDING"
            else:
                oi_actor = "UNKNOWN"
                oi_action = "NEUTRAL"

            # ── DIRECTION FOR BUYER (you) ──
            # Sellers writing CE = resistance = BUY PE for you
            # Sellers writing PE = support = BUY CE for you
            # Buyers buying CE = bullish bet = BUY CE for you
            # Buyers buying PE = bearish bet = BUY PE for you
            if oi_actor == "SELLERS" and oi_action == "FRESH WRITING":
                if opt_type == "CE":
                    buy_signal = "BUY PE"
                    signal_reason = f"Sellers WRITING {opt_type} at {int(strike)} = resistance building"
                else:
                    buy_signal = "BUY CE"
                    signal_reason = f"Sellers WRITING {opt_type} at {int(strike)} = support building"
            elif oi_actor == "BUYERS" and oi_action == "FRESH BUYING":
                if opt_type == "CE":
                    buy_signal = "BUY CE"
                    signal_reason = f"Buyers BUYING {opt_type} at {int(strike)} = bullish directional bet"
                else:
                    buy_signal = "BUY PE"
                    signal_reason = f"Buyers BUYING {opt_type} at {int(strike)} = bearish directional bet"
            elif oi_action == "SHORT COVERING":
                if opt_type == "CE":
                    buy_signal = "BUY CE"
                    signal_reason = f"CE sellers COVERING at {int(strike)} = resistance weakening, upside opening"
                else:
                    buy_signal = "BUY PE"
                    signal_reason = f"PE sellers COVERING at {int(strike)} = support weakening, downside opening"
            elif oi_action == "LONG UNWINDING":
                if opt_type == "CE":
                    buy_signal = "BUY PE"
                    signal_reason = f"CE buyers EXITING at {int(strike)} = bulls giving up"
                else:
                    buy_signal = "BUY CE"
                    signal_reason = f"PE buyers EXITING at {int(strike)} = bears giving up"
            else:
                buy_signal = "WAIT"
                signal_reason = "No clear direction"

            # ── TRAP SCORE CALCULATION ──
            trap_score = 0
            reasons = []

            # OI change > 15% → +3
            if abs(oi_change_pct) > 15:
                trap_score += 3
                reasons.append(f"OI jumped {oi_change_pct:+.1f}% ({oi_actor} {oi_action})")
            elif abs(oi_change_pct) > 8:
                trap_score += 1
                reasons.append(f"OI changed {oi_change_pct:+.1f}% ({oi_actor})")

            # Volume > 2x average → +3
            if volume_ratio > 2.0:
                trap_score += 3
                reasons.append(f"Volume {volume_ratio:.1f}x avg")
            elif volume_ratio > 1.5:
                trap_score += 1
                reasons.append(f"Volume {volume_ratio:.1f}x avg")

            # IV flat (< 5% change) while OI building → +2
            if iv_change < 5 and abs(oi_change_pct) > 5:
                trap_score += 2
                if oi_actor == "SELLERS":
                    reasons.append(f"IV flat ({iv_change:.1f}%) + sellers writing = stealth positioning")
                else:
                    reasons.append(f"IV flat ({iv_change:.1f}%) = accumulation without panic")

            # Spot barely moved (< 0.3%) → +2
            if spot_change_pct < 0.3:
                trap_score += 2
                reasons.append(f"Spot flat ({spot_change_pct:.2f}%) = hidden positioning before move")

            # Add the buy signal reason
            if trap_score >= 4:
                reasons.append(signal_reason)

            # Alert level
            if trap_score >= 6:
                alert_level = "FINGERPRINT"
            elif trap_score >= 4:
                alert_level = "WATCH"
            else:
                alert_level = "NORMAL"

            # Expiry label
            expiry_label = "CURRENT" if expiry == current_expiry else "NEXT"

            result = {
                "strike": int(strike),
                "optionType": opt_type,
                "expiry": expiry_str,
                "expiryLabel": expiry_label,
                "oi": oi,
                "oiChange": oi_change,
                "oiChangePct": oi_change_pct,
                "volume": volume,
                "volumeRatio": volume_ratio,
                "iv": iv_approx,
                "ivChange": iv_change,
                "ltp": ltp,
                "premChange": prem_change,
                "premChangePct": prem_change_pct,
                "oiActor": oi_actor,
                "oiAction": oi_action,
                "buySignal": buy_signal,
                "trapScore": trap_score,
                "alertLevel": alert_level,
                "reasons": reasons,
            }
            scan_results.append(result)

            # DB row
            db_rows.append((
                timestamp, index, strike, opt_type, expiry_str,
                oi, oi_change, oi_change_pct,
                volume, avg_vol, volume_ratio,
                iv_approx, iv_change, ltp,
                spot_price, spot_change_pct,
                trap_score, 0, alert_level,
            ))

        # Store in DB
        if db_rows:
            conn = _get_conn()
            conn.executemany("""
                INSERT INTO trap_snapshots
                (timestamp, symbol, strike, option_type, expiry,
                 oi, oi_change, oi_change_pct,
                 volume, avg_volume, volume_ratio,
                 iv, iv_change, ltp,
                 spot_price, spot_change_pct,
                 trap_score, is_cluster, alert_level)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, db_rows)
            conn.commit()
            conn.close()

        # ── CLUSTER DETECTION ──
        clusters = self._detect_clusters(scan_results, spot_price)

        # Update cluster flags in DB
        if clusters:
            conn = _get_conn()
            for cluster in clusters:
                for strike in cluster["strikes"]:
                    conn.execute(
                        "UPDATE trap_snapshots SET is_cluster=1 WHERE timestamp=? AND symbol=? AND strike=? AND option_type=?",
                        (timestamp, index, strike, cluster["side"])
                    )
            conn.commit()
            conn.close()

        # Sort by trap score desc
        scan_results.sort(key=lambda x: x["trapScore"], reverse=True)

        return {
            "strikes": scan_results,
            "clusters": clusters,
            "spot": spot_price,
            "spotChangePct": round(spot_change_pct, 3),
            "totalScanned": len(scan_results),
            "fingerprints": len([s for s in scan_results if s["alertLevel"] == "FINGERPRINT"]),
            "watchZones": len([s for s in scan_results if s["alertLevel"] == "WATCH"]),
            "timestamp": ist_now().strftime("%I:%M:%S %p IST"),
            "currentExpiry": str(current_expiry),
            "nextExpiry": str(next_expiry) if next_expiry else None,
        }

    def _detect_clusters(self, results, spot_price):
        """Detect 3+ consecutive OTM strikes with TrapScore >= 4 in same direction."""
        clusters = []
        for side in ["CE", "PE"]:
            # Get strikes for this side, sorted
            side_strikes = sorted(
                [r for r in results if r["optionType"] == side and r["trapScore"] >= 4],
                key=lambda x: x["strike"]
            )
            if len(side_strikes) < 3:
                continue

            # Find consecutive runs
            current_run = [side_strikes[0]]
            for i in range(1, len(side_strikes)):
                gap = abs(side_strikes[i]["strike"] - side_strikes[i - 1]["strike"])
                # Allow gap of up to 2 strike gaps (100 for Nifty, 200 for BankNifty)
                if gap <= 200:
                    current_run.append(side_strikes[i])
                else:
                    if len(current_run) >= 3:
                        clusters.append(self._build_cluster(current_run, side, spot_price))
                    current_run = [side_strikes[i]]

            if len(current_run) >= 3:
                clusters.append(self._build_cluster(current_run, side, spot_price))

        return clusters

    def _build_cluster(self, run, side, spot_price):
        avg_score = round(sum(r["trapScore"] for r in run) / len(run), 1)
        total_oi = sum(r["oiChange"] for r in run)
        strikes = [r["strike"] for r in run]

        # Determine who built it: majority buyer or seller?
        sellers = sum(1 for r in run if r.get("oiActor") == "SELLERS")
        buyers = sum(1 for r in run if r.get("oiActor") == "BUYERS")
        actor = "SELLERS" if sellers >= buyers else "BUYERS"

        # Correct direction based on actor + side
        if actor == "SELLERS":
            # Sellers writing CE = bearish (resistance) → you BUY PE
            # Sellers writing PE = bullish (support) → you BUY CE
            direction = "BEARISH" if side == "CE" else "BULLISH"
            buy_signal = "BUY PE" if side == "CE" else "BUY CE"
            action_desc = "writing"
            meaning = "resistance cluster" if side == "CE" else "support cluster"
        else:
            # Buyers buying CE = bullish → you BUY CE
            # Buyers buying PE = bearish → you BUY PE
            direction = "BULLISH" if side == "CE" else "BEARISH"
            buy_signal = "BUY CE" if side == "CE" else "BUY PE"
            action_desc = "buying"
            meaning = "bullish accumulation" if side == "CE" else "bearish accumulation"

        return {
            "side": side,
            "actor": actor,
            "direction": direction,
            "buySignal": buy_signal,
            "strikes": strikes,
            "strikeRange": f"{min(strikes)}-{max(strikes)}",
            "count": len(run),
            "avgScore": avg_score,
            "totalOIChange": total_oi,
            "signal": f"{actor} {action_desc} {side} cluster ({min(strikes)}-{max(strikes)}): {meaning}. {len(run)} strikes, avg score {avg_score}. For you: {buy_signal}",
            "confidence": "HIGH" if avg_score >= 6 else "MEDIUM",
        }

    # ── PUBLIC API METHODS ──────────────────────────────────────────────

    def get_alerts(self) -> list:
        """Get all active fingerprints (TrapScore >= 4) from latest scan."""
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        # Get latest timestamp
        row = conn.execute("SELECT MAX(timestamp) as ts FROM trap_snapshots").fetchone()
        if not row or not row["ts"]:
            conn.close()
            return []
        latest_ts = row["ts"]
        rows = conn.execute(
            "SELECT * FROM trap_snapshots WHERE timestamp=? AND trap_score >= 4 ORDER BY trap_score DESC",
            (latest_ts,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_history(self, days=7) -> list:
        """Get fingerprints from past N days."""
        cutoff = (ist_now() - timedelta(days=days)).isoformat()
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trap_snapshots WHERE timestamp > ? AND trap_score >= 4 ORDER BY timestamp DESC, trap_score DESC LIMIT 500",
            (cutoff,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_today_signals(self) -> list:
        """Get ALL signals (score >= 4) from today, grouped by scan time. Stays visible all day."""
        today_start = ist_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trap_snapshots WHERE timestamp >= ? AND trap_score >= 4 ORDER BY timestamp DESC, trap_score DESC",
            (today_start,)
        ).fetchall()
        conn.close()

        # Group by scan timestamp + deduplicate (keep highest score per strike+type per scan)
        signals = []
        seen = set()
        for r in rows:
            d = dict(r)
            # Format timestamp for display
            try:
                ts = datetime.fromisoformat(d["timestamp"])
                d["scanTime"] = ts.strftime("%I:%M %p")
                d["scanDate"] = ts.strftime("%d %b")
            except Exception:
                d["scanTime"] = d["timestamp"][:16]
                d["scanDate"] = ""

            # Deduplicate: same strike+type across scans → keep all (different times)
            # But within same scan → keep highest score only
            key = f"{d['timestamp'][:16]}_{d['symbol']}_{int(d['strike'])}_{d['option_type']}"
            if key not in seen:
                seen.add(key)
                signals.append(d)

        return signals

    def get_clusters(self) -> list:
        """Get active cluster alerts from latest scan."""
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT MAX(timestamp) as ts FROM trap_snapshots").fetchone()
        if not row or not row["ts"]:
            conn.close()
            return []
        latest_ts = row["ts"]
        rows = conn.execute(
            "SELECT * FROM trap_snapshots WHERE timestamp=? AND is_cluster=1 ORDER BY trap_score DESC",
            (latest_ts,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
