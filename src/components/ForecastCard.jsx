/**
 * ForecastCard
 * ────────────
 * Predictive narrative card showing:
 *   • Bias direction + persistence
 *   • Key levels (resistance/support/magnet)
 *   • Expected path (V-bottom / drift / continuation / pin)
 *   • Time horizon
 *   • Buyer action plan (entry/target/SL)
 *   • Why (3-5 reason bullets)
 *   • Confidence 0-10
 *
 * Pulls from /api/forecast/live (refreshes every 30s).
 * Renders one card per index (NIFTY, BANKNIFTY).
 */

import { memo } from "react";
import useSWRPoll from "../hooks/useSWRPoll";

const C = {
  bg: "#15151F",
  card: "#1A1A24",
  border: "#262636",
  dim: "#888",
  text: "#E5E5E5",
  green: "#30D158",
  red: "#FF453A",
  yellow: "#FFD60A",
  blue: "#0A84FF",
  orange: "#FF9F0A",
  purple: "#BF5AF2",
};

// Path-type → color + label
const PATH_STYLE = {
  V_BOTTOM:        { color: C.green,  label: "🟢 V-BOTTOM",       desc: "Reversal up forming" },
  INVERTED_V_TOP:  { color: C.red,    label: "🔴 INVERTED-V TOP",  desc: "Reversal down forming" },
  DRIFT_TO_PAIN:   { color: C.purple, label: "🧲 DRIFT TO PAIN",   desc: "Gravitational pull" },
  PIN:             { color: C.yellow, label: "📌 EXPIRY PIN",      desc: "Range-bound near pain" },
  CONTINUATION_UP: { color: C.green,  label: "📈 CONTINUATION UP", desc: "Bullish trend" },
  CONTINUATION_DOWN: { color: C.red,  label: "📉 CONTINUATION DN", desc: "Bearish trend" },
  RANGE_BOUND:     { color: C.dim,    label: "↔️  RANGE-BOUND",    desc: "Stuck between walls" },
  UNCLEAR:         { color: C.dim,    label: "⏸ UNCLEAR",          desc: "Wait for signal" },
};


function ForecastCardImpl() {
  const { data, isLoading } = useSWRPoll("/api/forecast/live", {
    refreshInterval: 30000,
    revalidateOnFocus: true,
  });

  const results = data?.results || {};
  const nifty = results.NIFTY;
  const bn = results.BANKNIFTY;

  if (isLoading && !nifty && !bn) {
    return (
      <div style={{
        background: C.card, border: `1px solid ${C.border}`,
        borderRadius: 10, padding: "12px 16px", marginBottom: 8,
      }}>
        <div style={{ color: C.dim, fontSize: 11 }}>
          🔮 Forecast loading… (first pulse may take 60s after engine boot)
        </div>
      </div>
    );
  }

  return (
    <div style={{
      background: C.card, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: "10px 12px", marginBottom: 8,
    }}>
      <div style={{
        color: C.dim, fontSize: 9, fontWeight: 800,
        letterSpacing: 0.6, textTransform: "uppercase",
        marginBottom: 8, display: "flex",
        justifyContent: "space-between", alignItems: "center",
      }}>
        <span>🔮 Forecast — what's expected next</span>
        <span style={{ fontFamily: "ui-monospace, monospace", fontWeight: 500 }}>
          updated every 60s
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <ForecastIndex label="NIFTY" data={nifty} />
        <ForecastIndex label="BANKNIFTY" data={bn} />
      </div>
    </div>
  );
}


function ForecastIndex({ label, data }) {
  if (!data || data.error) {
    return (
      <div style={{
        background: "rgba(255,255,255,0.02)",
        border: `1px solid ${C.border}`,
        borderRadius: 6, padding: "10px 12px",
      }}>
        <div style={{ color: C.text, fontSize: 12, fontWeight: 700 }}>
          {label}
        </div>
        <div style={{ color: C.dim, fontSize: 10, marginTop: 4 }}>
          {data?.error || "No forecast yet"}
        </div>
      </div>
    );
  }

  const path = data.path || {};
  const pathStyle = PATH_STYLE[path.type] || PATH_STYLE.UNCLEAR;
  const levels = data.key_levels || {};
  const action = data.buyer_action || {};
  const ctx = data.context || {};
  const conf = data.confidence || 0;
  const horizon = data.horizon_min || 0;
  const why = data.why || [];

  // Confidence color
  const confColor =
    conf >= 7 ? C.green :
    conf >= 5 ? C.yellow :
    conf >= 3 ? C.orange :
    C.red;

  return (
    <div style={{
      background: `${pathStyle.color}08`,
      border: `1px solid ${pathStyle.color}33`,
      borderRadius: 6, padding: "10px 12px",
    }}>
      {/* Header — index + spot + bias */}
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 6,
      }}>
        <span style={{ color: C.text, fontSize: 12, fontWeight: 800 }}>
          {label} ₹{data.spot?.toLocaleString()}
        </span>
        <span style={{
          color: confColor, fontSize: 10, fontWeight: 800,
          padding: "2px 6px", borderRadius: 3,
          background: `${confColor}22`,
        }}>
          {conf.toFixed(1)}/10
        </span>
      </div>

      {/* Path label */}
      <div style={{
        color: pathStyle.color, fontSize: 12, fontWeight: 700,
        marginBottom: 2,
      }}>
        {pathStyle.label}
      </div>
      <div style={{ color: "#bbb", fontSize: 10, marginBottom: 8 }}>
        {path.label || pathStyle.desc} · {horizon}m horizon
      </div>

      {/* Expected path narrative */}
      {path.narrative && (
        <div style={{
          fontSize: 10, color: "#ccc",
          background: "rgba(0,0,0,0.25)",
          padding: "6px 8px", borderRadius: 4,
          marginBottom: 8, lineHeight: 1.4,
        }}>
          {path.narrative}
        </div>
      )}

      {/* Key levels */}
      <div style={{
        display: "flex", flexDirection: "column", gap: 3,
        fontSize: 9, fontFamily: "ui-monospace, monospace",
        color: "#aaa", marginBottom: 8,
      }}>
        {levels.resistance && levels.resistance.length > 0 && (
          <div>
            <span style={{ color: C.red }}>R</span> {levels.resistance.map(r => r.toLocaleString()).join(" · ")}
          </div>
        )}
        {levels.magnet && (
          <div>
            <span style={{ color: C.purple }}>🧲</span> {levels.magnet.toLocaleString()} (max pain)
          </div>
        )}
        {levels.support && levels.support.length > 0 && (
          <div>
            <span style={{ color: C.green }}>S</span> {levels.support.map(s => s.toLocaleString()).join(" · ")}
          </div>
        )}
      </div>

      {/* Buyer action plan */}
      <div style={{
        background: "rgba(255,255,255,0.03)",
        borderLeft: `2px solid ${pathStyle.color}`,
        padding: "6px 8px", borderRadius: 3,
        fontSize: 10, lineHeight: 1.5,
        marginBottom: 6,
      }}>
        <div style={{ color: pathStyle.color, fontWeight: 700, marginBottom: 2 }}>
          🎯 Buyer Plan
        </div>
        {action.wait_for && (
          <div><span style={{ color: C.dim }}>Wait:</span> <span style={{ color: "#ddd" }}>{action.wait_for}</span></div>
        )}
        {action.then_buy && (
          <div><span style={{ color: C.dim }}>Buy:</span> <span style={{ color: "#fff", fontWeight: 600 }}>{action.then_buy}</span></div>
        )}
        {action.target_premium_pct && (
          <div><span style={{ color: C.dim }}>Target:</span> <span style={{ color: C.green }}>{action.target_premium_pct}</span>
            {action.target_spot && <span style={{ color: "#aaa" }}> (spot {action.target_spot})</span>}
          </div>
        )}
        {action.sl && (
          <div><span style={{ color: C.dim }}>SL:</span> <span style={{ color: C.red }}>{action.sl}</span></div>
        )}
        {action.qty && action.qty !== "Standard" && (
          <div><span style={{ color: C.dim }}>Qty:</span> <span style={{ color: C.yellow }}>{action.qty}</span></div>
        )}
      </div>

      {/* Why bullets */}
      {why.length > 0 && (
        <div style={{
          fontSize: 9, color: "#aaa", lineHeight: 1.4,
          paddingTop: 6, borderTop: `1px dashed ${C.border}`,
        }}>
          <div style={{ color: C.dim, fontWeight: 700,
                        textTransform: "uppercase", marginBottom: 2 }}>
            why
          </div>
          {why.map((w, i) => (
            <div key={i}>• {w}</div>
          ))}
        </div>
      )}

      {/* Context footer */}
      <div style={{
        marginTop: 6, fontSize: 8, color: C.dim,
        fontFamily: "ui-monospace, monospace",
        display: "flex", justifyContent: "space-between",
      }}>
        <span>{ctx.regime} · {ctx.time_window}</span>
        <span>VIX {ctx.vix?.toFixed(1)} · PCR {ctx.pcr?.toFixed(2)}</span>
      </div>
    </div>
  );
}


export default memo(ForecastCardImpl);
