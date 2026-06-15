"""
regime_analyze — reads regime_at_entry columns populated by regime_backfill
and prints the hard truth:
  • What % of trades were in CHOP regime at entry
  • What was the total P&L of CHOP-regime trades
  • What % of bleed buckets (WATCHER_EXIT / STOP_HUNTED / EARLY_CUT /
    PEAK_GIVEBACK / SL_HIT-no-peak) were in CHOP
  • What would have been blocked if filter were strict

Run from backend dir:
  python3 regime_analyze.py
"""
from __future__ import annotations
import os
import sqlite3
from collections import Counter, defaultdict


def _open(path):
    if not os.path.exists(path):
        return None
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def analyze(db_path: str, table: str, label: str):
    conn = _open(db_path)
    if conn is None:
        print(f"[ANALYZE] missing {db_path}")
        return
    print(f"\n{'=' * 72}")
    print(f" {label}   ({db_path} :: {table})")
    print('=' * 72)

    rows = conn.execute(
        f"SELECT id, idx, action, status, pnl_rupees, regime_at_entry, "
        f"range_pct_at_entry, candle_pct_at_entry, structure_5m, "
        f"structure_15m, structure_1h, peak_ltp, entry_price "
        f"FROM {table} WHERE status NOT IN ('OPEN','PENDING') "
        f"AND regime_at_entry IS NOT NULL AND regime_at_entry != ''"
    ).fetchall()
    n_total = len(rows)
    if n_total == 0:
        print(" no backfilled rows yet — run regime_backfill.py first")
        conn.close()
        return

    print(f"\n Total backfilled closed trades: {n_total}")

    # ── Overall regime distribution ───────────────────────────
    regime_counts = Counter(r["regime_at_entry"] for r in rows)
    regime_pnl = defaultdict(float)
    regime_wins = Counter()
    regime_loss = Counter()
    for r in rows:
        p = r["pnl_rupees"] or 0
        regime_pnl[r["regime_at_entry"]] += p
        if p > 0:
            regime_wins[r["regime_at_entry"]] += 1
        elif p < 0:
            regime_loss[r["regime_at_entry"]] += 1

    print("\n REGIME AT ENTRY  →  trades / wins / losses / total ₹  / avg ₹")
    print(" " + "-" * 70)
    for rg, cnt in sorted(regime_counts.items(), key=lambda x: -x[1]):
        w = regime_wins[rg]
        l = regime_loss[rg]
        tot = regime_pnl[rg]
        avg = tot / cnt if cnt else 0
        wr = (w / cnt * 100) if cnt else 0
        print(f"  {rg:<14}  {cnt:>4}   W:{w:>3}  L:{l:>3}  "
              f"₹{tot:>+12,.0f}  avg ₹{avg:>+8,.0f}  WR {wr:>5.1f}%")

    # ── Exit-status × regime ──────────────────────────────────
    print("\n EXIT STATUS × REGIME  (₹ totals; * = chop signature buckets)")
    print(" " + "-" * 70)
    status_x_regime = defaultdict(lambda: defaultdict(float))
    status_x_count = defaultdict(lambda: defaultdict(int))
    for r in rows:
        st = r["status"]
        rg = r["regime_at_entry"]
        status_x_regime[st][rg] += (r["pnl_rupees"] or 0)
        status_x_count[st][rg] += 1
    chop_buckets = {"WATCHER_EXIT", "STOP_HUNTED", "EARLY_CUT",
                    "PEAK_GIVEBACK", "INSTANT_REJECT", "STALE_TRADE_KILL"}
    statuses = sorted(status_x_count.keys(),
                      key=lambda s: sum(status_x_regime[s].values()))
    for st in statuses:
        chop_mark = " *" if st in chop_buckets else "  "
        cnt_total = sum(status_x_count[st].values())
        pnl_total = sum(status_x_regime[st].values())
        cnt_chop = status_x_count[st].get("CHOP", 0)
        pnl_chop = status_x_regime[st].get("CHOP", 0)
        pct = (cnt_chop / cnt_total * 100) if cnt_total else 0
        print(f"  {chop_mark}{st:<22}  total {cnt_total:>3} / ₹{pnl_total:>+11,.0f}   "
              f"chop {cnt_chop:>3} ({pct:>4.0f}%) / ₹{pnl_chop:>+11,.0f}")

    # ── Structure alignment vs action direction ──────────────
    print("\n STRUCTURE-ALIGNMENT vs ACTION  (5m+15m)")
    print(" " + "-" * 70)
    align_buckets = defaultdict(lambda: {"n": 0, "pnl": 0.0, "w": 0, "l": 0})
    for r in rows:
        is_ce = "CE" in (r["action"] or "")
        s5 = r["structure_5m"] or "UNKNOWN"
        s15 = r["structure_15m"] or "UNKNOWN"
        if is_ce and s5 == "UPTREND" and s15 == "UPTREND":
            cat = "CE aligned 5m+15m UP"
        elif is_ce and (s5 == "DOWNTREND" or s15 == "DOWNTREND"):
            cat = "CE counter-trend"
        elif not is_ce and s5 == "DOWNTREND" and s15 == "DOWNTREND":
            cat = "PE aligned 5m+15m DN"
        elif not is_ce and (s5 == "UPTREND" or s15 == "UPTREND"):
            cat = "PE counter-trend"
        elif s5 == "CHOP" or s15 == "CHOP":
            cat = "structure CHOP"
        else:
            cat = "mixed/other"
        b = align_buckets[cat]
        b["n"] += 1
        b["pnl"] += (r["pnl_rupees"] or 0)
        if (r["pnl_rupees"] or 0) > 0:
            b["w"] += 1
        elif (r["pnl_rupees"] or 0) < 0:
            b["l"] += 1
    for cat, b in sorted(align_buckets.items(), key=lambda x: -x[1]["pnl"]):
        wr = (b["w"] / b["n"] * 100) if b["n"] else 0
        avg = b["pnl"] / b["n"] if b["n"] else 0
        print(f"  {cat:<24}  n={b['n']:>3}  W:{b['w']:>3} L:{b['l']:>3}  "
              f"₹{b['pnl']:>+11,.0f}  avg ₹{avg:>+8,.0f}  WR {wr:>5.1f}%")

    # ── Range threshold sensitivity ──────────────────────────
    print("\n RANGE_PCT_AT_ENTRY HISTOGRAM (would CHOP at threshold X catch?)")
    print(" " + "-" * 70)
    bands = [(0, 0.1), (0.1, 0.15), (0.15, 0.2), (0.2, 0.3),
             (0.3, 0.4), (0.4, 0.6), (0.6, 1.0), (1.0, 99)]
    for lo, hi in bands:
        in_band = [r for r in rows
                   if lo <= (r["range_pct_at_entry"] or 0) < hi]
        n = len(in_band)
        pnl = sum(r["pnl_rupees"] or 0 for r in in_band)
        w = sum(1 for r in in_band if (r["pnl_rupees"] or 0) > 0)
        l = sum(1 for r in in_band if (r["pnl_rupees"] or 0) < 0)
        wr = (w / n * 100) if n else 0
        print(f"  range [{lo:.2f}% – {hi:.2f}%)   n={n:>3}  W:{w:>3} L:{l:>3}  "
              f"₹{pnl:>+11,.0f}  WR {wr:>5.1f}%")

    # ── Bottom line: what's the chop bleed total? ────────────
    chop_rows = [r for r in rows if r["regime_at_entry"] == "CHOP"]
    chop_pnl = sum(r["pnl_rupees"] or 0 for r in chop_rows)
    chop_wins = sum(1 for r in chop_rows if (r["pnl_rupees"] or 0) > 0)
    chop_loss = sum(1 for r in chop_rows if (r["pnl_rupees"] or 0) < 0)
    print(f"\n BOTTOM LINE — CHOP at entry:")
    print(f"   trades: {len(chop_rows)}/{n_total} ({len(chop_rows)/n_total*100:.1f}%)")
    print(f"   wins  : {chop_wins}    losses: {chop_loss}")
    print(f"   net ₹ : {chop_pnl:+,.0f}")
    if chop_pnl < 0:
        print(f"   IF chop-block were active during these trades,")
        print(f"   it would have saved ~₹{-chop_pnl:,.0f}")
    else:
        print(f"   chop trades NET POSITIVE — blocking them would have LOST money")
    conn.close()


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    targets = [
        ("/data/trades.db", "trades", "MAIN MODE (PnL tab)"),
        (os.path.join(base, "trades.db"), "trades", "MAIN MODE (PnL tab — local)"),
        ("/data/scalper_trades.db", "scalper_trades", "SCALPER MODE"),
        (os.path.join(base, "scalper_trades.db"), "scalper_trades", "SCALPER MODE (local)"),
    ]
    seen = set()
    for path, table, label in targets:
        if path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            analyze(path, table, label)


if __name__ == "__main__":
    main()
