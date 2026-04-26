/**
 * Section B — 3-Panel Chart (per spec §8.1)
 * Panel 1: Spot (white) + Future (cyan) overlay
 * Panel 2: Future Premium oscillator (area, ±5/±10 bands)
 * Panel 3: Trinity Deviation histogram + line (±5/±15/±30 bands, trap shading)
 *
 * Uses Recharts (per spec §8.2).
 */

import React, { useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer,
  AreaChart, Area, ReferenceLine, ReferenceArea, Bar, ComposedChart,
} from "recharts";

const PANEL_BG = "#0a0e1a";
const BORDER = "#1a2030";
const SPOT_COLOR = "#ffffff";
const FUTURE_COLOR = "#00d4ff";
const SYNTH_COLOR = "#ffaa00";

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div style={{
      background: "#0a0e1aee", border: `1px solid ${BORDER}`,
      borderRadius: 6, padding: "8px 10px", fontSize: 11, color: "#ccc",
    }}>
      <div style={{ color: "#888", marginBottom: 4 }}>{fmtTime(label)}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontWeight: 600 }}>
          {p.name}: {typeof p.value === "number" ? p.value.toFixed(2) : p.value}
        </div>
      ))}
    </div>
  );
}

export default function ThreePanelChart({ data, premiumBaseline = 0 }) {
  const safeData = useMemo(() => {
    if (!Array.isArray(data)) return [];
    return data.map(d => ({
      ...d,
      ts: d.ts,
      spot: d.spot,
      future: d.future,
      synthetic: d.synthetic,
      deviation: d.deviation,
      premium: d.premium,
      premium_minus_baseline: (d.premium || 0) - premiumBaseline,
    }));
  }, [data, premiumBaseline]);

  if (safeData.length === 0) {
    return (
      <div style={panelWrap}>
        <div style={{ color: "#555", padding: 60, textAlign: "center", fontSize: 12 }}>
          Building data... need ~30 seconds of ticks for chart.
        </div>
      </div>
    );
  }

  // Compute Y-axis domains for nice scaling
  const minSpot = Math.min(...safeData.map(d => Math.min(d.spot || 1e9, d.future || 1e9)));
  const maxSpot = Math.max(...safeData.map(d => Math.max(d.spot || 0, d.future || 0)));
  const padSpot = (maxSpot - minSpot) * 0.1 || 5;

  const maxAbsDev = Math.max(20, ...safeData.map(d => Math.abs(d.deviation || 0))) + 5;

  const minPrem = Math.min(...safeData.map(d => d.premium_minus_baseline || 0));
  const maxPrem = Math.max(...safeData.map(d => d.premium_minus_baseline || 0));

  return (
    <div style={panelWrap}>
      {/* PANEL 1 — Spot + Future overlay */}
      <PanelTitle text="Panel 1 — Spot (white) + Future (cyan)" />
      <div style={{ height: 200, marginBottom: 12 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={safeData}>
            <CartesianGrid stroke="#1a2030" strokeDasharray="2 4" />
            <XAxis dataKey="ts" tickFormatter={fmtTime} stroke="#555" tick={{ fontSize: 9 }} minTickGap={50} />
            <YAxis domain={[minSpot - padSpot, maxSpot + padSpot]} stroke="#555" tick={{ fontSize: 9 }} width={60} />
            <Tooltip content={<CustomTooltip />} />
            <Line type="monotone" dataKey="spot" name="Spot" stroke={SPOT_COLOR} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="future" name="Future" stroke={FUTURE_COLOR} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="synthetic" name="Synthetic" stroke={SYNTH_COLOR} strokeWidth={1} strokeDasharray="3 3" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* PANEL 2 — Premium oscillator */}
      <PanelTitle text="Panel 2 — Future Premium (deviation from 5-min baseline)" />
      <div style={{ height: 140, marginBottom: 12 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={safeData}>
            <defs>
              <linearGradient id="gradGreen" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#00ff88" stopOpacity={0.6} />
                <stop offset="100%" stopColor="#00ff88" stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="gradRed" x1="0" y1="1" x2="0" y2="0">
                <stop offset="0%" stopColor="#ff3366" stopOpacity={0.6} />
                <stop offset="100%" stopColor="#ff3366" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1a2030" strokeDasharray="2 4" />
            <XAxis dataKey="ts" tickFormatter={fmtTime} stroke="#555" tick={{ fontSize: 9 }} minTickGap={50} />
            <YAxis stroke="#555" tick={{ fontSize: 9 }} width={60}
                   domain={[Math.min(-12, minPrem - 2), Math.max(12, maxPrem + 2)]} />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={0} stroke="#666" strokeWidth={1} />
            <ReferenceLine y={5} stroke="#444" strokeDasharray="3 3" />
            <ReferenceLine y={-5} stroke="#444" strokeDasharray="3 3" />
            <ReferenceLine y={10} stroke="#666" strokeDasharray="3 3" />
            <ReferenceLine y={-10} stroke="#666" strokeDasharray="3 3" />
            <Area type="monotone" dataKey="premium_minus_baseline" name="Premium Δ"
                  stroke="#00d4ff" strokeWidth={2}
                  fill="url(#gradGreen)" isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* PANEL 3 — Trinity Deviation (THE KEY CHART) */}
      <PanelTitle text="Panel 3 — Trinity Deviation (synthetic − spot) ★ KEY METRIC" />
      <div style={{ height: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={safeData}>
            <CartesianGrid stroke="#1a2030" strokeDasharray="2 4" />
            <XAxis dataKey="ts" tickFormatter={fmtTime} stroke="#555" tick={{ fontSize: 9 }} minTickGap={50} />
            <YAxis stroke="#555" tick={{ fontSize: 9 }} width={60}
                   domain={[-maxAbsDev, maxAbsDev]} />
            <Tooltip content={<CustomTooltip />} />

            {/* Reference bands */}
            <ReferenceArea y1={-30} y2={-15} fill="#ff336622" />
            <ReferenceArea y1={15} y2={30} fill="#ffaa0022" />
            <ReferenceArea y1={-15} y2={-5} fill="#ff336611" />
            <ReferenceArea y1={5} y2={15} fill="#ffaa0011" />

            <ReferenceLine y={0} stroke="#666" strokeWidth={1} />
            <ReferenceLine y={5} stroke="#444" strokeDasharray="3 3" label={{ value: "+5", fill: "#666", fontSize: 9, position: "right" }} />
            <ReferenceLine y={-5} stroke="#444" strokeDasharray="3 3" label={{ value: "-5", fill: "#666", fontSize: 9, position: "right" }} />
            <ReferenceLine y={15} stroke="#ffaa00" strokeDasharray="3 3" label={{ value: "+15 WARN", fill: "#ffaa00", fontSize: 9, position: "right" }} />
            <ReferenceLine y={-15} stroke="#ffaa00" strokeDasharray="3 3" label={{ value: "-15 WARN", fill: "#ffaa00", fontSize: 9, position: "right" }} />
            <ReferenceLine y={30} stroke="#ff3366" strokeDasharray="3 3" label={{ value: "+30 EXTREME", fill: "#ff3366", fontSize: 9, position: "right" }} />
            <ReferenceLine y={-30} stroke="#ff3366" strokeDasharray="3 3" label={{ value: "-30 EXTREME", fill: "#ff3366", fontSize: 9, position: "right" }} />

            <Bar dataKey="deviation" name="Deviation" isAnimationActive={false}>
              {safeData.map((entry, i) => (
                <Bar key={i} fill={(entry.deviation || 0) >= 0 ? "#00ff8888" : "#ff336688"} />
              ))}
            </Bar>
            <Line type="monotone" dataKey="deviation" stroke="#fff" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function PanelTitle({ text }) {
  return (
    <div style={{
      fontSize: 10, color: "#888", fontWeight: 700, textTransform: "uppercase",
      letterSpacing: 1, marginBottom: 4, marginTop: 4,
    }}>{text}</div>
  );
}

const panelWrap = {
  background: PANEL_BG,
  border: `1px solid ${BORDER}`,
  borderRadius: 12,
  padding: "14px 16px",
};
