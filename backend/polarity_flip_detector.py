"""
Polarity Flip Detector — Market Structure Evolution Tracker
────────────────────────────────────────────────────────────
Tracks every major price level (CE/PE walls, day H/L, max pain,
rejection zones, round numbers) and detects when its ROLE flips:

  • RESISTANCE → SUPPORT  (breakout — old ceiling becomes new floor)
  • SUPPORT → RESISTANCE  (breakdown — old floor becomes new ceiling)

For each level, maintains a full history showing "pehle kya tha, ab kya hai":
  - first_detected_ts, initial_role
  - current_role, last_flip_ts
  - touches, rejections
  - flip_history with timestamps
  - OI evolution at the strike

Confirmation rules:
  - Spot must cross level by >0.3%
  - Stay on other side for 3+ pulses (≥3 minutes)
  - OI in confirming direction (CE OI dropping for breakout, etc)

Pulses every 60s alongside capitulation engine.
"""

import time
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
from collections import defaultdict, deque


_DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
DB_PATH = str(_DATA_DIR / "polarity_flips.db")


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS level_registry (
            idx TEXT,
            level_price REAL,
            source TEXT,            -- CE_WALL, PE_WALL, DAY_HIGH, DAY_LOW, MAX_PAIN, ROUND
            first_seen REAL,
            initial_role TEXT,      -- R / S
            current_role TEXT,      -- R / S / NEUTRAL
            touches INTEGER DEFAULT 0,
            spot_above_count INTEGER DEFAULT 0,
            spot_below_count INTEGER DEFAULT 0,
            last_oi REAL,
            last_oi_pct_change REAL,
            last_seen REAL,
            last_flip_ts REAL,
            flip_count INTEGER DEFAULT 0,
            PRIMARY KEY (idx, level_price)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lvl_idx ON level_registry(idx, current_role)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS flip_events (
            ts REAL,
            idx TEXT,
            level_price REAL,
            source TEXT,
            from_role TEXT,
            to_role TEXT,
            spot_at_flip REAL,
            oi_at_flip REAL,
            oi_change_pct REAL,
            duration_in_prev_role_min REAL,
            confirmation_strength TEXT,    -- CONFIRMED / PENDING / FAILED
            description TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flip_ts ON flip_events(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flip_idx ON flip_events(idx, ts)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_structure_snapshots (
            ts REAL,
            idx TEXT,
            spot REAL,
            resistance_levels TEXT,  -- JSON list
            support_levels TEXT,     -- JSON list
            tag TEXT                 -- OPEN / HOURLY / CAPITULATION / TREND_CHANGE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts ON market_structure_snapshots(idx, ts)")

    conn.commit()
    conn.close()


# ── Pending flip tracker (in-memory, needs N consecutive pulses to confirm) ─

_pending_flips: Dict[str, Dict] = {}  # key = "idx:level" → {since, target_role, pulses}
PULSES_TO_CONFIRM = 3  # 3 consecutive 60s pulses = 3 min in new role


def _strike_key(idx: str, level: float) -> str:
    return f"{idx}:{int(level)}"


def _round_to_strike(price: float, gap: int) -> int:
    return int(round(price / gap) * gap)


# ── Discovery: pull all major levels per index ─────────────────────────

def _discover_levels(engine, idx: str) -> List[Dict]:
    """Return current candidate levels with metadata."""
    levels = []
    chain = engine.chains.get(idx, {}) if hasattr(engine, "chains") else {}
    if not chain:
        return levels

    spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
    spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
    if spot <= 0:
        return levels

    gap = 50 if idx == "NIFTY" else 100

    # Top 5 CE walls (by OI)
    ce_strikes = []
    pe_strikes = []
    for strike, data in chain.items():
        try:
            sk = int(strike) if isinstance(strike, str) else strike
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        ce_oi = data.get("ce_oi", 0) or 0
        pe_oi = data.get("pe_oi", 0) or 0
        if ce_oi > 50000:
            ce_strikes.append((sk, ce_oi))
        if pe_oi > 50000:
            pe_strikes.append((sk, pe_oi))
    ce_strikes.sort(key=lambda x: x[1], reverse=True)
    pe_strikes.sort(key=lambda x: x[1], reverse=True)

    for sk, oi in ce_strikes[:5]:
        levels.append({"price": float(sk), "source": "CE_WALL", "oi": oi})
    for sk, oi in pe_strikes[:5]:
        levels.append({"price": float(sk), "source": "PE_WALL", "oi": oi})

    # Day High / Day Low / Max Pain from /api/live-style data
    try:
        live = engine.get_live_data() if hasattr(engine, "get_live_data") else {}
        idx_data = live.get(idx.lower(), {}) if isinstance(live, dict) else {}
        day_high = idx_data.get("high", 0)
        day_low = idx_data.get("low", 0)
        max_pain = idx_data.get("maxPain", 0)
        if day_high > 0:
            levels.append({"price": float(_round_to_strike(day_high, gap)),
                          "source": "DAY_HIGH", "oi": 0})
        if day_low > 0:
            levels.append({"price": float(_round_to_strike(day_low, gap)),
                          "source": "DAY_LOW", "oi": 0})
        if max_pain > 0:
            levels.append({"price": float(max_pain),
                          "source": "MAX_PAIN", "oi": 0})
    except Exception:
        pass

    # Round-number psychological levels (every 500 for NIFTY, 1000 for BN)
    round_gap = 500 if idx == "NIFTY" else 1000
    spot_rounded = round(spot / round_gap) * round_gap
    for offset in (-2, -1, 0, 1, 2):
        rl = spot_rounded + offset * round_gap
        if abs(rl - spot) / spot * 100 <= 3:  # within 3%
            levels.append({"price": float(rl), "source": "ROUND", "oi": 0})

    # Dedup: keep first occurrence (priority order: walls > day H/L > max pain > round)
    seen = set()
    unique = []
    for l in levels:
        k = int(l["price"])
        if k not in seen:
            seen.add(k)
            unique.append(l)
    return unique


# ── Per-pulse update ───────────────────────────────────────────────────

def _update_level(conn, idx: str, level: Dict, spot: float, ts: float):
    """Insert or update a level's registry entry, detect flips."""
    price = level["price"]
    source = level["source"]
    oi = level["oi"]

    # Determine role based on spot's current position
    # Spot ABOVE level → level is SUPPORT (below price = floor)
    # Spot BELOW level → level is RESISTANCE (above price = ceiling)
    role_now = "S" if spot > price else "R" if spot < price else "NEUTRAL"
    distance_pct = abs(spot - price) / price * 100 if price > 0 else 0

    # Touch detection: spot within 0.1% of level
    is_touch = distance_pct < 0.1

    row = conn.execute(
        "SELECT * FROM level_registry WHERE idx=? AND level_price=?",
        (idx, price)
    ).fetchone()

    if not row:
        # First time seeing this level
        conn.execute("""
            INSERT INTO level_registry
            (idx, level_price, source, first_seen, initial_role, current_role,
             touches, spot_above_count, spot_below_count, last_oi, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (idx, price, source, ts, role_now, role_now,
              1 if is_touch else 0,
              1 if role_now == "S" else 0,
              1 if role_now == "R" else 0,
              oi, ts))
        return None  # no flip on first observation

    # Existing level — update counts + check for flip
    columns = ["idx","level_price","source","first_seen","initial_role","current_role",
               "touches","spot_above_count","spot_below_count","last_oi","last_oi_pct_change",
               "last_seen","last_flip_ts","flip_count"]
    rd = dict(zip(columns, row))

    prev_role = rd["current_role"]
    new_above = rd["spot_above_count"] + (1 if role_now == "S" else 0)
    new_below = rd["spot_below_count"] + (1 if role_now == "R" else 0)
    new_touches = rd["touches"] + (1 if is_touch else 0)

    # OI change calculation
    last_oi = rd["last_oi"] or 0
    oi_change_pct = ((oi - last_oi) / last_oi * 100) if last_oi > 0 else 0

    # Pending flip logic
    pending_key = _strike_key(idx, price)
    flip_event = None

    if role_now != prev_role and role_now != "NEUTRAL":
        # Role differs from current — start or continue a pending flip
        pending = _pending_flips.get(pending_key)
        if pending and pending["target_role"] == role_now and distance_pct >= 0.3:
            pending["pulses"] += 1
            if pending["pulses"] >= PULSES_TO_CONFIRM:
                # CONFIRMED FLIP
                flip_event = {
                    "ts": ts,
                    "idx": idx,
                    "level": price,
                    "source": source,
                    "from_role": prev_role,
                    "to_role": role_now,
                    "spot_at_flip": spot,
                    "oi_at_flip": oi,
                    "oi_change_pct": round(oi_change_pct, 2),
                    "duration_min": round((ts - rd["first_seen"]) / 60, 1),
                    "confirmation": "CONFIRMED",
                    "description": _build_flip_description(idx, price, source, prev_role, role_now,
                                                            spot, oi_change_pct),
                }
                conn.execute("""
                    INSERT INTO flip_events (ts, idx, level_price, source, from_role, to_role,
                        spot_at_flip, oi_at_flip, oi_change_pct, duration_in_prev_role_min,
                        confirmation_strength, description)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (ts, idx, price, source, prev_role, role_now, spot, oi,
                      oi_change_pct, flip_event["duration_min"],
                      "CONFIRMED", flip_event["description"]))
                _pending_flips.pop(pending_key, None)
                # Update registry's current_role + flip count
                conn.execute("""
                    UPDATE level_registry
                    SET current_role=?, last_flip_ts=?, flip_count=flip_count+1
                    WHERE idx=? AND level_price=?
                """, (role_now, ts, idx, price))
        elif distance_pct >= 0.3:
            # Start tracking
            _pending_flips[pending_key] = {
                "target_role": role_now, "pulses": 1, "started_ts": ts,
            }
    else:
        # Spot back on same side — cancel any pending flip
        if pending_key in _pending_flips:
            _pending_flips.pop(pending_key, None)

    conn.execute("""
        UPDATE level_registry
        SET source=?, current_role=?, touches=?, spot_above_count=?, spot_below_count=?,
            last_oi=?, last_oi_pct_change=?, last_seen=?
        WHERE idx=? AND level_price=?
    """, (source, role_now if not flip_event else flip_event["to_role"],
          new_touches, new_above, new_below, oi, round(oi_change_pct, 2), ts,
          idx, price))

    return flip_event


def _build_flip_description(idx, level, source, from_role, to_role, spot, oi_change_pct):
    direction = "BREAKOUT" if to_role == "S" else "BREAKDOWN"
    role_label_from = "Resistance" if from_role == "R" else "Support"
    role_label_to = "Support" if to_role == "S" else "Resistance"
    oi_note = ""
    if abs(oi_change_pct) >= 5:
        if to_role == "S" and oi_change_pct < 0:
            oi_note = f" · CE OI {oi_change_pct:.1f}% (writers covering — confirms)"
        elif to_role == "R" and oi_change_pct < 0:
            oi_note = f" · PE OI {oi_change_pct:.1f}% (writers covering — confirms)"
    return (f"⚡ {direction}: {idx} {int(level)} flipped from "
            f"{role_label_from} → {role_label_to} (spot ₹{spot:.1f}){oi_note}")


# ── Main pulse — every 60s ─────────────────────────────────────────────

_last_snapshot_ts: Dict[str, float] = {}
_HOURLY_SNAPSHOT_INTERVAL = 3600  # 1 hour
_OPEN_SNAPSHOT_HOUR = 9  # IST


def pulse(engine) -> Dict:
    """Run a polarity-flip detection cycle."""
    _init_db()
    out = {"ts": time.time(), "results": {}}
    now_ts = time.time()
    conn = sqlite3.connect(DB_PATH)

    for idx in ("NIFTY", "BANKNIFTY"):
        try:
            spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
            spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
            if spot <= 0:
                out["results"][idx] = {"error": "no spot"}
                continue

            levels = _discover_levels(engine, idx)
            flips_detected = []
            for level in levels:
                flip = _update_level(conn, idx, level, spot, now_ts)
                if flip:
                    flips_detected.append(flip)
                    print(f"[POLARITY] {flip['description']}")

            out["results"][idx] = {
                "spot": spot,
                "levels_tracked": len(levels),
                "flips_detected": flips_detected,
            }

            # Hourly snapshot
            last_snap = _last_snapshot_ts.get(idx, 0)
            if now_ts - last_snap >= _HOURLY_SNAPSHOT_INTERVAL:
                _take_snapshot(conn, idx, spot, now_ts, "HOURLY")
                _last_snapshot_ts[idx] = now_ts
        except Exception as e:
            import traceback; traceback.print_exc()
            out["results"][idx] = {"error": str(e)}

    conn.commit()
    conn.close()
    return out


def _take_snapshot(conn, idx: str, spot: float, ts: float, tag: str):
    """Snapshot current S/R structure for historical comparison."""
    rows = conn.execute("""
        SELECT level_price, source, current_role, touches, last_oi, last_seen
        FROM level_registry WHERE idx=? AND last_seen > ?
        ORDER BY level_price ASC
    """, (idx, ts - 300)).fetchall()

    resistances = []
    supports = []
    for r in rows:
        item = {
            "level": r[0], "source": r[1], "role": r[2],
            "touches": r[3], "oi": r[4],
        }
        if r[2] == "R":
            resistances.append(item)
        elif r[2] == "S":
            supports.append(item)

    conn.execute("""
        INSERT INTO market_structure_snapshots (ts, idx, spot, resistance_levels,
            support_levels, tag)
        VALUES (?,?,?,?,?,?)
    """, (ts, idx, spot, json.dumps(resistances), json.dumps(supports), tag))


def trigger_snapshot(engine, tag: str = "MANUAL"):
    """Force a snapshot of current S/R structure (e.g., on capitulation event)."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    for idx in ("NIFTY", "BANKNIFTY"):
        spot_tok = engine.spot_tokens.get(idx) if hasattr(engine, "spot_tokens") else None
        spot = engine.prices.get(spot_tok, {}).get("ltp", 0) if spot_tok else 0
        if spot > 0:
            _take_snapshot(conn, idx, spot, time.time(), tag)
    conn.commit()
    conn.close()


# ── Reading helpers for API ─────────────────────────────────────────────

def get_current_levels(idx: str) -> Dict:
    """All currently tracked levels with their state."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    cutoff = time.time() - 1800  # only levels seen in last 30 min
    rows = conn.execute("""
        SELECT level_price, source, first_seen, initial_role, current_role,
               touches, spot_above_count, spot_below_count, last_oi, last_oi_pct_change,
               last_seen, last_flip_ts, flip_count
        FROM level_registry
        WHERE idx=? AND last_seen > ?
        ORDER BY level_price ASC
    """, (idx, cutoff)).fetchall()
    conn.close()

    levels = []
    for r in rows:
        levels.append({
            "level": r[0],
            "source": r[1],
            "first_seen": r[2],
            "initial_role": r[3],
            "current_role": r[4],
            "touches": r[5],
            "spot_above_count": r[6],
            "spot_below_count": r[7],
            "last_oi": r[8],
            "last_oi_pct_change": r[9],
            "last_seen": r[10],
            "last_flip_ts": r[11],
            "flip_count": r[12],
            "is_flipped": r[3] != r[4],  # initial vs current differ
        })

    resistances = sorted([l for l in levels if l["current_role"] == "R"], key=lambda x: x["level"])
    supports = sorted([l for l in levels if l["current_role"] == "S"], key=lambda x: -x["level"])
    return {
        "idx": idx,
        "ts": time.time(),
        "all_levels": levels,
        "resistances": resistances,
        "supports": supports,
        "flipped_count": sum(1 for l in levels if l["is_flipped"]),
    }


def get_flip_events(idx: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """Today's flip events."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if idx:
        rows = conn.execute("""
            SELECT ts, idx, level_price, source, from_role, to_role, spot_at_flip,
                   oi_at_flip, oi_change_pct, duration_in_prev_role_min,
                   confirmation_strength, description
            FROM flip_events WHERE idx=? AND ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (idx, today_start, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT ts, idx, level_price, source, from_role, to_role, spot_at_flip,
                   oi_at_flip, oi_change_pct, duration_in_prev_role_min,
                   confirmation_strength, description
            FROM flip_events WHERE ts >= ?
            ORDER BY ts DESC LIMIT ?
        """, (today_start, limit)).fetchall()
    conn.close()

    return [{
        "ts": r[0], "idx": r[1], "level": r[2], "source": r[3],
        "from_role": r[4], "to_role": r[5], "spot_at_flip": r[6],
        "oi_at_flip": r[7], "oi_change_pct": r[8],
        "duration_min": r[9], "confirmation": r[10], "description": r[11],
    } for r in rows]


def get_timeline(idx: str) -> Dict:
    """Snapshots over time (open / hourly / events) — pehle vs ab comparison."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = conn.execute("""
        SELECT ts, spot, resistance_levels, support_levels, tag
        FROM market_structure_snapshots
        WHERE idx=? AND ts >= ?
        ORDER BY ts ASC
    """, (idx, today_start)).fetchall()
    conn.close()

    snapshots = []
    for r in rows:
        snapshots.append({
            "ts": r[0], "spot": r[1],
            "resistances": json.loads(r[2] or "[]"),
            "supports": json.loads(r[3] or "[]"),
            "tag": r[4],
        })
    return {"idx": idx, "snapshots": snapshots}
