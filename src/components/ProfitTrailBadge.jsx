/**
 * ProfitTrailBadge
 * ────────────────
 * Compact + expanded view of profit-lock trailing SL ladder for an
 * open trade. Shows current stage hit, profit %, locked %,
 * next stage target.
 *
 * Props:
 *   tradeId, source ('MAIN' | 'SCALPER'), entry, currentLtp, currentSl
 *   compact (bool) — small inline badge if true, full panel otherwise
 */

import { useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

export default function ProfitTrailBadge({ tradeId, source = "MAIN",
                                            entry, currentLtp, currentSl,
                                            compact = false }) {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    let alive = true;
    const fetchStatus = async () => {
      try {
        const r = await fetch(`${API}/api/profit-trail/status/${tradeId}?source=${source}`);
        if (!r.ok) return;
        const j = await r.json();
        if (alive && !j.error) setStatus(j);
      } catch (e) { /* silent */ }
    };
    fetchStatus();
    const iv = setInterval(fetchStatus, 10000);
    return () => { alive = false; clearInterval(iv); };
  }, [tradeId, source]);

  // Fallback compute locally if API fails
  const fallback = useMemo(() => {
    if (!entry || !currentLtp) return null;
    const profit = ((currentLtp - entry) / entry * 100);
    const locked = currentSl > 0 ? ((currentSl - entry) / entry * 100) : null;
    return { profit_pct: profit, locked_pct: locked, current_sl: currentSl };
  }, [entry, currentLtp, currentSl]);

  const view = status || fallback;
  if (!view) return null;

  const profit = view.profit_pct ?? 0;
  const locked = view.locked_pct;
  const currentStage = view.current_stage;
  const nextStage = view.next_stage;
  const nextStagePremium = view.next_stage_at_premium;

  const isActive = currentStage != null;
  const isProfitable = profit >= 0;
  const lockedPositive = locked !== null && locked !== undefined && locked >= 0;

  // Color
  const color = lockedPositive ? "#30D158"        // green: profit locked
              : isActive ? "#FFD60A"              // yellow: stage hit, partial protection
              : isProfitable ? "#0A84FF"          // blue: in profit but no stage hit yet
              : "#888";                           // grey: no profit yet

  if (compact) {
    return (
      <span style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        background: `${color}22`, border: `1px solid ${color}55`,
        color, padding: "2px 8px", borderRadius: 6,
        fontSize: 10, fontWeight: 700, letterSpacing: 0.3,
      }}>
        🛡️ {isActive
          ? `Trail @ ${locked >= 0 ? "+" : ""}${(locked || 0).toFixed(1)}%`
          : profit > 0
            ? `+${profit.toFixed(1)}% (no lock yet)`
            : "Idle"}
      </span>
    );
  }

  return (
    <div style={{
      background: `${color}10`, border: `1px solid ${color}33`,
      borderRadius: 8, padding: "10px 12px", marginTop: 8,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 8 }}>
        <span style={{ color, fontSize: 11, fontWeight: 700,
                       textTransform: "uppercase", letterSpacing: 0.6 }}>
          🛡️ Profit-Lock Trailing SL
        </span>
        <span style={{
          background: color, color: "#000",
          padding: "2px 8px", borderRadius: 4,
          fontSize: 10, fontWeight: 800,
        }}>
          {isActive ? "ACTIVE" : "IDLE"}
        </span>
      </div>

      {/* Top metrics row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, marginBottom: 8 }}>
        <Metric label="Profit" value={`${profit >= 0 ? "+" : ""}${profit.toFixed(2)}%`}
                color={profit >= 0 ? "#30D158" : "#FF453A"} />
        <Metric label="Locked"
                value={locked != null ? `${locked >= 0 ? "+" : ""}${locked.toFixed(1)}%` : "—"}
                color={lockedPositive ? "#30D158" : locked !== null ? "#FF9F0A" : "#888"} />
        <Metric label="SL @"
                value={view.current_sl ? `₹${view.current_sl}` : "—"} color="#aaa" />
      </div>

      {/* Next stage info */}
      {nextStage && nextStagePremium && (
        <div style={{
          background: "rgba(0,0,0,0.25)", borderRadius: 5, padding: "6px 10px",
          fontSize: 10, color: "#aaa",
        }}>
          📍 Next stage: <strong style={{ color: "#fff" }}>+{nextStage}%</strong> at
          premium <strong style={{ color: "#0A84FF" }}>₹{nextStagePremium}</strong>
        </div>
      )}

      {/* Ladder visualization */}
      {view.ladder && view.ladder.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ color: "#888", fontSize: 9, fontWeight: 700,
                        textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }}>
            Ladder Progress
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {view.ladder.map((stage, i) => (
              <div key={i} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "2px 6px", borderRadius: 3,
                background: stage.hit
                  ? (stage.lock_pct >= 0 ? "rgba(48,209,88,0.12)" : "rgba(255,214,10,0.10)")
                  : "rgba(255,255,255,0.03)",
                fontSize: 9,
              }}>
                <span style={{
                  color: stage.hit
                    ? (stage.lock_pct >= 0 ? "#30D158" : "#FFD60A")
                    : "#666",
                  fontWeight: stage.hit ? 700 : 500,
                }}>
                  {stage.hit ? "✓" : "○"} +{stage.threshold}% profit
                </span>
                <span style={{
                  color: stage.lock_pct >= 0 ? "#30D158" : "#FF9F0A",
                  fontWeight: 600,
                }}>
                  → SL {stage.lock_pct >= 0 ? "+" : ""}{stage.lock_pct.toFixed(1)}%
                  {" "}(₹{stage.sl_target})
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


function Metric({ label, value, color }) {
  return (
    <div style={{
      background: "rgba(255,255,255,0.03)", borderRadius: 4,
      padding: "4px 6px", textAlign: "center",
    }}>
      <div style={{ color: "#666", fontSize: 8, fontWeight: 700, textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ color, fontSize: 12, fontWeight: 700 }}>{value}</div>
    </div>
  );
}
