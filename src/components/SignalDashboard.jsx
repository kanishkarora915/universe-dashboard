import { useState, useEffect } from "react";
import OIHeatmap from "./OIHeatmap";
import { PnLChart } from "./Charts";
import { SkeletonCard, EmptyState } from "./Skeleton";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const CARD = "#111118";
const BORDER = "#1E1E2E";
const BG = "#0A0A0F";

const fmt = (n) => (n ? Math.round(n).toLocaleString("en-IN") : "0");

function Collapsible({ title, defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button onClick={() => setOpen(!open)} style={{
        background: "none", border: "none", cursor: "pointer",
        color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase",
        letterSpacing: 1, padding: "8px 0", display: "flex", alignItems: "center", gap: 6, width: "100%",
      }}>
        <span style={{ color: "#333", fontSize: 12, transition: "transform 0.2s", transform: open ? "rotate(90deg)" : "rotate(0)" }}>▶</span>
        {title}
      </button>
      {open && children}
    </div>
  );
}

const Stat = ({ label, value, color = "#fff", sub }) => (
  <div style={{ background: BG, borderRadius: 8, padding: "10px 14px", flex: 1, minWidth: 80 }}>
    <div style={{ color: "#555", fontSize: 9, fontWeight: 700, textTransform: "uppercase" }}>{label}</div>
    <div style={{ color, fontWeight: 700, fontSize: 14 }}>{value}</div>
    {sub && <div style={{ color: "#444", fontSize: 9 }}>{sub}</div>}
  </div>
);

function VerdictCard({ label, verdict }) {
  if (!verdict) {
    return (
      <div style={{ flex: 1, minWidth: 200, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ color: "#555", fontSize: 12, fontWeight: 700 }}>{label}</div>
        <div style={{ color: "#333", fontSize: 20, fontWeight: 900, marginTop: 8 }}>NO DATA</div>
      </div>
    );
  }

  const action = verdict.action || "NO TRADE";
  const prob = verdict.winProbability || 0;
  const color = action === "BUY CE" ? GREEN : action === "BUY PE" ? RED : "#555";
  const icon = action === "BUY CE" ? "🚀" : action === "BUY PE" ? "💣" : "⏸️";

  return (
    <div style={{
      flex: 1, minWidth: 200, background: CARD,
      border: `2px solid ${color}44`, borderRadius: 12, padding: "16px 20px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ color: "#888", fontSize: 11, fontWeight: 700 }}>{label}</div>
        <div style={{ color, fontSize: 11, fontWeight: 700 }}>{prob}%</div>
      </div>
      <div style={{ color, fontSize: 24, fontWeight: 900, marginTop: 6 }}>
        {icon} {action}
      </div>
      {verdict.confidence && (
        <div style={{ color: "#555", fontSize: 10, marginTop: 4 }}>
          Confidence: {verdict.confidence} | ATM: {verdict.atm}
        </div>
      )}
      {/* Top reason */}
      {verdict.reasons && verdict.reasons[0] && (
        <div style={{ color: "#666", fontSize: 10, marginTop: 6, borderTop: `1px solid ${BORDER}`, paddingTop: 6 }}>
          {verdict.reasons[0]}
        </div>
      )}
    </div>
  );
}

function EngineBar({ name, score, max }) {
  const pct = Math.min((score / max) * 100, 100);
  const color = pct > 60 ? GREEN : pct > 30 ? YELLOW : pct > 0 ? ORANGE : "#222";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
      <div style={{ width: 100, fontSize: 9, color: "#666", fontWeight: 600, textTransform: "uppercase" }}>
        {name.replace(/_/g, " ").slice(0, 12)}
      </div>
      <div style={{ flex: 1, height: 12, background: "#0d0d15", borderRadius: 3, overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`, height: "100%", background: color,
          borderRadius: 3, transition: "width 0.3s",
        }} />
      </div>
      <div style={{ width: 24, fontSize: 9, color: "#888", textAlign: "right" }}>{score}</div>
    </div>
  );
}

function OpenTradeCard({ trade }) {
  if (!trade) {
    return (
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", marginBottom: 8 }}>OPEN TRADE</div>
        <div style={{ color: "#333", textAlign: "center", padding: 12, fontSize: 12 }}>No open positions</div>
      </div>
    );
  }

  const pnl = trade.pnl_rupees || 0;
  const pnlColor = pnl >= 0 ? GREEN : RED;

  return (
    <div style={{
      background: CARD, border: `1px solid ${pnlColor}33`, borderRadius: 12, padding: "16px 20px",
    }}>
      <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", marginBottom: 8 }}>OPEN TRADE</div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <span style={{ color: "#ccc", fontWeight: 700, fontSize: 14 }}>{trade.idx} </span>
          <span style={{
            color: trade.action?.includes("CE") ? GREEN : RED,
            fontSize: 11, fontWeight: 700, padding: "2px 6px",
            background: (trade.action?.includes("CE") ? GREEN : RED) + "22",
            borderRadius: 4,
          }}>{trade.action}</span>
          <span style={{ color: "#888", fontSize: 11, marginLeft: 8 }}>{trade.strike}</span>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: pnlColor, fontWeight: 900, fontSize: 18 }}>₹{fmt(pnl)}</div>
          <div style={{ color: "#555", fontSize: 9 }}>
            Entry ₹{trade.entry_price} | LTP ₹{trade.current_ltp}
          </div>
        </div>
      </div>
      {/* SL/T1/T2 bar */}
      <div style={{ display: "flex", gap: 8, marginTop: 8, fontSize: 9, color: "#666" }}>
        <span>SL ₹{trade.sl_price}</span>
        <span>T1 ₹{trade.t1_price}</span>
        <span>T2 ₹{trade.t2_price}</span>
        <span style={{ marginLeft: "auto", color: trade.breakeven_active ? GREEN : "#444" }}>
          {trade.breakeven_active ? "BE Active" : ""}
        </span>
      </div>
    </div>
  );
}

export default function SignalDashboard({ live, signals, oiSummary }) {
  const [verdict, setVerdict] = useState(null);
  const [openTrade, setOpenTrade] = useState(null);
  const [gapPred, setGapPred] = useState(null);

  useEffect(() => {
    fetch("/api/trap/verdict").then(r => r.ok ? r.json() : null).then(setVerdict).catch(() => {});
    fetch("/api/trades/open").then(r => r.ok ? r.json() : []).then(trades => {
      setOpenTrade(Array.isArray(trades) && trades.length > 0 ? trades[0] : null);
    }).catch(() => {});
    fetch("/api/autopsy/gap-prediction/NIFTY").then(r => r.ok ? r.json() : null).then(setGapPred).catch(() => {});

    const interval = setInterval(() => {
      fetch("/api/trap/verdict").then(r => r.ok ? r.json() : null).then(setVerdict).catch(() => {});
      fetch("/api/trades/open").then(r => r.ok ? r.json() : []).then(trades => {
        setOpenTrade(Array.isArray(trades) && trades.length > 0 ? trades[0] : null);
      }).catch(() => {});
    }, 15000);
    return () => clearInterval(interval);
  }, []);

  const nVerdict = verdict?.nifty || {};
  const bnVerdict = verdict?.banknifty || {};

  // Engine scores from verdict
  const engines = nVerdict.engineScores || bnVerdict.engineScores || {};
  const engineMax = {
    seller_positioning: 30, trap_fingerprints: 20, price_action: 20,
    oi_flow: 15, market_context: 15, vwap: 5,
    multi_timeframe: 15, fii_dii: 10, global_cues: 10,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Verdict Cards */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <VerdictCard label="NIFTY" verdict={nVerdict} />
        <VerdictCard label="BANKNIFTY" verdict={bnVerdict} />
      </div>

      {/* Engine Scores + Open Trade */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {/* Engine Bars */}
        <div style={{ flex: 1, minWidth: 250, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
          <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", marginBottom: 10 }}>
            ENGINE SCORES {nVerdict.action ? "(NIFTY)" : bnVerdict.action ? "(BANKNIFTY)" : ""}
          </div>
          {Object.entries(engineMax).map(([name, max]) => (
            <EngineBar key={name} name={name} score={engines[name] || 0} max={max} />
          ))}
        </div>

        {/* Open Trade */}
        <div style={{ flex: 1, minWidth: 250 }}>
          <OpenTradeCard trade={openTrade} />

          {/* Gap Prediction Mini */}
          {gapPred && gapPred.prediction !== "NEED DATA" && (
            <div style={{
              background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12,
              padding: "12px 16px", marginTop: 12,
            }}>
              <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", marginBottom: 4 }}>
                TOMORROW'S GAP
              </div>
              <div style={{
                color: gapPred.prediction === "GAP UP" ? GREEN : gapPred.prediction === "GAP DOWN" ? RED : YELLOW,
                fontWeight: 900, fontSize: 18,
              }}>
                {gapPred.prediction === "GAP UP" ? "📈" : gapPred.prediction === "GAP DOWN" ? "📉" : "➡️"} {gapPred.prediction} {gapPred.confidence}%
              </div>
            </div>
          )}
        </div>
      </div>

      {/* OI Heatmap — collapsible */}
      <Collapsible title="OI Heatmap — Institutional Positioning" defaultOpen={false}>
        {oiSummary ? (
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 280, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
              <OIHeatmap oiData={oiSummary} index="nifty" />
            </div>
            <div style={{ flex: 1, minWidth: 280, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
              <OIHeatmap oiData={oiSummary} index="banknifty" />
            </div>
          </div>
        ) : <SkeletonCard />}
      </Collapsible>

      {/* P&L Chart — collapsible */}
      <Collapsible title="P&L Performance" defaultOpen={false}>
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
          <PnLChart />
        </div>
      </Collapsible>
    </div>
  );
}
