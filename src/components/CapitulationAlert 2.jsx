/**
 * CapitulationAlert — global floating banner
 * Fires on every tab when a STRONG capitulation is detected (score >= 7).
 */

import { useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

export default function CapitulationAlert() {
  const [alerts, setAlerts] = useState([]);
  const dismissedRef = useRef({});

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch(`${API}/api/reversal/live`);
        if (!r.ok) return;
        const j = await r.json();
        if (!alive || !j.results) return;

        const fired = [];
        for (const [idx, data] of Object.entries(j.results)) {
          for (const dir of ["bullish", "bearish"]) {
            const d = data[dir];
            if (d && d.score >= 7 && d.verdict === "STRONG_CAPITULATION") {
              const k = `${idx}:${d.direction}`;
              const dismissed = dismissedRef.current[k];
              // Re-fire only if score increased >0.5 since dismissal
              if (dismissed == null || d.score > dismissed + 0.5) {
                fired.push({
                  key: k, idx, direction: d.direction,
                  score: d.score, verdict: d.verdict,
                  fired_count: d.fired_count,
                  reasons: d.reasons,
                  recommended_action: d.recommended_action,
                  spot: data.spot, atm_strike: data.atm_strike,
                });
              }
            }
          }
        }
        setAlerts(fired);
      } catch (e) { /* silent */ }
    };
    tick();
    const t = setInterval(tick, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (alerts.length === 0) return null;

  return (
    <div style={{
      position: "fixed", top: 70, left: "50%", transform: "translateX(-50%)",
      zIndex: 1100, display: "flex", flexDirection: "column", gap: 8,
      maxWidth: 720, width: "calc(100vw - 40px)",
    }}>
      {alerts.slice(0, 2).map(a => {
        const isBull = a.direction === "BULLISH";
        const color = isBull ? "#30D158" : "#FF453A";
        return (
          <div key={a.key} style={{
            background: `linear-gradient(180deg, ${color}30, ${color}10)`,
            border: `2px solid ${color}`,
            borderRadius: 10, padding: "12px 16px",
            boxShadow: `0 4px 20px ${color}55`,
            color: "#fff",
            animation: "capitulationPulse 1.4s ease-in-out infinite",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: color, boxShadow: `0 0 12px ${color}` }}/>
                  <span style={{ fontSize: 12, fontWeight: 800, color, textTransform: "uppercase", letterSpacing: 0.8 }}>
                    {isBull ? "🎯 BULLISH CAPITULATION" : "🎯 BEARISH CAPITULATION"} · {a.idx} · {a.score.toFixed(1)}/10
                  </span>
                </div>
                <div style={{ fontSize: 14, fontWeight: 700, color: "#fff", marginBottom: 4 }}>
                  Spot ₹{a.spot?.toFixed(2)} · ATM {a.atm_strike} · {a.fired_count} signals fired
                </div>
                {a.reasons?.length > 0 && (
                  <div style={{ fontSize: 11, color: "#ddd", lineHeight: 1.5, marginTop: 4 }}>
                    {a.reasons.slice(0, 3).map((r, i) => <div key={i}>· {r}</div>)}
                  </div>
                )}
                {a.recommended_action && (
                  <div style={{
                    marginTop: 8, padding: "5px 10px", background: color, color: "#000",
                    borderRadius: 5, fontSize: 12, fontWeight: 800, display: "inline-block",
                    letterSpacing: 0.4,
                  }}>
                    → {a.recommended_action}
                  </div>
                )}
              </div>
              <button
                onClick={() => {
                  dismissedRef.current[a.key] = a.score;
                  setAlerts(cur => cur.filter(x => x.key !== a.key));
                }}
                style={{
                  background: "transparent", border: `1px solid ${color}`,
                  color, fontSize: 11, padding: "4px 10px", borderRadius: 4,
                  cursor: "pointer", flexShrink: 0, fontWeight: 700,
                }}>
                Dismiss
              </button>
            </div>
          </div>
        );
      })}
      <style>{`
        @keyframes capitulationPulse {
          0%, 100% { box-shadow: 0 4px 20px rgba(48, 209, 88, 0.4); }
          50% { box-shadow: 0 4px 32px rgba(48, 209, 88, 0.7); }
        }
      `}</style>
    </div>
  );
}
