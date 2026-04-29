/**
 * Backtest Validator Tab — Compare system filters vs actual trade outcomes.
 *
 * Shows:
 *   - Aggregate stats (actual vs hypothetical P&L)
 *   - Verdict breakdown (matched/saved/missed)
 *   - Per-filter accuracy table
 *   - Equity curve overlay (actual vs system)
 *   - Per-trade expandable cards with filter reasons
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { createChart, LineSeries } from "lightweight-charts";

const ACCENT = "#0A84FF";
const GREEN = "#26a69a";
const RED = "#ef5350";
const ORANGE = "#FF9F0A";
const YELLOW = "#FFD60A";
const PURPLE = "#a855f7";
const BLUE = "#2962ff";
const FG = "#d4d4d8";
const FG_DIM = "#71717a";
const BG = "#0a0a0a";
const CARD = "#0f0f10";
const BORDER = "#1f1f24";

const fmtR = (n) => `₹${Math.round(n || 0).toLocaleString("en-IN")}`;
const fmtSign = (n) => `${(n || 0) >= 0 ? "+" : ""}${fmtR(n)}`;
const fmtPct = (n) => `${(n || 0).toFixed(1)}%`;

async function safeFetch(url, fb) {
  try { const r = await fetch(url); if (!r.ok) return fb; return await r.json(); } catch { return fb; }
}

const verdictColor = {
  MATCH_WIN: GREEN,
  MATCH_LOSS: ORANGE,
  SAVED: BLUE,
  MISSED: RED,
};

const verdictLabel = {
  MATCH_WIN: "MATCH WIN",
  MATCH_LOSS: "MATCH LOSS",
  SAVED: "SAVED",
  MISSED: "MISSED",
};

export default function BacktestTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filterType, setFilterType] = useState("ALL");
  const [expandedId, setExpandedId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    const r = await safeFetch("/api/backtest/full", null);
    setData(r);
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) {
    return (
      <div style={wrap}>
        <div style={{ color: FG_DIM, fontSize: 13, padding: 40, textAlign: "center" }}>
          Running backtest on all closed trades... reconstructing market state... replaying through 18 filters...
        </div>
      </div>
    );
  }

  if (!data || data.error) {
    return (
      <div style={wrap}>
        <div style={{ color: ORANGE, fontSize: 13, padding: 40, textAlign: "center" }}>
          {data?.error || "No data — backtest needs closed trades in DB."}
        </div>
        <button onClick={load} style={btnPrimary}>Reload</button>
      </div>
    );
  }

  const { total_trades, summary, verdict_breakdown, filter_stats, trades, equity_curve } = data;

  // Filter trades by verdict type
  const filteredTrades = filterType === "ALL"
    ? trades
    : trades.filter(t => t.verdict_type === filterType);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header */}
      <div style={wrap}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div>
            <div style={{ fontSize: 16, color: PURPLE, fontWeight: 700 }}>
              🔬 BACKTEST VALIDATOR
            </div>
            <div style={{ fontSize: 12, color: FG_DIM, marginTop: 4 }}>
              {total_trades} trades replayed through 18 filters · {data.generated_at?.slice(0, 16)}
            </div>
          </div>
          <button onClick={load} style={btnPrimary}>{loading ? "..." : "🔄 Re-run"}</button>
        </div>
      </div>

      {/* Aggregate Stats */}
      <div style={wrap}>
        <div style={sectionLabel}>📊 ACTUAL vs HYPOTHETICAL</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginBottom: 14 }}>
          <BigStat
            label="ACTUAL P&L"
            value={fmtSign(summary.actual_pnl)}
            sub={`Win rate ${summary.actual_win_rate}%`}
            color={summary.actual_pnl >= 0 ? GREEN : RED}
          />
          <BigStat
            label="HYPOTHETICAL P&L"
            value={fmtSign(summary.hypothetical_pnl)}
            sub={`Win rate ${summary.hypothetical_win_rate}%`}
            color={summary.hypothetical_pnl >= 0 ? GREEN : RED}
          />
          <BigStat
            label="IMPROVEMENT"
            value={fmtSign(summary.improvement)}
            sub={`${summary.improvement_pct >= 0 ? "+" : ""}${summary.improvement_pct}% vs actual`}
            color={summary.improvement >= 0 ? GREEN : RED}
          />
        </div>

        {/* Verdict breakdown */}
        <div style={sectionLabel}>VERDICT BREAKDOWN</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
          <Stat label="✓ MATCH WIN" value={verdict_breakdown.matched_wins} color={GREEN} sub="System allowed + won" />
          <Stat label="⚠️ MATCH LOSS" value={verdict_breakdown.matched_losses} color={ORANGE} sub="Allowed but lost" />
          <Stat label="✅ SAVED" value={verdict_breakdown.saved} color={BLUE} sub="Blocked + was loser" />
          <Stat label="❌ MISSED" value={verdict_breakdown.missed} color={RED} sub="Blocked + was winner" />
        </div>
        <div style={{
          marginTop: 10, padding: "8px 12px",
          background: BG, border: `1px solid ${BORDER}`, borderRadius: 4,
          fontSize: 12, color: FG,
        }}>
          🎯 Block Accuracy: <b style={{ color: GREEN }}>{verdict_breakdown.block_accuracy_pct}%</b> of blocked trades were actual losers (true positive rate)
        </div>
      </div>

      {/* Equity Curve */}
      <EquityCurveChart actual={equity_curve.actual} hypothetical={equity_curve.hypothetical} />

      {/* Per-Filter Performance */}
      <div style={wrap}>
        <div style={sectionLabel}>🛡️ PER-FILTER ACCURACY</div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                <th style={th}>FILTER</th>
                <th style={{ ...th, textAlign: "right" }}>BLOCKED</th>
                <th style={{ ...th, textAlign: "right" }}>BLOCKED CORRECT</th>
                <th style={{ ...th, textAlign: "right" }}>OVER-BLOCKED</th>
                <th style={{ ...th, textAlign: "right" }}>ACCURACY</th>
                <th style={{ ...th, textAlign: "right" }}>PASSED</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(filter_stats || {}).map(([name, s]) => (
                <tr key={name} style={{ borderBottom: `1px solid ${BORDER}33` }}>
                  <td style={td}><b>{name}</b></td>
                  <td style={{ ...td, textAlign: "right" }}>{s.blocked}</td>
                  <td style={{ ...td, textAlign: "right", color: GREEN }}>{s.blocked_lost}</td>
                  <td style={{ ...td, textAlign: "right", color: RED }}>{s.blocked_won}</td>
                  <td style={{ ...td, textAlign: "right", color: s.accuracy >= 70 ? GREEN : s.accuracy >= 50 ? YELLOW : RED, fontWeight: 700 }}>
                    {s.accuracy}%
                  </td>
                  <td style={{ ...td, textAlign: "right", color: FG_DIM }}>{s.passed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 11, color: FG_DIM, marginTop: 8, lineHeight: 1.5 }}>
          • <b>Blocked Correct</b>: Filter blocked, trade was actual loser (true positive)<br/>
          • <b>Over-Blocked</b>: Filter blocked, trade would have won (false positive)<br/>
          • <b>Accuracy</b>: Of blocked, % that were actual losers (higher = better filter)
        </div>
      </div>

      {/* Per-Trade Cards with Filter */}
      <div style={wrap}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={sectionLabel}>📋 TRADE-BY-TRADE ANALYSIS</div>
          <div style={{ display: "flex", gap: 4 }}>
            {["ALL", "MATCH_WIN", "MATCH_LOSS", "SAVED", "MISSED"].map(t => (
              <button key={t} onClick={() => setFilterType(t)} style={{
                background: filterType === t ? PURPLE : "transparent",
                color: filterType === t ? "#fff" : FG_DIM,
                border: `1px solid ${filterType === t ? PURPLE : BORDER}`,
                padding: "4px 10px", borderRadius: 3,
                fontSize: 10, fontWeight: 600, cursor: "pointer",
              }}>
                {verdictLabel[t] || t}
              </button>
            ))}
          </div>
        </div>

        <div style={{ maxHeight: 600, overflowY: "auto" }}>
          {filteredTrades.length === 0 && (
            <div style={{ color: FG_DIM, padding: 20, textAlign: "center" }}>
              No trades match this filter
            </div>
          )}
          {filteredTrades.slice(0, 100).map((t, i) => (
            <TradeCard
              key={`${t.source}-${t.trade_id}`}
              trade={t}
              expanded={expandedId === `${t.source}-${t.trade_id}`}
              onToggle={() => setExpandedId(expandedId === `${t.source}-${t.trade_id}` ? null : `${t.source}-${t.trade_id}`)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────── Equity Curve ───────────
function EquityCurveChart({ actual, hypothetical }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const disposedRef = useRef(false);

  useEffect(() => {
    if (!containerRef.current || !actual?.length) return;
    disposedRef.current = false;

    const chart = createChart(containerRef.current, {
      layout: { background: { color: BG }, textColor: FG_DIM, fontSize: 10 },
      grid: { vertLines: { color: "#1a1a22" }, horzLines: { color: "#1a1a22" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: BORDER },
      rightPriceScale: { borderColor: BORDER },
      width: containerRef.current.clientWidth,
      height: 300,
    });
    chartRef.current = chart;

    const actualSeries = chart.addSeries(LineSeries, { color: ORANGE, lineWidth: 2, priceLineVisible: false });
    const hypoSeries = chart.addSeries(LineSeries, { color: GREEN, lineWidth: 2, priceLineVisible: false });

    const toBars = (arr) => {
      const seen = new Set();
      const data = [];
      arr.forEach(p => {
        if (!p.ts) return;
        const t = Math.floor(new Date(p.ts).getTime() / 1000);
        if (seen.has(t)) return;
        seen.add(t);
        data.push({ time: t, value: p.pnl });
      });
      return data.sort((a, b) => a.time - b.time);
    };

    try { actualSeries.setData(toBars(actual)); } catch {}
    try { hypoSeries.setData(toBars(hypothetical)); } catch {}

    const ro = new ResizeObserver(() => {
      if (disposedRef.current || !containerRef.current) return;
      try { chart.applyOptions({ width: containerRef.current.clientWidth }); } catch {}
    });
    ro.observe(containerRef.current);

    return () => {
      disposedRef.current = true;
      try { ro.disconnect(); } catch {}
      try { chart.remove(); } catch {}
    };
  }, [actual, hypothetical]);

  return (
    <div style={wrap}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={sectionLabel}>📈 EQUITY CURVE: Actual vs Hypothetical</div>
        <div style={{ display: "flex", gap: 12, fontSize: 10 }}>
          <span><span style={{ color: ORANGE }}>━</span> Actual</span>
          <span><span style={{ color: GREEN }}>━</span> Hypothetical (system filters)</span>
        </div>
      </div>
      <div ref={containerRef} style={{ width: "100%", height: 300 }} />
    </div>
  );
}

// ─────────── Trade Card ───────────
function TradeCard({ trade, expanded, onToggle }) {
  const c = verdictColor[trade.verdict_type] || FG_DIM;

  return (
    <div style={{
      background: BG,
      border: `1px solid ${c}33`,
      borderLeft: `4px solid ${c}`,
      borderRadius: 4,
      padding: "10px 12px",
      marginBottom: 6,
      cursor: "pointer",
    }} onClick={onToggle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, flexWrap: "wrap" }}>
          <span style={{
            background: c, color: "#fff", padding: "2px 8px",
            borderRadius: 3, fontSize: 9, fontWeight: 800, letterSpacing: 0.3,
          }}>
            {verdictLabel[trade.verdict_type]}
          </span>
          <span style={{ color: FG_DIM, fontSize: 10 }}>#{trade.trade_id}</span>
          <span style={{ color: FG_DIM, fontSize: 10 }}>{trade.source}</span>
          <span style={{ color: FG, fontWeight: 700 }}>{trade.idx} {trade.action} {trade.strike}</span>
          <span style={{ color: FG_DIM, fontSize: 10 }}>{trade.weekday} · {trade.time_window}</span>
          {trade.is_expiry && <span style={{ color: ORANGE, fontSize: 10, fontWeight: 700 }}>EXPIRY</span>}
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: trade.actual_pnl >= 0 ? GREEN : RED, fontSize: 13, fontWeight: 700 }}>
            {fmtSign(trade.actual_pnl)}
          </div>
          <div style={{ color: FG_DIM, fontSize: 10 }}>{trade.actual_status}</div>
        </div>
      </div>

      {/* Filter chips */}
      <div style={{ display: "flex", gap: 4, marginTop: 8, flexWrap: "wrap" }}>
        {trade.filters.map((f, i) => (
          <span key={i} style={{
            fontSize: 10, padding: "2px 6px", borderRadius: 3,
            background: f.blocks ? RED + "22" : GREEN + "11",
            color: f.blocks ? RED : GREEN,
            border: `1px solid ${f.blocks ? RED + "44" : GREEN + "33"}`,
          }}>
            {f.icon} {f.filter} {f.blocks ? "BLOCK" : "PASS"}
          </span>
        ))}
      </div>

      {/* Expanded details */}
      {expanded && (
        <div style={{ marginTop: 10, padding: 10, background: CARD, borderRadius: 4 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 10, fontSize: 11 }}>
            <Mini label="Entry" value={`₹${trade.entry_price}`} />
            <Mini label="Exit" value={`₹${trade.exit_price || "—"}`} />
            <Mini label="Probability" value={`${trade.probability}%`} />
          </div>
          <div style={{ fontSize: 11, color: FG_DIM, fontWeight: 700, marginBottom: 6 }}>FILTER REASONS:</div>
          {trade.filters.map((f, i) => (
            <div key={i} style={{
              padding: "5px 8px", marginBottom: 4, borderRadius: 3,
              background: f.blocks ? "#3a1010" : "#103a1d",
              borderLeft: `3px solid ${f.blocks ? RED : GREEN}`,
              fontSize: 11,
            }}>
              <div style={{ color: f.blocks ? RED : GREEN, fontWeight: 700, fontSize: 10 }}>
                {f.icon} {f.filter}: {f.blocks ? "BLOCKED" : "PASSED"}
              </div>
              <div style={{ color: FG, marginTop: 2 }}>{f.reason}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────── helpers ───────────
function BigStat({ label, value, sub, color = FG }) {
  return (
    <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 4, padding: "12px 14px" }}>
      <div style={{ fontSize: 10, color: FG_DIM, fontWeight: 600, letterSpacing: 0.5, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color, marginTop: 4, fontFeatureSettings: "'tnum'" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: FG_DIM, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

function Stat({ label, value, color = FG, sub }) {
  return (
    <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 4, padding: "10px 12px" }}>
      <div style={{ fontSize: 9, color: FG_DIM, fontWeight: 600, letterSpacing: 0.3 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 9, color: FG_DIM, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function Mini({ label, value, color = FG }) {
  return (
    <div>
      <div style={{ fontSize: 8, color: FG_DIM, fontWeight: 700 }}>{label}</div>
      <div style={{ fontSize: 12, color, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  );
}

const wrap = {
  background: CARD,
  border: `1px solid ${BORDER}`,
  borderRadius: 6,
  padding: 16,
  fontFamily: "-apple-system, 'Segoe UI', system-ui, sans-serif",
};

const sectionLabel = {
  fontSize: 10,
  color: FG_DIM,
  fontWeight: 700,
  letterSpacing: 1,
  textTransform: "uppercase",
  marginBottom: 10,
};

const th = {
  padding: "8px 10px",
  color: FG_DIM,
  textAlign: "left",
  fontSize: 9,
  fontWeight: 700,
  letterSpacing: 0.5,
  textTransform: "uppercase",
};

const td = {
  padding: "6px 10px",
  color: FG,
  fontSize: 11,
};

const btnPrimary = {
  background: PURPLE,
  color: "#fff",
  border: "none",
  padding: "6px 14px",
  borderRadius: 4,
  fontSize: 11,
  fontWeight: 700,
  cursor: "pointer",
};
