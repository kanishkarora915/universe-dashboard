/**
 * Section D — Signal Sidebar (per spec §8.1)
 * Current signal card · Last 5 signals history · Trap zones · Recommended strike with confidence ring
 */

import React from "react";

const COLOR_CE = "#00ff88";
const COLOR_PE = "#ff3366";

function fmtRupees(n) {
  if (n === null || n === undefined) return "—";
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function ConfidenceRing({ confidence, size = 80 }) {
  const c = (confidence || 0) / 100;
  const radius = size / 2 - 6;
  const circ = 2 * Math.PI * radius;
  const offset = circ * (1 - c);
  const color = c >= 0.85 ? "#00ff88" : c >= 0.7 ? "#ffaa00" : "#888";

  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size/2} cy={size/2} r={radius} stroke="#1a2030" strokeWidth={6} fill="none" />
        <circle cx={size/2} cy={size/2} r={radius} stroke={color} strokeWidth={6} fill="none"
                strokeDasharray={circ} strokeDashoffset={offset}
                strokeLinecap="round" transform={`rotate(-90 ${size/2} ${size/2})`} />
      </svg>
      <div style={{
        position: "absolute", inset: 0,
        display: "flex", alignItems: "center", justifyContent: "center",
        flexDirection: "column",
      }}>
        <div style={{ fontSize: 16, fontWeight: 800, color }}>{Math.round(confidence || 0)}%</div>
        <div style={{ fontSize: 8, color: "#666" }}>conf</div>
      </div>
    </div>
  );
}

export default function SignalSidebar({ activeSignal, recentSignals, trapZones, regime, snapshot }) {
  const sig = activeSignal;

  return (
    <div style={wrap}>
      {/* CURRENT SIGNAL */}
      <Section title="Current Signal">
        {sig && sig.signal_type && sig.signal_type.startsWith("BUY_") ? (
          <div style={{
            background: sig.signal_type === "BUY_CE" ? "#00ff8811" : "#ff336611",
            border: `1px solid ${sig.signal_type === "BUY_CE" ? COLOR_CE : COLOR_PE}`,
            borderRadius: 10,
            padding: 14,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
              <div>
                <div style={{
                  fontSize: 18, fontWeight: 900,
                  color: sig.signal_type === "BUY_CE" ? COLOR_CE : COLOR_PE,
                }}>
                  {sig.signal_type} {sig.strike}
                </div>
                <div style={{ fontSize: 11, color: "#888", marginTop: 2 }}>
                  {sig.regime} · {sig.lot_size} qty/lot
                </div>
              </div>
              <ConfidenceRing confidence={sig.confidence} />
            </div>

            <div style={{
              display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginTop: 12,
            }}>
              <Stat label="Premium" value={fmtRupees(sig.premium)} color="#fff" />
              <Stat label="SL" value={fmtRupees(sig.stop_loss_premium)} color={COLOR_PE} />
              <Stat label="Target" value={fmtRupees(sig.target_premium)} color={COLOR_CE} />
            </div>

            {sig.suggested_lots > 0 && (
              <div style={{
                marginTop: 8, padding: 8, background: "#0a0e1a",
                borderRadius: 6, fontSize: 11, color: "#aaa",
              }}>
                Suggested lots: <b style={{ color: "#fff" }}>{sig.suggested_lots}</b>
                {" "}({sig.suggested_lots * sig.lot_size} qty · {fmtRupees((sig.premium || 0) * sig.suggested_lots * sig.lot_size)} cost)
              </div>
            )}

            {sig.expected_duration_mins && (
              <div style={{ fontSize: 10, color: "#777", marginTop: 6 }}>
                Expected duration: ~{sig.expected_duration_mins} mins
              </div>
            )}
            {sig.reasoning && (
              <div style={{ fontSize: 10, color: "#888", marginTop: 6, fontStyle: "italic", lineHeight: 1.4 }}>
                {sig.reasoning}
              </div>
            )}
          </div>
        ) : (
          <div style={{
            color: "#555", padding: 14, textAlign: "center", fontSize: 11,
            background: "#0a0e1a", border: "1px dashed #222", borderRadius: 8,
          }}>
            {regime === "CHURN" ? "Churn — no trade. Theta zone." :
             regime === "TRANSITIONING" ? "Transitioning — wait for regime confirmation." :
             "No active signal. Watching..."}
          </div>
        )}
      </Section>

      {/* TRAP ZONES */}
      <Section title="Trap Zones">
        {trapZones ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {trapZones.bull_trap && (
              <div style={zoneRow}>
                <span style={{ color: "#ff3366", fontSize: 10, fontWeight: 700 }}>↑ BULL TRAP CAP</span>
                <span style={{ color: "#fff", fontWeight: 800 }}>
                  {trapZones.bull_trap.upper_bound}
                </span>
              </div>
            )}
            {trapZones.bear_trap && (
              <div style={zoneRow}>
                <span style={{ color: "#00ff88", fontSize: 10, fontWeight: 700 }}>↓ BEAR TRAP FLOOR</span>
                <span style={{ color: "#fff", fontWeight: 800 }}>
                  {trapZones.bear_trap.lower_bound}
                </span>
              </div>
            )}
            {trapZones.display && (
              <div style={{
                fontSize: 10, color: "#888", lineHeight: 1.4,
                fontStyle: "italic", marginTop: 4,
              }}>
                {trapZones.display}
              </div>
            )}
          </div>
        ) : (
          <div style={{ color: "#555", fontSize: 11, textAlign: "center", padding: 10 }}>
            No trap data yet.
          </div>
        )}
      </Section>

      {/* RECENT SIGNALS HISTORY */}
      <Section title="Last 5 Signals">
        {recentSignals && recentSignals.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 200, overflowY: "auto" }}>
            {recentSignals.slice(0, 5).map((s, i) => (
              <div key={s.id || i} style={{
                padding: "6px 8px",
                background: "#0a0e1a",
                borderRadius: 4,
                borderLeft: `3px solid ${s.signal_type === "BUY_CE" ? COLOR_CE : COLOR_PE}`,
                fontSize: 10,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: s.signal_type === "BUY_CE" ? COLOR_CE : COLOR_PE, fontWeight: 700 }}>
                    {s.signal_type} {s.strike}
                  </span>
                  <span style={{ color: "#666" }}>{fmtTime(s.ts)}</span>
                </div>
                <div style={{ color: "#888", marginTop: 2 }}>
                  ₹{s.premium} · conf {Math.round(s.confidence)}% · {s.regime}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ color: "#555", fontSize: 11, textAlign: "center", padding: 10 }}>
            No signals yet today.
          </div>
        )}
      </Section>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{
        fontSize: 10, color: "#888", fontWeight: 700,
        textTransform: "uppercase", letterSpacing: 1, marginBottom: 6,
      }}>{title}</div>
      {children}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div style={{ background: "#0a0e1a", borderRadius: 4, padding: "6px 8px" }}>
      <div style={{ fontSize: 8, color: "#666", fontWeight: 700, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 12, color, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

const wrap = {
  background: "#0a0e1a",
  border: "1px solid #1a2030",
  borderRadius: 12,
  padding: 14,
};

const zoneRow = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "6px 8px",
  background: "#1a203044",
  borderRadius: 4,
  fontSize: 11,
};
