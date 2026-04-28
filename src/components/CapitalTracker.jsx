/**
 * CapitalTracker — Live capital state widget per system (SCALPER or MAIN).
 *
 * Shows:
 *   Base capital (target level)
 *   Current capital (live, can shrink with losses)
 *   Profit Bank (excess profits, never consumed by losses)
 *   Loss Recovered (profits that went to repair capital)
 *   History feed
 *   Withdraw button
 */

import React, { useEffect, useState } from "react";

const GREEN = "#30D158";
const RED = "#FF453A";
const ORANGE = "#FF9F0A";
const ACCENT = "#0A84FF";
const PURPLE = "#BF5AF2";
const YELLOW = "#FFD60A";
const GRAY = "#6b7280";
const BG = "#0A0A0F";
const CARD = "#111118";
const BORDER = "#1E1E2E";

const fmtR = (n) => `₹${Math.round(n || 0).toLocaleString("en-IN")}`;
const fmtL = (n) => {
  const x = Math.abs(n || 0);
  if (x >= 10000000) return `₹${(n / 10000000).toFixed(2)}Cr`;
  if (x >= 100000) return `₹${(n / 100000).toFixed(2)}L`;
  if (x >= 1000) return `₹${(n / 1000).toFixed(1)}k`;
  return fmtR(n);
};

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

export default function CapitalTracker({ system = "SCALPER" }) {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [newBase, setNewBase] = useState("");
  const [showHistory, setShowHistory] = useState(false);

  const load = async () => {
    const r = await safeFetch(`/api/capital/${system}`, null);
    if (r && !r.error) setData(r);
  };

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, [system]);

  const handleWithdraw = async (amount = null) => {
    if (!window.confirm(amount ? `Withdraw ₹${Math.round(amount).toLocaleString("en-IN")} from Profit Bank?` : "Withdraw entire Profit Bank?")) return;
    await postJSON(`/api/capital/${system}/withdraw`, amount ? { amount } : {});
    await load();
  };

  const saveBase = async () => {
    const v = parseFloat(newBase);
    if (!v || v <= 0) return alert("Invalid amount");
    await postJSON(`/api/capital/${system}/base`, { base_capital: v });
    setEditing(false);
    setNewBase("");
    await load();
  };

  if (!data) {
    return <div style={wrap}><div style={{ color: GRAY, fontSize: 11 }}>Loading capital tracker…</div></div>;
  }

  const base = data.base_capital || 0;
  const current = data.current_capital || 0;
  const bank = data.profit_bank || 0;
  const loss_rec = data.loss_recovered || 0;
  const withdrawn = data.total_withdrawn || 0;
  const repairNeeded = data.repair_needed || 0;
  const deficit = data.deficit_pct || 0;
  const growth = data.growth_pct || 0;
  const isBelow = data.below_base;

  const stateColor = isBelow ? RED : current === base ? ACCENT : GREEN;

  return (
    <div style={wrap}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontSize: 11, color: GRAY, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
            💰 Capital Tracker · {system}
          </div>
          <div style={{ fontSize: 10, color: "#555", marginTop: 2 }}>
            Auto-adjusts on profit/loss · Independent system
          </div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button onClick={() => setShowHistory(!showHistory)} style={btnSecondary}>
            {showHistory ? "Hide History" : "📜 History"}
          </button>
          <button onClick={() => setEditing(!editing)} style={btnSecondary}>
            ⚙️ Base
          </button>
        </div>
      </div>

      {/* Base capital edit */}
      {editing && (
        <div style={{ background: BG, padding: 10, borderRadius: 6, marginBottom: 10, border: `1px solid ${BORDER}` }}>
          <div style={{ fontSize: 10, color: GRAY, marginBottom: 4 }}>Set new base capital target:</div>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              type="text"
              inputMode="decimal"
              value={newBase}
              onChange={(e) => setNewBase(e.target.value)}
              placeholder={`Current: ${fmtR(base)}`}
              style={{
                flex: 1, background: "#000", border: `1px solid ${BORDER}`,
                color: "#fff", padding: "6px 10px", borderRadius: 4, fontSize: 12,
              }}
            />
            <button onClick={saveBase} style={btnPrimary}>Save</button>
          </div>
        </div>
      )}

      {/* Main capital row */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 10 }}>
        <Stat label="BASE (Target)" value={fmtL(base)} color="#fff" />
        <Stat
          label="RUNNING (Live)"
          value={fmtL(current)}
          color={stateColor}
          sub={isBelow ? `▼ ${deficit.toFixed(2)}% below` : current > base ? `▲ at base` : "✓ at base"}
        />
        <Stat
          label="PROFIT BANK"
          value={fmtL(bank)}
          color={GREEN}
          sub={withdrawn > 0 ? `Withdrawn: ${fmtL(withdrawn)}` : "Untouched by losses"}
        />
      </div>

      {/* Capital health bar */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: GRAY, marginBottom: 4 }}>
          <span>Health: {((current / base) * 100).toFixed(1)}%</span>
          {isBelow && <span style={{ color: RED }}>Repair needed: {fmtL(repairNeeded)}</span>}
        </div>
        <div style={{ height: 8, background: "#1a1a1a", borderRadius: 4, overflow: "hidden", position: "relative" }}>
          <div style={{
            width: `${Math.min((current / base) * 100, 100)}%`,
            height: "100%",
            background: isBelow ? RED : current >= base ? GREEN : ORANGE,
            transition: "width 0.4s",
          }} />
          {/* Base marker line */}
          <div style={{
            position: "absolute", top: 0, bottom: 0, left: "100%",
            borderLeft: `2px solid ${ACCENT}`,
          }} />
        </div>
      </div>

      {/* Loss recovered + Withdraw */}
      {(loss_rec > 0 || bank > 0) && (
        <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
          {loss_rec > 0 && (
            <div style={{
              flex: 1, background: BG, border: `1px solid ${ORANGE}33`, borderRadius: 6, padding: "8px 10px",
            }}>
              <div style={{ fontSize: 9, color: ORANGE, fontWeight: 700 }}>📉 LOSS RECOVERED</div>
              <div style={{ fontSize: 13, color: ORANGE, fontWeight: 700, marginTop: 2 }}>{fmtL(loss_rec)}</div>
              <div style={{ fontSize: 9, color: GRAY, marginTop: 2 }}>Profit deducted to fix capital</div>
            </div>
          )}
          {bank > 0 && (
            <div style={{
              flex: 1, background: GREEN + "11", border: `1px solid ${GREEN}33`, borderRadius: 6, padding: "8px 10px",
            }}>
              <div style={{ fontSize: 9, color: GREEN, fontWeight: 700 }}>💸 PROFIT BANK</div>
              <div style={{ fontSize: 13, color: GREEN, fontWeight: 700, marginTop: 2 }}>{fmtL(bank)}</div>
              <button onClick={() => handleWithdraw(null)} style={{
                background: GREEN, color: "#000", border: "none",
                padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                cursor: "pointer", marginTop: 4,
              }}>
                💸 Withdraw All
              </button>
            </div>
          )}
        </div>
      )}

      {/* History */}
      {showHistory && data.history && data.history.length > 0 && (
        <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 6, padding: 10, marginTop: 10, maxHeight: 280, overflowY: "auto" }}>
          <div style={{ fontSize: 10, color: GRAY, fontWeight: 700, marginBottom: 6 }}>
            📜 RECENT ADJUSTMENTS
          </div>
          {data.history.slice(0, 20).map((h, i) => {
            const c = h.event_type === "PROFIT_BANK" ? GREEN
                   : h.event_type === "PROFIT_REPAIR" ? ORANGE
                   : h.event_type === "LOSS" ? RED
                   : h.event_type === "WITHDRAW" ? PURPLE
                   : GRAY;
            const ts = h.ts ? new Date(h.ts).toLocaleString("en-IN", {
              timeZone: "Asia/Kolkata", day: "2-digit", month: "short",
              hour: "2-digit", minute: "2-digit", hour12: true,
            }) : "";
            return (
              <div key={i} style={{
                fontSize: 10, padding: "4px 0", borderBottom: `1px solid ${BORDER}33`,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: c, fontWeight: 700 }}>{h.event_type}</span>
                  <span style={{ color: GRAY, fontSize: 9 }}>{ts}</span>
                </div>
                <div style={{ color: "#ccc", marginTop: 2 }}>
                  {h.amount >= 0 ? "+" : ""}{fmtR(h.amount)}
                  {h.capital_after !== undefined && ` · Capital: ${fmtR(h.capital_after)}`}
                  {h.profit_bank_after !== undefined && h.profit_bank_after !== h.profit_bank_before && ` · Bank: ${fmtR(h.profit_bank_after)}`}
                </div>
                {h.description && (
                  <div style={{ color: "#888", fontSize: 9, marginTop: 1 }}>{h.description}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color = "#fff", sub }) {
  return (
    <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 6, padding: "8px 10px" }}>
      <div style={{ fontSize: 9, color: GRAY, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 16, color, fontWeight: 800, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 9, color: GRAY, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

const wrap = {
  background: CARD,
  border: `1px solid ${BORDER}`,
  borderRadius: 10,
  padding: 14,
  marginBottom: 12,
};

const btnSecondary = {
  background: "transparent",
  color: GRAY,
  border: `1px solid ${BORDER}`,
  padding: "5px 10px",
  borderRadius: 4,
  fontSize: 10,
  fontWeight: 700,
  cursor: "pointer",
};

const btnPrimary = {
  background: ACCENT,
  color: "#fff",
  border: "none",
  padding: "6px 14px",
  borderRadius: 4,
  fontSize: 11,
  fontWeight: 700,
  cursor: "pointer",
};
