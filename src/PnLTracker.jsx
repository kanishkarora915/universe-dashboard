import { useState, useEffect, useCallback } from "react";
import CapitalTracker from "./components/CapitalTracker";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const BORDER = "#1E1E2E";

// ── PnL PDF Export ──
// ════════════════════════════════════════════════════════════
// PROFESSIONAL PDF EXPORT — Daily / Weekly / Monthly
// Includes: Entry/Exit/SL times, Quantity, WHY entry/exit/SL,
//          Date+Time, Total PnL, full trade audit trail.
// ════════════════════════════════════════════════════════════

function exportPnLPDF(title, statsData, trades, dailyBreakdown, period = "DAILY") {
  const now = new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
  const s = statsData || {};
  const safeTrades = Array.isArray(trades) ? trades : [];
  const fmtR = (n) => `${(n || 0) >= 0 ? "+" : ""}${Math.round(n || 0).toLocaleString("en-IN")}`;
  const fmtTime = (iso) => {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
      });
    } catch { return iso; }
  };

  // Compute aggregates from actual trade data
  const closedTrades = safeTrades.filter(t => t.status !== "OPEN");
  const winTrades = closedTrades.filter(t => t.status === "T1_HIT" || t.status === "T2_HIT" || t.status === "TRAIL_EXIT");
  const lossTrades = closedTrades.filter(t => t.status === "SL_HIT" || t.status === "REVERSAL_EXIT");
  const beTrades = closedTrades.filter(t => t.status === "BREAKEVEN_EXIT");
  const totalPnl = closedTrades.reduce((sum, t) => sum + (t.pnl_rupees || 0), 0);
  const totalQty = closedTrades.reduce((sum, t) => sum + (t.qty || 0), 0);
  const totalCapital = closedTrades.reduce((sum, t) => sum + ((t.entry_price || 0) * (t.qty || 0)), 0);
  const winPnl = winTrades.reduce((sum, t) => sum + (t.pnl_rupees || 0), 0);
  const lossPnl = lossTrades.reduce((sum, t) => sum + (t.pnl_rupees || 0), 0);
  const avgWin = winTrades.length ? winPnl / winTrades.length : 0;
  const avgLoss = lossTrades.length ? lossPnl / lossTrades.length : 0;
  const winRate = closedTrades.length ? (winTrades.length / closedTrades.length * 100) : 0;
  const bestTrade = closedTrades.reduce((best, t) => (t.pnl_rupees || 0) > (best?.pnl_rupees || -Infinity) ? t : best, null);
  const worstTrade = closedTrades.reduce((worst, t) => (t.pnl_rupees || 0) < (worst?.pnl_rupees || Infinity) ? t : worst, null);
  const roi = totalCapital > 0 ? (totalPnl / totalCapital * 100) : 0;

  let html = `
    <div style="border-bottom:3px solid #1a8a2e;padding-bottom:12px;margin-bottom:16px">
      <div style="display:flex;justify-content:space-between;align-items:flex-end">
        <div>
          <div style="font-size:11px;color:#888;letter-spacing:2px;font-weight:700">UNIVERSE TRADING SYSTEM</div>
          <h1 style="margin:4px 0 0 0;font-size:24px;color:#111">${title}</h1>
          <div style="font-size:10px;color:#888;margin-top:4px">${period} REPORT · Generated ${now} IST</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:9px;color:#888;letter-spacing:1px">TOTAL P&L</div>
          <div style="font-size:32px;font-weight:900;color:${totalPnl >= 0 ? '#1a8a2e' : '#cc2020'};line-height:1">
            ₹${fmtR(totalPnl)}
          </div>
          <div style="font-size:11px;color:${roi >= 0 ? '#1a8a2e' : '#cc2020'};font-weight:600">
            ROI: ${roi >= 0 ? '+' : ''}${roi.toFixed(2)}% on ₹${fmtR(totalCapital)} deployed
          </div>
        </div>
      </div>
    </div>
  `;

  // ─── KEY METRICS GRID ───
  html += `<h2 style="font-size:13px;color:#333;margin-top:0;border-bottom:1px solid #eee;padding-bottom:4px">📊 Performance Summary</h2>
  <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:18px">
    <div style="background:#f8f8f8;border-radius:8px;padding:10px;text-align:center">
      <div style="font-size:8px;color:#888;text-transform:uppercase;letter-spacing:0.5px">Total Trades</div>
      <div style="font-size:20px;font-weight:900;color:#111">${closedTrades.length}</div>
    </div>
    <div style="background:#e8ffe8;border-radius:8px;padding:10px;text-align:center">
      <div style="font-size:8px;color:#1a8a2e;text-transform:uppercase">Wins</div>
      <div style="font-size:20px;font-weight:900;color:#1a8a2e">${winTrades.length}</div>
    </div>
    <div style="background:#ffe8e8;border-radius:8px;padding:10px;text-align:center">
      <div style="font-size:8px;color:#cc2020;text-transform:uppercase">Losses</div>
      <div style="font-size:20px;font-weight:900;color:#cc2020">${lossTrades.length}</div>
    </div>
    <div style="background:#fff5e0;border-radius:8px;padding:10px;text-align:center">
      <div style="font-size:8px;color:#cc7a00;text-transform:uppercase">Breakevens</div>
      <div style="font-size:20px;font-weight:900;color:#cc7a00">${beTrades.length}</div>
    </div>
    <div style="background:#f0f0ff;border-radius:8px;padding:10px;text-align:center">
      <div style="font-size:8px;color:#3333aa;text-transform:uppercase">Win Rate</div>
      <div style="font-size:20px;font-weight:900;color:${winRate >= 60 ? '#1a8a2e' : winRate >= 40 ? '#cc7a00' : '#cc2020'}">${winRate.toFixed(1)}%</div>
    </div>
    <div style="background:#f8f8f8;border-radius:8px;padding:10px;text-align:center">
      <div style="font-size:8px;color:#888;text-transform:uppercase">Total Qty</div>
      <div style="font-size:20px;font-weight:900;color:#111">${totalQty.toLocaleString("en-IN")}</div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:18px">
    <div style="background:#f8f8f8;border-radius:8px;padding:10px">
      <div style="font-size:8px;color:#888;text-transform:uppercase">Avg Win</div>
      <div style="font-size:14px;font-weight:700;color:#1a8a2e">₹${fmtR(avgWin)}</div>
    </div>
    <div style="background:#f8f8f8;border-radius:8px;padding:10px">
      <div style="font-size:8px;color:#888;text-transform:uppercase">Avg Loss</div>
      <div style="font-size:14px;font-weight:700;color:#cc2020">₹${fmtR(avgLoss)}</div>
    </div>
    <div style="background:#f8f8f8;border-radius:8px;padding:10px">
      <div style="font-size:8px;color:#888;text-transform:uppercase">Best Trade</div>
      <div style="font-size:14px;font-weight:700;color:#1a8a2e">₹${fmtR(bestTrade?.pnl_rupees || 0)}</div>
      <div style="font-size:9px;color:#888">${bestTrade ? `${bestTrade.idx} ${bestTrade.action} ${bestTrade.strike}` : "—"}</div>
    </div>
    <div style="background:#f8f8f8;border-radius:8px;padding:10px">
      <div style="font-size:8px;color:#888;text-transform:uppercase">Worst Trade</div>
      <div style="font-size:14px;font-weight:700;color:#cc2020">₹${fmtR(worstTrade?.pnl_rupees || 0)}</div>
      <div style="font-size:9px;color:#888">${worstTrade ? `${worstTrade.idx} ${worstTrade.action} ${worstTrade.strike}` : "—"}</div>
    </div>
  </div>`;

  // ─── DAILY BREAKDOWN (for weekly/monthly) ───
  if (dailyBreakdown && Object.keys(dailyBreakdown).length > 0) {
    html += `<h2 style="font-size:13px;color:#333;margin-top:18px;border-bottom:1px solid #eee;padding-bottom:4px">📅 Daily Breakdown</h2>
    <table style="margin-bottom:16px">
      <thead><tr><th>Date</th><th style="text-align:right">Trades</th><th style="text-align:right">Wins</th><th style="text-align:right">Losses</th><th style="text-align:right">Win %</th><th style="text-align:right">P&L</th></tr></thead>
      <tbody>`;
    for (const [day, d] of Object.entries(dailyBreakdown).sort().reverse()) {
      const cls = d.pnl >= 0 ? 'class="pos"' : 'class="neg"';
      const wr = d.trades ? Math.round((d.wins / d.trades) * 100) : 0;
      html += `<tr>
        <td><strong>${day}</strong></td>
        <td style="text-align:right">${d.trades}</td>
        <td style="text-align:right" class="pos">${d.wins}</td>
        <td style="text-align:right" class="neg">${d.losses}</td>
        <td style="text-align:right">${wr}%</td>
        <td style="text-align:right;font-weight:700" ${cls}>₹${fmtR(d.pnl)}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }

  // ─── INDIVIDUAL TRADE DETAILS ───
  if (closedTrades.length > 0) {
    html += `<h2 style="font-size:13px;color:#333;margin-top:18px;border-bottom:1px solid #eee;padding-bottom:4px">📋 Trade-by-Trade Audit Trail (${closedTrades.length} trades)</h2>`;

    for (let i = 0; i < closedTrades.length; i++) {
      const t = closedTrades[i];
      const isWin = ["T1_HIT", "T2_HIT", "TRAIL_EXIT"].includes(t.status);
      const isLoss = ["SL_HIT", "REVERSAL_EXIT"].includes(t.status);
      const isBE = t.status === "BREAKEVEN_EXIT";
      const isManual = t.status === "MANUAL_EXIT";
      const borderColor = isWin ? "#1a8a2e" : isLoss ? "#cc2020" : isBE ? "#cc7a00" : isManual ? "#7c3aed" : "#888";
      const bgColor = isWin ? "#f0fff0" : isLoss ? "#fff0f0" : isBE ? "#fffbf0" : isManual ? "#f8f0ff" : "#fafafa";

      // Hold duration
      let holdStr = "—";
      if (t.entry_time && t.exit_time) {
        try {
          const diff = (new Date(t.exit_time) - new Date(t.entry_time)) / 1000;
          const mins = Math.floor(diff / 60), secs = Math.floor(diff % 60);
          holdStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
        } catch {}
      }

      const pnlPct = t.entry_price > 0 ? ((t.exit_price || t.entry_price) - t.entry_price) / t.entry_price * 100 : 0;

      html += `
      <div style="border:2px solid ${borderColor};border-radius:8px;padding:14px;margin-bottom:12px;background:${bgColor};page-break-inside:avoid">
        <!-- Header row -->
        <div style="display:flex;justify-content:space-between;margin-bottom:10px;border-bottom:1px solid ${borderColor}33;padding-bottom:8px">
          <div>
            <div style="font-size:9px;color:#888;letter-spacing:0.5px">TRADE #${t.id || i+1}</div>
            <div style="font-size:16px;font-weight:900;color:#111;margin-top:2px">
              <span>${t.idx}</span>
              <span style="color:${t.action?.includes('CE') ? '#1a8a2e' : '#cc2020'};margin-left:8px">${t.action}</span>
              <span style="margin-left:8px">${t.strike}</span>
              <span style="background:${borderColor};color:#fff;padding:3px 10px;border-radius:4px;font-size:10px;font-weight:700;margin-left:8px">${t.status}</span>
            </div>
          </div>
          <div style="text-align:right">
            <div style="font-size:9px;color:#888">P&L</div>
            <div style="font-size:22px;font-weight:900;color:${(t.pnl_rupees || 0) >= 0 ? '#1a8a2e' : '#cc2020'};line-height:1">
              ₹${fmtR(t.pnl_rupees)}
            </div>
            <div style="font-size:10px;color:${pnlPct >= 0 ? '#1a8a2e' : '#cc2020'};font-weight:600">
              ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}% · ${(t.pnl_pts || 0) >= 0 ? '+' : ''}${(t.pnl_pts || 0).toFixed(1)} pts
            </div>
          </div>
        </div>

        <!-- Times grid -->
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px;font-size:10px">
          <div style="background:#fff;border:1px solid #ddd;border-radius:4px;padding:6px 8px">
            <div style="font-size:8px;color:#888;letter-spacing:0.5px">⏱ ENTRY TIME</div>
            <div style="color:#111;font-weight:600">${fmtTime(t.entry_time)}</div>
          </div>
          <div style="background:#fff;border:1px solid #ddd;border-radius:4px;padding:6px 8px">
            <div style="font-size:8px;color:#888;letter-spacing:0.5px">⏱ EXIT TIME</div>
            <div style="color:#111;font-weight:600">${fmtTime(t.exit_time)}</div>
          </div>
          <div style="background:#fff;border:1px solid #ddd;border-radius:4px;padding:6px 8px">
            <div style="font-size:8px;color:#888;letter-spacing:0.5px">⏳ HELD FOR</div>
            <div style="color:#111;font-weight:600">${holdStr}</div>
          </div>
        </div>

        <!-- Price levels grid -->
        <table style="margin-bottom:10px;font-size:10px">
          <thead><tr style="background:#fff">
            <th>ENTRY</th><th>EXIT</th><th>SL</th><th>T1</th><th>T2</th><th>QUANTITY</th><th>CAPITAL USED</th>
          </tr></thead>
          <tbody><tr>
            <td>₹${(t.entry_price || 0).toFixed(2)}</td>
            <td><strong>₹${(t.exit_price || t.current_ltp || 0).toFixed(2)}</strong></td>
            <td style="color:#cc2020">₹${t.sl_price || "—"}</td>
            <td style="color:#1a8a2e">₹${t.t1_price || "—"}</td>
            <td style="color:#1a8a2e">₹${t.t2_price || "—"}</td>
            <td><strong>${(t.lots || 0)}L × ${t.lot_size || 0} = ${(t.qty || 0).toLocaleString("en-IN")}</strong></td>
            <td><strong>₹${fmtR((t.entry_price || 0) * (t.qty || 0))}</strong></td>
          </tr></tbody>
        </table>

        <!-- Why Entry -->
        <div style="background:#fff;border-left:3px solid #0a84ff;padding:8px 12px;margin-bottom:6px;border-radius:4px">
          <div style="font-size:9px;color:#0a84ff;font-weight:700;letter-spacing:0.5px;margin-bottom:3px">🎯 WHY ENTRY (engine logic)</div>
          <div style="font-size:11px;color:#333;line-height:1.4">
            ${t.entry_reasoning || t.reason || `Verdict-based entry. Probability: ${t.probability || 0}%. Source: ${t.source || 'verdict'}.`}
          </div>
          ${t.entry_bull_pct || t.entry_bear_pct ? `
            <div style="font-size:10px;color:#888;margin-top:4px">
              Bull: <strong style="color:#1a8a2e">${Math.round(t.entry_bull_pct || 0)}%</strong>
              · Bear: <strong style="color:#cc2020">${Math.round(t.entry_bear_pct || 0)}%</strong>
              ${t.entry_spot ? ` · Spot @ entry: <strong>${t.entry_spot}</strong>` : ''}
            </div>
          ` : ''}
        </div>

        <!-- Why Exit -->
        <div style="background:#fff;border-left:3px solid ${borderColor};padding:8px 12px;margin-bottom:6px;border-radius:4px">
          <div style="font-size:9px;color:${borderColor};font-weight:700;letter-spacing:0.5px;margin-bottom:3px">🚪 WHY EXIT</div>
          <div style="font-size:11px;color:#333;line-height:1.4">
            ${t.exit_reason || `Closed at ₹${t.exit_price} via ${t.status}.`}
          </div>
        </div>

        <!-- Why SL (if SL_HIT) -->
        ${(isLoss || t.sl_reason) ? `
          <div style="background:#fff;border-left:3px solid #cc2020;padding:8px 12px;border-radius:4px">
            <div style="font-size:9px;color:#cc2020;font-weight:700;letter-spacing:0.5px;margin-bottom:3px">🛑 WHY STOPLOSS</div>
            <div style="font-size:11px;color:#333;line-height:1.4">
              ${t.sl_reason || `Stoploss at ₹${t.sl_price} hit. Original entry ₹${t.entry_price}. Loss = ${(((t.exit_price || t.sl_price) - t.entry_price) / t.entry_price * 100).toFixed(1)}%.`}
            </div>
            ${t.sl_hit_time ? `
              <div style="font-size:10px;color:#888;margin-top:4px">
                ⏱ SL hit time: <strong>${fmtTime(t.sl_hit_time)}</strong>
              </div>
            ` : ''}
            ${t.smart_sl_stage !== null && t.smart_sl_stage !== undefined ? `
              <div style="font-size:10px;color:#888;margin-top:2px">
                Smart SL stage: <strong>${t.smart_sl_stage}</strong> · Active SL: <strong>₹${t.smart_sl_value || t.sl_price}</strong>
              </div>
            ` : ''}
          </div>
        ` : ''}
      </div>`;
    }
  }

  // ─── FOOTER ───
  html += `
    <hr style="border:none;border-top:2px solid #ddd;margin:20px 0">
    <div style="text-align:center;font-size:10px;color:#aaa;line-height:1.6">
      <strong>UNIVERSE PnL ${period} REPORT</strong> · Generated ${now} IST<br>
      ${closedTrades.length} closed trades · Total deployed capital: ₹${fmtR(totalCapital)} · Net P&L: ₹${fmtR(totalPnl)} (${roi.toFixed(2)}% ROI)<br>
      <em>This is a paper-trading audit log. All times in IST.</em>
    </div>
  `;

  // Open print window
  const win = window.open("", "_blank", "width=1100,height=800");
  win.document.write(`<html><head><title>${title}</title>
    <style>
      @page { margin: 15mm; size: A4 portrait; }
      body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px; margin: 0; color: #111; max-width: 900px; }
      h1 { font-size: 20px; margin-bottom: 4px; }
      h2 { font-size: 14px; color: #555; margin-top: 18px; }
      table { width: 100%; border-collapse: collapse; font-size: 11px; margin-bottom: 16px; }
      th { background: #f5f5f5; padding: 6px 8px; text-align: left; font-weight: 700; border-bottom: 2px solid #ddd; }
      td { padding: 5px 8px; border-bottom: 1px solid #eee; }
      .pos { color: #1a8a2e; font-weight: 600; }
      .neg { color: #cc2020; font-weight: 600; }
      @media print { body { padding: 0; } div { page-break-inside: avoid; } }
    </style>
  </head><body>${html}</body></html>`);
  win.document.close();
  setTimeout(() => win.print(), 800);
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

  useEffect(() => {
    refresh();
    const iv = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 15000);
    return () => clearInterval(iv);
  }, [refresh]);

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

  const fmt = (n) => `${"₹"}${Math.round(n || 0).toLocaleString("en-IN")}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Independent Capital Tracker for MAIN P&L — base/running/profit-bank logic */}
      <CapitalTracker system="MAIN" />

      {/* EXPORT BUTTONS */}
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        <button onClick={() => {
          const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
          // Main P&L trades only — scalper has its own export in Scalper tab
          fetch(`/api/trades/date/${today}`)
            .then(r => r.json())
            .then(trades => {
              const safeT = Array.isArray(trades) ? trades : [];
              exportPnLPDF(`Main P&L Daily Report — ${today}`, null, safeT, null, "DAILY");
            })
            .catch(() => {});
        }} style={{ background: ORANGE + "22", color: ORANGE, border: `1px solid ${ORANGE}44`, borderRadius: 8, padding: "5px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
          📄 Export Daily
        </button>
        <button onClick={() => {
          // Last 7 days — main P&L only
          fetch("/api/trades/closed?days=7")
            .then(r => r.json())
            .then(mainT => {
              const trades = Array.isArray(mainT) ? mainT : [];
              const daily = {};
              trades.forEach(t => {
                if (t.status === "OPEN") return;
                const d = (t.entry_time || "").slice(0, 10);
                if (!d) return;
                if (!daily[d]) daily[d] = { trades: 0, wins: 0, losses: 0, pnl: 0 };
                daily[d].trades++;
                if (["T1_HIT", "T2_HIT", "TRAIL_EXIT"].includes(t.status)) daily[d].wins++;
                else if (["SL_HIT", "REVERSAL_EXIT"].includes(t.status)) daily[d].losses++;
                daily[d].pnl += (t.pnl_rupees || 0);
              });
              const today = new Date().toLocaleDateString("en-IN");
              exportPnLPDF(`Main P&L Weekly Report (Last 7 Days as of ${today})`, null, trades, daily, "WEEKLY");
            })
            .catch(() => {});
        }} style={{ background: ACCENT + "22", color: ACCENT, border: `1px solid ${ACCENT}44`, borderRadius: 8, padding: "5px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
          📄 Export Weekly
        </button>
        <button onClick={() => {
          const [y, m] = selectedMonth.split("-");
          // Main P&L trades only for the selected month
          fetch(`/api/trades/monthly/${y}/${m}`)
            .then(r => r.json())
            .then(mainReport => {
              const trades = Array.isArray(mainReport?.trades) ? mainReport.trades : [];
              const daily = mainReport?.daily || {};
              exportPnLPDF(`Main P&L Monthly Report — ${selectedMonth}`, null, trades, daily, "MONTHLY");
            })
            .catch(() => {});
        }} style={{ background: YELLOW + "22", color: YELLOW, border: `1px solid ${YELLOW}44`, borderRadius: 8, padding: "5px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
          📄 Export Monthly
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
          <div style={{ color: RED, fontWeight: 900, fontSize: 13, marginBottom: 6 }}>{"⚠"} POSITION ALERT — ACTION NEEDED</div>
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
  const statusLbl = { ...statusLabel, TRAIL_EXIT: "TRAIL EXIT ✓", BREAKEVEN_EXIT: "BE EXIT \u2248" };

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
          <div style={{ color: pc, fontWeight: 900, fontSize: 18 }}>{"₹"}{Math.round(t.pnl_rupees || 0).toLocaleString("en-IN")}</div>
          <div style={{ color: pc, fontSize: 10 }}>{(t.pnl_pts || 0) > 0 ? "+" : ""}{(t.pnl_pts || 0).toFixed(1)} pts ({profitPct > 0 ? "+" : ""}{profitPct}%)</div>
        </div>
      </div>

      {/* Row 2: Price Levels */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 5, marginBottom: 6 }}>
        {[
          { l: "ENTRY", v: `₹${t.entry_price}`, c: "#ccc" },
          { l: t.status === "OPEN" ? "CURRENT" : "EXIT", v: `₹${((t.status === "OPEN" ? t.current_ltp : t.exit_price) || 0).toFixed?.(1) || 0}`, c: t.status === "OPEN" ? ACCENT : sc },
          { l: slLabel, v: `₹${t.sl_price}`, c: slColor },
          { l: "T1", v: `₹${t.t1_price}`, c: GREEN },
          { l: "T2", v: `₹${t.t2_price}`, c: GREEN },
          { l: "PEAK", v: `₹${(t.peak_ltp || t.current_ltp || t.entry_price).toFixed?.(1) || 0}`, c: YELLOW },
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
            <span>SL {"₹"}{t.sl_price}</span>
            <span>Entry {"₹"}{t.entry_price}</span>
            <span>T1 {"₹"}{t.t1_price}</span>
            <span>T2 {"₹"}{t.t2_price}</span>
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
        <span>{et}{xt ? ` → ${xt}` : ""}</span>
        <span style={{ color: t.probability >= 70 ? GREEN : YELLOW }}>Prob: {t.probability}%</span>
      </div>

      {/* Breakeven/Trail details */}
      {t.status === "OPEN" && t.breakeven_active ? (
        <div style={{ marginTop: 6, padding: "4px 10px", background: ACCENT + "11", borderRadius: 6, color: ACCENT, fontSize: 10 }}>
          {t.trailing_active
            ? `Trailing SL active at ₹${t.sl_price} (locking ${t.sl_price > t.entry_price ? Math.round((t.sl_price - t.entry_price) * t.qty) : 0} profit). Peak: ₹${(t.peak_ltp || 0).toFixed?.(1)}`
            : `Breakeven active \u2014 SL moved to entry ₹${t.entry_price}. Zero loss guaranteed.`
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
          {"⚠"} {t.alerts}
        </div>
      )}

      {/* Stop Hunt */}
      {t.status === "STOP_HUNTED" && t.reversal_price > 0 && (
        <div style={{ marginTop: 6, padding: "6px 10px", background: PURPLE + "11", borderRadius: 6, color: PURPLE, fontSize: 11 }}>
          Reversal: price recovered to {"₹"}{(t.reversal_price || 0).toFixed?.(1) || t.reversal_price} after institutional SL flush
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
