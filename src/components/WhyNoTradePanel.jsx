/**
 * WhyNoTradePanel
 * ───────────────
 * Shows up in PnL tab when openTrades.length === 0 to explain WHY the
 * auto-trader hasn't entered any trades right now. Polls
 * /api/trades/why-no-trade every 15s and renders a per-gate pass/fail
 * table for both NIFTY and BANKNIFTY.
 */

import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

export default function WhyNoTradePanel() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch(`${API}/api/trades/why-no-trade`);
        if (!r.ok) {
          setError(`API ${r.status}`);
          return;
        }
        const j = await r.json();
        if (alive) {
          setData(j);
          setError(null);
        }
      } catch (e) {
        setError("network");
      }
    };
    tick();
    const id = setInterval(tick, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (!data) {
    return (
      <div style={{
        background: "#111118", border: "1px solid #1E1E2E",
        borderRadius: 12, padding: "16px 20px", marginTop: 16,
        color: "#666", fontSize: 12, textAlign: "center",
      }}>
        {error ? `Error: ${error}` : "Loading auto-trader diagnostics…"}
      </div>
    );
  }

  const niftyAnalysis = data.per_index?.NIFTY || {};
  const bnAnalysis = data.per_index?.BANKNIFTY || {};

  return (
    <div style={{
      background: "#111118",
      border: "1px solid #1E1E2E",
      borderRadius: 12,
      padding: "16px 20px",
      marginTop: 16,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8,
      }}>
        <div>
          <div style={{
            color: "#FF9F0A", fontSize: 12, fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 4,
          }}>
            🔍 Why No Trade Yet?
          </div>
          <div style={{ color: "#888", fontSize: 11 }}>
            Live diagnostic — refreshes every 15s · {data.now_ist} IST
          </div>
        </div>
        <button onClick={() => setExpanded(v => !v)} style={{
          background: "transparent", border: "1px solid #2A2A3F",
          color: "#aaa", fontSize: 11, padding: "4px 10px",
          borderRadius: 6, cursor: "pointer",
        }}>
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>

      {/* Top-level summary bar */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
        gap: 8, marginBottom: 12,
      }}>
        <Stat label="Engine" value={data.trade_manager_alive ? "ALIVE" : "DOWN"}
              color={data.trade_manager_alive ? "#30D158" : "#FF453A"} />
        <Stat label="Market" value={data.market_open ? "OPEN" : "CLOSED"}
              color={data.market_open ? "#30D158" : "#888"} />
        <Stat label="Regime" value={data.regime?.regime || "—"}
              color={data.vol_recommendations?.main_pnl_allowed ? "#30D158" : "#FF453A"} />
        <Stat label="Today trades" value={data.today_trade_count}
              sub={`cap 15 · ${15 - data.today_trade_count} left`} color="#0A84FF" />
        <Stat label="Open trades" value={data.open_trade_count}
              sub="cap 10" color="#A0DC5A" />
        <Stat label="Today P&L"
              value={`₹${Math.round(data.today_realised_pnl || 0).toLocaleString("en-IN")}`}
              color={(data.today_realised_pnl || 0) >= 0 ? "#30D158" : "#FF453A"} />
      </div>

      {/* Volatility recommendations / warnings */}
      {data.vol_recommendations?.warnings?.length > 0 && (
        <div style={{
          background: "rgba(255,159,10,0.08)", border: "1px solid #FF9F0A55",
          borderRadius: 8, padding: "8px 12px", marginBottom: 12,
        }}>
          <div style={{
            color: "#FF9F0A", fontSize: 10, fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 4,
          }}>
            Regime warnings
          </div>
          {data.vol_recommendations.warnings.map((w, i) => (
            <div key={i} style={{ color: "#ddd", fontSize: 11, lineHeight: 1.5 }}>
              · {w}
            </div>
          ))}
        </div>
      )}

      {expanded && (
        <>
          {/* Per-index breakdown */}
          <div style={{
            display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: 12,
          }}>
            <IndexBreakdown idx="NIFTY" analysis={niftyAnalysis} />
            <IndexBreakdown idx="BANKNIFTY" analysis={bnAnalysis} />
          </div>

          {/* Pending entries */}
          {Object.keys(data.pending_entries || {}).length > 0 && (
            <div style={{
              marginTop: 12, background: "rgba(10,132,255,0.08)",
              border: "1px solid #0A84FF55", borderRadius: 8, padding: "8px 12px",
            }}>
              <div style={{
                color: "#0A84FF", fontSize: 10, fontWeight: 700,
                textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 4,
              }}>
                Pending Entries — waiting for momentum confirmation
              </div>
              {Object.entries(data.pending_entries).map(([k, p]) => (
                <div key={k} style={{ color: "#ddd", fontSize: 11, lineHeight: 1.5 }}>
                  · <strong>{k}</strong>: {p.action} {p.strike} @ ₹{p.entry_price}
                  {" · "}prob {p.probability}% · age {p.age_sec}s (expires at 120s)
                </div>
              ))}
            </div>
          )}

          {/* Today's recent trades */}
          {data.today_recent_trades?.length > 0 && (
            <div style={{
              marginTop: 12, paddingTop: 10, borderTop: "1px dashed #1E1E2E",
            }}>
              <div style={{
                color: "#888", fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                letterSpacing: 0.6, marginBottom: 6,
              }}>
                Today's recent trades ({data.today_recent_trades.length})
              </div>
              {data.today_recent_trades.slice(0, 5).map(t => (
                <div key={t.id} style={{
                  display: "flex", justifyContent: "space-between",
                  fontSize: 11, padding: "3px 0", color: "#ccc",
                }}>
                  <span>#{t.id} {t.idx} {t.action} {t.strike} <span style={{color:"#666"}}>{t.status}</span></span>
                  <span style={{ color: (t.pnl ?? 0) >= 0 ? "#30D158" : "#FF453A" }}>
                    ₹{Math.round(t.pnl ?? 0).toLocaleString("en-IN")}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div style={{
            marginTop: 10, padding: "6px 10px", background: "rgba(255,255,255,0.02)",
            borderRadius: 6, fontSize: 10, color: "#666",
          }}>
            💡 {data.block_log_hint}
          </div>
        </>
      )}
    </div>
  );
}


function Stat({ label, value, sub, color = "#aaa" }) {
  return (
    <div style={{
      background: "#0A0A0F", border: "1px solid #1E1E2E",
      borderRadius: 6, padding: "6px 10px",
    }}>
      <div style={{
        color: "#666", fontSize: 9, fontWeight: 700,
        textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 2,
      }}>
        {label}
      </div>
      <div style={{ color, fontSize: 14, fontWeight: 700 }}>
        {value}
      </div>
      {sub && <div style={{ color: "#666", fontSize: 9 }}>{sub}</div>}
    </div>
  );
}


function IndexBreakdown({ idx, analysis }) {
  const action = analysis.verdict_action || "—";
  const prob = analysis.win_probability ?? 0;
  const wouldTake = analysis.would_take_trade;
  const gates = analysis.all_gates || [];
  const blocking = analysis.blocking_gates || [];

  const headerColor = wouldTake ? "#30D158" : (action === "NO TRADE" ? "#888" : "#FF9F0A");

  return (
    <div style={{
      background: "#0A0A0F", border: `1px solid ${headerColor}55`,
      borderRadius: 8, padding: "10px 12px",
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 8,
      }}>
        <div>
          <div style={{ color: "#fff", fontWeight: 700, fontSize: 13 }}>{idx}</div>
          <div style={{ color: "#888", fontSize: 10 }}>
            Verdict: <span style={{ color: headerColor, fontWeight: 700 }}>{action}</span>
            {" · "}Prob: <span style={{ color: prob >= 60 ? "#30D158" : prob >= 50 ? "#FFD60A" : "#888" }}>{prob}%</span>
          </div>
        </div>
        <span style={{
          background: headerColor, color: "#000",
          padding: "3px 10px", borderRadius: 4,
          fontSize: 10, fontWeight: 700, letterSpacing: 0.4,
        }}>
          {wouldTake ? "WOULD ENTER" : (blocking.length > 0 ? `${blocking.length} BLOCK` : "NO SIGNAL")}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {gates.map((g, i) => (
          <div key={i} style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            padding: "3px 6px", borderRadius: 4,
            background: g.pass ? "rgba(48,209,88,0.06)" : "rgba(255,69,58,0.08)",
            fontSize: 10,
          }}>
            <span style={{ color: g.pass ? "#30D158" : "#FF453A", fontWeight: 700 }}>
              {g.pass ? "✓" : "✕"} {g.name}
            </span>
            <span style={{ color: "#888" }}>{g.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
