/**
 * Section A — Header Strip (per spec §8.1)
 * Regime badge · Confidence % · Active signal · Connection dot
 */

import React from "react";

const COLOR = {
  REAL_RALLY: "#00ff88",
  REAL_CRASH: "#ff3366",
  BULL_TRAP: "#ffaa00",
  BEAR_TRAP: "#ffaa00",
  DISTRIBUTION: "#ff8800",
  ACCUMULATION: "#00aaff",
  CHURN: "#555555",
  TRANSITIONING: "#888888",
  UNKNOWN: "#666666",
};

const REGIME_LABEL = {
  REAL_RALLY: "🟢🟢 REAL RALLY",
  REAL_CRASH: "🔴🔴 REAL CRASH",
  BULL_TRAP: "🔴 BULL TRAP",
  BEAR_TRAP: "🟢 BEAR TRAP",
  DISTRIBUTION: "⚠️ DISTRIBUTION",
  ACCUMULATION: "⚠️ ACCUMULATION",
  CHURN: "⚪ CHURN",
  TRANSITIONING: "⏳ TRANSITIONING",
  UNKNOWN: "● UNKNOWN",
};


export default function HeaderStrip({ regime, confidence, activeSignal, connected, status, snapshot }) {
  const c = COLOR[regime] || "#666";
  return (
    <div style={{
      background: "#0a0e1a",
      border: `1px solid #1a2030`,
      borderRadius: 12,
      padding: "14px 18px",
      display: "flex",
      flexWrap: "wrap",
      gap: 16,
      alignItems: "center",
      justifyContent: "space-between",
    }}>
      {/* Left: Regime + confidence */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 10, color: "#666", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
            Regime
          </div>
          <div style={{
            fontSize: 18, fontWeight: 800, color: c, marginTop: 2,
            padding: "4px 12px", background: `${c}22`, border: `1px solid ${c}66`, borderRadius: 8,
          }}>
            {REGIME_LABEL[regime] || regime}
          </div>
        </div>

        <div style={{
          height: 40, width: 1, background: "#222",
        }} />

        <div>
          <div style={{ fontSize: 10, color: "#666", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
            Confidence
          </div>
          <div style={{
            fontSize: 22, fontWeight: 900,
            color: confidence >= 80 ? "#00ff88" : confidence >= 60 ? "#ffaa00" : "#888",
          }}>
            {confidence !== null && confidence !== undefined ? Math.round(confidence) : 0}%
          </div>
        </div>

        {snapshot?.spot && (
          <>
            <div style={{ height: 40, width: 1, background: "#222" }} />
            <div>
              <div style={{ fontSize: 10, color: "#666", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
                NIFTY
              </div>
              <div style={{ fontSize: 18, fontWeight: 800, color: "#fff" }}>{snapshot.spot}</div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: "#666", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
                FUT
              </div>
              <div style={{ fontSize: 18, fontWeight: 800, color: "#00d4ff" }}>{snapshot.future}</div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: "#666", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
                Synthetic
              </div>
              <div style={{ fontSize: 18, fontWeight: 800, color: "#ffaa00" }}>{snapshot.synthetic}</div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: "#666", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
                Trinity Δ
              </div>
              <div style={{
                fontSize: 18, fontWeight: 800,
                color: Math.abs(snapshot.deviation || 0) > 15 ? "#ffaa00" : "#fff",
              }}>
                {(snapshot.deviation || 0) >= 0 ? "+" : ""}{snapshot.deviation}
              </div>
            </div>
          </>
        )}
      </div>

      {/* Right: Active signal + connection */}
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        {activeSignal && activeSignal.signal_type && (
          <div style={{
            background: activeSignal.signal_type === "BUY_CE" ? "#00ff8822" : "#ff336622",
            border: `1px solid ${activeSignal.signal_type === "BUY_CE" ? "#00ff88" : "#ff3366"}`,
            color: activeSignal.signal_type === "BUY_CE" ? "#00ff88" : "#ff3366",
            padding: "6px 14px", borderRadius: 8,
            fontSize: 13, fontWeight: 800,
          }}>
            {activeSignal.signal_type} {activeSignal.strike} @ ₹{activeSignal.premium}
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{
            display: "inline-block", width: 10, height: 10, borderRadius: "50%",
            background: connected ? "#00ff88" : "#ff3366",
            boxShadow: connected ? "0 0 8px #00ff88" : "0 0 8px #ff3366",
          }} />
          <span style={{ fontSize: 11, color: "#888" }}>
            {connected ? "LIVE" : "DISCONNECTED"}
          </span>
        </div>
      </div>
    </div>
  );
}
