import { useState, useEffect, useCallback } from "react";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const BORDER = "#1E1E2E";

// ── PnL PDF Export ──
function exportPnLPDF(title, statsData, trades, dailyBreakdown) {
  const now = new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
  const s = statsData || {};
  const fmtR = (n) => `${(n || 0) >= 0 ? "+" : ""}${Math.round(n || 0).toLocaleString("en-IN")}`;

  let html = `
    <h1 style="margin-bottom:2px">UNIVERSE — ${title}</h1>
    <div style="font-size:11px;color:#888;margin-bottom:16px">Generated: ${now} IST</div>
  `;

  // Stats Summary
  if (s.total > 0) {
    html += `<h2>Performance Summary</h2>
    <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap">
      <div style="background:#f8f8f8;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:80px"><div style="font-size:9px;color:#888;text-transform:uppercase">Total Trades</div><div style="font-size:18px;font-weight:900">${s.total}</div></div>
      <div style="background:#f8f8f8;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:80px"><div style="font-size:9px;color:#888;text-transform:uppercase">Wins</div><div style="font-size:18px;font-weight:900;color:#1a8a2e">${s.wins}</div></div>
      <div style="background:#f8f8f8;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:80px"><div style="font-size:9px;color:#888;text-transform:uppercase">Losses</div><div style="font-size:18px;font-weight:900;color:#cc2020">${s.losses}</div></div>
      <div style="background:#f8f8f8;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:80px"><div style="font-size:9px;color:#888;text-transform:uppercase">Stop Hunts</div><div style="font-size:18px;font-weight:900;color:#7c3aed">${s.stopHunts || 0}</div></div>
      <div style="background:#f8f8f8;border-radius:8px;padding:10px 16px;text-align:center;flex:1;min-width:80px"><div style="font-size:9px;color:#888;text-transform:uppercase">Win Rate</div><div style="font-size:18px;font-weight:900;color:${s.winRate >= 60 ? '#1a8a2e' : '#cc2020'}">${s.winRate}%</div></div>
    </div>
    <div style="display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap">
      <div style="background:${s.totalPnl >= 0 ? '#e8ffe8' : '#ffe8e8'};border-radius:8px;padding:12px 20px;text-align:center;flex:1"><div style="font-size:9px;color:#888;text-transform:uppercase">Total P&L</div><div style="font-size:22px;font-weight:900;color:${s.totalPnl >= 0 ? '#1a8a2e' : '#cc2020'}">₹${fmtR(s.totalPnl)}</div></div>
      <div style="background:#f8f8f8;border-radius:8px;padding:12px 20px;text-align:center;flex:1"><div style="font-size:9px;color:#888;text-transform:uppercase">Avg Win</div><div style="font-size:16px;font-weight:700;color:#1a8a2e">₹${fmtR(s.avgWin)}</div></div>
      <div style="background:#f8f8f8;border-radius:8px;padding:12px 20px;text-align:center;flex:1"><div style="font-size:9px;color:#888;text-transform:uppercase">Avg Loss</div><div style="font-size:16px;font-weight:700;color:#cc2020">₹${fmtR(s.avgLoss)}</div></div>
    </div>`;
  }

  // Daily breakdown
  if (dailyBreakdown && Object.keys(dailyBreakdown).length > 0) {
    html += `<h2>Daily Breakdown</h2>
    <table><tr><th>Date</th><th>Trades</th><th>Wins</th><th>Losses</th><th>P&L</th></tr>`;
    for (const [day, d] of Object.entries(dailyBreakdown).sort()) {
      const cls = d.pnl >= 0 ? 'class="pos"' : 'class="neg"';
      html += `<tr><td><strong>${day}</strong></td><td>${d.trades}</td><td class="pos">${d.wins}</td><td class="neg">${d.losses}</td><td ${cls} style="font-weight:700">₹${fmtR(d.pnl)}</td></tr>`;
    }
    html += `</table>`;
  }

  // Trade Details
  if (trades && trades.length > 0) {
    html += `<h2>Trade Details (${trades.length} trades)</h2>`;
    for (const t of trades) {
      const isWin = t.status === "T1_HIT" || t.status === "T2_HIT";
      const isHunt = t.status === "STOP_HUNTED";
      const borderColor = isWin ? "#1a8a2e" : isHunt ? "#7c3aed" : t.status === "SL_HIT" ? "#cc2020" : "#ddd";
      const time = t.entry_time ? new Date(t.entry_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true, day: "2-digit", month: "short" }) : "";
      const exitTime = t.exit_time ? new Date(t.exit_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true }) : "";

      html += `<div style="border:1px solid ${borderColor};border-radius:8px;padding:12px;margin-bottom:10px;${isWin ? 'background:#f0fff0' : isHunt ? 'background:#f5f0ff' : t.status === 'SL_HIT' ? 'background:#fff0f0' : ''}">`;
      html += `<div style="display:flex;justify-content:space-between;margin-bottom:8px">
        <div><strong>${t.idx}</strong> <span style="color:${t.action?.includes('CE') ? '#1a8a2e' : '#cc2020'};font-weight:700">${t.action}</span> <strong>${t.strike}</strong> <span style="background:${borderColor}22;color:${borderColor};padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700">${t.status}</span></div>
        <div style="text-align:right"><div style="font-size:16px;font-weight:900;color:${(t.pnl_rupees || 0) >= 0 ? '#1a8a2e' : '#cc2020'}">₹${fmtR(t.pnl_rupees)} (${(t.pnl_pts || 0) > 0 ? '+' : ''}${(t.pnl_pts || 0).toFixed(1)} pts)</div></div>
      </div>`;
      html += `<table style="width:auto;margin-bottom:6px"><tr>
        <td><strong>Entry:</strong> ₹${t.entry_price}</td>
        <td><strong>Exit:</strong> ₹${t.exit_price || t.current_ltp || '-'}</td>
        <td><strong>SL:</strong> ₹${t.sl_price}</td>
        <td><strong>T1:</strong> ₹${t.t1_price}</td>
        <td><strong>T2:</strong> ₹${t.t2_price}</td>
        <td><strong>Qty:</strong> ${t.lots}L × ${t.lot_size} = ${t.qty}</td>
      </tr></table>`;
      html += `<div style="font-size:10px;color:#888">Entry: ${time}${exitTime ? ' → Exit: ' + exitTime : ''} | Probability: ${t.probability}% | Source: ${t.source || 'verdict'}</div>`;
      if (t.exit_reason) {
        html += `<div style="margin-top:6px;padding:6px 10px;background:${borderColor}11;border-radius:4px;color:${borderColor};font-size:11px;font-weight:600">${t.exit_reason}</div>`;
      }
      if (isHunt && t.reversal_price > 0) {
        html += `<div style="margin-top:4px;padding:6px 10px;background:#7c3aed11;border-radius:4px;color:#7c3aed;font-size:11px">Stop Hunt: Price reversed to ₹${t.reversal_price} after institutional SL flush</div>`;
      }
      html += `</div>`;
    }
  }

  // Footer
  html += `<hr style="border:none;border-top:2px solid #ddd;margin:20px 0">
    <div style="text-align:center;font-size:10px;color:#aaa">UNIVERSE PnL Report | ${now} IST | Nifty: 20L×65=1300 | BankNifty: 20L×30=600 | SL: 15% max</div>`;

  const win = window.open("", "_blank", "width=900,height=700");
  win.document.write(`<html><head><title>${title}</title>
    <style>
      body { font-family: -apple-system, sans-serif; padding: 24px; margin: 0; color: #111; }
      h1 { font-size: 20px; margin-bottom: 4px; }
      h2 { font-size: 14px; color: #555; margin-top: 20px; border-bottom: 2px solid #eee; padding-bottom: 4px; }
      table { width: 100%; border-collapse: collapse; font-size: 11px; margin-bottom: 16px; }
      th { background: #f5f5f5; padding: 6px 8px; text-align: left; font-weight: 700; border-bottom: 2px solid #ddd; }
      td { padding: 5px 8px; border-bottom: 1px solid #eee; }
      .pos { color: #1a8a2e; font-weight: 600; }
      .neg { color: #cc2020; font-weight: 600; }
      @media print { body { padding: 12px; } }
    </style>
  </head><body>${html}</body></html>`);
  win.document.close();
  setTimeout(() => win.print(), 500);
}

const statusColor = { OPEN: ACCENT, T1_HIT: GREEN, T2_HIT: GREEN, SL_HIT: RED, STOP_HUNTED: PURPLE };
const statusLabel = { OPEN: "OPEN", T1_HIT: "T1 HIT ✓", T2_HIT: "T2 HIT ✓✓", SL_HIT: "SL HIT ✗", STOP_HUNTED: "STOP HUNTED" };

export default function PnLTracker() {
  const [openTrades, setOpenTrades] = useState([]);
  const [closedTrades, setClosedTrades] = useState([]);
  const [stats, setStats] = useState(null);
  const [stopHunts, setStopHunts] = useState([]);
  const [tab, setTab] = useState("open");
  const [dates, setDates] = useState([]);
  const [selectedDate, setSelectedDate] = useState("");
  const [dateTrades, setDateTrades] = useState([]);
  const [monthlyReport, setMonthlyReport] = useState(null);
  const [selectedMonth, setSelectedMonth] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  });

  const [posAlerts, setPosAlerts] = useState([]);

  const safeFetch = async (url, fallback) => {
    try { const r = await fetch(url); if (!r.ok) return fallback; return await r.json(); } catch { return fallback; }
  };

  const refresh = useCallback(async () => {
    const [o, c, s, h, d, a] = await Promise.all([
      safeFetch("/api/trades/open", []),
      safeFetch("/api/trades/closed", []),
      safeFetch("/api/trades/stats", null),
      safeFetch("/api/trades/stop-hunts", []),
      safeFetch("/api/trades/dates", []),
      safeFetch("/api/trades/alerts", []),
    ]);
    if (Array.isArray(o)) setOpenTrades(o);
    if (Array.isArray(a)) setPosAlerts(a);
    if (Array.isArray(c)) setClosedTrades(c);
    if (s && s.total !== undefined) setStats(s);
    if (Array.isArray(h)) setStopHunts(h);
    if (Array.isArray(d)) setDates(d);
  }, []);

  useEffect(() => { refresh(); const iv = setInterval(refresh, 5000); return () => clearInterval(iv); }, [refresh]);

  // Fetch trades for selected date
  useEffect(() => {
    if (selectedDate) {
      fetch(`/api/trades/date/${selectedDate}`).then(r => r.json()).then(d => {
        if (Array.isArray(d)) setDateTrades(d);
      }).catch(() => {});
    }
  }, [selectedDate]);

  // Fetch monthly report
  useEffect(() => {
    if (selectedMonth && tab === "monthly") {
      const [y, m] = selectedMonth.split("-");
      fetch(`/api/trades/monthly/${y}/${m}`).then(r => r.json()).then(d => {
        if (d) setMonthlyReport(d);
      }).catch(() => {});
    }
  }, [selectedMonth, tab]);

  const fmt = (n) => `${"\u20B9"}${Math.round(n || 0).toLocaleString("en-IN")}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* EXPORT BUTTONS */}
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        <button onClick={() => {
          const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
          fetch(`/api/trades/date/${today}`).then(r => r.json()).then(trades => {
            const wins = trades.filter(t => t.status === "T1_HIT" || t.status === "T2_HIT");
            const losses = trades.filter(t => t.status === "SL_HIT");
            const closed = trades.filter(t => t.status !== "OPEN");
            const st = { total: trades.length, wins: wins.length, losses: losses.length, stopHunts: trades.filter(t => t.status === "STOP_HUNTED").length, winRate: closed.length ? Math.round(wins.length / closed.length * 100) : 0, totalPnl: closed.reduce((s, t) => s + (t.pnl_rupees || 0), 0), avgWin: wins.length ? wins.reduce((s, t) => s + t.pnl_rupees, 0) / wins.length : 0, avgLoss: losses.length ? losses.reduce((s, t) => s + t.pnl_rupees, 0) / losses.length : 0 };
            exportPnLPDF(`Daily PnL Report — ${today}`, st, trades, null);
          }).catch(() => {});
        }} style={{ background: ORANGE + "22", color: ORANGE, border: `1px solid ${ORANGE}44`, borderRadius: 8, padding: "5px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
          Export Today
        </button>
        <button onClick={() => {
          fetch("/api/trades/closed").then(r => r.json()).then(trades => {
            exportPnLPDF("Weekly PnL Report (Last 7 Days)", stats, trades, null);
          }).catch(() => {});
        }} style={{ background: ACCENT + "22", color: ACCENT, border: `1px solid ${ACCENT}44`, borderRadius: 8, padding: "5px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
          Export Weekly
        </button>
        <button onClick={() => {
          const [y, m] = selectedMonth.split("-");
          fetch(`/api/trades/monthly/${y}/${m}`).then(r => r.json()).then(report => {
            exportPnLPDF(`Monthly PnL Report — ${report.month}`, report.stats, report.trades, report.daily);
          }).catch(() => {});
        }} style={{ background: YELLOW + "22", color: YELLOW, border: `1px solid ${YELLOW}44`, borderRadius: 8, padding: "5px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
          Export Monthly
        </button>
      </div>

      {/* OVERALL P&L HERO */}
      {stats && (
        <div style={{ background: (stats.totalPnl || 0) >= 0 ? GREEN + "08" : RED + "08", borderRadius: 12, padding: "16px", border: `1px solid ${(stats.totalPnl || 0) >= 0 ? GREEN : RED}33` }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div>
              <div style={{ color: "#888", fontSize: 10, fontWeight: 700 }}>OVERALL P&L (LIVE)</div>
              <div style={{ color: (stats.totalPnl || 0) >= 0 ? GREEN : RED, fontSize: 28, fontWeight: 900 }}>{fmt(stats.totalPnl)}</div>
              <div style={{ color: "#666", fontSize: 10, marginTop: 2 }}>
                Closed: {fmt(stats.closedPnl)} | Open: <span style={{ color: (stats.openPnl || 0) >= 0 ? GREEN : RED }}>{fmt(stats.openPnl)}</span>
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ color: "#888", fontSize: 10 }}>Win Rate</div>
              <div style={{ color: stats.winRate >= 60 ? GREEN : stats.winRate >= 40 ? YELLOW : RED, fontSize: 24, fontWeight: 900 }}>{stats.winRate}%</div>
              {stats.currentStreak > 0 && <div style={{ color: stats.streakType === "WIN" ? GREEN : RED, fontSize: 10 }}>{stats.currentStreak} {stats.streakType} streak</div>}
            </div>
          </div>

          {/* Row 1: Trade Counts */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 6, marginBottom: 8 }}>
            {[
              { l: "TOTAL", v: stats.total, c: "#ccc" },
              { l: "OPEN", v: stats.open, c: ACCENT },
              { l: "WINS", v: stats.wins, c: GREEN },
              { l: "LOSSES", v: stats.losses, c: RED },
              { l: "BREAKEVEN", v: stats.breakevens || 0, c: ACCENT },
              { l: "STOP HUNTS", v: stats.stopHunts, c: PURPLE },
            ].map((s, i) => (
              <div key={i} style={{ textAlign: "center", background: "#0A0A12", borderRadius: 6, padding: "5px" }}>
                <div style={{ color: "#555", fontSize: 8, fontWeight: 700 }}>{s.l}</div>
                <div style={{ color: s.c, fontSize: 14, fontWeight: 900 }}>{s.v}</div>
              </div>
            ))}
          </div>

          {/* Row 2: Capital */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 6, marginBottom: 8 }}>
            {[
              { l: "RUNNING CAPITAL", v: fmt(stats.runningCapital || stats.maxCapital || 1000000), c: (stats.runningCapital || 1000000) < 1000000 ? RED : GREEN },
              { l: "IN USE", v: fmt(stats.openInvested), c: ACCENT },
              { l: "AVAILABLE", v: fmt(stats.availableCapital), c: GREEN },
              { l: "USED %", v: `${stats.capitalUsedPct || 0}%`, c: (stats.capitalUsedPct || 0) > 80 ? RED : (stats.capitalUsedPct || 0) > 50 ? YELLOW : GREEN },
              { l: "OPEN VALUE", v: fmt(stats.openCurrentValue), c: (stats.openPnl || 0) >= 0 ? GREEN : RED },
            ].map((s, i) => (
              <div key={i} style={{ textAlign: "center", background: "#0A0A12", borderRadius: 6, padding: "5px" }}>
                <div style={{ color: "#555", fontSize: 8, fontWeight: 700 }}>{s.l}</div>
                <div style={{ color: s.c, fontSize: 13, fontWeight: 700 }}>{s.v}</div>
              </div>
            ))}
          </div>

          {/* Row 3: Loss + Averages */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 6 }}>
            {[
              { l: "TOTAL LOSS", v: fmt(stats.totalLoss), c: RED },
              { l: "AVG WIN", v: fmt(stats.avgWin), c: GREEN },
              { l: "AVG LOSS", v: fmt(stats.avgLoss), c: RED },
              { l: "BEST TRADE", v: fmt(stats.bestTrade), c: GREEN },
            ].map((s, i) => (
              <div key={i} style={{ textAlign: "center", background: "#0A0A12", borderRadius: 6, padding: "5px" }}>
                <div style={{ color: "#555", fontSize: 8, fontWeight: 700 }}>{s.l}</div>
                <div style={{ color: s.c, fontSize: 13, fontWeight: 700 }}>{s.v}</div>
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
          { id: "history", label: "Date History", c: ORANGE },
          { id: "monthly", label: "Monthly Report", c: YELLOW },
        ].map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            background: tab === t.id ? t.c + "22" : "#111118", color: tab === t.id ? t.c : "#555",
            border: `1px solid ${tab === t.id ? t.c : BORDER}`, borderRadius: 8, padding: "6px 16px", fontSize: 11, fontWeight: 700, cursor: "pointer",
          }}>{t.label}</button>
        ))}
      </div>

      {/* POSITION ALERTS — Blinking */}
      {posAlerts.length > 0 && (
        <div style={{
          background: RED + "15", border: `2px solid ${RED}`, borderRadius: 10, padding: "12px 16px",
          animation: "blink 1s infinite",
        }}>
          <style>{`@keyframes blink { 0%,100% { opacity:1 } 50% { opacity:0.5 } }`}</style>
          <div style={{ color: RED, fontWeight: 900, fontSize: 13, marginBottom: 6 }}>{"\u26A0"} POSITION ALERT — ACTION NEEDED</div>
          {posAlerts.map((a, i) => (
            <div key={i} style={{ color: "#ccc", fontSize: 11, marginBottom: 4, padding: "4px 0", borderBottom: i < posAlerts.length - 1 ? `1px solid ${RED}33` : "none" }}>
              <span style={{ color: RED, fontWeight: 700 }}>{a.idx} {a.action} {a.strike}</span>
              <span style={{ color: "#888", marginLeft: 8 }}>{a.alerts}</span>
            </div>
          ))}
        </div>
      )}

      {/* CONTENT */}
      {tab === "open" && (
        <>
          {openTrades.length === 0 && (
            <div style={{ textAlign: "center", padding: 40, color: "#555" }}>
              <div style={{ fontSize: 13 }}>No open trades. Auto-enters when verdict shows {">"}60% probability.</div>
              <div style={{ fontSize: 11, color: "#444", marginTop: 4 }}>NIFTY: 20L × 65 = 1,300 qty | BANKNIFTY: 20L × 30 = 600 qty | SL: 15% max</div>
            </div>
          )}
          {openTrades.map((t, i) => <TradeCard key={i} t={t} onExit={async (id) => {
            if (!confirm(`Exit ${t.action} ${t.idx} ${t.strike} at current price?`)) return;
            const r = await fetch(`/api/trades/exit/${id}`, { method: "POST" });
            const res = await r.json();
            if (res.status === "closed") {
              alert(`Exited! PnL: ₹${res.pnl?.toLocaleString("en-IN")}`);
              window.location.reload();
            } else {
              alert(res.error || "Exit failed");
            }
          }} />)}
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

      {/* DATE HISTORY */}
      {tab === "history" && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <span style={{ color: ORANGE, fontWeight: 700, fontSize: 12 }}>Select Date:</span>
            <select value={selectedDate} onChange={e => setSelectedDate(e.target.value)} style={{
              background: "#0D0D15", color: ORANGE, border: `1px solid ${ORANGE}44`,
              borderRadius: 8, padding: "5px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer", outline: "none",
            }}>
              <option value="">-- Pick a date --</option>
              {dates.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            {selectedDate && <span style={{ color: "#555", fontSize: 11 }}>{dateTrades.length} trades on {selectedDate}</span>}
          </div>
          {!selectedDate && <div style={{ textAlign: "center", padding: 30, color: "#555", fontSize: 12 }}>Select a date to view that day's trades.</div>}
          {selectedDate && dateTrades.length === 0 && <div style={{ textAlign: "center", padding: 30, color: "#555", fontSize: 12 }}>No trades on {selectedDate}.</div>}
          {dateTrades.map((t, i) => <TradeCard key={i} t={t} />)}
        </>
      )}

      {/* MONTHLY REPORT */}
      {tab === "monthly" && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <span style={{ color: YELLOW, fontWeight: 700, fontSize: 12 }}>Month:</span>
            <input type="month" value={selectedMonth} onChange={e => setSelectedMonth(e.target.value)} style={{
              background: "#0D0D15", color: YELLOW, border: `1px solid ${YELLOW}44`,
              borderRadius: 8, padding: "5px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer", outline: "none",
            }} />
          </div>
          {monthlyReport && monthlyReport.stats?.total > 0 ? (
            <>
              {/* Monthly Stats */}
              <div style={{ background: "#0D0D15", borderRadius: 10, padding: "12px", border: `1px solid ${BORDER}` }}>
                <div style={{ color: YELLOW, fontWeight: 900, fontSize: 13, marginBottom: 10 }}>MONTHLY REPORT — {monthlyReport.month}</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 8, marginBottom: 8 }}>
                  {[
                    { l: "TOTAL", v: monthlyReport.stats.total, c: "#ccc" },
                    { l: "WINS", v: monthlyReport.stats.wins, c: GREEN },
                    { l: "LOSSES", v: monthlyReport.stats.losses, c: RED },
                    { l: "WIN RATE", v: `${monthlyReport.stats.winRate}%`, c: monthlyReport.stats.winRate >= 60 ? GREEN : RED },
                    { l: "TOTAL P&L", v: fmt(monthlyReport.stats.totalPnl), c: monthlyReport.stats.totalPnl >= 0 ? GREEN : RED },
                  ].map((s, i) => (
                    <div key={i} style={{ textAlign: "center", background: "#111118", borderRadius: 8, padding: "6px" }}>
                      <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>{s.l}</div>
                      <div style={{ color: s.c, fontSize: 16, fontWeight: 900 }}>{s.v}</div>
                    </div>
                  ))}
                </div>

                {/* Daily Breakdown */}
                {monthlyReport.daily && Object.keys(monthlyReport.daily).length > 0 && (
                  <div>
                    <div style={{ color: "#888", fontSize: 10, fontWeight: 700, marginBottom: 6 }}>DAILY BREAKDOWN</div>
                    <div style={{ overflowX: "auto" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
                        <thead>
                          <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                            <th style={{ padding: "4px 6px", color: "#555", textAlign: "left" }}>Date</th>
                            <th style={{ padding: "4px 6px", color: "#555", textAlign: "center" }}>Trades</th>
                            <th style={{ padding: "4px 6px", color: GREEN, textAlign: "center" }}>Wins</th>
                            <th style={{ padding: "4px 6px", color: RED, textAlign: "center" }}>Losses</th>
                            <th style={{ padding: "4px 6px", color: "#555", textAlign: "right" }}>P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(monthlyReport.daily).sort().map(([day, d]) => (
                            <tr key={day} style={{ borderBottom: `1px solid ${BORDER}33`, cursor: "pointer" }} onClick={() => { setSelectedDate(day); setTab("history"); }}>
                              <td style={{ padding: "4px 6px", color: ACCENT, fontWeight: 700 }}>{day}</td>
                              <td style={{ padding: "4px 6px", textAlign: "center", color: "#ccc" }}>{d.trades}</td>
                              <td style={{ padding: "4px 6px", textAlign: "center", color: GREEN }}>{d.wins}</td>
                              <td style={{ padding: "4px 6px", textAlign: "center", color: RED }}>{d.losses}</td>
                              <td style={{ padding: "4px 6px", textAlign: "right", color: d.pnl >= 0 ? GREEN : RED, fontWeight: 700 }}>{fmt(d.pnl)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>

              {/* Monthly Trades */}
              <div style={{ color: "#666", fontSize: 11, fontWeight: 700, marginTop: 8 }}>ALL TRADES ({monthlyReport.trades.length})</div>
              {monthlyReport.trades.map((t, i) => <TradeCard key={i} t={t} />)}
            </>
          ) : (
            <div style={{ textAlign: "center", padding: 30, color: "#555", fontSize: 12 }}>No trades in {selectedMonth}.</div>
          )}
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

function TradeCard({ t, onExit }) {
  const sc = statusColor[t.status] || (t.status === "TRAIL_EXIT" ? GREEN : t.status === "BREAKEVEN_EXIT" ? ACCENT : "#555");
  const pc = (t.pnl_rupees || 0) > 0 ? GREEN : (t.pnl_rupees || 0) < 0 ? RED : "#888";
  const ac = t.action?.includes("CE") ? GREEN : RED;
  const et = t.entry_time ? new Date(t.entry_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true, day: "2-digit", month: "short" }) : "";
  const xt = t.exit_time ? new Date(t.exit_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true }) : "";
  const profitPct = t.entry_price > 0 ? Math.round(((t.current_ltp || t.exit_price || t.entry_price) - t.entry_price) / t.entry_price * 100) : 0;
  const slLabel = t.breakeven_active ? (t.trailing_active ? `TRAIL SL` : "BREAKEVEN") : "SL";
  const slColor = t.breakeven_active ? (t.sl_price > t.entry_price ? GREEN : ACCENT) : RED;
  const statusLbl = { ...statusLabel, TRAIL_EXIT: "TRAIL EXIT \u2713", BREAKEVEN_EXIT: "BE EXIT \u2248" };

  return (
    <div style={{ background: "#111118", borderRadius: 10, padding: "12px 14px", border: `1px solid ${sc}33` }}>
      {/* Row 1: Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <span style={{ color: "#fff", fontWeight: 900, fontSize: 14 }}>{t.idx}</span>
          <span style={{ background: ac + "22", color: ac, padding: "3px 10px", borderRadius: 4, fontSize: 12, fontWeight: 900 }}>{t.action}</span>
          <span style={{ color: "#ccc", fontWeight: 700 }}>{t.strike}</span>
          <span style={{ background: sc + "22", color: sc, padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{statusLbl[t.status] || t.status}</span>
          {t.status === "OPEN" && t.breakeven_active ? (
            <span style={{ background: ACCENT + "22", color: ACCENT, padding: "2px 6px", borderRadius: 3, fontSize: 9, fontWeight: 700 }}>
              {t.trail_level === "TRAIL_75" ? "TRAIL 75%" : t.trail_level === "TRAIL_60" ? "TRAIL 60%" : "BREAKEVEN"}
            </span>
          ) : null}
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: pc, fontWeight: 900, fontSize: 18 }}>{"\u20B9"}{Math.round(t.pnl_rupees || 0).toLocaleString("en-IN")}</div>
          <div style={{ color: pc, fontSize: 10 }}>{(t.pnl_pts || 0) > 0 ? "+" : ""}{(t.pnl_pts || 0).toFixed(1)} pts ({profitPct > 0 ? "+" : ""}{profitPct}%)</div>
        </div>
      </div>

      {/* Row 2: Price Levels */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 5, marginBottom: 6 }}>
        {[
          { l: "ENTRY", v: `\u20B9${t.entry_price}`, c: "#ccc" },
          { l: t.status === "OPEN" ? "CURRENT" : "EXIT", v: `\u20B9${((t.status === "OPEN" ? t.current_ltp : t.exit_price) || 0).toFixed?.(1) || 0}`, c: t.status === "OPEN" ? ACCENT : sc },
          { l: slLabel, v: `\u20B9${t.sl_price}`, c: slColor },
          { l: "T1", v: `\u20B9${t.t1_price}`, c: GREEN },
          { l: "T2", v: `\u20B9${t.t2_price}`, c: GREEN },
          { l: "PEAK", v: `\u20B9${(t.peak_ltp || t.current_ltp || t.entry_price).toFixed?.(1) || 0}`, c: YELLOW },
        ].map((s, i) => (
          <div key={i} style={{ textAlign: "center" }}>
            <div style={{ color: "#555", fontSize: 8, fontWeight: 700 }}>{s.l}</div>
            <div style={{ color: s.c, fontSize: 11, fontWeight: 700 }}>{s.v}</div>
          </div>
        ))}
      </div>

      {/* Row 3: Progress bar (entry to T2) */}
      {t.status === "OPEN" && t.entry_price > 0 && (
        <div style={{ marginBottom: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 8, color: "#555", marginBottom: 2 }}>
            <span>SL {"\u20B9"}{t.sl_price}</span>
            <span>Entry {"\u20B9"}{t.entry_price}</span>
            <span>T1 {"\u20B9"}{t.t1_price}</span>
            <span>T2 {"\u20B9"}{t.t2_price}</span>
          </div>
          <div style={{ display: "flex", height: 6, borderRadius: 3, overflow: "hidden", background: "#1a1a25", position: "relative" }}>
            {(() => {
              const range = t.t2_price - t.sl_price;
              const pos = range > 0 ? Math.min(100, Math.max(0, ((t.current_ltp || t.entry_price) - t.sl_price) / range * 100)) : 50;
              const entryPos = range > 0 ? ((t.entry_price - t.sl_price) / range * 100) : 50;
              return (
                <>
                  <div style={{ width: `${entryPos}%`, background: RED + "44" }} />
                  <div style={{ width: `${Math.max(0, pos - entryPos)}%`, background: pos > entryPos ? GREEN : RED, transition: "width 0.3s" }} />
                  <div style={{ position: "absolute", left: `${pos}%`, top: -1, width: 8, height: 8, borderRadius: "50%", background: ACCENT, transform: "translateX(-50%)", border: "1px solid #fff" }} />
                </>
              );
            })()}
          </div>
        </div>
      )}

      {/* Row 4: Info */}
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#666" }}>
        <span>{t.lots}L × {t.lot_size} = {t.qty} qty</span>
        <span>{et}{xt ? ` \u2192 ${xt}` : ""}</span>
        <span style={{ color: t.probability >= 70 ? GREEN : YELLOW }}>Prob: {t.probability}%</span>
      </div>

      {/* Breakeven/Trail details */}
      {t.status === "OPEN" && t.breakeven_active ? (
        <div style={{ marginTop: 6, padding: "4px 10px", background: ACCENT + "11", borderRadius: 6, color: ACCENT, fontSize: 10 }}>
          {t.trailing_active
            ? `Trailing SL active at \u20B9${t.sl_price} (locking ${t.sl_price > t.entry_price ? Math.round((t.sl_price - t.entry_price) * t.qty) : 0} profit). Peak: \u20B9${(t.peak_ltp || 0).toFixed?.(1)}`
            : `Breakeven active \u2014 SL moved to entry \u20B9${t.entry_price}. Zero loss guaranteed.`
          }
        </div>
      ) : null}

      {/* Exit reason */}
      {t.exit_reason && (
        <div style={{ marginTop: 6, padding: "6px 10px", background: sc + "11", borderRadius: 6, color: sc, fontSize: 11 }}>{t.exit_reason}</div>
      )}

      {/* Alert */}
      {t.alerts && t.status === "OPEN" && (
        <div style={{ marginTop: 6, padding: "6px 10px", background: RED + "15", borderRadius: 6, color: RED, fontSize: 11, animation: "blink 1s infinite" }}>
          {"\u26A0"} {t.alerts}
        </div>
      )}

      {/* Stop Hunt */}
      {t.status === "STOP_HUNTED" && t.reversal_price > 0 && (
        <div style={{ marginTop: 6, padding: "6px 10px", background: PURPLE + "11", borderRadius: 6, color: PURPLE, fontSize: 11 }}>
          Reversal: price recovered to {"\u20B9"}{(t.reversal_price || 0).toFixed?.(1) || t.reversal_price} after institutional SL flush
        </div>
      )}

      {/* MANUAL EXIT BUTTON */}
      {t.status === "OPEN" && onExit && (
        <button onClick={() => onExit(t.id)} style={{
          marginTop: 8, width: "100%", padding: "8px 0",
          background: RED + "22", color: RED, border: `1px solid ${RED}44`,
          borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: "pointer",
          transition: "all 0.15s",
        }}
        onMouseOver={e => { e.target.style.background = RED; e.target.style.color = "#fff"; }}
        onMouseOut={e => { e.target.style.background = RED + "22"; e.target.style.color = RED; }}
        >
          EXIT NOW
        </button>
      )}
    </div>
  );
}
