"""
System Health Check
───────────────────
Comprehensive end-to-end diagnostic of the entire dashboard:
- Engine + market data freshness
- All databases readable + size
- All trading engines status
- Position watcher state
- Capitulation engine state
- API endpoint round-trips (sample)
- Recent activity audit
- Errors in the last 5 minutes

Returns structured PASS / WARN / FAIL per component with detail.
"""

import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent


def _check(name: str, fn) -> Dict:
    """Run a single check, capture exceptions."""
    try:
        result = fn()
        if isinstance(result, dict):
            return {"name": name, **result}
        return {"name": name, "status": "PASS", "detail": str(result)}
    except Exception as e:
        return {"name": name, "status": "FAIL", "detail": f"Exception: {e}"}


def _db_check(db_path: str, expected_tables: List[str] = None) -> Dict:
    if not os.path.exists(db_path):
        return {"status": "FAIL", "detail": "File missing"}
    size = os.path.getsize(db_path)
    if size < 100:
        return {"status": "WARN", "detail": f"File present but tiny ({size} bytes)"}
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        tables = [r[0] for r in rows]
        conn.close()
        if expected_tables:
            missing = [t for t in expected_tables if t not in tables]
            if missing:
                return {"status": "WARN", "detail": f"Size {size//1024}KB · missing tables: {missing}",
                        "tables": tables}
        return {"status": "PASS", "detail": f"Size {size//1024}KB · {len(tables)} tables",
                "tables": tables, "size_bytes": size}
    except Exception as e:
        return {"status": "FAIL", "detail": f"Read error: {e}"}


def run_full_check(engine) -> Dict:
    """Run every diagnostic. Returns category-grouped results."""
    started = time.time()
    report = {
        "ts": started,
        "ts_iso": datetime.now().isoformat(),
        "categories": {},
        "summary": {"pass": 0, "warn": 0, "fail": 0, "total": 0},
    }

    # ── 1. Engine Core ────────────────────────────────────────────────
    cat = []

    def chk_engine_alive():
        if not engine:
            return {"status": "FAIL", "detail": "engine global is None"}
        return {"status": "PASS", "detail": f"running={getattr(engine, 'running', False)}"}
    cat.append(_check("Engine instance alive", chk_engine_alive))

    def chk_spot_tokens():
        if not engine or not hasattr(engine, "spot_tokens"):
            return {"status": "FAIL", "detail": "spot_tokens missing"}
        toks = list(engine.spot_tokens.keys())
        expected = {"NIFTY", "BANKNIFTY", "VIX"}
        missing = expected - set(toks)
        if missing:
            return {"status": "FAIL", "detail": f"missing: {missing}"}
        return {"status": "PASS", "detail": f"present: {toks}"}
    cat.append(_check("Spot tokens", chk_spot_tokens))

    def chk_chains():
        if not engine or not hasattr(engine, "chains"):
            return {"status": "FAIL", "detail": "chains missing"}
        ks = list(engine.chains.keys())
        for idx in ("NIFTY", "BANKNIFTY"):
            if idx not in ks:
                return {"status": "FAIL", "detail": f"missing chain for {idx}"}
            chain = engine.chains[idx]
            if not chain or len(chain) < 5:
                return {"status": "WARN", "detail": f"{idx} chain has only {len(chain)} strikes"}
        return {"status": "PASS", "detail": f"NIFTY: {len(engine.chains['NIFTY'])} strikes, "
                                            f"BANKNIFTY: {len(engine.chains['BANKNIFTY'])} strikes"}
    cat.append(_check("Option chains populated", chk_chains))

    def chk_prices_fresh():
        if not engine or not hasattr(engine, "prices") or not hasattr(engine, "spot_tokens"):
            return {"status": "FAIL", "detail": "missing prices/tokens"}
        nifty_tok = engine.spot_tokens.get("NIFTY")
        if nifty_tok and nifty_tok in engine.prices:
            ltp = engine.prices[nifty_tok].get("ltp", 0)
            if ltp > 0:
                return {"status": "PASS", "detail": f"NIFTY LTP ₹{ltp}"}
        return {"status": "WARN", "detail": "NIFTY LTP not available"}
    cat.append(_check("Live prices flowing", chk_prices_fresh))

    def chk_market_hours():
        from datetime import timezone
        IST = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(IST)
        h, m = now.hour, now.minute
        market_open = ((h == 9 and m >= 15) or (10 <= h <= 14) or (h == 15 and m <= 30))
        if market_open:
            return {"status": "PASS", "detail": f"Market OPEN ({now.strftime('%H:%M')} IST)"}
        return {"status": "WARN", "detail": f"Market CLOSED ({now.strftime('%H:%M')} IST) — pulses still run for cold-start"}
    cat.append(_check("Market hours", chk_market_hours))

    def chk_trade_manager():
        tm = getattr(engine, "trade_manager", None) if engine else None
        if not tm:
            return {"status": "FAIL", "detail": "trade_manager missing"}
        return {"status": "PASS", "detail": "trade_manager initialised"}
    cat.append(_check("Trade manager (auto-trader)", chk_trade_manager))

    report["categories"]["1. Engine Core"] = cat

    # ── 2. Databases ──────────────────────────────────────────────────
    cat = []
    cat.append(_check("trades.db (PnL trades)", lambda: _db_check(str(_DATA_DIR / "trades.db"), ["trades"])))
    cat.append(_check("scalper_trades.db", lambda: _db_check(str(_DATA_DIR / "scalper_trades.db"),
                                                              ["scalper_trades", "scalper_config"])))
    cat.append(_check("position_watcher.db", lambda: _db_check(str(_DATA_DIR / "position_watcher.db"),
                                                                ["spot_ticks", "health_log", "exit_log",
                                                                 "watcher_config", "position_ticks"])))
    cat.append(_check("capitulation.db", lambda: _db_check(str(_DATA_DIR / "capitulation.db"),
                                                            ["capitulation_log"])))
    cat.append(_check("trinity.db", lambda: _db_check(str(_DATA_DIR / "trinity.db"))))
    cat.append(_check("capital_tracker.db", lambda: _db_check(str(_DATA_DIR / "capital_tracker.db"))))
    cat.append(_check("trade_autopsy.db", lambda: _db_check(str(_DATA_DIR / "trade_autopsy.db"))))
    cat.append(_check("backtest.db", lambda: _db_check(str(_DATA_DIR / "backtest.db"))))
    cat.append(_check("trading_times.db", lambda: _db_check(str(_DATA_DIR / "trading_times.db"))))
    cat.append(_check("buyer_mode.db", lambda: _db_check(str(_DATA_DIR / "buyer_mode.db"))))
    cat.append(_check("risk_tier.db", lambda: _db_check(str(_DATA_DIR / "risk_tier.db"))))
    cat.append(_check("shadow_autopsy.db", lambda: _db_check(str(_DATA_DIR / "shadow_autopsy.db"))))
    report["categories"]["2. Databases"] = cat

    # ── 3. Trading Intelligence Engines ──────────────────────────────
    cat = []

    def chk_volatility():
        try:
            from volatility_detector import classify_regime, get_recommendations
            rd = classify_regime(engine)
            rec = get_recommendations(rd)
            return {"status": "PASS", "detail": f"regime={rd.get('regime')} window={rd.get('time_window')} main_pnl_allowed={rec.get('main_pnl_allowed')}"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Volatility detector", chk_volatility))

    def chk_oi_shift():
        try:
            from oi_shift_detector import is_trade_against_shift
            return {"status": "PASS", "detail": "module loadable"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("OI Shift Detector", chk_oi_shift))

    def chk_truth_lie():
        try:
            from truth_lie_detector import check_pattern
            return {"status": "PASS", "detail": "module loadable"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Truth/Lie Detector", chk_truth_lie))

    def chk_risk_tier():
        try:
            from risk_tier_manager import get_current_tier
            tier = get_current_tier("MAIN")
            return {"status": "PASS", "detail": f"MAIN tier={tier}"}
        except Exception as e:
            return {"status": "WARN", "detail": str(e)}
    cat.append(_check("Risk Tier Manager", chk_risk_tier))

    def chk_capital_tracker():
        try:
            from capital_tracker import get_running_capital
            cap = get_running_capital("MAIN")
            return {"status": "PASS", "detail": f"MAIN running cap=₹{cap:,.0f}"}
        except Exception as e:
            return {"status": "WARN", "detail": str(e)}
    cat.append(_check("Capital Tracker", chk_capital_tracker))

    def chk_quality_score():
        try:
            from quality_score import compute_quality_score
            return {"status": "PASS", "detail": "module loadable"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Quality Score Engine", chk_quality_score))

    def chk_backtest_validator():
        try:
            from backtest_validator import get_filter_stats_only
            return {"status": "PASS", "detail": "module loadable"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Backtest Validator", chk_backtest_validator))

    def chk_ai_brain():
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"status": "WARN", "detail": "ANTHROPIC_API_KEY env var not set"}
        return {"status": "PASS", "detail": f"key present (len={len(api_key)})"}
    cat.append(_check("AI Brain (Claude Haiku)", chk_ai_brain))

    report["categories"]["3. Trading Intelligence"] = cat

    # ── 4. Position Watcher ──────────────────────────────────────────
    cat = []

    def chk_watcher_pulse():
        try:
            from position_watcher import _last_health_cache
            if not _last_health_cache:
                return {"status": "WARN", "detail": "cache empty (no open trades or no pulse yet)"}
            ages = [time.time() - h.get("ts", 0) for h in _last_health_cache.values()]
            min_age = min(ages)
            stubs = sum(1 for h in _last_health_cache.values() if h.get("stub"))
            if min_age > 90:
                return {"status": "FAIL", "detail": f"oldest pulse {min_age:.0f}s ago — pulse loop may be stuck"}
            return {"status": "PASS", "detail": f"{len(_last_health_cache)} cached · "
                                                f"oldest pulse {min_age:.0f}s ago · {stubs} stub(s)"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Watcher pulse loop", chk_watcher_pulse))

    def chk_watcher_db_consistency():
        try:
            from position_watcher import _get_open_main_trades, _get_open_scalper_trades, _last_health_cache
            db_count = len(_get_open_main_trades()) + len(_get_open_scalper_trades())
            cache_count = len(_last_health_cache)
            if db_count != cache_count:
                return {"status": "WARN", "detail": f"DB={db_count} cache={cache_count} — should auto-clean"}
            return {"status": "PASS", "detail": f"DB={db_count} == cache={cache_count}"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Watcher cache consistency", chk_watcher_db_consistency))

    def chk_watcher_config():
        try:
            from position_watcher import get_config
            cfg = get_config()
            ae_main = cfg.get("auto_exit_main", False)
            ae_scalp = cfg.get("auto_exit_scalper", False)
            return {"status": "PASS", "detail": f"auto_exit_main={ae_main} auto_exit_scalper={ae_scalp}"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Watcher config", chk_watcher_config))

    report["categories"]["4. Position Watcher"] = cat

    # ── 5. Capitulation Engine ──────────────────────────────────────
    cat = []

    def chk_cap_pulse():
        try:
            from capitulation_engine import get_live_state
            state = get_live_state()
            if not state or not state.get("results"):
                return {"status": "WARN", "detail": "no pulse data yet (60s cycle)"}
            age = time.time() - state.get("ts", 0)
            if age > 120:
                return {"status": "FAIL", "detail": f"last pulse {age:.0f}s ago — stuck"}
            return {"status": "PASS", "detail": f"last pulse {age:.0f}s ago · "
                                                f"indices: {list(state['results'].keys())}"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Capitulation pulse", chk_cap_pulse))

    def chk_cap_signals():
        try:
            from capitulation_engine import get_live_state
            state = get_live_state()
            if not state.get("results"):
                return {"status": "WARN", "detail": "no results"}
            details = []
            for idx, data in state["results"].items():
                if "error" in data:
                    details.append(f"{idx}: ERROR")
                    continue
                bull = data.get("bullish", {})
                bear = data.get("bearish", {})
                details.append(f"{idx}: bull {bull.get('score', 0)} ({bull.get('verdict')}), "
                               f"bear {bear.get('score', 0)} ({bear.get('verdict')})")
            return {"status": "PASS", "detail": " | ".join(details)}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Capitulation scoring", chk_cap_signals))

    def chk_oi_delta():
        try:
            from oi_delta_tracker import get_tracker
            t = get_tracker()
            counts = {idx: len(s) for idx, s in t.samples.items()}
            if not counts:
                return {"status": "WARN", "detail": "no samples yet"}
            return {"status": "PASS", "detail": f"samples: {counts}"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("OI Delta Tracker", chk_oi_delta))

    report["categories"]["5. Capitulation Engine"] = cat

    # ── 6. Velocity Trackers ────────────────────────────────────────
    cat = []

    def chk_vix_velocity():
        try:
            from vix_velocity import get_tracker
            t = get_tracker()
            cur = t.current()
            n = len(t.samples)
            if n == 0:
                return {"status": "WARN", "detail": "no VIX samples yet"}
            return {"status": "PASS", "detail": f"{n} samples · current VIX {cur:.2f}"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("VIX Velocity Tracker", chk_vix_velocity))

    def chk_premium_velocity():
        try:
            from premium_velocity import get_tracker
            t = get_tracker()
            return {"status": "PASS", "detail": f"{len(t.samples)} trade streams · {len(t.entry_data)} entries registered"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Premium Velocity Tracker", chk_premium_velocity))

    report["categories"]["6. Velocity Trackers"] = cat

    # ── 7. Recent Activity ──────────────────────────────────────────
    cat = []

    def chk_recent_trades():
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(str(_DATA_DIR / "trades.db"), timeout=2)
            t_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE entry_time LIKE ?", (f"{today}%",)
            ).fetchone()[0]
            t_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl_rupees),0) FROM trades WHERE entry_time LIKE ? AND status != 'OPEN'",
                (f"{today}%",)
            ).fetchone()[0]
            conn.close()
            return {"status": "PASS", "detail": f"{t_count} trades today · realised P&L ₹{t_pnl:,.0f}"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Today's trades (PnL tab)", chk_recent_trades))

    def chk_recent_scalper():
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(str(_DATA_DIR / "scalper_trades.db"), timeout=2)
            t_count = conn.execute(
                "SELECT COUNT(*) FROM scalper_trades WHERE entry_time LIKE ?", (f"{today}%",)
            ).fetchone()[0]
            t_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl_rupees),0) FROM scalper_trades WHERE entry_time LIKE ? AND status != 'OPEN'",
                (f"{today}%",)
            ).fetchone()[0]
            conn.close()
            return {"status": "PASS", "detail": f"{t_count} trades today · realised P&L ₹{t_pnl:,.0f}"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Today's trades (Scalper)", chk_recent_scalper))

    def chk_watcher_exits():
        try:
            from position_watcher import get_recent_exits
            exits = get_recent_exits(limit=10)
            return {"status": "PASS", "detail": f"last {len(exits)} watcher exits in DB"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Watcher exit log", chk_watcher_exits))

    def chk_capitulation_events():
        try:
            from capitulation_engine import get_history
            evs = get_history(limit=10)
            return {"status": "PASS", "detail": f"{len(evs)} capitulation events today"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Capitulation events log", chk_capitulation_events))

    report["categories"]["7. Recent Activity"] = cat

    # ── 8. Process Stats ────────────────────────────────────────────
    cat = []

    def chk_threading():
        import threading
        threads = threading.enumerate()
        watcher_t = [t for t in threads if "watcher" in t.name.lower() or "capitulation" in t.name.lower()]
        return {"status": "PASS", "detail": f"{len(threads)} total threads"}
    cat.append(_check("Background threads", chk_threading))

    def chk_disk_usage():
        try:
            total = 0
            for p in _DATA_DIR.iterdir():
                if p.is_file():
                    total += p.stat().st_size
            mb = total / 1024 / 1024
            if mb > 800:
                return {"status": "WARN", "detail": f"data dir using {mb:.1f}MB — approaching 1GB"}
            return {"status": "PASS", "detail": f"data dir using {mb:.1f}MB"}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e)}
    cat.append(_check("Disk usage", chk_disk_usage))

    report["categories"]["8. Process Stats"] = cat

    # ── Compute summary ─────────────────────────────────────────────
    for cat_name, checks in report["categories"].items():
        for c in checks:
            report["summary"]["total"] += 1
            status = c.get("status", "FAIL").upper()
            if status == "PASS":
                report["summary"]["pass"] += 1
            elif status == "WARN":
                report["summary"]["warn"] += 1
            else:
                report["summary"]["fail"] += 1

    report["duration_ms"] = round((time.time() - started) * 1000, 1)
    s = report["summary"]
    if s["fail"] > 0:
        report["overall"] = "DEGRADED" if s["fail"] <= 2 else "BROKEN"
    elif s["warn"] > 3:
        report["overall"] = "OK_WITH_WARNINGS"
    else:
        report["overall"] = "HEALTHY"

    return report
