/**
 * SmartBiasIndicator
 * ──────────────────
 * Shows what the verdict engine is THINKING right now — including
 * "smart move awareness" adjustments (range position, move exhaustion,
 * capitulation reversal).
 *
 * User insight: system was blindly trend-following — kept buying PE
 * after market already dropped 1%. This component surfaces the new
 * adjustment logic so user sees:
 *   - Raw bull/bear scores
 *   - What got penalized (e.g. "BEAR halved due to range_pos 22")
 *   - Final action recommendation
 *
 * Drops onto PnL + Scalper tabs at top.
 */

import { memo } from "react";
import useSWRPoll from "../hooks/useSWRPoll";

const C = {
  bg: "#15151F",
  border: "#262636",
  dim: "#888",
  green: "#30D158",
  red: "#FF453A",
  yellow: "#FFD60A",
  blue: "#0A84FF",
  orange: "#FF9F0A",
  purple: "#BF5AF2",
};


function SmartBiasIndicatorImpl() {
  // /api/trades/why-no-trade has the verdict_snapshot + per_index data
  // OR pull from /api/live (lightweight) — but we need bias adjustments.
  // The verdict includes smartBias now.
  const { data } = useSWRPoll("/api/trades/why-no-trade", {
    refreshInterval: 5000,
  });

  const verdict = data?.verdict_snapshot || {};
  const niftyData = verdict.nifty || {};
  const bnData = verdict.banknifty || {};

  return (
    <div style={{
      background: C.bg, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: "10px 12px",
      marginBottom: 8,
    }}>
      <div style={{
        color: C.dim, fontSize: 9, fontWeight: 800,
        letterSpacing: 0.6, textTransform: "uppercase",
        marginBottom: 6,
      }}>
        🧠 Smart Bias — what system is thinking
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <IndexBias label="NIFTY" data={niftyData} />
        <IndexBias label="BANKNIFTY" data={bnData} />
      </div>
    </div>
  );
}


function IndexBias({ label, data }) {
  const action = data.action || "—";
  const winProb = data.winProbability || 0;

  // Smart bias info — exposed by backend smartBias field
  const smart = data.smartBias || {};
  const rangePos = smart.rangePosition;
  const fromOpen = smart.fromOpenPct;
  const adjustments = smart.adjustments || [];
  const verdictType = smart.verdict || "TREND_FOLLOW";

  // Verdict color
  let verdictColor = C.dim;
  let verdictLabel = "—";
  if (verdictType === "TREND_FOLLOW") {
    verdictColor = action.includes("CE") ? C.green : action.includes("PE") ? C.red : C.dim;
    verdictLabel = "TREND FOLLOW";
  } else if (verdictType === "EXHAUSTED_BEAR") {
    verdictColor = C.orange;
    verdictLabel = "BEAR EXHAUSTED";
  } else if (verdictType === "EXHAUSTED_BULL") {
    verdictColor = C.orange;
    verdictLabel = "BULL EXHAUSTED";
  } else if (verdictType === "REVERSAL_FORMING") {
    verdictColor = C.purple;
    verdictLabel = "REVERSAL FORMING";
  }

  // Range position visual
  const rangeBar = rangePos != null ? rangePos : 50;
  const rangeColor =
    rangeBar < 25 ? C.green :    // near low = bounce zone
    rangeBar > 75 ? C.red :       // near high = pullback zone
    C.dim;

  return (
    <div style={{
      background: "rgba(255,255,255,0.02)",
      border: `1px solid ${verdictColor}33`,
      borderRadius: 6, padding: "8px 10px",
    }}>
      {/* Header */}
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 6,
      }}>
        <span style={{ color: "#fff", fontSize: 11, fontWeight: 800, letterSpacing: 0.5 }}>
          {label}
        </span>
        <span style={{
          color: verdictColor, fontSize: 10, fontWeight: 700,
          padding: "2px 6px", borderRadius: 3,
          background: `${verdictColor}22`,
          letterSpacing: 0.4,
        }}>
          {verdictLabel}
        </span>
      </div>

      {/* Verdict line */}
      <div style={{
        fontSize: 11, color: "#ccc", marginBottom: 6,
        fontFamily: "ui-monospace, monospace",
      }}>
        Action: <span style={{ color: verdictColor, fontWeight: 700 }}>{action}</span>
        {" "}@ {winProb}% prob
      </div>

      {/* Range position bar */}
      {rangePos != null && (
        <div style={{ marginBottom: 6 }}>
          <div style={{
            display: "flex", justifyContent: "space-between",
            fontSize: 9, color: C.dim, marginBottom: 2,
          }}>
            <span>Day low</span>
            <span style={{ color: rangeColor, fontWeight: 700 }}>
              {rangePos.toFixed(0)}%
            </span>
            <span>Day high</span>
          </div>
          <div style={{
            position: "relative",
            height: 4,
            background: "rgba(255,255,255,0.08)",
            borderRadius: 2,
          }}>
            <div style={{
              position: "absolute",
              left: 0, top: 0, bottom: 0,
              width: `${rangeBar}%`,
              background: rangeColor,
              borderRadius: 2,
            }} />
          </div>
          {fromOpen != null && (
            <div style={{
              fontSize: 9, color: C.dim, marginTop: 2,
              fontFamily: "ui-monospace, monospace",
            }}>
              From open: {fromOpen >= 0 ? "+" : ""}{fromOpen.toFixed(2)}%
            </div>
          )}
        </div>
      )}

      {/* Adjustments — what got penalized/boosted */}
      {adjustments.length > 0 ? (
        <div style={{
          marginTop: 6, paddingTop: 6,
          borderTop: `1px dashed ${C.border}`,
        }}>
          <div style={{
            color: C.yellow, fontSize: 9, fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.4,
            marginBottom: 4,
          }}>
            ⚠️ Smart adjustments fired
          </div>
          {adjustments.map((adj, i) => (
            <div key={i} style={{
              fontSize: 9, color: "#bbb",
              lineHeight: 1.4, marginBottom: 2,
              paddingLeft: 6, borderLeft: `2px solid ${
                adj.name?.includes("CAPITULATION") ? C.purple :
                adj.name?.includes("RANGE") ? C.orange :
                C.yellow
              }`,
            }}>
              {adj.reason}
            </div>
          ))}
        </div>
      ) : (
        <div style={{
          fontSize: 9, color: C.dim, fontStyle: "italic",
          marginTop: 4,
        }}>
          No bias adjustments — pure trend signal
        </div>
      )}
    </div>
  );
}


// Memo — only re-render when underlying data changes
export default memo(SmartBiasIndicatorImpl);
