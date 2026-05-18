import { useState, useEffect, useCallback } from "react";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const CARD = "#111118";
const BORDER = "#1E1E2E";
const BG = "#0A0A0F";

const fmt = (n) => (n ? Math.round(n).toLocaleString("en-IN") : "0");
const fmtL = (n) => (n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0");

// ── API ──────────────────────────────────────────────────────────────────

async function fetchTT(endpoint) {
  try {
    const res = await fetch(`/api/trading-times/${endpoint}`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

// ── Shared UI ────────────────────────────────────────────────────────────

const Card = ({ children, style = {} }) => (
  <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px", ...style }}>
    {children}
  </div>
);

const Label = ({ children }) => (
  <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
    {children}
  </div>
);

const Stat = ({ label, value, color = "#fff", sub }) => (
  <div style={{ background: BG, borderRadius: 8, padding: "10px 14px", flex: 1, minWidth: 90 }}>
    <div style={{ color: "#555", fontSize: 9, fontWeight: 700, textTransform: "uppercase" }}>{label}</div>
    <div style={{ color, fontWeight: 700, fontSize: 14 }}>{value}</div>
    {sub && <div style={{ color: "#444", fontSize: 9 }}>{sub}</div>}
  </div>
);

const Badge = ({ text, color }) => (
  <span style={{ background: color + "22", color, border: `1px solid ${color}44`, borderRadius: 6, padding: "2px 8px", fontSize: 10, fontWeight: 700 }}>
    {text}
  </span>
);

// ══════════════════════════════════════════════════════════════════════════
// SECTION 1: LIVE SIGNAL BANNER
// ══════════════════════════════════════════════════════════════════════════

function SignalBanner({ signal }) {
  if (!signal || signal.windowType === "NO_DATA") {
    return (
      <Card style={{ textAlign: "center", padding: 24 }}>
        <div style={{ color: "#555", fontSize: 14 }}>Waiting for market data...</div>
        <div style={{ color: "#333", fontSize: 11, marginTop: 4 }}>Snapshots captured every 5 minutes during market hours</div>
      </Card>
    );
  }

  const colors = {
    BLAST: signal.blastDirection === "BULLISH" ? GREEN : RED,
    PRE_BLAST: ORANGE,
    TRENDING: signal.blastDirection === "BULLISH" ? GREEN : RED,
    EXHAUSTION: YELLOW,
    SIDEWAYS: "#555",
  };

  const icons = {
    BLAST: signal.blastDirection === "BULLISH" ? "🚀" : "💣",
    PRE_BLAST: "⚡",
    TRENDING: signal.blastDirection === "BULLISH" ? "📈" : "📉",
    EXHAUSTION: "😴",
    SIDEWAYS: "➡️",
  };

  const color = colors[signal.windowType] || "#555";
  const icon = icons[signal.windowType] || "📊";

  return (
    <Card style={{ borderColor: color + "66", borderWidth: 2 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ fontSize: 28, fontWeight: 900, color, display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 32 }}>{icon}</span>
            {signal.windowType === "BLAST"
              ? `${signal.blastDirection} BLAST`
              : signal.windowType === "PRE_BLAST"
              ? `PRE-BLAST ${signal.blastDirection}`
              : signal.windowType === "TRENDING"
              ? `TRENDING ${signal.blastDirection}`
              : signal.windowType}
          </div>
          <div style={{ color: "#888", fontSize: 12, marginTop: 4 }}>{signal.message}</div>
        </div>
        <div style={{ textAlign: "center" }}>
          <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>CONFIDENCE</div>
          <div style={{ fontSize: 32, fontWeight: 900, color }}>{signal.confidence}%</div>
          <div style={{ width: 80, height: 6, background: "#1a1a2e", borderRadius: 3, overflow: "hidden", marginTop: 4 }}>
            <div style={{ width: `${signal.confidence}%`, height: "100%", background: color, borderRadius: 3 }} />
          </div>
        </div>
      </div>
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 2: LAYER CARDS
// ══════════════════════════════════════════════════════════════════════════

function LayerCards({ data }) {
  if (!data) return null;

  const d = data;
  const ceChgColor = d.ce_oi_net_change > 0 ? GREEN : d.ce_oi_net_change < 0 ? RED : "#555";
  const peChgColor = d.pe_oi_net_change > 0 ? GREEN : d.pe_oi_net_change < 0 ? RED : "#555";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Row 1: OI Flow + Premium */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {/* Card A: OI Flow */}
        <Card style={{ flex: 1, minWidth: 200 }}>
          <Label>OI FLOW</Label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Stat label="CE OI Change" value={fmtL(d.ce_oi_net_change)} color={ceChgColor} />
            <Stat label="PE OI Change" value={fmtL(d.pe_oi_net_change)} color={peChgColor} />
            <Stat label="PCR" value={d.pcr} color={d.pcr > 1.1 ? GREEN : d.pcr < 0.9 ? RED : YELLOW} sub={`Δ ${d.pcr_change > 0 ? "+" : ""}${d.pcr_change}`} />
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            {d.ce_unwinding ? <Badge text="CE UNWINDING" color={GREEN} /> : null}
            {d.pe_unwinding ? <Badge text="PE UNWINDING" color={RED} /> : null}
            {d.ce_accum_blocks >= 3 && <Badge text={`CE ACCUM ${d.ce_accum_blocks} blocks`} color={RED} />}
            {d.pe_accum_blocks >= 3 && <Badge text={`PE ACCUM ${d.pe_accum_blocks} blocks`} color={GREEN} />}
          </div>
        </Card>

        {/* Card B: Premium */}
        <Card style={{ flex: 1, minWidth: 200 }}>
          <Label>PREMIUM BEHAVIOR</Label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Stat label="ATM CE" value={`₹${d.atm_ce_ltp?.toFixed(1)}`} color={d.ce_premium_change > 0 ? GREEN : RED} sub={`Δ ${d.ce_premium_change > 0 ? "+" : ""}${d.ce_premium_change?.toFixed(1)}`} />
            <Stat label="ATM PE" value={`₹${d.atm_pe_ltp?.toFixed(1)}`} color={d.pe_premium_change > 0 ? RED : GREEN} sub={`Δ ${d.pe_premium_change > 0 ? "+" : ""}${d.pe_premium_change?.toFixed(1)}`} />
            <Stat label="CE/PE Ratio" value={d.premium_ratio} color={d.premium_ratio > 1.15 ? GREEN : d.premium_ratio < 0.85 ? RED : "#ccc"} />
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <Stat label="IV Skew" value={d.iv_skew?.toFixed(1)} color={d.iv_skew > 1 ? GREEN : d.iv_skew < -1 ? RED : "#888"} />
            <Stat label="Vol Ratio" value={`${d.volume_ratio}x`} color={d.volume_ratio > 2 ? GREEN : d.volume_ratio < 0.5 ? RED : "#888"} sub="CE/PE" />
          </div>
        </Card>
      </div>

      {/* Row 2: Velocity + Institutional + Alerts */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {/* Card C: Velocity */}
        <Card style={{ flex: 1, minWidth: 150 }}>
          <Label>VELOCITY & MOMENTUM</Label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Stat label="Velocity" value={`${d.velocity_score}/10`} color={d.velocity_score > 6 ? ORANGE : d.velocity_score > 3 ? YELLOW : "#555"} />
            <Stat label="Acceleration" value={d.acceleration > 0 ? `+${d.acceleration}` : d.acceleration} color={d.acceleration > 0 ? GREEN : d.acceleration < 0 ? RED : "#555"} />
            <Stat label="Spot Δ5min" value={`${d.spot_change_5min > 0 ? "+" : ""}${d.spot_change_5min}`} color={d.spot_change_5min > 0 ? GREEN : RED} sub={`${d.spot_change_pct}%`} />
            <Stat label="vs VWAP" value={d.spot_vs_vwap > 0 ? `+${d.spot_vs_vwap}` : d.spot_vs_vwap} color={d.spot_vs_vwap > 0 ? GREEN : RED} />
          </div>
        </Card>

        {/* Card D: Institutional */}
        <Card style={{ flex: 1, minWidth: 150 }}>
          <Label>INSTITUTIONAL FOOTPRINT</Label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Stat label="Hedge Ratio" value={d.hedge_ratio >= 999 ? "∞" : d.hedge_ratio} color={d.hedge_ratio > 5 ? ORANGE : "#888"} sub={d.hedge_trend} />
            <Stat label="Conviction" value={d.conviction} color={d.conviction === "MAX" ? ORANGE : d.conviction === "HIGH" ? GREEN : YELLOW} />
            <Stat label="Max Pain" value={fmt(d.max_pain)} color={ACCENT} sub={d.max_pain_shift !== 0 ? `Shift ${d.max_pain_shift > 0 ? "+" : ""}${d.max_pain_shift}` : "Stable"} />
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <Stat label="CE Wall" value={fmt(d.top_ce_wall)} color={RED} sub={d.ce_wall_shift !== 0 ? `${d.ce_wall_shift > 0 ? "↑" : "↓"}${Math.abs(d.ce_wall_shift)}` : ""} />
            <Stat label="PE Wall" value={fmt(d.top_pe_wall)} color={GREEN} sub={d.pe_wall_shift !== 0 ? `${d.pe_wall_shift > 0 ? "↑" : "↓"}${Math.abs(d.pe_wall_shift)}` : ""} />
            <Stat label="OI COG" value={fmt(d.oi_cog)} color={PURPLE} sub={d.cog_shift > 0 ? `↑${d.cog_shift}` : d.cog_shift < 0 ? `↓${Math.abs(d.cog_shift)}` : ""} />
          </div>
        </Card>
      </div>

      {/* Pattern Alerts */}
      {(d.hedge_flip || d.ce_accum_blocks >= 4 || d.pe_accum_blocks >= 4 || d.velocity_score > 7) && (
        <Card style={{ borderColor: ORANGE + "44" }}>
          <Label>PATTERN ALERTS</Label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {d.hedge_flip ? <Badge text="HEDGE FLIP DETECTED" color={ORANGE} /> : null}
            {d.ce_accum_blocks >= 4 && <Badge text={`CE ACCUMULATION ${d.ce_accum_blocks} BLOCKS`} color={RED} />}
            {d.pe_accum_blocks >= 4 && <Badge text={`PE ACCUMULATION ${d.pe_accum_blocks} BLOCKS`} color={GREEN} />}
            {d.velocity_score > 7 && <Badge text={`HIGH VELOCITY ${d.velocity_score}`} color={ORANGE} />}
            {d.cog_shift > 50 && <Badge text={`COG SHIFTING UP +${d.cog_shift}`} color={GREEN} />}
            {d.cog_shift < -50 && <Badge text={`COG SHIFTING DOWN ${d.cog_shift}`} color={RED} />}
          </div>
        </Card>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 3: TIMELINE
// ══════════════════════════════════════════════════════════════════════════

function Timeline({ data }) {
  if (!data || !data.snapshots || data.snapshots.length === 0) {
    return (
      <Card>
        <Label>TODAY'S TIMELINE</Label>
        <div style={{ color: "#333", textAlign: "center", padding: 16 }}>No snapshots yet today</div>
      </Card>
    );
  }

  const windowColors = {
    SIDEWAYS: "#333", TRENDING: ACCENT, PRE_BLAST: ORANGE, BLAST: RED, EXHAUSTION: YELLOW,
  };

  const s = data.summary || {};

  return (
    <Card>
      <Label>TODAY'S TIMELINE</Label>
      <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        <Stat label="Total" value={s.total || 0} color={ACCENT} />
        <Stat label="Sideways" value={s.sideways || 0} color="#555" />
        <Stat label="Trending" value={s.trending || 0} color={ACCENT} />
        <Stat label="Pre-Blast" value={s.preBlast || 0} color={ORANGE} />
        <Stat label="Blast" value={s.blast || 0} color={RED} />
        <Stat label="Exhaustion" value={s.exhaustion || 0} color={YELLOW} />
      </div>

      {/* Visual timeline bar */}
      <div style={{ display: "flex", height: 24, borderRadius: 6, overflow: "hidden", marginBottom: 10, border: `1px solid ${BORDER}` }}>
        {data.snapshots.map((snap, i) => (
          <div
            key={i}
            title={`${snap.timestamp?.split("T")[1]?.slice(0, 5)} — ${snap.window_type} ${snap.blast_direction !== "NONE" ? snap.blast_direction : ""}`}
            style={{
              flex: 1,
              background: windowColors[snap.window_type] || "#222",
              opacity: snap.window_type === "BLAST" ? 1 : 0.7,
              borderRight: i < data.snapshots.length - 1 ? `1px solid ${BG}` : "none",
              cursor: "pointer",
            }}
          />
        ))}
      </div>

      {/* Legend */}
      <div style={{ display: "flex", gap: 12, fontSize: 9, color: "#666", marginBottom: 10 }}>
        {Object.entries(windowColors).map(([k, c]) => (
          <span key={k} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: c, display: "inline-block" }} />
            {k}
          </span>
        ))}
      </div>

      {/* Timeline table */}
      <div style={{ overflowX: "auto", maxHeight: 300, overflowY: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${BORDER}`, position: "sticky", top: 0, background: CARD }}>
              {["TIME", "WINDOW", "DIR", "SPOT", "VEL", "PCR", "CE OI", "PE OI", "CONF"].map(h => (
                <th key={h} style={{ padding: "6px 4px", color: "#555", fontWeight: 700, fontSize: 9, textAlign: "center" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.snapshots.slice().reverse().map((snap, i) => (
              <tr key={i} style={{
                borderBottom: `1px solid ${BORDER}11`,
                background: snap.window_type === "BLAST" ? (snap.blast_direction === "BULLISH" ? GREEN + "08" : RED + "08") : "transparent",
              }}>
                <td style={{ padding: "5px 4px", textAlign: "center", color: "#888" }}>{snap.timestamp?.split("T")[1]?.slice(0, 5)}</td>
                <td style={{ padding: "5px 4px", textAlign: "center" }}>
                  <Badge text={snap.window_type} color={windowColors[snap.window_type] || "#555"} />
                </td>
                <td style={{ padding: "5px 4px", textAlign: "center", color: snap.blast_direction === "BULLISH" ? GREEN : snap.blast_direction === "BEARISH" ? RED : "#333", fontWeight: 700 }}>
                  {snap.blast_direction !== "NONE" ? snap.blast_direction?.slice(0, 4) : "—"}
                </td>
                <td style={{ padding: "5px 4px", textAlign: "center", color: "#ccc" }}>{fmt(snap.spot)}</td>
                <td style={{ padding: "5px 4px", textAlign: "center", color: snap.velocity_score > 6 ? ORANGE : "#888" }}>{snap.velocity_score}</td>
                <td style={{ padding: "5px 4px", textAlign: "center", color: snap.pcr > 1.1 ? GREEN : snap.pcr < 0.9 ? RED : "#888" }}>{snap.pcr}</td>
                <td style={{ padding: "5px 4px", textAlign: "center", color: snap.ce_oi_net_change > 0 ? GREEN : RED, fontSize: 10 }}>{fmtL(snap.ce_oi_net_change)}</td>
                <td style={{ padding: "5px 4px", textAlign: "center", color: snap.pe_oi_net_change > 0 ? GREEN : RED, fontSize: 10 }}>{fmtL(snap.pe_oi_net_change)}</td>
                <td style={{ padding: "5px 4px", textAlign: "center", color: snap.confidence > 70 ? GREEN : snap.confidence > 50 ? YELLOW : "#555", fontWeight: 700 }}>{snap.confidence}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 4: YESTERDAY vs TODAY
// ══════════════════════════════════════════════════════════════════════════

function YesterdayComparison({ data }) {
  if (!data || data.error || !data.strikes) {
    return (
      <Card>
        <Label>YESTERDAY vs TODAY OI</Label>
        <div style={{ color: "#333", textAlign: "center", padding: 16 }}>No yesterday data available yet. Will save at 3:25 PM.</div>
      </Card>
    );
  }

  return (
    <Card>
      <Label>YESTERDAY vs TODAY OI — {data.previousDate}</Label>
      <div style={{ overflowX: "auto", maxHeight: 350, overflowY: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${BORDER}`, position: "sticky", top: 0, background: CARD }}>
              {["STRIKE", "YD CE OI", "TD CE OI", "CE Δ", "CE %", "YD PE OI", "TD PE OI", "PE Δ", "PE %"].map(h => (
                <th key={h} style={{ padding: "6px 4px", color: "#555", fontWeight: 700, fontSize: 9, textAlign: "center" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.strikes.map((s) => {
              const ceBig = Math.abs(s.cePctChange) > 30;
              const peBig = Math.abs(s.pePctChange) > 30;
              return (
                <tr key={s.strike} style={{
                  borderBottom: `1px solid ${BORDER}11`,
                  background: s.isATM ? ACCENT + "11" : (ceBig || peBig) ? ORANGE + "06" : "transparent",
                }}>
                  <td style={{ padding: "5px 4px", textAlign: "center", fontWeight: s.isATM ? 900 : 400, color: s.isATM ? ACCENT : "#ccc" }}>
                    {s.strike} {s.isATM ? "★" : ""}
                  </td>
                  <td style={{ padding: "5px 4px", textAlign: "center", color: "#666" }}>{fmtL(s.yesterdayCE)}</td>
                  <td style={{ padding: "5px 4px", textAlign: "center", color: "#ccc" }}>{fmtL(s.todayCE)}</td>
                  <td style={{ padding: "5px 4px", textAlign: "center", color: s.ceChange > 0 ? GREEN : s.ceChange < 0 ? RED : "#333", fontWeight: ceBig ? 700 : 400 }}>{fmtL(s.ceChange)}</td>
                  <td style={{ padding: "5px 4px", textAlign: "center", color: s.cePctChange > 0 ? GREEN : s.cePctChange < 0 ? RED : "#333", fontSize: 10 }}>{s.cePctChange}%</td>
                  <td style={{ padding: "5px 4px", textAlign: "center", color: "#666" }}>{fmtL(s.yesterdayPE)}</td>
                  <td style={{ padding: "5px 4px", textAlign: "center", color: "#ccc" }}>{fmtL(s.todayPE)}</td>
                  <td style={{ padding: "5px 4px", textAlign: "center", color: s.peChange > 0 ? GREEN : s.peChange < 0 ? RED : "#333", fontWeight: peBig ? 700 : 400 }}>{fmtL(s.peChange)}</td>
                  <td style={{ padding: "5px 4px", textAlign: "center", color: s.pePctChange > 0 ? GREEN : s.pePctChange < 0 ? RED : "#333", fontSize: 10 }}>{s.pePctChange}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 5: REPORTS
// ══════════════════════════════════════════════════════════════════════════

function Reports({ index }) {
  const [period, setPeriod] = useState("daily");
  const [report, setReport] = useState(null);

  useEffect(() => {
    if (period === "daily") fetchTT("report/daily").then(setReport);
    else if (period === "weekly") fetchTT("report/weekly").then(setReport);
    else fetchTT("report/monthly").then(setReport);
  }, [period]);

  const handleExport = () => {
    if (!report) return;
    const title = `Trading Times ${period.toUpperCase()} Report — ${index}`;
    const win = window.open("", "_blank", "width=900,height=700");
    let html = `<h1>${title}</h1><p class="meta">Generated: ${new Date().toLocaleString("en-IN")}</p>`;

    if (period === "daily" && report.indices) {
      const d = report.indices[index.toLowerCase()];
      if (d) {
        html += `<h2>Summary — ${report.date}</h2>`;
        html += `<div class="summary">
          <div class="sum-card"><div class="sum-label">Snapshots</div><div class="sum-value">${d.totalSnapshots}</div></div>
          <div class="sum-card"><div class="sum-label">Blasts</div><div class="sum-value">${d.blastCount}</div></div>
          <div class="sum-card"><div class="sum-label">Avg Velocity</div><div class="sum-value">${d.avgVelocity}</div></div>
        </div>`;
        if (d.bestBlast) {
          html += `<h3>Best Blast: ${d.bestBlast.time} — ${d.bestBlast.direction} (${d.bestBlast.move} pts, ${d.bestBlast.confidence}% conf)</h3>`;
        }
        html += `<h2>Timeline</h2><table><tr><th>Time</th><th>Window</th><th>Direction</th><th>Spot</th><th>Velocity</th><th>PCR</th><th>Confidence</th></tr>`;
        (d.snapshots || []).forEach(s => {
          html += `<tr><td>${s.timestamp?.split("T")[1]?.slice(0,5)}</td><td>${s.window_type}</td><td>${s.blast_direction}</td><td>${Math.round(s.spot)}</td><td>${s.velocity_score}</td><td>${s.pcr}</td><td>${s.confidence}%</td></tr>`;
        });
        html += `</table>`;
      }
    } else if (period === "weekly") {
      html += `<h2>Period: ${report.period}</h2>`;
      html += `<div class="summary">
        <div class="sum-card"><div class="sum-label">Total Blasts</div><div class="sum-value">${report.totalBlasts}</div></div>
        <div class="sum-card"><div class="sum-label">Avg/Day</div><div class="sum-value">${report.avgBlastsPerDay}</div></div>
        <div class="sum-card"><div class="sum-label">Best Hour</div><div class="sum-value">${report.bestBlastHour}</div></div>
      </div>`;
      html += `<h2>Daily Breakdown</h2><table><tr><th>Date</th><th>Snapshots</th><th>Blasts</th></tr>`;
      (report.dailySummaries || []).forEach(d => {
        html += `<tr><td>${d.date}</td><td>${d.snapshots}</td><td>${d.blasts}</td></tr>`;
      });
      html += `</table>`;
    } else {
      html += `<h2>Month: ${report.month}</h2>`;
      html += `<div class="summary">
        <div class="sum-card"><div class="sum-label">Trading Days</div><div class="sum-value">${report.tradingDays}</div></div>
        <div class="sum-card"><div class="sum-label">Total Blasts</div><div class="sum-value">${report.totalBlasts}</div></div>
      </div>`;
    }

    win.document.write(`<html><head><title>${title}</title>
      <style>body{font-family:-apple-system,sans-serif;padding:24px;color:#111}h1{font-size:20px}h2{font-size:15px;color:#555;border-bottom:2px solid #eee;padding-bottom:4px;margin-top:20px}h3{font-size:13px;color:#333}.meta{font-size:11px;color:#888}table{width:100%;border-collapse:collapse;font-size:11px;margin-bottom:16px}th{background:#f5f5f5;padding:6px 8px;text-align:left;font-weight:700;border-bottom:2px solid #ddd}td{padding:5px 8px;border-bottom:1px solid #eee}.summary{display:flex;gap:12px;margin:12px 0;flex-wrap:wrap}.sum-card{background:#f8f8f8;border-radius:8px;padding:10px 14px;text-align:center;flex:1;min-width:100px;border:1px solid #eee}.sum-label{font-size:9px;color:#888;text-transform:uppercase}.sum-value{font-size:16px;font-weight:700;margin-top:4px}@media print{body{padding:12px}}</style>
    </head><body>${html}</body></html>`);
    win.document.close();
    setTimeout(() => win.print(), 500);
  };

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <Label>REPORTS</Label>
        <div style={{ display: "flex", gap: 6 }}>
          {["daily", "weekly", "monthly"].map(p => (
            <button key={p} onClick={() => setPeriod(p)} style={{
              background: period === p ? ACCENT : "transparent",
              color: period === p ? "#fff" : "#555",
              border: `1px solid ${period === p ? ACCENT : BORDER}`,
              borderRadius: 6, padding: "4px 10px", fontSize: 10, fontWeight: 700, cursor: "pointer",
            }}>
              {p.toUpperCase()}
            </button>
          ))}
          <button onClick={handleExport} style={{
            background: PURPLE, color: "#fff", border: "none", borderRadius: 6,
            padding: "4px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer",
          }}>
            Export PDF
          </button>
        </div>
      </div>

      {!report ? (
        <div style={{ color: "#333", textAlign: "center", padding: 16 }}>Loading report...</div>
      ) : report.error ? (
        <div style={{ color: "#555", textAlign: "center", padding: 16 }}>{report.error}</div>
      ) : period === "daily" && report.indices ? (
        <div>
          {Object.entries(report.indices).map(([idx, d]) => (
            <div key={idx} style={{ marginBottom: 12 }}>
              <div style={{ color: ACCENT, fontWeight: 700, fontSize: 12, marginBottom: 6 }}>{idx.toUpperCase()}</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Stat label="Snapshots" value={d.totalSnapshots} color={ACCENT} />
                <Stat label="Blasts" value={d.blastCount} color={d.blastCount > 0 ? ORANGE : "#555"} />
                <Stat label="Avg Velocity" value={d.avgVelocity} color={d.avgVelocity > 4 ? ORANGE : "#888"} />
                {d.bestBlast && <Stat label="Best Blast" value={`${d.bestBlast.time} ${d.bestBlast.direction}`} color={d.bestBlast.direction === "BULLISH" ? GREEN : RED} sub={`${d.bestBlast.move} pts`} />}
              </div>
            </div>
          ))}
        </div>
      ) : period === "weekly" ? (
        <div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
            <Stat label="Period" value={report.period} color={ACCENT} />
            <Stat label="Total Blasts" value={report.totalBlasts} color={ORANGE} />
            <Stat label="Avg/Day" value={report.avgBlastsPerDay} color={ACCENT} />
            <Stat label="Best Hour" value={report.bestBlastHour} color={GREEN} />
          </div>
          {report.dailySummaries && (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                  {["DATE", "SNAPSHOTS", "BLASTS"].map(h => (
                    <th key={h} style={{ padding: "6px", color: "#555", fontWeight: 700, fontSize: 9, textAlign: "center" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.dailySummaries.map((d, i) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${BORDER}11` }}>
                    <td style={{ padding: "5px 6px", textAlign: "center", color: "#888" }}>{d.date}</td>
                    <td style={{ padding: "5px 6px", textAlign: "center", color: "#ccc" }}>{d.snapshots}</td>
                    <td style={{ padding: "5px 6px", textAlign: "center", color: d.blasts > 0 ? ORANGE : "#333", fontWeight: 700 }}>{d.blasts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Stat label="Month" value={report.month} color={ACCENT} />
            <Stat label="Trading Days" value={report.tradingDays} color={ACCENT} />
            <Stat label="Total Blasts" value={report.totalBlasts} color={ORANGE} />
          </div>
        </div>
      )}
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// MAIN TAB
// ══════════════════════════════════════════════════════════════════════════

export default function TradingTimesTab() {
  const [index, setIndex] = useState("NIFTY");
  const [live, setLive] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [smartEvents, setSmartEvents] = useState([]);
  const [story, setStory] = useState(null);

  const loadData = useCallback(() => {
    fetchTT(`live/${index}`).then(setLive);
    fetchTT(`timeline/${index}`).then(setTimeline);
    // NEW: smart events from times_tab_engine
    fetch(`/api/times/events?idx=${index}`).then(r => r.json()).then(d => {
      if (d?.events) setSmartEvents(d.events);
    }).catch(() => {});
    fetch(`/api/times/story?idx=${index}`).then(r => r.json()).then(d => {
      if (d && !d.error) setStory(d);
    }).catch(() => {});
    setLastUpdate(new Date().toLocaleTimeString("en-IN"));
  }, [index]);

  useEffect(() => {
    loadData();
    const interval = setInterval(() => {
      if (document.visibilityState === "visible") loadData();
    }, 30000);
    return () => clearInterval(interval);
  }, [loadData]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <div>
            <div style={{ color: ACCENT, fontWeight: 900, fontSize: 15 }}>TRADING TIMES — REGIME DETECTOR</div>
            <div style={{ color: "#555", fontSize: 11 }}>Sideways, Trending, Blast detection with institutional footprint analysis</div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {["NIFTY", "BANKNIFTY"].map(idx => (
              <button key={idx} onClick={() => setIndex(idx)} style={{
                background: index === idx ? ACCENT : "transparent",
                color: index === idx ? "#fff" : "#555",
                border: `1px solid ${index === idx ? ACCENT : BORDER}`,
                borderRadius: 6, padding: "5px 12px", fontSize: 11, fontWeight: 700, cursor: "pointer",
              }}>
                {idx}
              </button>
            ))}
            <button onClick={loadData} style={{
              background: "transparent", color: ACCENT, border: `1px solid ${ACCENT}44`,
              borderRadius: 6, padding: "5px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer",
            }}>
              Refresh
            </button>
          </div>
        </div>
        {lastUpdate && <div style={{ color: "#333", fontSize: 10, marginTop: 4 }}>Auto-refresh: 30s | Last: {lastUpdate}</div>}
      </Card>

      {/* Signal Banner */}
      <SignalBanner signal={live?.signal} />

      {/* SMART EVENTS — kya hua, kab hua, kyu hua */}
      <SmartEventsTimeline events={smartEvents} story={story} />

      {/* Layer Cards */}
      <LayerCards data={live?.latest} />

      {/* Timeline */}
      <Timeline data={timeline} />

      {/* Yesterday vs Today */}
      <YesterdayComparison data={live?.yesterday} />

      {/* Reports */}
      <Reports index={index} />
    </div>
  );
}

function SmartEventsTimeline({ events, story }) {
  if (!events || events.length === 0) {
    return (
      <Card>
        <Label>📊 SMART EVENTS — Kya Hua, Kyu Hua, Kab Hua</Label>
        <div style={{ color: "#555", textAlign: "center", padding: 20, fontSize: 12 }}>
          No events yet today. OI shifts, hidden activity, regime changes will appear here.
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <Label>📊 SMART EVENTS — {events.length} events today</Label>
        {story && (
          <span style={{
            padding: "3px 10px",
            background: story.bias === "BULLISH" ? GREEN + "22" : story.bias === "BEARISH" ? RED + "22" : YELLOW + "22",
            color: story.bias === "BULLISH" ? GREEN : story.bias === "BEARISH" ? RED : YELLOW,
            border: `1px solid ${story.bias === "BULLISH" ? GREEN : story.bias === "BEARISH" ? RED : YELLOW}`,
            borderRadius: 4,
            fontSize: 10,
            fontWeight: 700,
          }}>
            BIAS: {story.bias}
          </span>
        )}
      </div>

      {story && (
        <div style={{
          background: BG, borderRadius: 6, padding: "8px 10px", marginBottom: 10,
          fontSize: 10, color: "#888",
          display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8,
        }}>
          <div><b style={{ color: "#fff" }}>{story.bull_events}</b> bull events</div>
          <div><b style={{ color: "#fff" }}>{story.bear_events}</b> bear events</div>
          <div><b style={{ color: "#fff" }}>{story.wall_shifts_count}</b> wall shifts</div>
          <div><b style={{ color: story.net_pnl >= 0 ? GREEN : RED }}>₹{Math.round(story.net_pnl || 0).toLocaleString("en-IN")}</b> P&L</div>
        </div>
      )}

      <div style={{ maxHeight: 500, overflowY: "auto" }}>
        {events.slice().reverse().map((e, i) => (
          <div key={i} style={{
            background: BG, borderLeft: `3px solid ${
              e.type === "OI_WALL_SHIFT" ? "#fb5607" :
              e.type?.startsWith("HIDDEN_") ? "#a855f7" :
              e.type === "REGIME_CHANGE" ? "#0a84ff" :
              e.type?.startsWith("TRADE_") ? "#26a69a" :
              "#666"
            }`,
            borderRadius: 4,
            padding: "10px 12px",
            marginBottom: 6,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
              <span style={{ color: "#fff", fontWeight: 700, fontSize: 12 }}>{e.title}</span>
              <span style={{ color: "#666", fontSize: 10 }}>{e.time_str}</span>
            </div>
            {e.math && (
              <div style={{ fontSize: 10, color: "#bbb", marginTop: 2 }}>
                <span style={{ color: ACCENT, fontWeight: 700 }}>📊 Math:</span> {e.math}
              </div>
            )}
            {e.why && (
              <div style={{ fontSize: 10, color: "#bbb", marginTop: 2 }}>
                <span style={{ color: GREEN, fontWeight: 700 }}>💡 Why:</span> {e.why}
              </div>
            )}
            {e.trap && (
              <div style={{ fontSize: 10, color: "#fbbf24", marginTop: 2, fontStyle: "italic" }}>
                <span style={{ fontWeight: 700 }}>🎯 Trap:</span> {e.trap}
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
