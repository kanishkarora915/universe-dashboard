/**
 * ScalperTab — INDEPENDENT scalper trading dashboard.
 *
 * Fully separate from main P&L:
 *  - User sets capital + qty per trade (own config)
 *  - Engine picks trades itself (enable/disable toggle)
 *  - Real SL/T1/T2 calculated from % of entry
 *  - Own DB, own stats, own open/closed
 *  - LIVE TICK LTP (1s polling for real-trading-app feel)
 *  - Manual EXIT button per trade
 *  - Tick chart per trade (entry, peak, exit visual)
 *  - Capital usage panel (committed, available, live value, unrealized)
 *  - Entry reasoning + exit reason on every card
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import SmartSLLadder from "./components/SmartSLLadder";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const CARD = "#111118";
const BORDER = "#1E1E2E";
const BG = "#0A0A0F";

const rupees = (n) => `₹${Math.round(n || 0).toLocaleString("en-IN")}`;
const rupeesL = (n) => {
  const x = Math.abs(n || 0);
  if (x >= 10000000) return `₹${(n / 10000000).toFixed(2)}Cr`;
  if (x >= 100000) return `₹${(n / 100000).toFixed(2)}L`;
  if (x >= 1000) return `₹${(n / 1000).toFixed(1)}k`;
  return `₹${Math.round(n || 0).toLocaleString("en-IN")}`;
};
const pctFmt = (n, d = 2) => `${(n || 0) >= 0 ? "+" : ""}${(n || 0).toFixed(d)}%`;

async function safeFetch(url, fb) {
  try { const r = await fetch(url); if (!r.ok) return fb; return await r.json(); } catch { return fb; }
}
async function postJSON(url, body = {}) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

export default function ScalperTab() {
  const [status, setStatus] = useState(null);
  const [config, setConfig] = useState(null);
  const [openTrades, setOpenTrades] = useState([]);
  const [closedTrades, setClosedTrades] = useState([]);
  const [capitalUsage, setCapitalUsage] = useState(null);
  const [livePrices, setLivePrices] = useState({});  // {tradeId: {ltp, pnl_rupees, pnl_pct}}
  const [saving, setSaving] = useState(false);
  const [expandedId, setExpandedId] = useState(null);

  // form state
  const [capital, setCapital] = useState("");
  const [niftyQty, setNiftyQty] = useState("");
  const [bnQty, setBnQty] = useState("");
  const [slPct, setSlPct] = useState("");
  const [t1Pct, setT1Pct] = useState("");
  const [t2Pct, setT2Pct] = useState("");
  const [threshold, setThreshold] = useState("");
  const [dailyCap, setDailyCap] = useState("");

  // Full reload (5s) — config, status, full lists
  const fullLoad = useCallback(async () => {
    const [st, cf, op, cl, cu] = await Promise.all([
      safeFetch("/api/scalper/status", null),
      safeFetch("/api/scalper/config", null),
      safeFetch("/api/scalper/trades/open", []),
      safeFetch("/api/scalper/trades/closed?days=30", []),
      safeFetch("/api/scalper/capital-usage", null),
    ]);
    setStatus(st);
    if (cf && !cf.error) {
      setConfig(cf);
      if (!capital) setCapital(String(cf.capital || 1000000));
      if (!niftyQty) setNiftyQty(String(cf.nifty_qty || 0));
      if (!bnQty) setBnQty(String(cf.banknifty_qty || 0));
      if (!slPct) setSlPct(String(((cf.sl_pct || 0.12) * 100).toFixed(1)));
      if (!t1Pct) setT1Pct(String(((cf.t1_pct || 0.20) * 100).toFixed(1)));
      if (!t2Pct) setT2Pct(String(((cf.t2_pct || 0.40) * 100).toFixed(1)));
      if (!threshold) setThreshold(String(cf.threshold || 55));
      if (!dailyCap) setDailyCap(String(cf.daily_cap || 15));
    }
    setOpenTrades(Array.isArray(op) ? op : []);
    setClosedTrades(Array.isArray(cl) ? cl : []);
    if (cu && !cu.error) setCapitalUsage(cu);
  }, [capital, niftyQty, bnQty, slPct, t1Pct, t2Pct, threshold, dailyCap]);

  // Live tick poll (1s) — zero-latency LTP from engine.chains in-memory
  const livePoll = useCallback(async () => {
    const r = await safeFetch("/api/scalper/live-prices", null);
    if (r && Array.isArray(r.prices)) {
      const map = {};
      r.prices.forEach(p => { map[p.id] = p; });
      setLivePrices(map);
    }
  }, []);

  useEffect(() => {
    fullLoad();
    const ivFull = setInterval(fullLoad, 5000);
    const ivTick = setInterval(livePoll, 1000);  // 1s tick poll like real trading apps
    return () => { clearInterval(ivFull); clearInterval(ivTick); };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const saveConfig = async () => {
    setSaving(true);
    await postJSON("/api/scalper/config", {
      capital: parseFloat(capital) || 1000000,
      nifty_qty: parseInt(niftyQty, 10) || 0,
      banknifty_qty: parseInt(bnQty, 10) || 0,
      sl_pct: (parseFloat(slPct) || 12) / 100,
      t1_pct: (parseFloat(t1Pct) || 20) / 100,
      t2_pct: (parseFloat(t2Pct) || 40) / 100,
      threshold: parseInt(threshold, 10) || 55,
      daily_cap: parseInt(dailyCap, 10) || 15,
    });
    await fullLoad();
    setSaving(false);
  };

  const toggleScalper = async () => {
    const endpoint = status?.enabled ? "/api/scalper/disable" : "/api/scalper/enable";
    await postJSON(endpoint, {});
    await fullLoad();
  };

  const manualExit = async (tradeId) => {
    if (!window.confirm("Manually exit this trade at current LTP?")) return;
    await postJSON(`/api/scalper/trades/${tradeId}/exit`, {});
    await fullLoad();
  };

  // Smart SL toggle state
  const [smartSL, setSmartSL] = useState(null);
  useEffect(() => {
    const fetchSL = async () => {
      const r = await safeFetch("/api/scalper/smart-sl", null);
      if (r && !r.error) setSmartSL(r);
    };
    fetchSL();
    const iv = setInterval(fetchSL, 10000);
    return () => clearInterval(iv);
  }, []);

  const toggleSmartSL = async () => {
    const r = await postJSON("/api/scalper/smart-sl/toggle", {});
    if (r && r.config) setSmartSL(prev => ({ ...prev, ...r.config }));
  };

  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const closedToday = closedTrades.filter(t => (t.entry_time || "").startsWith(today));
  const todayPnl = closedToday.reduce((s, t) => s + (t.pnl_rupees || 0), 0);
  const todayWins = closedToday.filter(t => t.status === "T1_HIT" || t.status === "T2_HIT").length;
  const todayLosses = closedToday.filter(t => t.status === "SL_HIT").length;
  const openLivePnl = capitalUsage?.unrealized_pnl ?? 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header */}
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div>
            <div style={{ color: ORANGE, fontSize: 15, fontWeight: 900 }}>⚡ SCALPER MODE — Independent</div>
            <div style={{ color: "#777", fontSize: 11, marginTop: 2 }}>
              Own capital · Live tick LTP (1s) · Manual exit · Smart SL toggle
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {/* Smart SL Toggle */}
            <button onClick={toggleSmartSL} style={{
              background: smartSL?.enabled ? GREEN + "22" : "#222",
              color: smartSL?.enabled ? GREEN : "#888",
              border: `1px solid ${smartSL?.enabled ? GREEN : BORDER}`,
              padding: "6px 14px", borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: "pointer",
            }}>
              🛡️ Smart SL: {smartSL?.enabled ? "ON" : "OFF"}
            </button>
            <div style={{
              background: status?.enabled ? GREEN + "22" : "#333",
              color: status?.enabled ? GREEN : "#888",
              padding: "4px 12px", borderRadius: 20, fontSize: 11, fontWeight: 700,
              border: `1px solid ${status?.enabled ? GREEN : BORDER}`,
            }}>
              {status?.enabled ? "● LIVE" : "○ OFF"}
            </div>
            <button onClick={toggleScalper} style={{
              background: status?.enabled ? RED : GREEN, color: "#fff", border: "none",
              padding: "6px 16px", borderRadius: 6, fontSize: 12, fontWeight: 700, cursor: "pointer",
            }}>
              {status?.enabled ? "STOP" : "START"}
            </button>
          </div>
        </div>
      </div>

      {/* CAPITAL USAGE PANEL */}
      <CapitalUsagePanel usage={capitalUsage} config={config} todayPnl={todayPnl} openLivePnl={openLivePnl} />

      {/* Quick stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 }}>
        <StatBox label="Today Realized" value={rupees(todayPnl)} color={todayPnl >= 0 ? GREEN : RED} sub={`${closedToday.length} closed`} />
        <StatBox label="Open Live P&L" value={rupees(openLivePnl)} color={openLivePnl >= 0 ? GREEN : RED} sub={`${openTrades.length} open`} />
        <StatBox label="Wins Today" value={todayWins} color={GREEN} />
        <StatBox label="Losses Today" value={todayLosses} color={RED} />
        <StatBox
          label="Win Rate"
          value={`${todayWins + todayLosses > 0 ? Math.round((todayWins / (todayWins + todayLosses)) * 100) : 0}%`}
          color={todayWins > todayLosses ? GREEN : todayLosses > todayWins ? RED : "#888"}
        />
        <StatBox
          label="Trades Left"
          value={`${(config?.daily_cap || 15) - closedToday.length - openTrades.length}`}
          color={YELLOW}
          sub={`cap ${config?.daily_cap || 15}`}
        />
      </div>

      {/* CONFIG (collapsed by default) */}
      <details style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "12px 16px" }}>
        <summary style={{ cursor: "pointer", color: "#aaa", fontSize: 12, fontWeight: 700 }}>
          ⚙️ Scalper Config — Capital, Qty, SL/T1/T2 (independent of main P&L)
        </summary>
        <div style={{ marginTop: 12 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10, marginBottom: 10 }}>
            <FieldInput label="Capital (₹)" value={capital} onChange={setCapital} hint="e.g. 1000000" />
            <FieldInput label="NIFTY qty/trade" value={niftyQty} onChange={setNiftyQty} hint="0 = auto from capital" />
            <FieldInput label="BANKNIFTY qty/trade" value={bnQty} onChange={setBnQty} hint="0 = auto" />
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: 10, marginBottom: 10 }}>
            <FieldInput label="SL %" value={slPct} onChange={setSlPct} />
            <FieldInput label="T1 %" value={t1Pct} onChange={setT1Pct} />
            <FieldInput label="T2 %" value={t2Pct} onChange={setT2Pct} />
            <FieldInput label="Min Win %" value={threshold} onChange={setThreshold} />
            <FieldInput label="Daily Cap" value={dailyCap} onChange={setDailyCap} />
          </div>
          <button onClick={saveConfig} disabled={saving} style={{
            background: saving ? "#333" : ACCENT, color: "#fff", border: "none",
            padding: "8px 20px", borderRadius: 6, fontSize: 12, fontWeight: 700,
            cursor: saving ? "wait" : "pointer",
          }}>
            {saving ? "Saving…" : "Save Config"}
          </button>
        </div>
      </details>

      {/* OPEN TRADES — with live ticks */}
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ color: "#aaa", fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>
          🔴 OPEN TRADES ({openTrades.length}) <span style={{ color: GREEN, fontSize: 9, marginLeft: 8 }}>● LIVE TICK 1s</span>
        </div>
        {openTrades.length === 0 && (
          <div style={{ color: "#555", textAlign: "center", padding: 20, fontSize: 12 }}>No open scalper trades.</div>
        )}
        {openTrades.map(t => (
          <ScalperTradeCard
            key={t.id}
            t={t}
            livePrice={livePrices[t.id]}
            isExpanded={expandedId === t.id}
            onToggleExpand={() => setExpandedId(expandedId === t.id ? null : t.id)}
            onManualExit={() => manualExit(t.id)}
          />
        ))}
      </div>

      {/* TODAY'S CLOSED */}
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ color: "#aaa", fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>
          ✅ TODAY'S CLOSED ({closedToday.length})
        </div>
        {closedToday.length === 0 && (
          <div style={{ color: "#555", textAlign: "center", padding: 20, fontSize: 12 }}>No closed trades today.</div>
        )}
        {closedToday.map(t => (
          <ScalperTradeCard
            key={t.id} t={t}
            isExpanded={expandedId === t.id}
            onToggleExpand={() => setExpandedId(expandedId === t.id ? null : t.id)}
          />
        ))}
      </div>

      {/* RECENT HISTORY */}
      {closedTrades.length > closedToday.length && (
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
          <div style={{ color: "#aaa", fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>
            📜 Recent History ({closedTrades.length - closedToday.length} trades)
          </div>
          {closedTrades.filter(t => !closedToday.includes(t)).slice(0, 20).map(t => (
            <ScalperTradeCard
              key={t.id} t={t}
              isExpanded={expandedId === t.id}
              onToggleExpand={() => setExpandedId(expandedId === t.id ? null : t.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────── Capital Usage Panel ───────────
function CapitalUsagePanel({ usage, config, todayPnl, openLivePnl }) {
  if (!usage || !config) return null;
  const cap = usage.capital || 1000000;
  const committed = usage.committed || 0;
  const available = usage.available || cap;
  const committedPct = usage.committed_pct || 0;
  const liveValue = usage.live_value || 0;
  const unrealized = usage.unrealized_pnl || 0;
  const totalPnl = (usage.realized_today || 0) + unrealized;

  return (
    <div style={{
      background: CARD,
      border: `1px solid ${unrealized >= 0 ? GREEN + "44" : RED + "44"}`,
      borderRadius: 12,
      padding: "16px 20px",
    }}>
      <div style={{ color: "#aaa", fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 12 }}>
        💰 CAPITAL USAGE — Live
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
        <BigStat label="Total Capital" value={rupeesL(cap)} color="#fff" />
        <BigStat label="Committed" value={rupeesL(committed)} color={ORANGE} sub={`${committedPct.toFixed(1)}% used`} />
        <BigStat label="Available" value={rupeesL(available)} color={GREEN} />
        <BigStat label="Live Value (open)" value={rupeesL(liveValue)} color={ACCENT} />
        <BigStat label="Unrealized P&L" value={rupees(unrealized)} color={unrealized >= 0 ? GREEN : RED} sub={unrealized >= 0 ? "▲" : "▼"} />
        <BigStat label="Today Total P&L" value={rupees(totalPnl)} color={totalPnl >= 0 ? GREEN : RED} sub={`R: ${rupees(usage.realized_today || 0)} + U: ${rupees(unrealized)}`} />
      </div>

      {/* Capital usage bar */}
      <div style={{ marginTop: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#777", marginBottom: 4 }}>
          <span>Used: {rupeesL(committed)} ({committedPct.toFixed(1)}%)</span>
          <span>Available: {rupeesL(available)}</span>
        </div>
        <div style={{ height: 8, background: "#1a1a1a", borderRadius: 4, overflow: "hidden", display: "flex" }}>
          <div style={{
            width: `${Math.min(committedPct, 100)}%`,
            background: committedPct > 80 ? RED : committedPct > 50 ? ORANGE : ACCENT,
            transition: "width 0.3s",
          }} />
        </div>
        {/* Capital safety warnings */}
        {committedPct >= 95 && (
          <div style={{
            marginTop: 8, padding: 8, background: RED + "22", border: `1px solid ${RED}66`,
            borderRadius: 6, fontSize: 11, color: RED, fontWeight: 600,
          }}>
            🛑 CAPITAL FULL — No new trades will be placed until existing positions close
          </div>
        )}
        {committedPct >= 80 && committedPct < 95 && (
          <div style={{
            marginTop: 8, padding: 8, background: ORANGE + "22", border: `1px solid ${ORANGE}66`,
            borderRadius: 6, fontSize: 11, color: ORANGE, fontWeight: 600,
          }}>
            ⚠️ Capital {committedPct.toFixed(0)}% used · only {rupeesL(available)} left for new trades
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────── Trade Card with Live Tick + Expand + Manual Exit ───────────
function ScalperTradeCard({ t, livePrice, isExpanded, onToggleExpand, onManualExit }) {
  const isOpen = t.status === "OPEN";

  // Use live tick LTP if available (zero-latency), else DB current_ltp
  const cur = livePrice?.ltp || t.current_ltp || t.exit_price || t.entry_price;
  const livePnl = isOpen
    ? (livePrice?.pnl_rupees ?? (cur - t.entry_price) * (t.qty || 0))
    : (t.pnl_rupees || 0);
  const pnlPct = livePrice?.pnl_pct ?? (t.entry_price > 0 ? ((cur - t.entry_price) / t.entry_price) * 100 : 0);
  const pnlColor = livePnl >= 0 ? GREEN : RED;
  const isCE = (t.action || "").includes("CE");
  const actColor = isCE ? GREEN : RED;

  const statusColors = {
    OPEN: ACCENT, T1_HIT: GREEN, T2_HIT: GREEN, SL_HIT: RED,
    TRAIL_EXIT: GREEN, TIMEOUT_EXIT: "#888", MANUAL_EXIT: PURPLE,
    BREAKEVEN_EXIT: ACCENT,
  };
  const sc = statusColors[t.status] || "#666";

  const et = t.entry_time ? new Date(t.entry_time).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
  }) : "";
  const xt = t.exit_time ? new Date(t.exit_time).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
  }) : "";

  return (
    <div style={{
      background: BG, borderRadius: 8, padding: "10px 12px", marginBottom: 8,
      border: `1px solid ${sc}33`, cursor: "pointer",
    }}>
      <div onClick={onToggleExpand}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, flexWrap: "wrap", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{ color: "#fff", fontWeight: 800, fontSize: 13 }}>{t.idx}</span>
            <span style={{ background: actColor + "22", color: actColor, padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 800 }}>{t.action}</span>
            <span style={{ color: "#ccc", fontWeight: 700 }}>{t.strike}</span>
            <span style={{ background: sc + "22", color: sc, padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>
              {isOpen ? "● LIVE" : t.status}
            </span>
            {isOpen && livePrice && (
              <span style={{ color: GREEN, fontSize: 9, animation: "pulse 1s infinite" }}>● TICK</span>
            )}
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ color: pnlColor, fontSize: 16, fontWeight: 800 }}>{rupees(livePnl)}</div>
            <div style={{ color: pnlColor, fontSize: 10 }}>{pctFmt(pnlPct)}</div>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 6, fontSize: 10 }}>
          <Cell label="ENTRY" val={`₹${t.entry_price}`} color="#ccc" />
          <Cell
            label={isOpen ? "LIVE LTP" : "EXIT"}
            val={`₹${cur?.toFixed?.(2) || cur}`}
            color={isOpen ? ACCENT : sc}
            highlight={isOpen}
          />
          <Cell label="SL" val={`₹${t.sl_price}`} color={RED} />
          <Cell label="T1" val={`₹${t.t1_price}`} color={GREEN} />
          <Cell label="T2" val={`₹${t.t2_price}`} color={GREEN} />
          <Cell label="QTY" val={`${t.qty}`} color="#999" />
        </div>

        <div style={{ fontSize: 9, color: "#666", marginTop: 6 }}>
          ⏱ {et}{xt ? ` → ${xt}` : ""}
          {" · "}Capital: {rupees(t.capital_used || (t.entry_price * t.qty))}
          {" · "}Prob: {t.probability || 0}%
          {!isExpanded && <span style={{ color: ACCENT, marginLeft: 8 }}>↓ click to expand</span>}
        </div>
      </div>

      {/* EXPANDED VIEW */}
      {isExpanded && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${BORDER}` }}>
          {/* ENTRY LOGIC */}
          <Section title="🎯 ENTRY LOGIC — kyu liya">
            {t.entry_reasoning ? (
              <div style={{ color: "#ccc", fontSize: 11, lineHeight: 1.5 }}>
                <div style={{ marginBottom: 4 }}>
                  <strong style={{ color: ACCENT }}>Verdict:</strong> {t.entry_reasoning}
                </div>
                {(t.entry_bull_pct || t.entry_bear_pct) && (
                  <div style={{ display: "flex", gap: 12, fontSize: 10, color: "#888" }}>
                    <span>Bull: <b style={{ color: GREEN }}>{Math.round(t.entry_bull_pct || 0)}%</b></span>
                    <span>Bear: <b style={{ color: RED }}>{Math.round(t.entry_bear_pct || 0)}%</b></span>
                    <span>Spot @ entry: <b style={{ color: "#ccc" }}>{t.entry_spot || "—"}</b></span>
                  </div>
                )}
              </div>
            ) : (
              <div style={{ color: "#555", fontSize: 10 }}>Entry reasoning not captured (older trade)</div>
            )}
          </Section>

          {/* EXIT LOGIC */}
          {!isOpen && t.exit_reason && (
            <Section title="🚪 EXIT LOGIC — kyu nikla">
              <div style={{ color: "#ccc", fontSize: 11, lineHeight: 1.5 }}>{t.exit_reason}</div>
            </Section>
          )}

          {/* SMART SL LADDER (visible only for OPEN trades) */}
          {isOpen && (
            <Section title="🛡️ SMART SL LADDER">
              <SmartSLLadder
                tradeId={t.id}
                entry={t.entry_price}
                action={t.action}
                currentLtp={cur}
                entrySpot={t.entry_spot}
                currentSpot={null}
              />
            </Section>
          )}

          {/* TICK CHART */}
          <Section title="📈 LIVE TICK CHART">
            <TickChart tradeId={t.id} entry={t.entry_price} sl={t.sl_price} t1={t.t1_price} t2={t.t2_price} />
          </Section>

          {/* MANUAL EXIT BUTTON */}
          {isOpen && onManualExit && (
            <button onClick={(e) => { e.stopPropagation(); onManualExit(); }} style={{
              background: PURPLE, color: "#fff", border: "none",
              padding: "8px 16px", borderRadius: 6, fontSize: 12, fontWeight: 700,
              cursor: "pointer", marginTop: 10, width: "100%",
            }}>
              🚪 MANUAL EXIT @ ₹{cur?.toFixed?.(2)}  (P&L {rupees(livePnl)})
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────── Tick Chart (lightweight-charts) ───────────
function TickChart({ tradeId, entry, sl, t1, t2 }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const [ticks, setTicks] = useState([]);
  const disposedRef = useRef(false);

  // Fetch tick data
  useEffect(() => {
    let cancelled = false;
    const fetchTicks = async () => {
      const r = await safeFetch(`/api/scalper/trades/${tradeId}/ticks?limit=300`, null);
      if (!cancelled && r && Array.isArray(r.ticks)) setTicks(r.ticks);
    };
    fetchTicks();
    const iv = setInterval(fetchTicks, 2000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [tradeId]);

  // Init chart once
  useEffect(() => {
    if (!containerRef.current) return;
    disposedRef.current = false;

    const chart = createChart(containerRef.current, {
      layout: { background: { color: BG }, textColor: "#888", fontSize: 10 },
      grid: { vertLines: { color: "#1a1a22" }, horzLines: { color: "#1a1a22" } },
      timeScale: { timeVisible: true, secondsVisible: true, borderColor: BORDER },
      rightPriceScale: { borderColor: BORDER },
      width: containerRef.current.clientWidth,
      height: 200,
    });
    chartRef.current = chart;

    const series = chart.addSeries(LineSeries, {
      color: ACCENT, lineWidth: 2, priceLineVisible: true,
    });
    seriesRef.current = series;

    // Reference lines: entry, SL, T1, T2
    try {
      series.createPriceLine({ price: entry, color: "#888", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Entry" });
      if (sl) series.createPriceLine({ price: sl, color: RED, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "SL" });
      if (t1) series.createPriceLine({ price: t1, color: GREEN, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "T1" });
      if (t2) series.createPriceLine({ price: t2, color: GREEN, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "T2" });
    } catch {}

    const ro = new ResizeObserver(() => {
      if (disposedRef.current || !containerRef.current) return;
      try { chart.applyOptions({ width: containerRef.current.clientWidth }); } catch {}
    });
    ro.observe(containerRef.current);

    return () => {
      disposedRef.current = true;
      try { ro.disconnect(); } catch {}
      try { chart.remove(); } catch {}
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [entry, sl, t1, t2]);

  // Update data on tick change
  useEffect(() => {
    if (disposedRef.current || !seriesRef.current) return;
    if (!Array.isArray(ticks) || ticks.length === 0) return;
    const seen = new Set();
    const data = [];
    [...ticks].sort((a, b) => a.ts - b.ts).forEach(p => {
      const t = Math.floor(p.ts / 1000);
      if (!seen.has(t)) {
        seen.add(t);
        data.push({ time: t, value: p.ltp });
      }
    });
    try { seriesRef.current.setData(data); } catch {}
    try { chartRef.current?.timeScale().fitContent(); } catch {}
  }, [ticks]);

  if (ticks.length === 0) {
    return (
      <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 6, padding: 20, textAlign: "center", color: "#555", fontSize: 11 }}>
        No tick data yet. Ticks captured every cycle from entry.
      </div>
    );
  }

  return (
    <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 6, padding: 8 }}>
      <div ref={containerRef} style={{ width: "100%", height: 200 }} />
      <div style={{ fontSize: 9, color: "#666", marginTop: 4 }}>
        {ticks.length} ticks · {ticks.length > 1 ? `${Math.round((ticks[ticks.length-1].ts - ticks[0].ts) / 60000)}min duration` : "live"}
      </div>
    </div>
  );
}

// ─────────── helpers ───────────
function StatBox({ label, value, color = "#fff", sub }) {
  return (
    <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 8, padding: "10px 14px" }}>
      <div style={{ color: "#666", fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ color, fontSize: 17, fontWeight: 800, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ color: "#555", fontSize: 9, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}
function BigStat({ label, value, color = "#fff", sub }) {
  return (
    <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 8, padding: "12px 14px" }}>
      <div style={{ color: "#666", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ color, fontSize: 18, fontWeight: 800, marginTop: 4 }}>{value}</div>
      {sub && <div style={{ color: "#555", fontSize: 9, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}
function FieldInput({ label, value, onChange, hint }) {
  return (
    <div>
      <div style={{ color: "#888", fontSize: 10, fontWeight: 600, marginBottom: 4 }}>{label}</div>
      <input
        type="text" inputMode="decimal" value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: "100%", background: BG, border: `1px solid ${BORDER}`, color: "#fff",
          padding: "7px 10px", borderRadius: 6, fontSize: 13, fontWeight: 600, outline: "none", boxSizing: "border-box",
        }}
        placeholder={hint}
      />
      {hint && <div style={{ color: "#444", fontSize: 9, marginTop: 3 }}>{hint}</div>}
    </div>
  );
}
function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 10, color: "#888", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 6, padding: 10 }}>{children}</div>
    </div>
  );
}
function Cell({ label, val, color, highlight }) {
  return (
    <div style={{
      background: CARD, borderRadius: 4, padding: "4px 6px",
      ...(highlight ? { boxShadow: `0 0 4px ${color}66`, border: `1px solid ${color}` } : {}),
    }}>
      <div style={{ color: "#555", fontSize: 8, fontWeight: 700 }}>{label}</div>
      <div style={{ color, fontSize: 11, fontWeight: 700 }}>{val}</div>
    </div>
  );
}
