/**
 * SmartMoneyPanel — Institutional Flow Intelligence for Option Buyers
 * ───────────────────────────────────────────────────────────────────
 * Detects 4 institutional patterns in real-time:
 *   🚧 WRITER_DRIP   — slow short-build (resistance/support hardening)
 *   🎯 BUYER_DRIP    — slow long-build (smart money directional bet)
 *   ⚡ WRITER_COVER  — squeeze starting (forced buying back)
 *   📉 BUYER_EXIT    — longs giving up (failed thesis)
 *
 * Each finding includes a buyer-specific recommendation —
 * what CE/PE to buy, when, and why.
 */

import { useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

const ACTIVITY_META = {
  WRITER_DRIP:  { icon: "🚧", label: "Writer Building",  color: "#FF9F0A", border: "#FF9F0A55", bg: "rgba(255,159,10,0.08)" },
  BUYER_DRIP:   { icon: "🎯", label: "Buyer Building",   color: "#0A84FF", border: "#0A84FF55", bg: "rgba(10,132,255,0.08)" },
  WRITER_COVER: { icon: "⚡", label: "Writer Covering",  color: "#30D158", border: "#30D15855", bg: "rgba(48,209,88,0.10)" },
  BUYER_EXIT:   { icon: "📉", label: "Buyer Exit",       color: "#FF453A", border: "#FF453A55", bg: "rgba(255,69,58,0.08)" },
};

const URGENCY_COLOR = {
  HIGH:   "#FF453A",
  MEDIUM: "#FF9F0A",
  LOW:    "#888",
};

export default function SmartMoneyPanel() {
  const [idx, setIdx] = useState("NIFTY");
  const [data, setData] = useState(null);
  const [forcing, setForcing] = useState(false);
  const [drillStrike, setDrillStrike] = useState(null);

  const refresh = async () => {
    try {
      const r = await fetch(`${API}/api/smart-money/live`);
      if (!r.ok) return;
      const j = await r.json();
      setData(j);
    } catch (e) { /* silent */ }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, []);

  const forcePulse = async () => {
    setForcing(true);
    try {
      await fetch(`${API}/api/smart-money/pulse-now`, { method: "POST" });
      await refresh();
    } catch (e) { /* silent */ }
    finally { setForcing(false); }
  };

  const idxData = data?.results?.[idx];
  const lastPulse = data?.ts ? Math.round((Date.now() / 1000 - data.ts)) : null;

  return (
    <div style={{
      background: "linear-gradient(180deg, #0F0F1A 0%, #111118 100%)",
      border: "1px solid #2A2A3F",
      borderRadius: 14,
      padding: "20px 24px",
      marginBottom: 16,
      boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
    }}>
      {/* HEADER */}
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "flex-start", marginBottom: 16, flexWrap: "wrap", gap: 12,
      }}>
        <div>
          <div style={{
            fontSize: 18, fontWeight: 800, letterSpacing: -0.3,
            background: "linear-gradient(90deg, #0A84FF 0%, #BF5AF2 100%)",
            WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
          }}>
            💰 Smart Money Tracker
          </div>
          <div style={{ color: "#888", fontSize: 11, marginTop: 4 }}>
            Real-time institutional flow detection · 4 patterns · buyer-actionable
            {lastPulse !== null && ` · last pulse ${lastPulse}s ago`}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {["NIFTY", "BANKNIFTY"].map(i => (
            <button key={i} onClick={() => setIdx(i)} style={{
              background: idx === i ? "#0A84FF22" : "transparent",
              color: idx === i ? "#0A84FF" : "#888",
              border: `1px solid ${idx === i ? "#0A84FF55" : "#2A2A3F"}`,
              padding: "6px 14px", fontSize: 11, fontWeight: 700,
              borderRadius: 6, cursor: "pointer", letterSpacing: 0.4,
            }}>
              {i}
            </button>
          ))}
          <button onClick={forcePulse} disabled={forcing} style={{
            background: "#0A84FF22", border: "1px solid #0A84FF55",
            color: "#0A84FF", fontSize: 11, fontWeight: 700,
            padding: "6px 14px", borderRadius: 6, cursor: forcing ? "wait" : "pointer",
          }}>
            {forcing ? "Pulsing…" : "⚡ Force Pulse"}
          </button>
        </div>
      </div>

      {!idxData ? (
        <EmptyState message="Loading institutional flow data…" />
      ) : idxData.error ? (
        <EmptyState message={`⏳ ${idxData.error}`} />
      ) : (
        <>
          {/* NET INSTITUTIONAL VIEW (banner) */}
          <NetInstitutionalView view={idxData.net_view} spot={idxData.spot} idx={idx} />

          {/* GROUPED FINDINGS — 4 categories */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 14 }}>
            <ActivityGroup
              activity="WRITER_DRIP"
              title="Writers Building"
              subtitle="Resistance / support hardening"
              findings={idxData.grouped?.WRITER_DRIP || []}
              spot={idxData.spot}
              onDrill={setDrillStrike}
            />
            <ActivityGroup
              activity="BUYER_DRIP"
              title="Buyers Building"
              subtitle="Smart money directional bets"
              findings={idxData.grouped?.BUYER_DRIP || []}
              spot={idxData.spot}
              onDrill={setDrillStrike}
            />
            <ActivityGroup
              activity="WRITER_COVER"
              title="Writers Covering"
              subtitle="Squeeze starting — premiums explode"
              findings={idxData.grouped?.WRITER_COVER || []}
              spot={idxData.spot}
              onDrill={setDrillStrike}
            />
            <ActivityGroup
              activity="BUYER_EXIT"
              title="Buyers Exiting"
              subtitle="Failed thesis — avoid these strikes"
              findings={idxData.grouped?.BUYER_EXIT || []}
              spot={idxData.spot}
              onDrill={setDrillStrike}
            />
          </div>

          {/* FOOTER STATS */}
          <div style={{
            marginTop: 12, paddingTop: 10, borderTop: "1px dashed #1E1E2E",
            display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8,
            fontSize: 10, color: "#666",
          }}>
            <span>📊 {idxData.strikes_analyzed} NTM strikes scanned · {idxData.total_findings} signals fired</span>
            <span>Window: 30 min · Drip range: 50–5000 lots/min · Sustained: ≥65%</span>
          </div>
        </>
      )}

      {drillStrike && (
        <StrikeDrillModal
          idx={idx}
          strike={drillStrike}
          onClose={() => setDrillStrike(null)}
        />
      )}
    </div>
  );
}


function EmptyState({ message }) {
  return (
    <div style={{
      padding: "40px 20px", textAlign: "center", color: "#666",
      background: "rgba(255,255,255,0.02)", borderRadius: 10,
    }}>
      {message}
    </div>
  );
}


function NetInstitutionalView({ view, spot, idx }) {
  if (!view) return null;
  const biasColors = { BULLISH: "#30D158", BEARISH: "#FF453A", NEUTRAL: "#888" };
  const c = biasColors[view.bias] || "#888";
  return (
    <div style={{
      background: `linear-gradient(180deg, ${c}15 0%, ${c}05 100%)`,
      border: `1px solid ${c}55`,
      borderRadius: 10, padding: "12px 16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontSize: 10, color: "#888", fontWeight: 700,
                        textTransform: "uppercase", letterSpacing: 0.6 }}>
            Net Institutional View
          </div>
          <div style={{ fontSize: 16, fontWeight: 800, color: c, marginTop: 2 }}>
            {view.bias}
            <span style={{ color: "#aaa", fontSize: 11, fontWeight: 500, marginLeft: 8 }}>
              · {idx} spot ₹{spot?.toFixed(2)}
            </span>
          </div>
        </div>
        {view.trade_zone && (
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 9, color: "#888", textTransform: "uppercase" }}>
              Trade Zone
            </div>
            <div style={{ fontSize: 13, color: "#0A84FF", fontWeight: 700,
                          fontFamily: "ui-monospace, monospace" }}>
              {view.trade_zone}
            </div>
          </div>
        )}
      </div>
      <div style={{
        display: "flex", flexDirection: "column", gap: 4,
        fontSize: 11, color: "#ddd", lineHeight: 1.6,
      }}>
        {(view.summary || []).map((s, i) => <div key={i}>{s}</div>)}
      </div>
    </div>
  );
}


function ActivityGroup({ activity, title, subtitle, findings, spot, onDrill }) {
  const meta = ACTIVITY_META[activity];
  return (
    <div style={{
      background: meta.bg, border: `1px solid ${meta.border}`,
      borderRadius: 10, padding: "12px 14px",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 16 }}>{meta.icon}</span>
        <span style={{ color: meta.color, fontSize: 11, fontWeight: 700,
                       textTransform: "uppercase", letterSpacing: 0.6 }}>
          {title}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "#666",
                       background: `${meta.color}22`, padding: "1px 6px", borderRadius: 4 }}>
          {findings.length}
        </span>
      </div>
      <div style={{ color: "#777", fontSize: 10, marginBottom: 10 }}>{subtitle}</div>

      {findings.length === 0 ? (
        <div style={{ color: "#555", fontSize: 11, padding: "10px 0", textAlign: "center" }}>
          No signals fired
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {findings.map((f, i) => (
            <FindingCard key={i} finding={f} meta={meta} spot={spot} onDrill={onDrill} />
          ))}
        </div>
      )}
    </div>
  );
}


function FindingCard({ finding, meta, spot, onDrill }) {
  const f = finding;
  const rec = f.recommendation || {};
  const urgencyColor = URGENCY_COLOR[rec.urgency] || "#888";
  const distFromSpot = spot ? ((f.strike - spot) / spot * 100).toFixed(2) : null;
  const dirArrow = f.strike > spot ? "↑" : "↓";

  return (
    <div style={{
      background: "rgba(0,0,0,0.25)", borderRadius: 8, padding: "10px 12px",
      border: `1px solid ${meta.border}`,
      cursor: onDrill ? "pointer" : "default",
    }} onClick={onDrill ? () => onDrill(f.strike) : undefined}>

      {/* Top row: strike + side + score */}
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: "#fff", fontWeight: 800, fontSize: 14 }}>
            {f.strike}
          </span>
          <span style={{
            background: f.side === "CE" ? "#30D15822" : "#FF453A22",
            color: f.side === "CE" ? "#30D158" : "#FF453A",
            padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700,
            letterSpacing: 0.4,
          }}>
            {f.side}
          </span>
          {distFromSpot && (
            <span style={{ color: "#666", fontSize: 9 }}>
              {dirArrow} {Math.abs(distFromSpot)}% from spot
            </span>
          )}
        </div>
        <div style={{
          background: meta.color, color: "#000",
          padding: "2px 8px", borderRadius: 4,
          fontSize: 10, fontWeight: 800,
        }}>
          {f.score}/10
        </div>
      </div>

      {/* Middle: metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
                    gap: 6, marginBottom: 8, fontSize: 10 }}>
        <Metric label="Rate" value={`${f.rate_per_min}/min`} />
        <Metric label="Duration" value={`${f.duration_min} min`} />
        <Metric label="LTP Δ" value={`${f.ltp_change_pct > 0 ? "+" : ""}${f.ltp_change_pct}%`}
                color={f.ltp_change_pct > 0 ? "#30D158" : "#FF453A"} />
      </div>

      {/* OI evolution mini */}
      <div style={{ fontSize: 9, color: "#888", marginBottom: 8 }}>
        {f.first_oi.toLocaleString("en-IN")} → {f.last_oi.toLocaleString("en-IN")} OI
        {" · "}
        ₹{f.first_ltp} → ₹{f.last_ltp}
        {" · "}
        Total: {f.total_change_lots > 0 ? "+" : ""}{f.total_change_lots.toLocaleString("en-IN")}
      </div>

      {/* RECOMMENDATION (the value) */}
      {rec.action && (
        <div style={{
          background: `${urgencyColor}15`, border: `1px solid ${urgencyColor}55`,
          borderRadius: 6, padding: "8px 10px", marginTop: 4,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "center", marginBottom: 3 }}>
            <span style={{ color: urgencyColor, fontSize: 11, fontWeight: 800,
                           letterSpacing: 0.4 }}>
              → {rec.action}
            </span>
            <span style={{
              background: urgencyColor, color: "#000",
              padding: "1px 6px", borderRadius: 3,
              fontSize: 8, fontWeight: 700, letterSpacing: 0.4,
            }}>
              {rec.urgency}
            </span>
          </div>
          {rec.reason && (
            <div style={{ color: "#bbb", fontSize: 10, lineHeight: 1.5 }}>
              {rec.reason}
            </div>
          )}
          {rec.trade_window && (
            <div style={{
              marginTop: 4, color: urgencyColor, fontSize: 9, fontWeight: 600,
              fontFamily: "ui-monospace, monospace",
            }}>
              ⏱ {rec.trade_window}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function Metric({ label, value, color = "#aaa" }) {
  return (
    <div style={{
      background: "rgba(255,255,255,0.03)", borderRadius: 4,
      padding: "3px 6px", textAlign: "center",
    }}>
      <div style={{ color: "#666", fontSize: 8, fontWeight: 700, textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ color, fontSize: 11, fontWeight: 700 }}>{value}</div>
    </div>
  );
}


function StrikeDrillModal({ idx, strike, onClose }) {
  const [history, setHistory] = useState(null);
  useEffect(() => {
    fetch(`${API}/api/smart-money/strike/${strike}?idx=${idx}&minutes=60`)
      .then(r => r.ok ? r.json() : null)
      .then(j => setHistory(j))
      .catch(() => {});
  }, [idx, strike]);

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 1500, padding: 20,
    }} onClick={onClose}>
      <div style={{
        background: "#111118", border: "1px solid #2A2A3F",
        borderRadius: 12, padding: "20px 24px",
        maxWidth: 720, width: "100%", maxHeight: "85vh", overflow: "auto",
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between",
                      alignItems: "center", marginBottom: 16 }}>
          <div>
            <div style={{ color: "#fff", fontSize: 18, fontWeight: 800 }}>
              {idx} {strike} — 60 min history
            </div>
            <div style={{ color: "#888", fontSize: 11, marginTop: 4 }}>
              Per-minute OI + LTP evolution for both CE and PE
            </div>
          </div>
          <button onClick={onClose} style={{
            background: "transparent", border: "1px solid #2A2A3F",
            color: "#aaa", fontSize: 14, padding: "6px 12px",
            borderRadius: 6, cursor: "pointer",
          }}>
            ✕ Close
          </button>
        </div>

        {!history ? (
          <div style={{ color: "#666", padding: 20, textAlign: "center" }}>
            Loading…
          </div>
        ) : (history.history || []).length === 0 ? (
          <div style={{ color: "#666", padding: 20, textAlign: "center" }}>
            No data captured yet for this strike
          </div>
        ) : (
          <DrillTable history={history.history} />
        )}
      </div>
    </div>
  );
}


function DrillTable({ history }) {
  // Show last 30 samples to keep modal usable
  const slice = history.slice(-30);
  return (
    <div>
      <div style={{
        display: "grid", gridTemplateColumns: "80px 90px 90px 90px 90px",
        gap: 6, padding: "6px 0", fontSize: 9, color: "#666",
        fontWeight: 700, textTransform: "uppercase", borderBottom: "1px solid #1E1E2E",
      }}>
        <span>Time</span>
        <span style={{ color: "#30D158" }}>CE OI</span>
        <span style={{ color: "#30D158" }}>CE LTP</span>
        <span style={{ color: "#FF453A" }}>PE OI</span>
        <span style={{ color: "#FF453A" }}>PE LTP</span>
      </div>
      {slice.map((row, i) => {
        const t = new Date(row.ts * 1000).toLocaleTimeString("en-IN",
          { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
        const prev = i > 0 ? slice[i - 1] : null;
        const ceOiD = prev ? row.ce_oi - prev.ce_oi : 0;
        const peOiD = prev ? row.pe_oi - prev.pe_oi : 0;
        return (
          <div key={i} style={{
            display: "grid", gridTemplateColumns: "80px 90px 90px 90px 90px",
            gap: 6, padding: "4px 0", fontSize: 10,
            borderBottom: "1px dashed #1E1E2E15",
          }}>
            <span style={{ color: "#888", fontFamily: "ui-monospace, monospace" }}>{t}</span>
            <span style={{ color: "#ccc" }}>
              {row.ce_oi.toLocaleString("en-IN")}
              {ceOiD !== 0 && (
                <span style={{ color: ceOiD > 0 ? "#30D158" : "#FF9F0A",
                               fontSize: 9, marginLeft: 4 }}>
                  ({ceOiD > 0 ? "+" : ""}{ceOiD})
                </span>
              )}
            </span>
            <span style={{ color: "#aaa" }}>₹{row.ce_ltp}</span>
            <span style={{ color: "#ccc" }}>
              {row.pe_oi.toLocaleString("en-IN")}
              {peOiD !== 0 && (
                <span style={{ color: peOiD > 0 ? "#30D158" : "#FF9F0A",
                               fontSize: 9, marginLeft: 4 }}>
                  ({peOiD > 0 ? "+" : ""}{peOiD})
                </span>
              )}
            </span>
            <span style={{ color: "#aaa" }}>₹{row.pe_ltp}</span>
          </div>
        );
      })}
    </div>
  );
}
