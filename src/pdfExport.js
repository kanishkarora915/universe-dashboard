/**
 * PDF Export utility — uses window.print() with a styled printable div.
 * No external library needed.
 */

function openPrintWindow(title, htmlContent) {
  const win = window.open("", "_blank", "width=900,height=700");
  win.document.write(`
    <html><head><title>${title}</title>
    <style>
      body { font-family: -apple-system, sans-serif; background: #fff; color: #111; padding: 24px; margin: 0; }
      h1 { font-size: 20px; margin-bottom: 4px; }
      h2 { font-size: 15px; color: #555; margin-top: 20px; margin-bottom: 8px; }
      .meta { font-size: 11px; color: #888; margin-bottom: 16px; }
      table { width: 100%; border-collapse: collapse; font-size: 11px; margin-bottom: 20px; }
      th { background: #f5f5f5; padding: 6px 8px; text-align: left; font-weight: 700; border-bottom: 2px solid #ddd; }
      td { padding: 5px 8px; border-bottom: 1px solid #eee; }
      .pos { color: #1a8a2e; font-weight: 600; }
      .neg { color: #cc2020; font-weight: 600; }
      .atm { background: #e8f0ff; font-weight: 700; }
      .summary { display: flex; gap: 16px; margin-bottom: 16px; }
      .sum-card { background: #f8f8f8; border-radius: 8px; padding: 10px 16px; text-align: center; flex: 1; }
      .sum-label { font-size: 9px; color: #888; text-transform: uppercase; letter-spacing: 1px; }
      .sum-value { font-size: 16px; font-weight: 700; margin-top: 4px; }
      .signal-card { border: 1px solid #ddd; border-radius: 8px; padding: 14px; margin-bottom: 12px; }
      .signal-header { display: flex; justify-content: space-between; margin-bottom: 8px; }
      .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 700; }
      .pass { color: #1a8a2e; } .warn { color: #cc8800; } .fail { color: #999; }
      @media print { body { padding: 12px; } }
    </style>
    </head><body>${htmlContent}</body></html>
  `);
  win.document.close();
  setTimeout(() => { win.print(); }, 500);
}

const fmt = (n) => n ? Math.round(n).toLocaleString("en-IN") : "0";
const fmtL = (n) => n ? `${(n / 100000).toFixed(1)}L` : "0";

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
    for (const s of d.strikes) {
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
      <div><strong>${s.instrument}</strong> <span class="badge" style="background:${s.type.includes("PUT") ? "#ffe0e0" : "#e0ffe0"}">${s.type}</span> <span class="badge" style="background:#fff3cd">${s.status}</span></div>
      <div style="font-size:18px;font-weight:900;color:#7c3aed">${s.score}/${s.maxScore}</div>
    </div>`;
    html += `<div style="font-size:11px;color:#888;margin-bottom:8px">${s.strike} | ${s.expiry} | ${s.time}</div>`;
    html += `<table style="width:auto"><tr><td><strong>Entry:</strong> ₹${s.entry}</td><td><strong>T1:</strong> ₹${s.t1}</td><td><strong>T2:</strong> ₹${s.t2}</td><td><strong>SL:</strong> ₹${s.sl}</td><td><strong>R:R</strong> ${s.rr}</td></tr></table>`;
    html += `<div style="margin-top:8px;font-size:11px">`;
    for (const r of s.reasoning) {
      const icon = r.pass === true ? "✅" : r.pass === "warn" ? "⚠️" : "❌";
      const cls = r.pass === true ? "pass" : r.pass === "warn" ? "warn" : "fail";
      html += `<div class="${cls}" style="margin-bottom:3px">${icon} ${r.text}</div>`;
    }
    html += `</div></div>`;
  }

  openPrintWindow("UNIVERSE Signals Report", html);
}
