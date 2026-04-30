/**
 * PositionHealthCard
 * ──────────────────
 * Embedded inside open-trade rows on PnL + Scalper tabs.
 * Shows live 0-10 health score, top reasons, and suggested action.
 *
 * Props:
 *   source: "MAIN" | "SCALPER"
 *   tradeId: number
 *   action: "BUY_CE" | "BUY_PE"  (used as fallback hint)
 *   compact: bool (default false) — small inline mode
 */

import { useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

const VERDICT_COLORS = {
  STRONG:   { bg: "rgba(48, 209, 88, 0.10)", fg: "#30D158", border: "#1F7A36" },
  HEALTHY:  { bg: "rgba(160, 220, 90, 0.08)", fg: "#A0DC5A", border: "#5C7A30" },
  WARNING:  { bg: "rgba(255, 159, 10, 0.10)", fg: "#FF9F0A", border: "#7A4F0A" },
  CRITICAL: { bg: "rgba(255, 69, 58, 0.12)", fg: "#FF453A", border: "#7A1F1A" },
};

function colorFor(score, verdict) {
  if (verdict && VERDICT_COLORS[verdict]) return VERDICT_COLORS[verdict];
  if (score >= 9) return VERDICT_COLORS.STRONG;
  if (score >= 6) return VERDICT_COLORS.HEALTHY;
  if (score >= 4) return VERDICT_COLORS.WARNING;
  return VERDICT_COLORS.CRITICAL;
}

const TRIGGER_LABELS = {
  REVERSAL_PATTERN: "Reversal candle",
  VIX_CRUSH: "VIX crush",
  THETA_WINS: "Theta winning",
  DAY_HIGH_TRAP: "Day-high trap",
  POST_LUNCH_STALL: "Post-lunch stall",
  PATTERN_LOSER: "Pattern loser",
};

export default function PositionHealthCard({ source = "MAIN", tradeId, action, compact = false }) {
  const [health, setHealth] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const fetchOnce = async () => {
      try {
        const r = await fetch(`${API}/api/positions/health/${tradeId}?source=${source}`);
        if (!r.ok) return;
        const j = await r.json();
        if (alive && j?.current) setHealth(j.current);
      } catch (e) {
        // silent
      } finally {
        if (alive) setLoading(false);
      }
    };
    fetchOnce();
    const t = setInterval(fetchOnce, 15000); // refresh every 15s
    return () => { alive = false; clearInterval(t); };
  }, [source, tradeId]);

  const palette = useMemo(
    () => colorFor(health?.score ?? 7, health?.verdict),
    [health]
  );

  if (loading && !health) {
    return (
      <div style={{
        fontSize: 11, color: "#666", padding: "4px 8px",
        background: "rgba(255,255,255,0.02)", borderRadius: 6,
        border: "1px solid #1E1E2E", display: "inline-block", marginTop: 6,
      }}>
        Health: loading…
      </div>
    );
  }

  if (!health) {
    return (
      <div style={{
        fontSize: 11, color: "#888", padding: "4px 8px",
        background: "rgba(10, 132, 255, 0.06)", borderRadius: 6,
        border: "1px solid #0A84FF44", display: "inline-block", marginTop: 6,
      }}>
        ⏳ Watcher initialising — first health pulse within 30s
      </div>
    );
  }

  const score = health.score ?? 0;
  const verdict = health.stub ? "INITIALISING" : (health.verdict || "—");
  const reasons = health.reasons || [];
  const suggested = health.stub ? "WAITING" : (health.suggested_action || "HOLD");
  const profitPct = health.profit_pct;
  const holdMin = health.hold_minutes;

  if (compact) {
    return (
      <span style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        background: palette.bg, border: `1px solid ${palette.border}`,
        color: palette.fg, padding: "2px 8px", borderRadius: 6,
        fontSize: 11, fontWeight: 600,
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: palette.fg, boxShadow: `0 0 6px ${palette.fg}`,
        }} />
        Health {score.toFixed(1)} · {verdict}
      </span>
    );
  }

  return (
    <div style={{
      background: palette.bg,
      border: `1px solid ${palette.border}`,
      borderRadius: 8,
      padding: "8px 10px",
      marginTop: 8,
    }}>
      {/* Header row */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        gap: 8,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            width: 36, height: 36, borderRadius: "50%",
            background: palette.fg, color: "#000",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 14, fontWeight: 800,
            boxShadow: `0 0 12px ${palette.fg}55`,
          }}>
            {score.toFixed(1)}
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: palette.fg, letterSpacing: 0.5 }}>
              {verdict}
            </div>
            <div style={{ fontSize: 10, color: "#999" }}>
              {profitPct != null ? `${profitPct > 0 ? "+" : ""}${profitPct.toFixed(1)}%` : "—"}
              {" · "}
              {holdMin != null ? `${holdMin.toFixed(0)}m held` : "—"}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <ActionBadge action={suggested} palette={palette} />
          <button
            onClick={() => setExpanded(v => !v)}
            style={{
              background: "transparent", border: `1px solid ${palette.border}`,
              color: palette.fg, fontSize: 10, padding: "2px 8px",
              borderRadius: 4, cursor: "pointer",
            }}>
            {expanded ? "less" : "why?"}
          </button>
        </div>
      </div>

      {/* Top reasons (always visible if any) */}
      {reasons.length > 0 && (
        <div style={{
          marginTop: 8,
          fontSize: 11, color: "#bbb", lineHeight: 1.5,
        }}>
          <div style={{
            color: palette.fg, fontSize: 10, fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 4,
          }}>
            Watcher reasons
          </div>
          {(expanded ? reasons : reasons.slice(0, 2)).map((r, i) => (
            <div key={i} style={{ marginBottom: 2 }}>· {r}</div>
          ))}
          {!expanded && reasons.length > 2 && (
            <div style={{ color: "#666", fontSize: 10 }}>
              +{reasons.length - 2} more reason{reasons.length - 2 > 1 ? "s" : ""}
            </div>
          )}
        </div>
      )}

      {/* Expanded: component breakdown */}
      {expanded && health.components && (
        <div style={{
          marginTop: 10, paddingTop: 8,
          borderTop: `1px dashed ${palette.border}`,
          display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 6, fontSize: 10,
        }}>
          <ComponentChip label="Drawdown" data={health.components.drawdown} extra={
            health.profit_pct != null ? `${health.profit_pct >= 0 ? "+" : ""}${health.profit_pct.toFixed(1)}%` : null
          }/>
          <ComponentChip label="Candle" data={health.components.candle} />
          <ComponentChip label="VIX" data={health.components.vix} extra={
            health.components.vix?.delta_15m != null
              ? `Δ15m ${health.components.vix.delta_15m > 0 ? "+" : ""}${health.components.vix.delta_15m}%`
              : null
          }/>
          <ComponentChip label="Premium" data={health.components.premium} extra={
            health.components.premium?.spot_change_10m != null
              ? `spot ${health.components.premium.spot_change_10m > 0 ? "+" : ""}${health.components.premium.spot_change_10m}% / prem ${health.components.premium.premium_change_10m > 0 ? "+" : ""}${health.components.premium.premium_change_10m}%`
              : null
          }/>
          <ComponentChip label="Day Extreme" data={health.components.proximity} />
          <ComponentChip label="Time Decay" data={health.components.time} />
          <ComponentChip label="Pattern" data={health.components.pattern} />
        </div>
      )}
    </div>
  );
}


function ActionBadge({ action, palette }) {
  const labels = {
    EXIT_NOW: "EXIT NOW",
    TIGHTEN_SL: "TIGHT SL",
    MONITOR: "WATCH",
    HOLD: "HOLD",
  };
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
      padding: "3px 8px", borderRadius: 4,
      background: palette.fg, color: "#000",
    }}>
      {labels[action] || action}
    </span>
  );
}


function ComponentChip({ label, data, extra }) {
  const penalty = data?.penalty || 0;
  const color = penalty >= 1.5 ? "#FF453A"
              : penalty >= 0.8 ? "#FF9F0A"
              : penalty >= 0.3 ? "#FFD60A"
              : "#30D158";
  return (
    <div style={{
      background: "rgba(255,255,255,0.02)",
      border: "1px solid rgba(255,255,255,0.05)",
      borderRadius: 4, padding: "4px 6px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ color: "#888", fontSize: 9, textTransform: "uppercase" }}>{label}</span>
        <span style={{ color, fontSize: 10, fontWeight: 700 }}>
          {penalty > 0 ? `-${penalty.toFixed(1)}` : "OK"}
        </span>
      </div>
      {extra && <div style={{ color: "#aaa", fontSize: 9, marginTop: 2 }}>{extra}</div>}
      {data?.reason && (
        <div style={{ color: "#888", fontSize: 9, marginTop: 2, lineHeight: 1.3 }}>
          {data.reason}
        </div>
      )}
    </div>
  );
}
