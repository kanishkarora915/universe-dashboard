import { useState, useEffect, useCallback } from "react";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const BORDER = "#1E1E2E";

const statusColor = { OPEN: ACCENT, T1_HIT: GREEN, T2_HIT: GREEN, SL_HIT: RED, STOP_HUNTED: PURPLE };
const statusLabel = { OPEN: "OPEN", T1_HIT: "T1 HIT ✓", T2_HIT: "T2 HIT ✓✓", SL_HIT: "SL HIT ✗", STOP_HUNTED: "STOP HUNTED" };

export default function PnLTracker() {
  const [openTrades, setOpenTrades] = useState([]);
  const [closedTrades, setClosedTrades] = useState([]);
  const [stats, setStats] = useState(null);
  const [stopHunts, setStopHunts] = useState([]);
  const [tab, setTab] = useState("open");

  const refresh = useCallback(async () => {
    try {
      const [o, c, s, h] = await Promise.all([
        fetch("/api/trades/open").then(r => r.json()).catch(() => []),
        fetch("/api/trades/closed").then(r => r.json()).catch(() => []),
        fetch("/api/trades/stats").then(r => r.json()).catch(() => null),
        fetch("/api/trades/stop-hunts").then(r => r.json()).catch(() => []),
      ]);
      if (Array.isArray(o)) setOpenTrades(o);
      if (Array.isArray(c)) setClosedTrades(c);
      if (s) setStats(s);
      if (Array.isArray(h)) setStopHunts(h);
    } catch {}
  }, []);

  useEffect(() => { refresh(); const iv = setInterval(refresh, 5000); return () => clearInterval(iv); }, [refresh]);

  const fmt = (n) => `${"\u20B9"}${Math.round(n || 0).toLocaleString("en-IN")}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* STATS */}
      {stats && (
        <div style={{ background: "#0D0D15", borderRadius: 12, padding: "14px", border: `1px solid ${BORDER}` }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 8, marginBottom: 8 }}>
            {[
              { l: "TOTAL", v: stats.total, c: "#ccc" },
              { l: "OPEN", v: stats.open, c: ACCENT },
              { l: "WINS", v: stats.wins, c: GREEN },
              { l: "LOSSES", v: stats.losses, c: RED },
              { l: "STOP HUNTS", v: stats.stopHunts, c: PURPLE },
              { l: "WIN RATE", v: `${stats.winRate}%`, c: stats.winRate >= 60 ? GREEN : stats.winRate >= 40 ? YELLOW : RED },
            ].map((s, i) => (
              <div key={i} style={{ textAlign: "center", background: "#111118", borderRadius: 8, padding: "6px" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>{s.l}</div>
                <div style={{ color: s.c, fontSize: 16, fontWeight: 900 }}>{s.v}</div>
              </div>
            ))}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
            {[
              { l: "TOTAL P&L", v: fmt(stats.totalPnl), c: stats.totalPnl >= 0 ? GREEN : RED },
              { l: "AVG WIN", v: fmt(stats.avgWin), c: GREEN },
              { l: "AVG LOSS", v: fmt(stats.avgLoss), c: RED },
              { l: "BEST TRADE", v: fmt(stats.bestTrade), c: GREEN },
            ].map((s, i) => (
              <div key={i} style={{ textAlign: "center", background: "#111118", borderRadius: 8, padding: "6px" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>{s.l}</div>
                <div style={{ color: s.c, fontSize: 16, fontWeight: 900 }}>{s.v}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TABS */}
      <div style={{ display: "flex", gap: 6 }}>
        {[
          { id: "open", label: `Open (${openTrades.length})`, c: ACCENT },
          { id: "closed", label: `Closed (${closedTrades.length})`, c: GREEN },
          { id: "hunts", label: `Stop Hunts (${stopHunts.length})`, c: PURPLE },
        ].map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            background: tab === t.id ? t.c + "22" : "#111118", color: tab === t.id ? t.c : "#555",
            border: `1px solid ${tab === t.id ? t.c : BORDER}`, borderRadius: 8, padding: "6px 16px", fontSize: 11, fontWeight: 700, cursor: "pointer",
          }}>{t.label}</button>
        ))}
      </div>

      {/* CONTENT */}
      {tab === "open" && (
        <>
          {openTrades.length === 0 && (
            <div style={{ textAlign: "center", padding: 40, color: "#555" }}>
              <div style={{ fontSize: 13 }}>No open trades. Auto-enters when verdict shows {">"}60% probability.</div>
              <div style={{ fontSize: 11, color: "#444", marginTop: 4 }}>NIFTY: 20L × 65 = 1,300 qty | BANKNIFTY: 20L × 30 = 600 qty | SL: 15% max</div>
            </div>
          )}
          {openTrades.map((t, i) => <TradeCard key={i} t={t} />)}
        </>
      )}

      {tab === "closed" && (
        <>
          {closedTrades.length === 0 && <div style={{ textAlign: "center", padding: 30, color: "#555", fontSize: 12 }}>No closed trades yet.</div>}
          {closedTrades.map((t, i) => <TradeCard key={i} t={t} />)}
        </>
      )}

      {tab === "hunts" && (
        <>
          <div style={{ background: PURPLE + "0A", borderRadius: 10, padding: "10px 14px", border: `1px solid ${PURPLE}33` }}>
            <div style={{ color: PURPLE, fontWeight: 700, fontSize: 12 }}>STOP HUNT DETECTION</div>
            <div style={{ color: "#888", fontSize: 11, marginTop: 4 }}>Trades where SL hit but price reversed {">"}50% — institutions flushed retail then moved in original direction.</div>
          </div>
          {stopHunts.length === 0 && <div style={{ textAlign: "center", padding: 30, color: "#555", fontSize: 12 }}>No stop hunts detected yet.</div>}
          {stopHunts.map((t, i) => <TradeCard key={i} t={t} />)}
        </>
      )}

      {/* Config */}
      <div style={{ background: "#0A0A12", borderRadius: 8, padding: "8px 12px", border: `1px solid ${BORDER}33` }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#555" }}>
          <span>NIFTY: 20L × 65 = 1,300</span>
          <span>BANKNIFTY: 20L × 30 = 600</span>
          <span>SL: 15%</span>
          <span>T1: +20% | T2: +40%</span>
          <span>Auto @ {">"}60%</span>
        </div>
      </div>
    </div>
  );
}

function TradeCard({ t }) {
  const sc = statusColor[t.status] || "#555";
  const pc = (t.pnl_rupees || 0) > 0 ? GREEN : (t.pnl_rupees || 0) < 0 ? RED : "#888";
  const ac = t.action?.includes("CE") ? GREEN : RED;
  const et = t.entry_time ? new Date(t.entry_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true, day: "2-digit", month: "short" }) : "";
  const xt = t.exit_time ? new Date(t.exit_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true }) : "";

  return (
    <div style={{ background: "#111118", borderRadius: 10, padding: "12px 14px", border: `1px solid ${sc}33` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: "#fff", fontWeight: 900, fontSize: 14 }}>{t.idx}</span>
          <span style={{ background: ac + "22", color: ac, padding: "3px 10px", borderRadius: 4, fontSize: 12, fontWeight: 900 }}>{t.action}</span>
          <span style={{ color: "#ccc", fontWeight: 700 }}>{t.strike}</span>
          <span style={{ background: sc + "22", color: sc, padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{statusLabel[t.status] || t.status}</span>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: pc, fontWeight: 900, fontSize: 18 }}>{"\u20B9"}{Math.round(t.pnl_rupees || 0).toLocaleString("en-IN")}</div>
          <div style={{ color: pc, fontSize: 10 }}>{(t.pnl_pts || 0) > 0 ? "+" : ""}{(t.pnl_pts || 0).toFixed(1)} pts</div>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 6, marginBottom: 8 }}>
        {[
          { l: "ENTRY", v: `\u20B9${t.entry_price}`, c: "#ccc" },
          { l: t.status === "OPEN" ? "CURRENT" : "EXIT", v: `\u20B9${(t.status === "OPEN" ? t.current_ltp : t.exit_price || 0).toFixed?.(1) || 0}`, c: t.status === "OPEN" ? ACCENT : sc },
          { l: "SL (15%)", v: `\u20B9${t.sl_price}`, c: RED },
          { l: "T1 (+20%)", v: `\u20B9${t.t1_price}`, c: GREEN },
          { l: "T2 (+40%)", v: `\u20B9${t.t2_price}`, c: GREEN },
        ].map((s, i) => (
          <div key={i} style={{ textAlign: "center" }}>
            <div style={{ color: "#555", fontSize: 8, fontWeight: 700 }}>{s.l}</div>
            <div style={{ color: s.c, fontSize: 12, fontWeight: 700 }}>{s.v}</div>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#666" }}>
        <span>{t.lots}L × {t.lot_size} = {t.qty} qty</span>
        <span>{et}{xt ? ` → ${xt}` : ""}</span>
        <span style={{ color: t.probability >= 70 ? GREEN : YELLOW }}>Prob: {t.probability}%</span>
      </div>
      {t.exit_reason && (
        <div style={{ marginTop: 8, padding: "6px 10px", background: sc + "11", borderRadius: 6, color: sc, fontSize: 11 }}>{t.exit_reason}</div>
      )}
      {t.status === "STOP_HUNTED" && t.reversal_price > 0 && (
        <div style={{ marginTop: 6, padding: "6px 10px", background: PURPLE + "11", borderRadius: 6, color: PURPLE, fontSize: 11 }}>
          Reversal: price recovered to {"\u20B9"}{t.reversal_price.toFixed?.(1) || t.reversal_price} after institutional SL flush
        </div>
      )}
    </div>
  );
}
