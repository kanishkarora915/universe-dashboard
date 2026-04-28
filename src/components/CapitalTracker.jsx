/**
 * CapitalTracker — Professional account summary widget per system.
 *
 * Industry-standard accounting view (like Zerodha Console / Upstox Pro):
 *   - Net Capital (current + unrealized)
 *   - Realized P&L (closed trades)
 *   - Unrealized P&L (open trades)
 *   - Day / Week / Month performance
 *   - Win rate, drawdown, returns
 *   - Best/worst trade
 *
 * No "profit bank" gimmicks — clean financial reporting.
 * No withdraw button — just real account state.
 */

import React, { useEffect, useState } from "react";

const GREEN = "#26a69a";
const RED = "#ef5350";
const BLUE = "#2962ff";
const GRAY = "#6b7280";
const FG = "#d4d4d8";
const FG_DIM = "#71717a";
const BG = "#0a0a0a";
const CARD = "#0f0f10";
const BORDER = "#1f1f24";
const BORDER_LIGHT = "#27272a";

const fmtR = (n) => `₹${Math.round(n || 0).toLocaleString("en-IN")}`;
const fmtSign = (n) => `${(n || 0) >= 0 ? "+" : ""}${fmtR(n)}`;
const fmtPct = (n) => `${(n || 0) >= 0 ? "+" : ""}${(n || 0).toFixed(2)}%`;
const fmtL = (n) => {
  const x = Math.abs(n || 0);
  if (x >= 10000000) return `${n >= 0 ? "" : "-"}₹${(Math.abs(n) / 10000000).toFixed(2)}Cr`;
  if (x >= 100000) return `${n >= 0 ? "" : "-"}₹${(Math.abs(n) / 100000).toFixed(2)}L`;
  if (x >= 1000) return `${n >= 0 ? "" : "-"}₹${(Math.abs(n) / 1000).toFixed(1)}k`;
  return fmtR(n);
};

async function safeFetch(url, fb) {
  try { const r = await fetch(url); if (!r.ok) return fb; return await r.json(); } catch { return fb; }
}
async function postJSON(url, body = {}) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return await r.json();
  } catch { return null; }
}

export default function CapitalTracker({ system = "SCALPER" }) {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [newBase, setNewBase] = useState("");

  const load = async () => {
    const r = await safeFetch(`/api/capital/${system}/account`, null);
    if (r && !r.error) setData(r);
  };

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, [system]);

  const saveBase = async () => {
    const v = parseFloat(newBase);
    if (!v || v <= 0) return;
    await postJSON(`/api/capital/${system}/base`, { base_capital: v });
    setEditing(false);
    setNewBase("");
    await load();
  };

  if (!data) {
    return <div style={wrap}><div style={{ color: FG_DIM, fontSize: 12 }}>Loading account...</div></div>;
  }

  const {
    base_capital, current_capital, net_capital,
    realized_pnl_total, unrealized_pnl,
    day_pnl, week_pnl, month_pnl,
    returns_pct, day_pct, week_pct, month_pct,
    total_trades, wins, losses, open_count,
    win_rate, max_drawdown, drawdown_pct,
    best_trade, worst_trade, avg_win, avg_loss,
  } = data;

  const totalPnl = realized_pnl_total + unrealized_pnl;
  const isProfit = totalPnl >= 0;

  return (
    <div style={wrap}>
      {/* Header — minimal, Bloomberg-ish */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline",
        paddingBottom: 12, borderBottom: `1px solid ${BORDER_LIGHT}`, marginBottom: 14,
      }}>
        <div>
          <div style={{ fontSize: 10, color: FG_DIM, fontWeight: 600, letterSpacing: 1, textTransform: "uppercase" }}>
            ACCOUNT · {system}
          </div>
          <div style={{ fontSize: 11, color: FG_DIM, marginTop: 2 }}>
            Real-time accounting · Independent system
          </div>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          <button onClick={() => setEditing(!editing)} style={btnGhost}>
            {editing ? "Cancel" : "Edit Base"}
          </button>
          <button onClick={async () => {
            if (!window.confirm(`Re-sync account from trade history?\n\nThis recalculates Realized P&L from all closed trades.`)) return;
            const r = await postJSON(`/api/capital/${system}/backfill`, {});
            if (r?.ok) await load();
          }} style={btnGhost}>
            Re-sync
          </button>
        </div>
      </div>

      {/* Edit base inline */}
      {editing && (
        <div style={{ background: BG, padding: 10, borderRadius: 4, marginBottom: 12, border: `1px solid ${BORDER}` }}>
          <div style={{ fontSize: 10, color: FG_DIM, marginBottom: 4 }}>Base capital (₹):</div>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              type="text" inputMode="decimal" value={newBase}
              onChange={(e) => setNewBase(e.target.value)}
              placeholder={String(base_capital)}
              style={inputStyle}
            />
            <button onClick={saveBase} style={btnPrimary}>Save</button>
          </div>
        </div>
      )}

      {/* TOP ROW — Hero P&L + Net Capital */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={labelStyle}>NET P&L (REALIZED + UNREALIZED)</div>
          <div style={{
            fontSize: 32, fontWeight: 700, color: isProfit ? GREEN : RED,
            fontFeatureSettings: "'tnum'", lineHeight: 1.1, marginTop: 4,
          }}>
            {fmtSign(totalPnl)}
          </div>
          <div style={{ fontSize: 12, color: isProfit ? GREEN : RED, marginTop: 4, fontWeight: 500 }}>
            {fmtPct(returns_pct)} on capital
          </div>
        </div>
        <div style={{ borderLeft: `1px solid ${BORDER_LIGHT}`, paddingLeft: 14 }}>
          <div style={labelStyle}>NET CAPITAL</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: FG, fontFeatureSettings: "'tnum'", marginTop: 4 }}>
            {fmtL(net_capital)}
          </div>
          <div style={{ fontSize: 11, color: FG_DIM, marginTop: 4 }}>
            Base: {fmtL(base_capital)}
          </div>
        </div>
      </div>

      {/* MIDDLE ROW — Realized + Unrealized split */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14 }}>
        <Cell label="REALIZED P&L" value={fmtSign(realized_pnl_total)} color={realized_pnl_total >= 0 ? GREEN : RED} />
        <Cell label={`UNREALIZED (${open_count} open)`} value={fmtSign(unrealized_pnl)} color={unrealized_pnl >= 0 ? GREEN : RED} />
      </div>

      {/* PERFORMANCE — Day / Week / Month */}
      <div style={{
        background: BG, border: `1px solid ${BORDER}`, borderRadius: 4,
        padding: 12, marginBottom: 14,
      }}>
        <div style={{ ...labelStyle, marginBottom: 10 }}>PERFORMANCE</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
          <Period label="Day" pnl={day_pnl} pct={day_pct} />
          <Period label="Week (7d)" pnl={week_pnl} pct={week_pct} />
          <Period label="Month (30d)" pnl={month_pnl} pct={month_pct} />
        </div>
      </div>

      {/* TRADE STATS */}
      <div style={{
        background: BG, border: `1px solid ${BORDER}`, borderRadius: 4,
        padding: 12, marginBottom: 14,
      }}>
        <div style={{ ...labelStyle, marginBottom: 10 }}>TRADE STATISTICS</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10 }}>
          <Mini label="Total" value={total_trades} />
          <Mini label="Wins" value={wins} color={GREEN} />
          <Mini label="Losses" value={losses} color={RED} />
          <Mini label="Win Rate" value={`${win_rate.toFixed(1)}%`} color={win_rate >= 60 ? GREEN : win_rate >= 40 ? FG : RED} />
        </div>
      </div>

      {/* RISK METRICS */}
      <div style={{
        background: BG, border: `1px solid ${BORDER}`, borderRadius: 4,
        padding: 12, marginBottom: 0,
      }}>
        <div style={{ ...labelStyle, marginBottom: 10 }}>RISK & RETURNS</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Mini label="Max Drawdown" value={fmtL(-max_drawdown)} sub={`-${drawdown_pct.toFixed(2)}%`} color={RED} />
          <Mini label="Best Trade" value={fmtSign(best_trade)} color={GREEN} />
          <Mini label="Avg Win" value={fmtSign(avg_win)} color={GREEN} />
          <Mini label="Avg Loss" value={fmtSign(avg_loss)} color={RED} />
        </div>
        {avg_win > 0 && avg_loss < 0 && (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${BORDER}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: FG_DIM }}>
              <span>Risk:Reward Ratio</span>
              <span style={{ color: avg_win / Math.abs(avg_loss) >= 1.5 ? GREEN : avg_win / Math.abs(avg_loss) >= 1 ? FG : RED, fontWeight: 600 }}>
                1 : {(avg_win / Math.abs(avg_loss)).toFixed(2)}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ──────────── Sub-components ────────────

function Cell({ label, value, color }) {
  return (
    <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 4, padding: "10px 12px" }}>
      <div style={labelStyle}>{label}</div>
      <div style={{
        fontSize: 16, fontWeight: 700, color, marginTop: 4,
        fontFeatureSettings: "'tnum'",
      }}>{value}</div>
    </div>
  );
}

function Period({ label, pnl, pct }) {
  const isPos = (pnl || 0) >= 0;
  return (
    <div>
      <div style={{ fontSize: 10, color: FG_DIM, fontWeight: 500 }}>{label}</div>
      <div style={{
        fontSize: 15, fontWeight: 700, color: isPos ? GREEN : RED,
        marginTop: 4, fontFeatureSettings: "'tnum'",
      }}>
        {fmtSign(pnl)}
      </div>
      <div style={{ fontSize: 10, color: isPos ? GREEN : RED, marginTop: 2 }}>
        {fmtPct(pct)}
      </div>
    </div>
  );
}

function Mini({ label, value, sub, color = FG }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: FG_DIM, fontWeight: 500 }}>{label}</div>
      <div style={{
        fontSize: 14, fontWeight: 700, color, marginTop: 3,
        fontFeatureSettings: "'tnum'",
      }}>{value}</div>
      {sub && <div style={{ fontSize: 9, color: FG_DIM, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

const labelStyle = {
  fontSize: 9,
  color: FG_DIM,
  fontWeight: 600,
  letterSpacing: 0.5,
  textTransform: "uppercase",
};

const wrap = {
  background: CARD,
  border: `1px solid ${BORDER}`,
  borderRadius: 6,
  padding: 16,
  marginBottom: 12,
  fontFamily: "-apple-system, 'Segoe UI', system-ui, sans-serif",
};

const btnGhost = {
  background: "transparent",
  color: FG_DIM,
  border: `1px solid ${BORDER_LIGHT}`,
  padding: "5px 10px",
  borderRadius: 3,
  fontSize: 10,
  fontWeight: 500,
  cursor: "pointer",
  letterSpacing: 0.3,
};

const btnPrimary = {
  background: BLUE,
  color: "#fff",
  border: "none",
  padding: "6px 14px",
  borderRadius: 3,
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
};

const inputStyle = {
  flex: 1,
  background: "#000",
  border: `1px solid ${BORDER_LIGHT}`,
  color: FG,
  padding: "6px 10px",
  borderRadius: 3,
  fontSize: 12,
  fontFamily: "monospace",
  outline: "none",
};
