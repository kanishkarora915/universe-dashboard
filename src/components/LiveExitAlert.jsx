/**
 * LiveExitAlert
 * ─────────────
 * Floating top-center banner that surfaces CRITICAL health scores
 * across all open positions (PnL + Scalper). Mounted once globally
 * in Universe.jsx.
 *
 * Behaviour:
 *  - Polls /api/positions/health every 15s
 *  - Shows positions with score < 4 (CRITICAL)
 *  - Auto-dismisses if score recovers above 5
 *  - User can dismiss; same trade won't re-alert until score worsens by 1+ point
 */

import { useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";
const REFRESH_MS = 15000;

export default function LiveExitAlert() {
  const [criticals, setCriticals] = useState([]);
  const dismissedRef = useRef({});  // key -> last dismissed score

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch(`${API}/api/positions/health`);
        if (!r.ok) return;
        const j = await r.json();
        if (!alive) return;
        const list = Array.isArray(j?.positions) ? j.positions : [];
        const crit = list.filter(p => (p?.score ?? 10) < 4);
        // Filter out user-dismissed unless score worsened by 1+
        const filtered = crit.filter(p => {
          const k = `${p.source}:${p.trade_id}`;
          const prev = dismissedRef.current[k];
          if (prev == null) return true;
          return p.score < prev - 1;
        });
        setCriticals(filtered);
      } catch (e) {
        // silent
      }
    };
    tick();
    const t = setInterval(tick, REFRESH_MS);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (criticals.length === 0) return null;

  return (
    <div style={{
      position: "fixed",
      top: 70, left: "50%", transform: "translateX(-50%)",
      zIndex: 1000,
      display: "flex", flexDirection: "column", gap: 8,
      maxWidth: 600, width: "calc(100vw - 40px)",
    }}>
      {criticals.slice(0, 3).map(p => {
        const k = `${p.source}:${p.trade_id}`;
        const reasons = p.reasons || [];
        return (
          <div key={k} style={{
            background: "linear-gradient(180deg, rgba(255,69,58,0.18), rgba(255,69,58,0.08))",
            border: "1px solid #FF453A",
            borderRadius: 10,
            padding: "10px 14px",
            boxShadow: "0 4px 16px rgba(255,69,58,0.25)",
            color: "#fff",
            animation: "exitPulse 2s ease-in-out infinite",
          }}>
            <div style={{
              display: "flex", justifyContent: "space-between",
              alignItems: "flex-start", gap: 12,
            }}>
              <div style={{ flex: 1 }}>
                <div style={{
                  display: "flex", alignItems: "center", gap: 8,
                  fontSize: 11, fontWeight: 700, color: "#FF453A",
                  textTransform: "uppercase", letterSpacing: 0.6,
                }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: "50%",
                    background: "#FF453A", boxShadow: "0 0 8px #FF453A",
                  }}/>
                  CRITICAL · Health {p.score?.toFixed(1)} · {p.source}
                </div>
                <div style={{
                  marginTop: 4, fontSize: 14, fontWeight: 800, color: "#fff",
                }}>
                  {p.idx} {p.action} {p.strike}
                  <span style={{
                    fontSize: 11, color: "#bbb", fontWeight: 500, marginLeft: 8,
                  }}>
                    Entry ₹{p.entry_price?.toFixed(2)} · Now ₹{p.current_premium?.toFixed(2)}
                    {" · "}
                    <span style={{ color: p.profit_pct >= 0 ? "#30D158" : "#FF453A" }}>
                      {p.profit_pct > 0 ? "+" : ""}{p.profit_pct?.toFixed(1)}%
                    </span>
                  </span>
                </div>
                {reasons.length > 0 && (
                  <div style={{ marginTop: 6, fontSize: 11, color: "#ddd", lineHeight: 1.5 }}>
                    {reasons.slice(0, 2).map((r, i) => (
                      <div key={i}>· {r}</div>
                    ))}
                  </div>
                )}
                <div style={{
                  marginTop: 8, fontSize: 10, color: "#FF9F0A", fontWeight: 700,
                  textTransform: "uppercase", letterSpacing: 0.5,
                }}>
                  Suggested: {p.suggested_action || "EXIT"}
                </div>
              </div>
              <button
                onClick={() => {
                  dismissedRef.current[k] = p.score;
                  setCriticals(cur => cur.filter(x => `${x.source}:${x.trade_id}` !== k));
                }}
                style={{
                  background: "transparent", border: "1px solid #FF453A",
                  color: "#FF453A", fontSize: 11, padding: "4px 10px",
                  borderRadius: 4, cursor: "pointer", flexShrink: 0,
                }}>
                Dismiss
              </button>
            </div>
          </div>
        );
      })}
      {criticals.length > 3 && (
        <div style={{
          textAlign: "center", color: "#FF9F0A", fontSize: 11, fontWeight: 600,
        }}>
          +{criticals.length - 3} more critical positions — see PnL / Scalper tab
        </div>
      )}
      <style>{`
        @keyframes exitPulse {
          0%, 100% { box-shadow: 0 4px 16px rgba(255,69,58,0.25); }
          50% { box-shadow: 0 4px 24px rgba(255,69,58,0.55); }
        }
      `}</style>
    </div>
  );
}
