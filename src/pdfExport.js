/**
 * PDF Export utility — uses window.print() with styled printable HTML.
 * No external library needed.
 */

function openPrintWindow(title, htmlContent) {
  const win = window.open("", "_blank", "width=900,height=700");
  win.document.write(`
    <html><head><title>${title}</title>
    <style>
      body { font-family: -apple-system, sans-serif; background: #fff; color: #111; padding: 24px; margin: 0; }
      h1 { font-size: 20px; margin-bottom: 4px; }
      h2 { font-size: 15px; color: #555; margin-top: 24px; margin-bottom: 8px; border-bottom: 2px solid #eee; padding-bottom: 4px; }
      h3 { font-size: 13px; color: #333; margin-top: 16px; margin-bottom: 6px; }
      .meta { font-size: 11px; color: #888; margin-bottom: 16px; }
      table { width: 100%; border-collapse: collapse; font-size: 11px; margin-bottom: 16px; }
      th { background: #f5f5f5; padding: 6px 8px; text-align: left; font-weight: 700; border-bottom: 2px solid #ddd; }
      td { padding: 5px 8px; border-bottom: 1px solid #eee; }
      .pos { color: #1a8a2e; font-weight: 600; }
      .neg { color: #cc2020; font-weight: 600; }
      .atm { background: #e8f0ff; font-weight: 700; }
      .summary { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
      .sum-card { background: #f8f8f8; border-radius: 8px; padding: 10px 14px; text-align: center; flex: 1; min-width: 100px; border: 1px solid #eee; }
      .sum-label { font-size: 9px; color: #888; text-transform: uppercase; letter-spacing: 1px; }
      .sum-value { font-size: 16px; font-weight: 700; margin-top: 4px; }
      .signal-card { border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin-bottom: 10px; }
      .signal-header { display: flex; justify-content: space-between; margin-bottom: 8px; }
      .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 700; }
      .pass { color: #1a8a2e; } .warn { color: #cc8800; } .fail { color: #999; }
      .rec-card { border: 1px solid #4a90d9; border-radius: 8px; padding: 12px; margin-bottom: 10px; background: #f0f6ff; }
      .reason-item { margin-bottom: 4px; font-size: 11px; color: #333; padding-left: 16px; text-indent: -12px; }
      .alert-row { padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: 11px; display: flex; justify-content: space-between; }
      .section-divider { border: none; border-top: 2px solid #ddd; margin: 20px 0; }
      .bias-bullish { color: #1a8a2e; font-weight: 900; font-size: 14px; }
      .bias-bearish { color: #cc2020; font-weight: 900; font-size: 14px; }
      .bias-neutral { color: #cc8800; font-weight: 900; font-size: 14px; }
      .level-box { display: inline-block; background: #f0f0f0; border-radius: 4px; padding: 4px 10px; margin: 2px; font-weight: 700; font-size: 12px; }
      .page-break { page-break-before: always; }
      @media print { body { padding: 12px; } .page-break { page-break-before: always; } }
    </style>
    </head><body>${htmlContent}</body></html>
  `);
  win.document.close();
  setTimeout(() => { win.print(); }, 500);
}

const fmt = (n) => n ? Math.round(n).toLocaleString("en-IN") : "0";
const fmtL = (n) => n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0";
const fmtPrem = (n) => n > 0 ? `+${n.toFixed(1)}` : n < 0 ? `${n.toFixed(1)}` : "0";

// ── EXISTING EXPORTS (kept for backward compatibility) ────────────────

export function exportOIToPDF(oiData, indexLabel = "ALL") {
  let html = `<h1>UNIVERSE - OI Change Report</h1>`;
  html += `<div class="meta">Generated: ${new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST</div>`;

  for (const key of ["nifty", "banknifty"]) {
    const d = oiData[key];
    if (!d) continue;
    if (indexLabel !== "ALL" && indexLabel !== key.toUpperCase()) continue;

    const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
    html += `<h2>${label} (LTP: ${fmt(d.ltp)} | ATM: ${fmt(d.atm)} | PCR: ${d.pcr})</h2>`;

    html += `<div class="summary">
      <div class="sum-card"><div class="sum-label">Total CE OI</div><div class="sum-value">${fmtL(d.totalCEOI)}</div></div>
      <div class="sum-card"><div class="sum-label">Total PE OI</div><div class="sum-value">${fmtL(d.totalPEOI)}</div></div>
      <div class="sum-card"><div class="sum-label">+ OI Change</div><div class="sum-value pos">+${fmtL(d.ceOIChangePos + d.peOIChangePos)}</div></div>
      <div class="sum-card"><div class="sum-label">- OI Change</div><div class="sum-value neg">${fmtL(d.ceOIChangeNeg + d.peOIChangeNeg)}</div></div>
      <div class="sum-card"><div class="sum-label">Net Change</div><div class="sum-value">${fmtL(d.netOIChange)}</div></div>
    </div>`;

    html += `<table><tr><th>Strike</th><th>CE OI</th><th>CE Change</th><th>CE LTP</th><th>PE LTP</th><th>PE Change</th><th>PE OI</th></tr>`;
    for (const s of d.strikes || []) {
      const cls = s.isATM ? ' class="atm"' : "";
      const ceChg = s.ceOIChange > 0 ? `<span class="pos">+${fmtL(s.ceOIChange)}</span>` : s.ceOIChange < 0 ? `<span class="neg">${fmtL(s.ceOIChange)}</span>` : "-";
      const peChg = s.peOIChange > 0 ? `<span class="pos">+${fmtL(s.peOIChange)}</span>` : s.peOIChange < 0 ? `<span class="neg">${fmtL(s.peOIChange)}</span>` : "-";
      html += `<tr${cls}><td>${fmt(s.strike)}${s.isATM ? " (ATM)" : ""}</td><td>${fmtL(s.ceOI)}</td><td>${ceChg}</td><td>${s.ceLTP?.toFixed(1) || "-"}</td><td>${s.peLTP?.toFixed(1) || "-"}</td><td>${peChg}</td><td>${fmtL(s.peOI)}</td></tr>`;
    }
    html += `</table>`;
  }

  openPrintWindow("UNIVERSE OI Change Report", html);
}

export function exportSignalsToPDF(signals) {
  let html = `<h1>UNIVERSE - Trading Signals Report</h1>`;
  html += `<div class="meta">Generated: ${new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST</div>`;

  if (!signals || signals.length === 0) {
    html += `<p>No active signals at this time.</p>`;
    openPrintWindow("UNIVERSE Signals Report", html);
    return;
  }

  for (const s of signals) {
    html += `<div class="signal-card">`;
    html += `<div class="signal-header">
      <div><strong>${s.instrument}</strong> <span class="badge" style="background:${s.type?.includes("PUT") ? "#ffe0e0" : "#e0ffe0"}">${s.type}</span> <span class="badge" style="background:#fff3cd">${s.status}</span></div>
      <div style="font-size:18px;font-weight:900;color:#7c3aed">${s.score}/${s.maxScore}</div>
    </div>`;
    html += `<div style="font-size:11px;color:#888;margin-bottom:8px">${s.strike} | ${s.expiry} | ${s.time}</div>`;
    html += `<table style="width:auto"><tr><td><strong>Entry:</strong> ${s.entry}</td><td><strong>T1:</strong> ${s.t1}</td><td><strong>T2:</strong> ${s.t2}</td><td><strong>SL:</strong> ${s.sl}</td><td><strong>R:R</strong> ${s.rr}</td></tr></table>`;
    html += `<div style="margin-top:8px;font-size:11px">`;
    for (const r of (s.reasoning || [])) {
      const icon = r.pass === true ? "+" : r.pass === "warn" ? "!" : "-";
      const cls = r.pass === true ? "pass" : r.pass === "warn" ? "warn" : "fail";
      html += `<div class="${cls}" style="margin-bottom:3px">[${icon}] ${r.text}</div>`;
    }
    html += `</div></div>`;
  }

  openPrintWindow("UNIVERSE Signals Report", html);
}


// ── FULL A-Z DAILY REPORT ─────────────────────────────────────────────

export function exportFullReport({ live, unusual, signals, oiSummary, sellerData, tradeAnalysis, intraday, nextday, weekly, pnlStats, pnlTrades }) {
  const now = new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
  const dateStr = new Date().toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "long", year: "numeric", month: "long", day: "numeric" });

  let html = `
    <h1 style="margin-bottom:2px">UNIVERSE - Daily Intelligence Report</h1>
    <div style="font-size:13px;color:#333;margin-bottom:2px">${dateStr}</div>
    <div class="meta">Generated: ${now} IST | Market Hours: 9:15 AM - 3:30 PM IST</div>
    <hr class="section-divider">
  `;

  // ═══════════════════════════════════════════════════════════════════
  // 1. MARKET OVERVIEW
  // ═══════════════════════════════════════════════════════════════════
  html += `<h2>1. MARKET OVERVIEW</h2>`;
  if (live) {
    for (const key of ["nifty", "banknifty"]) {
      const d = live[key];
      if (!d) continue;
      const label = key === "nifty" ? "NIFTY 50" : "BANKNIFTY";
      const chgClass = d.change >= 0 ? "pos" : "neg";
      html += `<div class="summary">
        <div class="sum-card"><div class="sum-label">${label}</div><div class="sum-value">${fmt(d.ltp)}</div></div>
        <div class="sum-card"><div class="sum-label">Change</div><div class="sum-value ${chgClass}">${d.change >= 0 ? "+" : ""}${d.change?.toFixed(1) || 0} (${d.changePercent?.toFixed(2) || 0}%)</div></div>
        <div class="sum-card"><div class="sum-label">Day High</div><div class="sum-value">${fmt(d.high)}</div></div>
        <div class="sum-card"><div class="sum-label">Day Low</div><div class="sum-value">${fmt(d.low)}</div></div>
        <div class="sum-card"><div class="sum-label">Prev Close</div><div class="sum-value">${fmt(d.close || d.prevClose)}</div></div>
      </div>`;
    }
  } else {
    html += `<p style="color:#888">Market data not available</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 2. TRADE AI RECOMMENDATIONS
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><h2>2. TRADE AI - SMART MONEY RECOMMENDATIONS</h2>`;
  if (tradeAnalysis) {
    for (const key of ["nifty", "banknifty"]) {
      const d = tradeAnalysis[key];
      if (!d) continue;
      const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
      const biasClass = d.sellerBias === "BULLISH" ? "bias-bullish" : d.sellerBias === "BEARISH" ? "bias-bearish" : "bias-neutral";

      html += `<h3>${label} | LTP: ${fmt(d.ltp)} | ATM: ${d.atm}</h3>`;
      html += `<div style="margin-bottom:10px"><strong>Seller Bias: </strong><span class="${biasClass}">${d.sellerBias}</span></div>`;

      // Key levels
      if (d.keyLevels) {
        html += `<div style="margin-bottom:10px">`;
        if (d.keyLevels.resistance?.length) {
          html += `<strong style="color:#cc2020">Resistance:</strong> `;
          d.keyLevels.resistance.forEach(l => { html += `<span class="level-box">${l}</span> `; });
          html += `<br>`;
        }
        if (d.keyLevels.support?.length) {
          html += `<strong style="color:#1a8a2e">Support:</strong> `;
          d.keyLevels.support.forEach(l => { html += `<span class="level-box">${l}</span> `; });
        }
        html += `</div>`;
      }

      // Recommendations
      if (d.recommendations?.length) {
        for (const rec of d.recommendations) {
          const confColor = rec.confidence === "HIGH" ? "#1a8a2e" : "#cc8800";
          html += `<div class="rec-card">
            <div style="display:flex;justify-content:space-between;margin-bottom:6px">
              <strong style="font-size:13px">${rec.action} @ ${rec.strike}</strong>
              <span class="badge" style="background:${confColor}22;color:${confColor}">${rec.confidence}</span>
            </div>
            <div style="font-size:11px;color:#555">${rec.reason}</div>
          </div>`;
        }
      }

      // Reasons
      if (d.reasons?.length) {
        html += `<div style="margin-top:8px"><strong>Analysis:</strong></div>`;
        d.reasons.forEach((r, i) => {
          html += `<div class="reason-item">${i + 1}. ${r}</div>`;
        });
      }
    }
  } else {
    html += `<p style="color:#888">Trade analysis not available</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 3. SELLER ACTIVITY
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><div class="page-break"></div><h2>3. SELLER ACTIVITY (20x Capital - Smart Money)</h2>`;
  if (sellerData) {
    for (const key of ["nifty", "banknifty"]) {
      const d = sellerData[key];
      if (!d) continue;
      const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";

      html += `<h3>${label} Seller Flow</h3>`;
      html += `<div class="summary">
        <div class="sum-card"><div class="sum-label">CE Writing</div><div class="sum-value neg">${fmtL(d.ceWritingOI)}</div></div>
        <div class="sum-card"><div class="sum-label">PE Writing</div><div class="sum-value pos">${fmtL(d.peWritingOI)}</div></div>
        <div class="sum-card"><div class="sum-label">CE Short Cover</div><div class="sum-value">${fmtL(d.ceShortCoverOI)}</div></div>
        <div class="sum-card"><div class="sum-label">PE Short Cover</div><div class="sum-value">${fmtL(d.peShortCoverOI)}</div></div>
        <div class="sum-card"><div class="sum-label">Net Seller OI</div><div class="sum-value" style="color:#7c3aed">${fmtL(d.netSellerOI)}</div></div>
      </div>`;

      // Strike table
      if (d.strikes?.length) {
        const actLabel = { WRITING: "Writing", SHORT_COVER: "Short Cover", BUYING: "Buying", LONG_UNWIND: "Long Unwind", NEUTRAL: "-" };
        const actColor = { WRITING: "#cc8800", SHORT_COVER: "#7c3aed", BUYING: "#1a8a2e", LONG_UNWIND: "#cc2020", NEUTRAL: "#888" };
        html += `<table>
          <tr><th>Strike</th><th>CE OI Chg</th><th>CE Prem</th><th>CE Type</th><th>PE OI Chg</th><th>PE Prem</th><th>PE Type</th></tr>`;
        for (const st of d.strikes) {
          const cls = st.isATM ? ' class="atm"' : "";
          html += `<tr${cls}>
            <td>${fmt(st.strike)}${st.isATM ? " (ATM)" : ""}</td>
            <td class="${st.ceOIChange > 0 ? "pos" : st.ceOIChange < 0 ? "neg" : ""}">${st.ceOIChange > 0 ? "+" : ""}${fmtL(st.ceOIChange)}</td>
            <td class="${st.cePremChange > 0 ? "pos" : st.cePremChange < 0 ? "neg" : ""}">${fmtPrem(st.cePremChange)}</td>
            <td style="color:${actColor[st.ceActivity]};font-weight:700">${actLabel[st.ceActivity]}</td>
            <td class="${st.peOIChange > 0 ? "pos" : st.peOIChange < 0 ? "neg" : ""}">${st.peOIChange > 0 ? "+" : ""}${fmtL(st.peOIChange)}</td>
            <td class="${st.pePremChange > 0 ? "pos" : st.pePremChange < 0 ? "neg" : ""}">${fmtPrem(st.pePremChange)}</td>
            <td style="color:${actColor[st.peActivity]};font-weight:700">${actLabel[st.peActivity]}</td>
          </tr>`;
        }
        html += `</table>`;
      }
    }
  } else {
    html += `<p style="color:#888">Seller data not available</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 4. OI CHANGE SUMMARY
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><div class="page-break"></div><h2>4. OI CHANGE SUMMARY</h2>`;
  if (oiSummary) {
    for (const key of ["nifty", "banknifty"]) {
      const d = oiSummary[key];
      if (!d) continue;
      const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
      const ceNet = d.ceOIChangePos + d.ceOIChangeNeg;
      const peNet = d.peOIChangePos + d.peOIChangeNeg;

      html += `<h3>${label} (LTP: ${fmt(d.ltp)} | ATM: ${fmt(d.atm)} | PCR: ${d.pcr})</h3>`;
      html += `<div class="summary">
        <div class="sum-card"><div class="sum-label">Total CE OI</div><div class="sum-value">${fmtL(d.totalCEOI)}</div></div>
        <div class="sum-card"><div class="sum-label">Total PE OI</div><div class="sum-value">${fmtL(d.totalPEOI)}</div></div>
        <div class="sum-card"><div class="sum-label">CE +OI</div><div class="sum-value pos">+${fmtL(d.ceOIChangePos)}</div></div>
        <div class="sum-card"><div class="sum-label">CE -OI</div><div class="sum-value neg">${fmtL(d.ceOIChangeNeg)}</div></div>
        <div class="sum-card"><div class="sum-label">PE +OI</div><div class="sum-value pos">+${fmtL(d.peOIChangePos)}</div></div>
        <div class="sum-card"><div class="sum-label">PE -OI</div><div class="sum-value neg">${fmtL(d.peOIChangeNeg)}</div></div>
        <div class="sum-card"><div class="sum-label">Net CE</div><div class="sum-value ${ceNet >= 0 ? "pos" : "neg"}">${ceNet >= 0 ? "+" : ""}${fmtL(ceNet)}</div></div>
        <div class="sum-card"><div class="sum-label">Net PE</div><div class="sum-value ${peNet >= 0 ? "pos" : "neg"}">${peNet >= 0 ? "+" : ""}${fmtL(peNet)}</div></div>
      </div>`;

      // Strike-wise table
      if (d.strikes?.length) {
        html += `<table><tr><th>Strike</th><th>CE OI</th><th>CE Chg</th><th>CE LTP</th><th>PE LTP</th><th>PE Chg</th><th>PE OI</th></tr>`;
        for (const s of d.strikes) {
          const cls = s.isATM ? ' class="atm"' : "";
          html += `<tr${cls}><td>${fmt(s.strike)}${s.isATM ? " (ATM)" : ""}</td>
            <td>${fmtL(s.ceOI)}</td>
            <td class="${s.ceOIChange > 0 ? "pos" : s.ceOIChange < 0 ? "neg" : ""}">${s.ceOIChange > 0 ? "+" : ""}${fmtL(s.ceOIChange)}</td>
            <td>${s.ceLTP?.toFixed(1) || "-"}</td>
            <td>${s.peLTP?.toFixed(1) || "-"}</td>
            <td class="${s.peOIChange > 0 ? "pos" : s.peOIChange < 0 ? "neg" : ""}">${s.peOIChange > 0 ? "+" : ""}${fmtL(s.peOIChange)}</td>
            <td>${fmtL(s.peOI)}</td></tr>`;
        }
        html += `</table>`;
      }
    }
  } else {
    html += `<p style="color:#888">OI data not available</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 5. TRADING SIGNALS
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><div class="page-break"></div><h2>5. TRADING SIGNALS</h2>`;
  if (signals && signals.length > 0) {
    for (const s of signals) {
      html += `<div class="signal-card">
        <div class="signal-header">
          <div><strong>${s.instrument}</strong> <span class="badge" style="background:${s.type?.includes("PUT") ? "#ffe0e0" : "#e0ffe0"}">${s.type}</span></div>
          <div style="font-size:16px;font-weight:900;color:#7c3aed">${s.score}/${s.maxScore}</div>
        </div>
        <div style="font-size:11px;color:#888;margin-bottom:6px">${s.strike} | ${s.expiry} | ${s.time}</div>
        <table style="width:auto;margin-bottom:6px"><tr>
          <td><strong>Entry:</strong> ${s.entry}</td><td><strong>T1:</strong> ${s.t1}</td>
          <td><strong>T2:</strong> ${s.t2}</td><td><strong>SL:</strong> ${s.sl}</td>
          <td><strong>R:R:</strong> ${s.rr}</td>
        </tr></table>`;
      if (s.reasoning?.length) {
        html += `<div style="font-size:11px">`;
        for (const r of s.reasoning) {
          const cls = r.pass === true ? "pass" : r.pass === "warn" ? "warn" : "fail";
          html += `<div class="${cls}" style="margin-bottom:2px">${r.pass === true ? "[+]" : r.pass === "warn" ? "[!]" : "[-]"} ${r.text}</div>`;
        }
        html += `</div>`;
      }
      html += `</div>`;
    }
  } else {
    html += `<p style="color:#888">No active signals</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 6. UNUSUAL ACTIVITY ALERTS
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><div class="page-break"></div><h2>6. UNUSUAL ACTIVITY ALERTS (OI Change > 1L)</h2>`;
  if (unusual && unusual.length > 0) {
    html += `<table><tr><th>Time</th><th>Type</th><th>Instrument</th><th>OI Change</th><th>Premium</th><th>Alert</th><th>Signal</th></tr>`;
    for (const u of unusual) {
      const alertColor = u.alert === "CRITICAL" ? "#cc2020" : u.alert === "HIGH" ? "#cc8800" : "#888";
      html += `<tr>
        <td>${u.time}</td>
        <td style="font-weight:700;color:${alertColor}">${u.type}</td>
        <td>${u.instrument}</td>
        <td>${u.oiChange}</td>
        <td class="${u.premChange?.includes("+") ? "pos" : "neg"}">${u.premChange}</td>
        <td style="color:${alertColor};font-weight:700">${u.alert}</td>
        <td style="font-size:10px">${u.signal}</td>
      </tr>`;
    }
    html += `</table>`;
  } else {
    html += `<p style="color:#888">No unusual activity detected</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 7. INTRADAY ANALYSIS
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><h2>7. INTRADAY ANALYSIS</h2>`;
  if (intraday) {
    for (const key of ["nifty", "banknifty"]) {
      const d = intraday[key];
      if (!d) continue;
      const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
      html += `<h3>${label}</h3>`;

      if (d.summary) html += `<p style="font-size:12px;color:#333">${d.summary}</p>`;

      html += `<div class="summary">`;
      if (d.trend) html += `<div class="sum-card"><div class="sum-label">Trend</div><div class="sum-value">${d.trend}</div></div>`;
      if (d.support) html += `<div class="sum-card"><div class="sum-label">Support</div><div class="sum-value pos">${d.support}</div></div>`;
      if (d.resistance) html += `<div class="sum-card"><div class="sum-label">Resistance</div><div class="sum-value neg">${d.resistance}</div></div>`;
      if (d.pivotPoint) html += `<div class="sum-card"><div class="sum-label">Pivot</div><div class="sum-value">${d.pivotPoint}</div></div>`;
      html += `</div>`;

      // Timeframes
      for (const tf of ["5min", "15min", "1hr"]) {
        const tfData = d[tf];
        if (!tfData) continue;
        html += `<div style="font-size:11px;margin-bottom:8px"><strong>${tf}:</strong> `;
        if (tfData.trend) html += `Trend: ${tfData.trend} | `;
        if (tfData.rsi) html += `RSI: ${tfData.rsi} | `;
        if (tfData.macd) html += `MACD: ${tfData.macd} | `;
        if (tfData.supertrend) html += `SuperTrend: ${tfData.supertrend}`;
        html += `</div>`;
      }
    }
  } else {
    html += `<p style="color:#888">Intraday data not available</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 8. NEXT DAY OUTLOOK
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><h2>8. NEXT DAY OUTLOOK</h2>`;
  if (nextday) {
    for (const key of ["nifty", "banknifty"]) {
      const d = nextday[key];
      if (!d) continue;
      const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
      html += `<h3>${label}</h3>`;
      if (d.summary) html += `<p style="font-size:12px;color:#333">${d.summary}</p>`;
      html += `<div class="summary">`;
      if (d.expectedRange) html += `<div class="sum-card"><div class="sum-label">Expected Range</div><div class="sum-value">${d.expectedRange}</div></div>`;
      if (d.pivot) html += `<div class="sum-card"><div class="sum-label">Pivot</div><div class="sum-value">${d.pivot}</div></div>`;
      if (d.support1) html += `<div class="sum-card"><div class="sum-label">S1</div><div class="sum-value pos">${d.support1}</div></div>`;
      if (d.resistance1) html += `<div class="sum-card"><div class="sum-label">R1</div><div class="sum-value neg">${d.resistance1}</div></div>`;
      if (d.bias) html += `<div class="sum-card"><div class="sum-label">Bias</div><div class="sum-value">${d.bias}</div></div>`;
      html += `</div>`;
      if (d.keyLevels) {
        html += `<div style="font-size:11px">Key Levels: `;
        for (const [k, v] of Object.entries(d.keyLevels)) {
          html += `${k}: ${v} | `;
        }
        html += `</div>`;
      }
    }
  } else {
    html += `<p style="color:#888">Next day data not available</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 9. WEEKLY OUTLOOK
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><h2>9. WEEKLY OUTLOOK</h2>`;
  if (weekly) {
    for (const key of ["nifty", "banknifty"]) {
      const d = weekly[key];
      if (!d) continue;
      const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
      html += `<h3>${label}</h3>`;
      if (d.summary) html += `<p style="font-size:12px;color:#333">${d.summary}</p>`;
      html += `<div class="summary">`;
      if (d.weeklyTrend) html += `<div class="sum-card"><div class="sum-label">Weekly Trend</div><div class="sum-value">${d.weeklyTrend}</div></div>`;
      if (d.weeklyRange) html += `<div class="sum-card"><div class="sum-label">Weekly Range</div><div class="sum-value">${d.weeklyRange}</div></div>`;
      if (d.weeklyBias) html += `<div class="sum-card"><div class="sum-label">Bias</div><div class="sum-value">${d.weeklyBias}</div></div>`;
      html += `</div>`;
    }
  } else {
    html += `<p style="color:#888">Weekly data not available</p>`;
  }

  // ═══════════════════════════════════════════════════════════════════
  // 10. PnL TRACKER — TRADE LOG
  // ═══════════════════════════════════════════════════════════════════
  html += `<hr class="section-divider"><div class="page-break"></div><h2>10. PnL TRACKER — TRADE LOG</h2>`;

  if (pnlStats && pnlStats.total > 0) {
    html += `<div class="summary">
      <div class="sum-card"><div class="sum-label">Total Trades</div><div class="sum-value">${pnlStats.total}</div></div>
      <div class="sum-card"><div class="sum-label">Wins</div><div class="sum-value pos">${pnlStats.wins}</div></div>
      <div class="sum-card"><div class="sum-label">Losses</div><div class="sum-value neg">${pnlStats.losses}</div></div>
      <div class="sum-card"><div class="sum-label">Stop Hunts</div><div class="sum-value" style="color:#7c3aed">${pnlStats.stopHunts}</div></div>
      <div class="sum-card"><div class="sum-label">Win Rate</div><div class="sum-value ${pnlStats.winRate >= 60 ? 'pos' : 'neg'}">${pnlStats.winRate}%</div></div>
      <div class="sum-card"><div class="sum-label">Total P&L</div><div class="sum-value ${pnlStats.totalPnl >= 0 ? 'pos' : 'neg'}">${pnlStats.totalPnl >= 0 ? '+' : ''}${Math.round(pnlStats.totalPnl).toLocaleString("en-IN")}</div></div>
    </div>`;

    html += `<div class="summary">
      <div class="sum-card"><div class="sum-label">Avg Win</div><div class="sum-value pos">${Math.round(pnlStats.avgWin || 0).toLocaleString("en-IN")}</div></div>
      <div class="sum-card"><div class="sum-label">Avg Loss</div><div class="sum-value neg">${Math.round(pnlStats.avgLoss || 0).toLocaleString("en-IN")}</div></div>
      <div class="sum-card"><div class="sum-label">Best Trade</div><div class="sum-value pos">${Math.round(pnlStats.bestTrade || 0).toLocaleString("en-IN")}</div></div>
      <div class="sum-card"><div class="sum-label">Worst Trade</div><div class="sum-value neg">${Math.round(pnlStats.worstTrade || 0).toLocaleString("en-IN")}</div></div>
    </div>`;
  } else {
    html += `<p style="color:#888">No trade stats available</p>`;
  }

  if (pnlTrades && pnlTrades.length > 0) {
    html += `<h3>Trade History (${pnlTrades.length} trades)</h3>`;
    html += `<table><tr>
      <th>Time</th><th>Index</th><th>Action</th><th>Strike</th>
      <th>Entry</th><th>Exit</th><th>SL</th><th>T1</th>
      <th>Qty</th><th>P&L (pts)</th><th>P&L (₹)</th><th>Status</th>
    </tr>`;
    for (const t of pnlTrades) {
      const statusCls = (t.status === "T1_HIT" || t.status === "T2_HIT") ? "pos" : t.status === "SL_HIT" ? "neg" : "";
      const time = t.entry_time ? new Date(t.entry_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true, day: "2-digit", month: "short" }) : "-";
      html += `<tr>
        <td>${time}</td>
        <td><strong>${t.idx}</strong></td>
        <td>${t.action}</td>
        <td>${t.strike}</td>
        <td>${t.entry_price}</td>
        <td>${t.exit_price || t.current_ltp || "-"}</td>
        <td>${t.sl_price}</td>
        <td>${t.t1_price}</td>
        <td>${t.lots}L x ${t.lot_size} = ${t.qty}</td>
        <td class="${statusCls}">${t.pnl_pts > 0 ? "+" : ""}${(t.pnl_pts || 0).toFixed(1)}</td>
        <td class="${statusCls}" style="font-weight:700">${(t.pnl_rupees || 0) >= 0 ? "+" : ""}${Math.round(t.pnl_rupees || 0).toLocaleString("en-IN")}</td>
        <td class="${statusCls}" style="font-weight:700">${t.status}</td>
      </tr>`;
    }
    html += `</table>`;
  }

  // Footer
  html += `<hr class="section-divider">
    <div style="text-align:center;font-size:10px;color:#aaa;margin-top:20px">
      UNIVERSE Intelligence Report | Generated: ${now} IST | Data Source: NSE via Zerodha Kite Connect
    </div>`;

  openPrintWindow(`UNIVERSE Daily Report - ${dateStr}`, html);
}
