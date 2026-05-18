/**
 * Section C — Strike Heatmap (per spec §8.1)
 * 9-cell grid (4 PE | ATM | 4 CE) showing strike, LTP, OI%, deviation.
 * Color intensity = stress level (|deviation|).
 */

import React from "react";

function stressColor(deviation) {
  const abs = Math.abs(deviation || 0);
  if (abs >= 30) return "#ff3366";
  if (abs >= 15) return "#ffaa00";
  if (abs >= 5) return "#ffdd00";
  return "#444";
}

function fmtL(n) {
  if (!n) return "0";
  if (Math.abs(n) >= 100000) return `${(n / 100000).toFixed(1)}L`;
  return Math.round(n).toLocaleString("en-IN");
}

export default function StrikeHeatmap({ heatmap }) {
  if (!heatmap || !Array.isArray(heatmap.strikes) || heatmap.strikes.length === 0) {
    return (
      <div style={wrap}>
        <Title>Strike Heatmap</Title>
        <div style={{ color: "#555", padding: 30, textAlign: "center", fontSize: 12 }}>
          Loading 9-strike data...
        </div>
      </div>
    );
  }

  const spot = heatmap.spot;
  const atm = heatmap.atm;
  const strikes = heatmap.strikes; // sorted by offset

  return (
    <div style={wrap}>
      <Title>
        Strike Heatmap — Synthetic Stress Map
        <span style={{ marginLeft: 10, fontSize: 11, color: "#888", fontWeight: 400 }}>
          Spot: {spot} · ATM: {atm}
        </span>
      </Title>

      <div style={{
        display: "grid",
        gridTemplateColumns: `repeat(${strikes.length}, 1fr)`,
        gap: 6,
      }}>
        {strikes.map((s) => {
          const isATM = s.strike === atm;
          const stressC = stressColor(s.deviation);
          const intensity = Math.min(1, Math.abs(s.deviation || 0) / 30);
          const bg = `rgba(${parseInt(stressC.slice(1,3),16)}, ${parseInt(stressC.slice(3,5),16)}, ${parseInt(stressC.slice(5,7),16)}, ${0.1 + intensity * 0.4})`;

          return (
            <div key={s.strike} style={{
              background: bg,
              border: `1px solid ${isATM ? "#00d4ff" : stressC}66`,
              borderRadius: 8,
              padding: "10px 8px",
              textAlign: "center",
              boxShadow: isATM ? "0 0 8px #00d4ff44" : "none",
            }}>
              <div style={{
                fontSize: 9, color: "#888", fontWeight: 700,
                textTransform: "uppercase", letterSpacing: 0.5,
              }}>
                {isATM ? "ATM" : (s.offset_pts >= 0 ? `+${s.offset_pts}` : s.offset_pts)}
              </div>
              <div style={{ fontSize: 13, color: "#fff", fontWeight: 800, marginTop: 2 }}>
                {s.strike}
              </div>
              <div style={{
                fontSize: 14, fontWeight: 800, marginTop: 4,
                color: (s.deviation || 0) >= 0 ? "#00ff88" : "#ff3366",
              }}>
                {(s.deviation || 0) >= 0 ? "+" : ""}{(s.deviation || 0).toFixed(1)}
              </div>
              <div style={{ fontSize: 9, color: "#777", marginTop: 4, lineHeight: 1.4 }}>
                <div>CE: {s.ce_ltp?.toFixed?.(1) || 0}</div>
                <div>PE: {s.pe_ltp?.toFixed?.(1) || 0}</div>
              </div>
              <div style={{ fontSize: 8, color: "#666", marginTop: 4 }}>
                CE OI: {fmtL(s.ce_oi)}<br />
                PE OI: {fmtL(s.pe_oi)}
              </div>
              <div style={{
                fontSize: 8, color: stressC, marginTop: 4, fontWeight: 700,
              }}>
                w {(s.weight * 100).toFixed(0)}%
              </div>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div style={{
        display: "flex", gap: 14, marginTop: 12, fontSize: 10, color: "#666",
        justifyContent: "center", flexWrap: "wrap",
      }}>
        <Legend color="#444" label="Normal (|Δ|<5)" />
        <Legend color="#ffdd00" label="Watch (|Δ|>5)" />
        <Legend color="#ffaa00" label="Warning (|Δ|>15)" />
        <Legend color="#ff3366" label="Extreme (|Δ|>30)" />
      </div>
    </div>
  );
}

function Title({ children }) {
  return (
    <div style={{
      fontSize: 11, color: "#888", fontWeight: 700,
      textTransform: "uppercase", letterSpacing: 1, marginBottom: 10,
    }}>{children}</div>
  );
}

function Legend({ color, label }) {
  return (
    <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <span style={{ display: "inline-block", width: 10, height: 10, background: color, borderRadius: 2 }} />
      {label}
    </span>
  );
}

const wrap = {
  background: "#0a0e1a",
  border: "1px solid #1a2030",
  borderRadius: 12,
  padding: "14px 16px",
};
