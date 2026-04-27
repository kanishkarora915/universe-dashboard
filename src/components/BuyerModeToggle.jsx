/**
 * BuyerModeToggle — One-click philosophy switch.
 *
 * HEDGER (capital protection) ↔ BUYER (trend-riding).
 * Affects: BE threshold, trail give-back, reversal exit, T1 partial,
 *          scalper max-hold, scalper cooldowns.
 */

import React, { useState, useEffect } from "react";

const GREEN = "#10b981";
const RED = "#ef4444";
const YELLOW = "#f59e0b";
const BLUE = "#3b82f6";
const GRAY = "#6b7280";
const CARD = "#111823";
const BG = "#0b0f14";
const BORDER = "#1f2937";

async function safeFetch(url, fb) {
  try { const r = await fetch(url); if (!r.ok) return fb; return await r.json(); } catch { return fb; }
}
async function postJSON(url, body = {}) {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await r.json();
  } catch { return null; }
}

const ROW = ({ label, hedger, buyer, isBuyer }) => (
  <tr style={{ borderBottom: `1px solid ${BORDER}33` }}>
    <td style={{ padding: "6px 10px", color: "#aaa", fontSize: 11 }}>{label}</td>
    <td style={{
      padding: "6px 10px", textAlign: "center", fontSize: 11, fontWeight: 600,
      color: isBuyer ? GRAY : "#fff",
      background: !isBuyer ? `${BLUE}22` : "transparent",
    }}>{hedger}</td>
    <td style={{
      padding: "6px 10px", textAlign: "center", fontSize: 11, fontWeight: 600,
      color: isBuyer ? "#fff" : GRAY,
      background: isBuyer ? `${GREEN}22` : "transparent",
    }}>{buyer}</td>
  </tr>
);

export default function BuyerModeToggle() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showCompare, setShowCompare] = useState(false);

  const load = async () => {
    const d = await safeFetch("/api/buyer-mode", null);
    if (d && !d.error) setData(d);
  };

  useEffect(() => {
    load();
    const iv = setInterval(load, 30_000);
    return () => clearInterval(iv);
  }, []);

  const toggle = async () => {
    setLoading(true);
    await postJSON("/api/buyer-mode/toggle");
    await load();
    setLoading(false);
  };

  if (!data) {
    return (
      <div style={wrap}>
        <div style={{ color: GRAY, fontSize: 11 }}>Loading mode...</div>
      </div>
    );
  }

  const isBuyer = data.is_buyer;
  const accentColor = isBuyer ? GREEN : BLUE;
  const modeLabel = isBuyer ? "BUYER MODE" : "HEDGER MODE";
  const philosophy = isBuyer
    ? "Trend-riding · Hold winners · Big profit hunting"
    : "Capital protection · Quick exits · Conservative";

  return (
    <div style={wrap}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ color: "#888", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
            🧠 Trading Philosophy
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
            <span style={{
              fontSize: 18, fontWeight: 900, color: accentColor,
              padding: "4px 14px",
              background: `${accentColor}22`,
              border: `1px solid ${accentColor}66`,
              borderRadius: 8,
            }}>
              {isBuyer ? "🚀" : "🛡️"} {modeLabel}
            </span>
          </div>
          <div style={{ color: GRAY, fontSize: 11, marginTop: 4 }}>{philosophy}</div>
        </div>

        {/* Toggle button */}
        <div style={{ display: "flex", gap: 6 }}>
          <button onClick={() => setShowCompare(!showCompare)} style={{
            background: "transparent",
            color: "#888",
            border: `1px solid ${BORDER}`,
            padding: "6px 12px", borderRadius: 6, fontSize: 11, fontWeight: 600,
            cursor: "pointer",
          }}>
            {showCompare ? "Hide" : "Show"} Compare
          </button>
          <button onClick={toggle} disabled={loading} style={{
            background: isBuyer ? BLUE : GREEN,
            color: "#fff",
            border: "none",
            padding: "8px 18px",
            borderRadius: 6,
            fontSize: 12, fontWeight: 800,
            cursor: loading ? "wait" : "pointer",
            letterSpacing: 0.5,
          }}>
            {loading ? "..." : (isBuyer ? "→ HEDGER" : "→ BUYER")}
          </button>
        </div>
      </div>

      {/* Active thresholds summary */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
        gap: 8,
      }}>
        <Stat label="Breakeven At" value={`+${data.thresholds.breakeven_pct}%`} hint={isBuyer ? "Late lock" : "Quick lock"} />
        <Stat label="Trail Give-back" value={`${data.thresholds.trail_giveback_pct}%`} hint={isBuyer ? "Loose" : "Tight"} />
        <Stat label="Reversal Exit" value={`${data.thresholds.reversal_exit_pct}%`} hint="after 10 min" />
        <Stat
          label="T1 Partial Book"
          value={data.thresholds.t1_partial_booking ? "ON 50%" : "OFF (full ride)"}
          hint={isBuyer ? "Ride to T2" : "Book half"}
        />
        <Stat label="Scalper Hold" value={`${data.thresholds.scalper_max_hold_min}m`} hint={isBuyer ? "Long" : "Quick"} />
        <Stat label="Conviction Exit" value={data.thresholds.conviction_exit_enabled ? "ON" : "OFF"} hint="engine flip" />
      </div>

      {/* Comparison table */}
      {showCompare && (
        <div style={{ marginTop: 14, overflow: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ borderBottom: `2px solid ${BORDER}` }}>
                <th style={{ padding: "8px 10px", textAlign: "left", color: "#666", fontSize: 10, fontWeight: 700 }}>Setting</th>
                <th style={{ padding: "8px 10px", textAlign: "center", color: BLUE, fontSize: 10, fontWeight: 700 }}>
                  🛡️ HEDGER
                </th>
                <th style={{ padding: "8px 10px", textAlign: "center", color: GREEN, fontSize: 10, fontWeight: 700 }}>
                  🚀 BUYER
                </th>
              </tr>
            </thead>
            <tbody>
              <ROW label="Breakeven trigger" hedger="+2%" buyer="+20%" isBuyer={isBuyer} />
              <ROW label="Peak trail give-back" hedger="50%" buyer="25%" isBuyer={isBuyer} />
              <ROW label="Tight trail trigger" hedger="@+35% lock 75%" buyer="@+60% lock 85%" isBuyer={isBuyer} />
              <ROW label="Reversal exit" hedger="-3% after 10min" buyer="-8% after 10min" isBuyer={isBuyer} />
              <ROW label="T1 partial book" hedger="50% qty" buyer="OFF (full ride)" isBuyer={isBuyer} />
              <ROW label="Conviction exit" hedger="<50% triggers BE" buyer="DISABLED" isBuyer={isBuyer} />
              <ROW label="Engine flip cycles" hedger="1" buyer="3" isBuyer={isBuyer} />
              <ROW label="Scalper max hold" hedger="30 min" buyer="180 min" isBuyer={isBuyer} />
              <ROW label="Scalper same-strike cooldown" hedger="10 min" buyer="2 min" isBuyer={isBuyer} />
              <ROW label="Scalper SL" hedger="12%" buyer="18%" isBuyer={isBuyer} />
              <ROW label="Scalper T1 / T2" hedger="+20% / +40%" buyer="+50% / +100%" isBuyer={isBuyer} />
            </tbody>
          </table>

          <div style={{ marginTop: 10, padding: 10, background: BG, borderRadius: 6, fontSize: 11, color: "#aaa", lineHeight: 1.5 }}>
            <strong style={{ color: isBuyer ? GREEN : BLUE }}>
              {isBuyer ? "🚀 BUYER MODE" : "🛡️ HEDGER MODE"} active
            </strong>
            <br />
            {isBuyer
              ? "System will now hold winners longer, ignore short-term noise, and ride trends to T2 without partial booking. Wider SL accepts more drawdown for bigger upside."
              : "System will lock profits quickly at +2%, exit on conviction drops, book 50% at T1. Conservative — protects capital but caps upside."}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, hint }) {
  return (
    <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 6, padding: "8px 10px" }}>
      <div style={{ fontSize: 9, color: "#666", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>
        {label}
      </div>
      <div style={{ fontSize: 13, color: "#fff", fontWeight: 700, marginTop: 2 }}>{value}</div>
      {hint && <div style={{ fontSize: 9, color: "#666", marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

const wrap = {
  background: CARD,
  border: `1px solid ${BORDER}`,
  borderRadius: 10,
  padding: 14,
  marginTop: 12,
};
