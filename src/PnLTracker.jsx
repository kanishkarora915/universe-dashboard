import { useState, useEffect } from "react";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const CARD = "#111118";
const BORDER = "#1E1E2E";

const NIFTY_LOT = 75;
const BANKNIFTY_LOT = 30;

function getLotSize(instrument) {
  return instrument === "BANKNIFTY" ? BANKNIFTY_LOT : NIFTY_LOT;
}

function loadTrades() {
  try {
    return JSON.parse(localStorage.getItem("universe_pnl_trades") || "[]");
  } catch { return []; }
}

function saveTrades(trades) {
  localStorage.setItem("universe_pnl_trades", JSON.stringify(trades));
}

export default function PnLTracker({ signals }) {
  const [trades, setTrades] = useState(loadTrades);
  const [tab, setTab] = useState("open"); // open | closed | analytics

  useEffect(() => { saveTrades(trades); }, [trades]);

  // Auto-add new signals as open trades
  useEffect(() => {
    if (!signals || signals.length === 0) return;
    const existingIds = new Set(trades.map(t => t.signalId));
    const newTrades = [];
    for (const s of signals) {
      const sid = `${s.instrument}-${s.strike}-${s.time}`;
      if (!existingIds.has(sid) && s.status === "ACTIVE") {
        const entryParts = s.entry.split("\u2013");
        const entryAvg = entryParts.length === 2
          ? (parseFloat(entryParts[0]) + parseFloat(entryParts[1])) / 2
          : parseFloat(s.entry) || 0;
        newTrades.push({
          signalId: sid,
          instrument: s.instrument,
          type: s.type,
          strike: s.strike,
          expiry: s.expiry,
          entryPrice: Math.round(entryAvg),
          exitPrice: null,
          t1: parseFloat(s.t1) || 0,
          t2: parseFloat(s.t2) || 0,
          sl: parseFloat(s.sl) || 0,
          score: s.score,
          entryTime: s.time,
          exitTime: null,
          status: "OPEN",
          lots: 1,
        });
      }
    }
    if (newTrades.length > 0) {
      setTrades(prev => [...newTrades, ...prev]);
    }
  }, [signals]);

  const openTrades = trades.filter(t => t.status === "OPEN");
  const closedTrades = trades.filter(t => t.status !== "OPEN");

  const markExit = (idx, exitPrice) => {
    setTrades(prev => prev.map((t, i) => i === idx ? {
      ...t, exitPrice: parseFloat(exitPrice), exitTime: new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: true }),
      status: parseFloat(exitPrice) >= t.entryPrice ? "WIN" : "LOSS"
    } : t));
  };

  const deleteTrade = (idx) => {
    setTrades(prev => prev.filter((_, i) => i !== idx));
  };

  const clearAll = () => {
    if (confirm("Clear all trade history?")) {
      setTrades([]);
    }
  };

  // Analytics
  const totalTrades = closedTrades.length;
  const wins = closedTrades.filter(t => t.status === "WIN").length;
  const losses = closedTrades.filter(t => t.status === "LOSS").length;
  const winRate = totalTrades > 0 ? ((wins / totalTrades) * 100).toFixed(1) : 0;

  const pnlList = closedTrades.map(t => {
    const lot = getLotSize(t.instrument);
    return (t.exitPrice - t.entryPrice) * lot * t.lots;
  });
  const totalPnL = pnlList.reduce((a, b) => a + b, 0);
  const avgProfit = pnlList.filter(p => p > 0).length > 0
    ? pnlList.filter(p => p > 0).reduce((a, b) => a + b, 0) / pnlList.filter(p => p > 0).length : 0;
  const avgLoss = pnlList.filter(p => p < 0).length > 0
    ? pnlList.filter(p => p < 0).reduce((a, b) => a + b, 0) / pnlList.filter(p => p < 0).length : 0;
  const bestTrade = pnlList.length > 0 ? Math.max(...pnlList) : 0;
  const worstTrade = pnlList.length > 0 ? Math.min(...pnlList) : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Sub-tabs */}
      <div style={{ display: "flex", gap: 0, background: CARD, borderRadius: 10, overflow: "hidden", border: `1px solid ${BORDER}` }}>
        {[["open", `Open (${openTrades.length})`], ["closed", `Closed (${closedTrades.length})`], ["analytics", "Analytics"]].map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} style={{
            flex: 1, padding: "10px", background: tab === id ? ACCENT + "22" : "transparent",
            color: tab === id ? ACCENT : "#555", border: "none", cursor: "pointer",
            fontWeight: tab === id ? 700 : 400, fontSize: 12, borderBottom: tab === id ? `2px solid ${ACCENT}` : "none",
          }}>{label}</button>
        ))}
        <button onClick={clearAll} style={{
          padding: "10px 14px", background: RED + "11", color: RED, border: "none",
          cursor: "pointer", fontSize: 10, fontWeight: 700,
        }}>Clear All</button>
      </div>

      {/* Open Trades */}
      {tab === "open" && (
        openTrades.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "#555" }}>
            <div style={{ fontSize: 14 }}>No open trades. Signals with score 5+ auto-add here.</div>
          </div>
        ) : openTrades.map((t, idx) => {
          const realIdx = trades.indexOf(t);
          const lot = getLotSize(t.instrument);
          return (
            <TradeCard key={t.signalId} t={t} lot={lot} onExit={(price) => markExit(realIdx, price)} onDelete={() => deleteTrade(realIdx)} />
          );
        })
      )}

      {/* Closed Trades */}
      {tab === "closed" && (
        closedTrades.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "#555" }}>
            <div style={{ fontSize: 14 }}>No closed trades yet. Mark exit on open trades to see PnL.</div>
          </div>
        ) : closedTrades.map((t, idx) => {
          const lot = getLotSize(t.instrument);
          const pnl = (t.exitPrice - t.entryPrice) * lot * t.lots;
          return (
            <div key={idx} style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "14px 18px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div>
                  <span style={{ color: ACCENT, fontWeight: 700, fontSize: 14 }}>{t.instrument}</span>
                  <span style={{ color: t.type.includes("PUT") ? RED : GREEN, fontSize: 11, marginLeft: 8, fontWeight: 700 }}>{t.type}</span>
                  <span style={{ color: "#555", fontSize: 11, marginLeft: 8 }}>{t.strike}</span>
                </div>
                <span style={{ color: t.status === "WIN" ? GREEN : RED, fontWeight: 900, fontSize: 16 }}>
                  {pnl >= 0 ? "+" : ""}{Math.round(pnl).toLocaleString("en-IN")}
                </span>
              </div>
              <div style={{ display: "flex", gap: 20, fontSize: 11, color: "#666" }}>
                <span>Entry: {t.entryPrice} | Exit: {t.exitPrice}</span>
                <span>Lots: {t.lots} x {lot}</span>
                <span>{t.entryTime} → {t.exitTime}</span>
                <span style={{ color: t.status === "WIN" ? GREEN : RED, fontWeight: 700 }}>{t.status}</span>
              </div>
            </div>
          );
        })
      )}

      {/* Analytics */}
      {tab === "analytics" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
            <AnalCard label="Total Trades" value={totalTrades} color="#ccc" />
            <AnalCard label="Win Rate" value={`${winRate}%`} color={parseFloat(winRate) >= 50 ? GREEN : RED} />
            <AnalCard label="Total PnL" value={`${totalPnL >= 0 ? "+" : ""}${Math.round(totalPnL).toLocaleString("en-IN")}`} color={totalPnL >= 0 ? GREEN : RED} />
            <AnalCard label="Wins / Losses" value={`${wins} / ${losses}`} color={YELLOW} />
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
            <AnalCard label="Avg Profit" value={`+${Math.round(avgProfit).toLocaleString("en-IN")}`} color={GREEN} />
            <AnalCard label="Avg Loss" value={Math.round(avgLoss).toLocaleString("en-IN")} color={RED} />
            <AnalCard label="Best Trade" value={`+${Math.round(bestTrade).toLocaleString("en-IN")}`} color={GREEN} />
            <AnalCard label="Worst Trade" value={Math.round(worstTrade).toLocaleString("en-IN")} color={RED} />
          </div>

          {/* PnL Bar Chart */}
          {pnlList.length > 0 && (
            <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 18px" }}>
              <div style={{ color: "#555", fontSize: 10, fontWeight: 700, letterSpacing: 1.5, marginBottom: 12 }}>TRADE-BY-TRADE PNL</div>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 120 }}>
                {pnlList.map((pnl, i) => {
                  const maxAbs = Math.max(...pnlList.map(Math.abs), 1);
                  const h = Math.max(Math.abs(pnl) / maxAbs * 100, 4);
                  return (
                    <div key={i} style={{
                      flex: 1, maxWidth: 40,
                      height: h, background: pnl >= 0 ? GREEN : RED,
                      borderRadius: "4px 4px 0 0", opacity: 0.8,
                      alignSelf: "flex-end",
                    }} title={`Trade ${i + 1}: ${pnl >= 0 ? "+" : ""}${Math.round(pnl)}`} />
                  );
                })}
              </div>
            </div>
          )}

          {totalTrades === 0 && (
            <div style={{ textAlign: "center", padding: 40, color: "#555" }}>
              <div style={{ fontSize: 14 }}>Close some trades to see analytics</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TradeCard({ t, lot, onExit, onDelete }) {
  const [exitInput, setExitInput] = useState("");
  const [showInput, setShowInput] = useState(false);

  return (
    <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "14px 18px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
        <div>
          <span style={{ color: ACCENT, fontWeight: 700, fontSize: 14 }}>{t.instrument}</span>
          <span style={{ color: t.type.includes("PUT") ? RED : GREEN, fontSize: 11, marginLeft: 8, fontWeight: 700,
            padding: "2px 8px", background: (t.type.includes("PUT") ? RED : GREEN) + "22", borderRadius: 10 }}>{t.type}</span>
          <span style={{ color: PURPLE, fontSize: 11, marginLeft: 8, fontWeight: 700 }}>{t.score}/9</span>
          <div style={{ color: "#555", fontSize: 11, marginTop: 4 }}>{t.strike} | {t.expiry} | {t.entryTime}</div>
        </div>
        <button onClick={onDelete} style={{
          background: "transparent", color: "#333", border: "none", cursor: "pointer", fontSize: 16,
        }}>x</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 10 }}>
        <MiniStat label="Entry" value={`${t.entryPrice}`} color="#ccc" />
        <MiniStat label="T1" value={`${t.t1}`} color={GREEN} />
        <MiniStat label="T2" value={`${t.t2}`} color={GREEN} />
        <MiniStat label="SL" value={`${t.sl}`} color={RED} />
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <span style={{ color: "#555", fontSize: 11 }}>Lot: {t.lots} x {lot} = {t.lots * lot} qty</span>
        {!showInput ? (
          <button onClick={() => setShowInput(true)} style={{
            background: YELLOW + "22", color: YELLOW, border: `1px solid ${YELLOW}44`,
            borderRadius: 6, padding: "4px 12px", cursor: "pointer", fontSize: 11, fontWeight: 700, marginLeft: "auto",
          }}>Mark Exit</button>
        ) : (
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <input type="number" placeholder="Exit price" value={exitInput} onChange={e => setExitInput(e.target.value)}
              style={{ width: 80, padding: "4px 8px", background: "#0D0D15", border: `1px solid ${BORDER}`,
                borderRadius: 6, color: "#fff", fontSize: 12 }} />
            <button onClick={() => { if (exitInput) onExit(exitInput); }} style={{
              background: GREEN + "22", color: GREEN, border: `1px solid ${GREEN}44`,
              borderRadius: 6, padding: "4px 10px", cursor: "pointer", fontSize: 11, fontWeight: 700,
            }}>Save</button>
          </div>
        )}
      </div>
    </div>
  );
}

function MiniStat({ label, value, color }) {
  return (
    <div style={{ background: "#0D0D15", borderRadius: 6, padding: "6px 10px" }}>
      <div style={{ color: "#555", fontSize: 9, marginBottom: 2 }}>{label}</div>
      <div style={{ color, fontWeight: 700, fontSize: 13 }}>{value}</div>
    </div>
  );
}

function AnalCard({ label, value, color }) {
  return (
    <div style={{ background: "#0D0D15", borderRadius: 8, padding: "10px 14px", textAlign: "center" }}>
      <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
      <div style={{ color, fontWeight: 900, fontSize: 18 }}>{value}</div>
    </div>
  );
}
