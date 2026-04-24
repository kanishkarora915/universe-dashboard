/**
 * ScalperTab — Dedicated, self-contained scalper trading dashboard.
 *
 * Fully autonomous:
 *  - User sets capital + qty per trade
 *  - Engine picks trades itself (enable/disable toggle)
 *  - Real SL/T1/T2 calculated from % of entry price
 *  - Own P&L, own stats, own open/closed lists
 *  - Zero overlap with main verdict system
 */

import { useState, useEffect, useCallback } from "react";

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
const pct = (n, d = 2) => `${(n || 0) >= 0 ? "+" : ""}${(n || 0).toFixed(d)}%`;

async function safeFetch(url, fallback) {
  try { const r = await fetch(url); if (!r.ok) return fallback; return await r.json(); } catch { return fallback; }
}
async function postJSON(url, body) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

export default function ScalperTab() {
  const [status, setStatus] = useState(null);
  const [config, setConfig] = useState(null);
  const [stats, setStats] = useState(null);
  const [openTrades, setOpenTrades] = useState([]);
  const [closedTrades, setClosedTrades] = useState([]);
  const [saving, setSaving] = useState(false);

  // form state
  const [capital, setCapital] = useState("");
  const [niftyQty, setNiftyQty] = useState("");
  const [bnQty, setBnQty] = useState("");
  const [slPct, setSlPct] = useState("");
  const [t1Pct, setT1Pct] = useState("");
  const [t2Pct, setT2Pct] = useState("");
  const [threshold, setThreshold] = useState("");
  const [dailyCap, setDailyCap] = useState("");

  const load = useCallback(async () => {
    const [st, cf, sStats, op, cl] = await Promise.all([
      safeFetch("/api/scalper/status", null),
      safeFetch("/api/scalper/config", null),
      safeFetch("/api/scalper/stats", null),
      safeFetch("/api/scalper/trades/open", []),
      safeFetch("/api/scalper/trades/closed?days=30", []),
    ]);
    setStatus(st);
    setStats(sStats);
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
  }, [capital, niftyQty, bnQty, slPct, t1Pct, t2Pct, threshold, dailyCap]);

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const saveConfig = async () => {
    setSaving(true);
    const body = {
      capital: parseFloat(capital) || 1000000,
      nifty_qty: parseInt(niftyQty, 10) || 0,
      banknifty_qty: parseInt(bnQty, 10) || 0,
      sl_pct: (parseFloat(slPct) || 12) / 100,
      t1_pct: (parseFloat(t1Pct) || 20) / 100,
      t2_pct: (parseFloat(t2Pct) || 40) / 100,
      threshold: parseInt(threshold, 10) || 55,
      daily_cap: parseInt(dailyCap, 10) || 15,
    };
    await postJSON("/api/scalper/config", body);
    await load();
    setSaving(false);
  };

  const toggleScalper = async () => {
    const endpoint = status?.enabled ? "/api/scalper/disable" : "/api/scalper/enable";
    await postJSON(endpoint, {});
    await load();
  };

  // Stats
  const closedToday = closedTrades.filter(t => {
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
    return (t.entry_time || "").startsWith(today);
  });
  const todayPnl = closedToday.reduce((s, t) => s + (t.pnl_rupees || 0), 0);
  const todayWins = closedToday.filter(t => t.status === "T1_HIT" || t.status === "T2_HIT").length;
  const todayLosses = closedToday.filter(t => t.status === "SL_HIT").length;
  const openPnl = openTrades.reduce((s, t) => {
    const cur = t.current_ltp || t.entry_price;
    return s + ((cur - t.entry_price) * (t.qty || 0));
  }, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header Card */}
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div>
            <div style={{ color: ORANGE, fontSize: 15, fontWeight: 900 }}>⚡ SCALPER MODE</div>
            <div style={{ color: "#777", fontSize: 11, marginTop: 2 }}>
              Autonomous scalper — own capital, own qty, own P&L. Winner banega, losses khud handle karega.
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{
              background: status?.enabled ? GREEN + "22" : "#333",
              color: status?.enabled ? GREEN : "#888",
              padding: "4px 12px",
              borderRadius: 20,
              fontSize: 11,
              fontWeight: 700,
              border: `1px solid ${status?.enabled ? GREEN : BORDER}`,
            }}>
              {status?.enabled ? "● LIVE" : "○ OFF"}
            </div>
            <button onClick={toggleScalper} style={{
              background: status?.enabled ? RED : GREEN,
              color: "#fff",
              border: "none",
              padding: "6px 16px",
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
            }}>
              {status?.enabled ? "STOP SCALPER" : "START SCALPER"}
            </button>
          </div>
        </div>
      </div>

      {/* Live Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
        <StatBox label="Today's P&L" value={rupees(todayPnl)} color={todayPnl >= 0 ? GREEN : RED} sub={`${closedToday.length} closed`} />
        <StatBox label="Open P&L" value={rupees(openPnl)} color={openPnl >= 0 ? GREEN : RED} sub={`${openTrades.length} open`} />
        <StatBox label="Wins" value={todayWins} color={GREEN} />
        <StatBox label="Losses" value={todayLosses} color={RED} />
        <StatBox
          label="Today Win %"
          value={`${todayWins + todayLosses > 0 ? Math.round((todayWins / (todayWins + todayLosses)) * 100) : 0}%`}
          color={todayWins > todayLosses ? GREEN : todayLosses > todayWins ? RED : "#888"}
        />
        <StatBox
          label="Trades Left Today"
          value={`${(config?.daily_cap || 15) - closedToday.length - openTrades.length}`}
          color={YELLOW}
          sub={`cap ${config?.daily_cap || 15}`}
        />
      </div>

      {/* Config Card */}
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ color: "#777", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 12 }}>
          YOUR CONFIG — Capital & Quantity
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10, marginBottom: 12 }}>
          <FieldInput label="Capital (₹)" value={capital} onChange={setCapital} hint="e.g. 1000000" />
          <FieldInput label="NIFTY qty/trade" value={niftyQty} onChange={setNiftyQty} hint="0 = auto from capital" />
          <FieldInput label="BANKNIFTY qty/trade" value={bnQty} onChange={setBnQty} hint="0 = auto from capital" />
        </div>

        <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8, marginTop: 12 }}>
          Targets & Risk
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 10, marginBottom: 12 }}>
          <FieldInput label="Stop-Loss %" value={slPct} onChange={setSlPct} hint="default 12" />
          <FieldInput label="Target 1 %" value={t1Pct} onChange={setT1Pct} hint="default 20" />
          <FieldInput label="Target 2 %" value={t2Pct} onChange={setT2Pct} hint="default 40" />
          <FieldInput label="Min Win Prob %" value={threshold} onChange={setThreshold} hint="default 55" />
          <FieldInput label="Daily Cap" value={dailyCap} onChange={setDailyCap} hint="default 15" />
        </div>

        <button onClick={saveConfig} disabled={saving} style={{
          background: saving ? "#333" : ACCENT,
          color: "#fff",
          border: "none",
          padding: "8px 20px",
          borderRadius: 6,
          fontSize: 12,
          fontWeight: 700,
          cursor: saving ? "wait" : "pointer",
        }}>
          {saving ? "Saving…" : "Save Config"}
        </button>

        {config && config.capital && (
          <div style={{ marginTop: 12, padding: 10, background: BG, borderRadius: 6, fontSize: 11, color: "#888" }}>
            <span style={{ color: "#aaa" }}>Active: </span>
            Capital {rupees(config.capital)} · NIFTY qty {config.nifty_qty || "auto"} · BN qty {config.banknifty_qty || "auto"} ·
            SL {(config.sl_pct * 100).toFixed(1)}% · T1 {(config.t1_pct * 100).toFixed(1)}% · T2 {(config.t2_pct * 100).toFixed(1)}%
          </div>
        )}
      </div>

      {/* Open Trades */}
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ color: "#777", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>
          OPEN TRADES ({openTrades.length})
        </div>
        {openTrades.length === 0 && (
          <div style={{ color: "#555", textAlign: "center", padding: 20, fontSize: 12 }}>No open scalper trades.</div>
        )}
        {openTrades.map(t => <ScalperTradeCard key={t.id} t={t} />)}
      </div>

      {/* Today's Closed */}
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ color: "#777", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>
          TODAY'S CLOSED ({closedToday.length})
        </div>
        {closedToday.length === 0 && (
          <div style={{ color: "#555", textAlign: "center", padding: 20, fontSize: 12 }}>No closed trades today.</div>
        )}
        {closedToday.map(t => <ScalperTradeCard key={t.id} t={t} />)}
      </div>

      {/* Recent History */}
      {closedTrades.length > closedToday.length && (
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
          <div style={{ color: "#777", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>
            RECENT HISTORY ({closedTrades.length - closedToday.length} trades)
          </div>
          {closedTrades.filter(t => !closedToday.includes(t)).slice(0, 30).map(t => (
            <ScalperTradeCard key={t.id} t={t} />
          ))}
        </div>
      )}
    </div>
  );
}

// ───── helper components ─────

function StatBox({ label, value, color = "#fff", sub }) {
  return (
    <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 8, padding: "10px 14px" }}>
      <div style={{ color: "#666", fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ color, fontSize: 18, fontWeight: 800, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ color: "#555", fontSize: 9, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function FieldInput({ label, value, onChange, hint }) {
  return (
    <div>
      <div style={{ color: "#888", fontSize: 10, fontWeight: 600, marginBottom: 4 }}>{label}</div>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: "100%",
          background: BG,
          border: `1px solid ${BORDER}`,
          color: "#fff",
          padding: "7px 10px",
          borderRadius: 6,
          fontSize: 13,
          fontWeight: 600,
          outline: "none",
          boxSizing: "border-box",
        }}
        placeholder={hint}
      />
      {hint && <div style={{ color: "#444", fontSize: 9, marginTop: 3 }}>{hint}</div>}
    </div>
  );
}

function ScalperTradeCard({ t }) {
  const isOpen = t.status === "OPEN";
  const cur = t.current_ltp || t.exit_price || t.entry_price;
  const livePnl = isOpen ? (cur - t.entry_price) * (t.qty || 0) : (t.pnl_rupees || 0);
  const pnlPct = t.entry_price > 0 ? ((cur - t.entry_price) / t.entry_price) * 100 : 0;
  const pnlColor = livePnl >= 0 ? GREEN : RED;
  const isCE = (t.action || "").includes("CE");
  const actColor = isCE ? GREEN : RED;

  const statusColors = {
    OPEN: ACCENT, T1_HIT: GREEN, T2_HIT: GREEN, SL_HIT: RED,
    TRAIL_EXIT: GREEN, TIME_EXIT: "#888", BREAKEVEN_EXIT: ACCENT,
  };
  const sc = statusColors[t.status] || "#666";

  const et = t.entry_time ? new Date(t.entry_time).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true, day: "2-digit", month: "short",
  }) : "";
  const xt = t.exit_time ? new Date(t.exit_time).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: true,
  }) : "";

  return (
    <div style={{ background: BG, borderRadius: 8, padding: "10px 12px", marginBottom: 6, border: `1px solid ${sc}33` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, flexWrap: "wrap", gap: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <span style={{ color: "#fff", fontWeight: 800, fontSize: 13 }}>{t.idx}</span>
          <span style={{ background: actColor + "22", color: actColor, padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 800 }}>{t.action}</span>
          <span style={{ color: "#ccc", fontWeight: 700 }}>{t.strike}</span>
          <span style={{ background: sc + "22", color: sc, padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>
            {t.status === "OPEN" ? "● LIVE" : t.status}
          </span>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: pnlColor, fontSize: 15, fontWeight: 800 }}>{rupees(livePnl)}</div>
          <div style={{ color: pnlColor, fontSize: 10 }}>{pct(pnlPct)}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 6, fontSize: 10 }}>
        <Cell label="ENTRY" val={`₹${t.entry_price}`} color="#ccc" />
        <Cell label={isOpen ? "CURRENT" : "EXIT"} val={`₹${(isOpen ? t.current_ltp : t.exit_price)?.toFixed?.(1) || 0}`} color={isOpen ? ACCENT : sc} />
        <Cell label="SL" val={`₹${t.sl_price}`} color={RED} />
        <Cell label="T1" val={`₹${t.t1_price}`} color={GREEN} />
        <Cell label="T2" val={`₹${t.t2_price}`} color={GREEN} />
        <Cell label="QTY" val={`${t.qty} (${t.lots}L)`} color="#999" />
      </div>

      <div style={{ fontSize: 9, color: "#555", marginTop: 6 }}>
        Entry: {et}{xt ? `  →  Exit: ${xt}` : ""}
        {t.exit_reason ? ` · ${t.exit_reason}` : ""}
        {" · "}Prob: {t.probability || 0}%
      </div>
    </div>
  );
}

function Cell({ label, val, color }) {
  return (
    <div style={{ background: CARD, borderRadius: 4, padding: "4px 6px" }}>
      <div style={{ color: "#555", fontSize: 8, fontWeight: 700 }}>{label}</div>
      <div style={{ color, fontSize: 11, fontWeight: 700 }}>{val}</div>
    </div>
  );
}
