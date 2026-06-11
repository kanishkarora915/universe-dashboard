import React, { useState, useEffect } from "react";
import { useTheme } from "./ThemeContext";

const fmt = (n) => {
  if (n === null || n === undefined) return "₹0";
  const sign = n > 0 ? "+" : "";
  return `${sign}₹${Math.round(n).toLocaleString("en-IN")}`;
};

const fmtPct = (n) => {
  if (n === null || n === undefined) return "0%";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
};

async function fetchDailyReport(date) {
  const url = date
    ? `/api/admin/daily-report?date=${date}`
    : "/api/admin/daily-report";
  const res = await fetch(url);
  return await res.json();
}

async function fetchRecentDays(days = 7) {
  const res = await fetch(`/api/admin/recent-days-report?days=${days}`);
  return await res.json();
}

function Card({ title, children, color }) {
  const theme = useTheme();
  return (
    <div style={{
      background: theme.SURFACE,
      border: `1px solid ${color || theme.BORDER}`,
      borderRadius: 8,
      padding: 16,
      marginBottom: 12,
    }}>
      <div style={{
        color: theme.MUTED, fontSize: 12, fontWeight: 600,
        marginBottom: 8, textTransform: "uppercase", letterSpacing: 0.5,
      }}>{title}</div>
      {children}
    </div>
  );
}

function StatBox({ label, value, color, sublabel }) {
  const theme = useTheme();
  return (
    <div style={{
      background: theme.SURFACE_2 || theme.SURFACE,
      borderRadius: 6,
      padding: 12,
      flex: 1,
      minWidth: 140,
    }}>
      <div style={{ color: theme.MUTED, fontSize: 11, marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ color: color || theme.TEXT, fontSize: 22, fontWeight: 700 }}>
        {value}
      </div>
      {sublabel && (
        <div style={{ color: theme.MUTED, fontSize: 11, marginTop: 4 }}>
          {sublabel}
        </div>
      )}
    </div>
  );
}

export default function DailyReportTab() {
  const theme = useTheme();
  const today = new Date().toISOString().split("T")[0];
  const [date, setDate] = useState(today);
  const [report, setReport] = useState(null);
  const [recentDays, setRecentDays] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadData = async (d) => {
    setLoading(true);
    try {
      const [r, rd] = await Promise.all([
        fetchDailyReport(d),
        fetchRecentDays(14),
      ]);
      setReport(r);
      setRecentDays(rd);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(date); }, [date]);

  if (loading && !report) {
    return <div style={{ padding: 24, color: theme.MUTED }}>Loading...</div>;
  }
  if (!report) return null;

  const s = report.scalper || {};
  const m = report.main || {};
  const c = report.combined || {};
  const totalPnl = c.total_pnl || 0;
  const isProfit = totalPnl > 0;
  const tabsByStatus = (data) => {
    const t = data.trades_by_status || {};
    return Object.entries(t)
      .sort((a, b) => Math.abs(b[1].pnl) - Math.abs(a[1].pnl));
  };

  return (
    <div style={{ padding: 16, color: theme.TEXT, maxWidth: 1200, margin: "0 auto" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22 }}>📊 Daily Report</h2>
          <div style={{ color: theme.MUTED, fontSize: 12, marginTop: 4 }}>
            Date-wise P&L + exit breakdown + rule firings
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="date"
            value={date}
            max={today}
            onChange={(e) => setDate(e.target.value)}
            style={{
              padding: "8px 12px",
              background: theme.SURFACE,
              color: theme.TEXT,
              border: `1px solid ${theme.BORDER}`,
              borderRadius: 6,
              fontSize: 14,
            }}
          />
          <button
            onClick={() => setDate(today)}
            style={{
              padding: "8px 16px",
              background: date === today ? theme.ACCENT : theme.SURFACE,
              color: date === today ? "#fff" : theme.TEXT,
              border: `1px solid ${theme.BORDER}`,
              borderRadius: 6,
              cursor: "pointer",
            }}>
            Today
          </button>
          <button
            onClick={() => loadData(date)}
            style={{
              padding: "8px 12px",
              background: theme.SURFACE,
              color: theme.TEXT,
              border: `1px solid ${theme.BORDER}`,
              borderRadius: 6,
              cursor: "pointer",
            }}>
            🔄
          </button>
        </div>
      </div>

      {/* Combined P&L Hero */}
      <div style={{
        background: isProfit
          ? `linear-gradient(135deg, ${theme.GREEN_BG || "#0a3a1a"} 0%, ${theme.SURFACE} 100%)`
          : `linear-gradient(135deg, ${theme.RED_BG || "#3a0a0a"} 0%, ${theme.SURFACE} 100%)`,
        border: `1px solid ${isProfit ? theme.GREEN : theme.RED}`,
        borderRadius: 8,
        padding: 20,
        marginBottom: 16,
      }}>
        <div style={{ color: theme.MUTED, fontSize: 13, marginBottom: 8 }}>
          {date} Combined Net P&L
        </div>
        <div style={{
          fontSize: 38, fontWeight: 800,
          color: isProfit ? theme.GREEN : theme.RED,
        }}>
          {fmt(totalPnl)}
        </div>
        <div style={{ color: theme.MUTED, fontSize: 13, marginTop: 8 }}>
          {c.total_trades || 0} trades · WR {c.win_rate || 0}%
        </div>
      </div>

      {/* Per-tab stats */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
        <Card title="⚡ Scalper" color={s.pnl > 0 ? theme.GREEN : (s.pnl < 0 ? theme.RED : theme.BORDER)}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <StatBox label="P&L" value={fmt(s.pnl)} color={s.pnl > 0 ? theme.GREEN : theme.RED} />
            <StatBox label="Trades" value={s.n || 0} sublabel={`${s.wins || 0}W / ${s.losses || 0}L`} />
            <StatBox label="Win Rate" value={`${s.win_rate || 0}%`} />
          </div>
        </Card>
        <Card title="💰 Main (PnL)" color={m.pnl > 0 ? theme.GREEN : (m.pnl < 0 ? theme.RED : theme.BORDER)}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <StatBox label="P&L" value={fmt(m.pnl)} color={m.pnl > 0 ? theme.GREEN : theme.RED} />
            <StatBox label="Trades" value={m.n || 0} sublabel={`${m.wins || 0}W / ${m.losses || 0}L`} />
            <StatBox label="Win Rate" value={`${m.win_rate || 0}%`} />
          </div>
        </Card>
      </div>

      {/* Top winner / loser */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
        {[s.top_winner, m.top_winner].filter(Boolean).length > 0 && (() => {
          const candidates = [s.top_winner, m.top_winner].filter(Boolean);
          const winner = candidates.reduce((a, b) => a.pnl > b.pnl ? a : b);
          return (
            <Card title="⭐ Top Win" color={theme.GREEN}>
              <div style={{ fontSize: 20, fontWeight: 700, color: theme.GREEN }}>{fmt(winner.pnl)}</div>
              <div style={{ fontSize: 13, marginTop: 6 }}>
                {winner.idx} {winner.action} {winner.strike}
              </div>
              <div style={{ fontSize: 11, color: theme.MUTED, marginTop: 4 }}>
                Peak {fmtPct(winner.peak_pct)} · Prob {winner.prob}% · {winner.time}
              </div>
            </Card>
          );
        })()}
        {[s.top_loser, m.top_loser].filter(Boolean).length > 0 && (() => {
          const candidates = [s.top_loser, m.top_loser].filter(Boolean);
          const loser = candidates.reduce((a, b) => a.pnl < b.pnl ? a : b);
          return (
            <Card title="💀 Top Loss" color={theme.RED}>
              <div style={{ fontSize: 20, fontWeight: 700, color: theme.RED }}>{fmt(loser.pnl)}</div>
              <div style={{ fontSize: 13, marginTop: 6 }}>
                {loser.idx} {loser.action} {loser.strike}
              </div>
              <div style={{ fontSize: 11, color: theme.MUTED, marginTop: 4 }}>
                Peak {fmtPct(loser.peak_pct)} · Prob {loser.prob}% · {loser.time}
              </div>
            </Card>
          );
        })()}
      </div>

      {/* Per-status breakdown */}
      <Card title="Exit Breakdown">
        <div style={{ overflowX: "auto" }}>
          {["scalper", "main"].map(tab => {
            const data = tab === "scalper" ? s : m;
            const rows = tabsByStatus(data);
            if (!rows.length) return null;
            return (
              <div key={tab} style={{ marginBottom: 12 }}>
                <div style={{ color: theme.MUTED, fontSize: 11, marginBottom: 6, fontWeight: 600 }}>
                  {tab.toUpperCase()}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {rows.map(([status, sd]) => (
                    <div key={status} style={{
                      padding: "6px 10px",
                      background: theme.SURFACE_2 || theme.SURFACE,
                      borderRadius: 4,
                      fontSize: 12,
                      border: `1px solid ${sd.pnl > 0 ? theme.GREEN : sd.pnl < 0 ? theme.RED : theme.BORDER}`,
                    }}>
                      <span style={{ fontWeight: 600 }}>{status}</span>
                      <span style={{ color: theme.MUTED, marginLeft: 4 }}>×{sd.n}</span>
                      <span style={{
                        marginLeft: 6,
                        color: sd.pnl > 0 ? theme.GREEN : sd.pnl < 0 ? theme.RED : theme.MUTED,
                        fontWeight: 600,
                      }}>{fmt(sd.pnl)}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      {/* Hour-by-hour */}
      <Card title="Hour by Hour">
        {(() => {
          const hours = {};
          for (const tab of [s, m]) {
            for (const [h, data] of Object.entries(tab.by_hour || {})) {
              if (!hours[h]) hours[h] = { n: 0, pnl: 0 };
              hours[h].n += data.n;
              hours[h].pnl += data.pnl;
            }
          }
          const sorted = Object.entries(hours).sort();
          if (!sorted.length) return <div style={{ color: theme.MUTED }}>No trades</div>;
          const maxPnl = Math.max(...sorted.map(([_, d]) => Math.abs(d.pnl)));
          return (
            <div>
              {sorted.map(([hour, data]) => {
                const width = maxPnl > 0 ? Math.abs(data.pnl) / maxPnl * 100 : 0;
                return (
                  <div key={hour} style={{ display: "flex", alignItems: "center", marginBottom: 6, fontSize: 12 }}>
                    <div style={{ width: 70, color: theme.MUTED }}>{hour}</div>
                    <div style={{ width: 50, color: theme.MUTED, fontSize: 11 }}>{data.n}t</div>
                    <div style={{ flex: 1, height: 14, background: theme.SURFACE_2 || theme.SURFACE, borderRadius: 3, position: "relative" }}>
                      <div style={{
                        position: "absolute",
                        left: 0, top: 0, bottom: 0,
                        width: `${width}%`,
                        background: data.pnl > 0 ? theme.GREEN : theme.RED,
                        borderRadius: 3,
                      }} />
                    </div>
                    <div style={{
                      width: 110, textAlign: "right",
                      color: data.pnl > 0 ? theme.GREEN : theme.RED,
                      fontWeight: 600,
                    }}>
                      {fmt(data.pnl)}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })()}
      </Card>

      {/* Damage control firings */}
      {(() => {
        const allRules = {};
        for (const tab of [s, m]) {
          for (const [r, n] of Object.entries(tab.new_rules_fired || {})) {
            allRules[r] = (allRules[r] || 0) + n;
          }
        }
        const fired = Object.entries(allRules).filter(([_, n]) => n > 0);
        if (!fired.length) return null;
        return (
          <Card title="🛡 Damage Control Fired">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {fired.map(([rule, n]) => (
                <div key={rule} style={{
                  padding: "6px 12px",
                  background: theme.SURFACE_2 || theme.SURFACE,
                  borderRadius: 4,
                  fontSize: 12,
                  border: `1px solid ${theme.BORDER}`,
                }}>
                  <span style={{ fontWeight: 600 }}>{rule}</span>
                  <span style={{ color: theme.MUTED, marginLeft: 6 }}>{n}×</span>
                </div>
              ))}
            </div>
          </Card>
        );
      })()}

      {/* Last 14 days history */}
      <Card title="📅 Last 14 Days">
        {recentDays?.by_date && Object.keys(recentDays.by_date).length > 0 ? (
          <div>
            {Object.entries(recentDays.by_date)
              .sort((a, b) => b[0].localeCompare(a[0]))
              .map(([d, tabs]) => {
                const combined = tabs.combined_pnl || 0;
                const isWinner = combined > 0;
                return (
                  <div
                    key={d}
                    onClick={() => setDate(d)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      padding: "10px 8px",
                      borderBottom: `1px solid ${theme.BORDER}`,
                      cursor: "pointer",
                      background: d === date ? (theme.SURFACE_2 || theme.SURFACE) : "transparent",
                    }}>
                    <div style={{ width: 120, fontSize: 13, fontWeight: 600 }}>{d}</div>
                    <div style={{ flex: 1, fontSize: 12, color: theme.MUTED }}>
                      {(tabs.scalper?.n || 0) + (tabs.main?.n || 0)} trades
                    </div>
                    <div style={{ width: 110, textAlign: "right", color: theme.MUTED, fontSize: 12 }}>
                      Sc: {fmt(tabs.scalper?.pnl || 0)}
                    </div>
                    <div style={{ width: 110, textAlign: "right", color: theme.MUTED, fontSize: 12 }}>
                      Mn: {fmt(tabs.main?.pnl || 0)}
                    </div>
                    <div style={{
                      width: 130,
                      textAlign: "right",
                      color: isWinner ? theme.GREEN : (combined < 0 ? theme.RED : theme.MUTED),
                      fontWeight: 700,
                      fontSize: 14,
                    }}>
                      {fmt(combined)}
                    </div>
                  </div>
                );
              })}
            <div style={{ marginTop: 10, padding: "8px 12px", background: theme.SURFACE_2 || theme.SURFACE, borderRadius: 4 }}>
              <span style={{ color: theme.MUTED, fontSize: 12 }}>
                {recentDays.totals?.days_with_data} days · avg/day:
              </span>
              <span style={{
                marginLeft: 8,
                fontWeight: 700,
                color: (recentDays.totals?.avg_per_day || 0) > 0 ? theme.GREEN : theme.RED,
              }}>
                {fmt(recentDays.totals?.avg_per_day || 0)}
              </span>
              <span style={{ marginLeft: 16, color: theme.MUTED, fontSize: 12 }}>Total:</span>
              <span style={{
                marginLeft: 4,
                fontWeight: 700,
                color: (recentDays.totals?.grand_total_pnl || 0) > 0 ? theme.GREEN : theme.RED,
              }}>
                {fmt(recentDays.totals?.grand_total_pnl || 0)}
              </span>
            </div>
          </div>
        ) : (
          <div style={{ color: theme.MUTED }}>No data</div>
        )}
      </Card>
    </div>
  );
}
