/**
 * AutopsyMindWidget — Pattern-based predictive alerts from Smart Autopsy Mind.
 *
 * Consumes /api/mind/summary/{index} to show:
 *  - Today's top similar past days (with % similarity)
 *  - Predicted direction + confidence
 *  - Narrative explanation ("Aaj ka din Apr 17 jaisa — tab +130 pts gaya")
 *  - Count of days learned
 */

import React, { useEffect, useState } from "react";

const GREEN = "#10b981";
const RED = "#ef4444";
const YELLOW = "#f59e0b";
const BLUE = "#3b82f6";
const GRAY = "#6b7280";
const BG = "#0b0f14";
const CARD = "#111823";
const BORDER = "#1f2937";

function dirColor(dir) {
  if (!dir) return GRAY;
  if (dir.includes("UP")) return GREEN;
  if (dir.includes("DOWN")) return RED;
  if (dir === "V_REVERSAL") return YELLOW;
  return BLUE;
}

function dirIcon(dir) {
  if (!dir) return "•";
  if (dir.includes("UP")) return "▲";
  if (dir.includes("DOWN")) return "▼";
  if (dir === "V_REVERSAL") return "↻";
  return "—";
}

export default function AutopsyMindWidget({ index = "NIFTY", apiBase = "" }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);

  const fetchSummary = async () => {
    try {
      const r = await fetch(`${apiBase}/api/mind/summary/${index}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setData(j);
      setErr(null);
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSummary();
    const iv = setInterval(fetchSummary, 60_000);
    return () => clearInterval(iv);
  }, [index]);

  const recordNow = async () => {
    try {
      await fetch(`${apiBase}/api/mind/record-now/${index}`, { method: "POST" });
      await fetchSummary();
    } catch (e) {
      console.error("[MIND] record-now failed", e);
    }
  };

  if (loading) {
    return (
      <div style={wrapStyle}>
        <div style={{ color: GRAY, fontSize: 12 }}>🧠 Mind loading…</div>
      </div>
    );
  }

  if (err) {
    return (
      <div style={wrapStyle}>
        <div style={{ color: RED, fontSize: 12 }}>🧠 Mind error: {err}</div>
      </div>
    );
  }

  const prediction = data?.prediction || {};
  const similar = data?.similar || [];
  const narrative = data?.narrative || "";
  const stats = data?.stats || {};
  const daysLearned = stats.days_recorded || 0;

  const predDir = prediction.predicted_direction || "UNKNOWN";
  const predConf = prediction.confidence_pct || 0;
  const predNarr = prediction.narrative || "Not enough historical data to predict.";

  return (
    <div style={wrapStyle}>
      {/* Header */}
      <div style={headerRow}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 18 }}>🧠</span>
          <div>
            <div style={titleStyle}>Smart Autopsy Mind</div>
            <div style={{ fontSize: 11, color: GRAY, marginTop: 2 }}>
              {index} · Learned from {daysLearned} past days
            </div>
          </div>
        </div>
        <button onClick={recordNow} style={btnStyle} title="Force record today's pattern">
          Record Now
        </button>
      </div>

      {/* Prediction Card */}
      <div style={predCardStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <div style={{ fontSize: 11, color: GRAY, textTransform: "uppercase", letterSpacing: 0.5 }}>
            Today's Prediction
          </div>
          <div
            style={{
              fontSize: 10,
              padding: "2px 8px",
              borderRadius: 10,
              background: `${dirColor(predDir)}22`,
              color: dirColor(predDir),
              fontWeight: 600,
            }}
          >
            {predConf.toFixed(0)}% confidence
          </div>
        </div>
        <div
          style={{
            marginTop: 6,
            fontSize: 20,
            fontWeight: 700,
            color: dirColor(predDir),
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span>{dirIcon(predDir)}</span>
          <span>{predDir.replace("_", " ")}</span>
        </div>
        <div style={{ marginTop: 6, fontSize: 12, color: "#d1d5db", lineHeight: 1.5 }}>
          {predNarr}
        </div>
      </div>

      {/* Similar Days */}
      {similar.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div
            style={{
              fontSize: 11,
              color: GRAY,
              textTransform: "uppercase",
              letterSpacing: 0.5,
              marginBottom: 6,
            }}
          >
            Most Similar Past Days
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {similar.slice(0, 5).map((s, i) => (
              <div key={i} style={simRowStyle}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
                  <span
                    style={{
                      fontSize: 10,
                      padding: "2px 6px",
                      borderRadius: 4,
                      background: `${dirColor(s.direction)}22`,
                      color: dirColor(s.direction),
                      fontWeight: 600,
                      flexShrink: 0,
                    }}
                  >
                    {dirIcon(s.direction)} {s.direction}
                  </span>
                  <span style={{ fontSize: 12, color: "#e5e7eb", fontWeight: 500 }}>
                    {s.date}
                  </span>
                  <span
                    style={{
                      fontSize: 11,
                      color: (s.day_change_pct || 0) >= 0 ? GREEN : RED,
                      fontWeight: 600,
                    }}
                  >
                    {(s.day_change_pct || 0) >= 0 ? "+" : ""}
                    {(s.day_change_pct || 0).toFixed(2)}%
                  </span>
                </div>
                <div style={{ fontSize: 11, color: YELLOW, fontWeight: 600, flexShrink: 0 }}>
                  {(s.similarity_pct || 0).toFixed(0)}% match
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Narrative */}
      {narrative && (
        <div
          style={{
            marginTop: 12,
            padding: 10,
            background: "#0f172a",
            border: `1px solid ${BORDER}`,
            borderRadius: 6,
            fontSize: 11,
            color: "#9ca3af",
            fontStyle: "italic",
            lineHeight: 1.5,
          }}
        >
          💡 {narrative}
        </div>
      )}

      {daysLearned === 0 && (
        <div
          style={{
            marginTop: 12,
            padding: 10,
            background: "#1a1305",
            border: `1px solid ${YELLOW}44`,
            borderRadius: 6,
            fontSize: 11,
            color: YELLOW,
          }}
        >
          ⚠️ No patterns learned yet. Mind records daily at 3:25 PM IST or click "Record Now".
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// STYLES
// ═══════════════════════════════════════════════════════════

const wrapStyle = {
  background: CARD,
  border: `1px solid ${BORDER}`,
  borderRadius: 10,
  padding: 14,
  marginTop: 16,
};

const headerRow = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  marginBottom: 12,
};

const titleStyle = {
  fontSize: 14,
  fontWeight: 700,
  color: "#e5e7eb",
};

const btnStyle = {
  background: "transparent",
  border: `1px solid ${BORDER}`,
  color: GRAY,
  fontSize: 10,
  padding: "4px 8px",
  borderRadius: 4,
  cursor: "pointer",
  fontWeight: 500,
};

const predCardStyle = {
  background: BG,
  border: `1px solid ${BORDER}`,
  borderRadius: 8,
  padding: 12,
};

const simRowStyle = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "6px 8px",
  background: BG,
  borderRadius: 4,
  border: `1px solid ${BORDER}`,
};
