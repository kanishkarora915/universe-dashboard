import { useEffect, useRef, useState } from "react";
import { createChart, AreaSeries, LineSeries } from "lightweight-charts";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const CARD = "#111118";
const BG = "#0A0A0F";
const BORDER = "#1E1E2E";

const fmt = (n) => (n ? Math.round(n).toLocaleString("en-IN") : "0");

export function PnLChart() {
  const chartRef = useRef(null);
  const containerRef = useRef(null);
  const [view, setView] = useState("all"); // all | open | closed
  const [trades, setTrades] = useState({ open: [], closed: [] });

  useEffect(() => {
    Promise.all([
      fetch("/api/trades/open").then(r => r.ok ? r.json() : []).catch(() => []),
      fetch("/api/trades/closed").then(r => r.ok ? r.json() : []).catch(() => []),
    ]).then(([open, closed]) => {
      setTrades({ open: Array.isArray(open) ? open : [], closed: Array.isArray(closed) ? closed : [] });
    });
  }, []);

  const allTrades = [...trades.closed, ...trades.open];
  const displayTrades = view === "open" ? trades.open : view === "closed" ? trades.closed : allTrades;

  // Build chart when data changes
  useEffect(() => {
    if (!containerRef.current) return;

    const sorted = [...trades.closed].sort((a, b) => (a.exit_time || a.entry_time).localeCompare(b.exit_time || b.entry_time));
    if (sorted.length === 0) {
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
      return;
    }

    let cumPnl = 0;
    const series = [];
    sorted.forEach(t => {
      cumPnl += t.pnl_rupees || 0;
      const d = t.exit_time || t.entry_time;
      if (d) {
        try {
          series.push({ time: new Date(d).toISOString().split("T")[0], value: Math.round(cumPnl) });
        } catch {}
      }
    });

    // Add open trade P&L
    trades.open.forEach(t => {
      cumPnl += t.pnl_rupees || 0;
      const d = t.entry_time;
      if (d) {
        try {
          series.push({ time: new Date(d).toISOString().split("T")[0], value: Math.round(cumPnl) });
        } catch {}
      }
    });

    if (series.length === 0) return;

    const byDate = {};
    series.forEach(s => { byDate[s.time] = s.value; });
    const uniqueSeries = Object.entries(byDate).map(([time, value]) => ({ time, value })).sort((a, b) => a.time.localeCompare(b.time));

    if (chartRef.current) chartRef.current.remove();

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 180,
      layout: { background: { color: "transparent" }, textColor: "#444" },
      grid: { vertLines: { color: "#141420" }, horzLines: { color: "#141420" } },
      rightPriceScale: { borderColor: "#1a1a2e" },
      timeScale: { borderColor: "#1a1a2e", timeVisible: false },
      crosshair: { mode: 0 },
    });

    const lastVal = uniqueSeries[uniqueSeries.length - 1]?.value || 0;
    // lightweight-charts v5 API: addSeries(SeriesType, options)
    const areaSeries = chart.addSeries(AreaSeries, {
      lineColor: lastVal >= 0 ? GREEN : RED,
      topColor: lastVal >= 0 ? GREEN + "22" : RED + "22",
      bottomColor: "transparent",
      lineWidth: 2,
    });
    areaSeries.setData(uniqueSeries);

    chart.timeScale().fitContent();
    chartRef.current = chart;

    const resize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.remove(); chartRef.current = null; };
  }, [trades]);

  const totalPnl = allTrades.reduce((s, t) => s + (t.pnl_rupees || 0), 0);

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase" }}>P&L PERFORMANCE</div>
        <div style={{ display: "flex", gap: 4 }}>
          {[{ k: "all", l: `All (${allTrades.length})` }, { k: "open", l: `Open (${trades.open.length})` }, { k: "closed", l: `Closed (${trades.closed.length})` }].map(b => (
            <button key={b.k} onClick={() => setView(b.k)} style={{
              background: view === b.k ? ACCENT + "22" : "transparent",
              color: view === b.k ? ACCENT : "#444",
              border: `1px solid ${view === b.k ? ACCENT + "44" : BORDER}`,
              borderRadius: 4, padding: "3px 8px", fontSize: 9, fontWeight: 600, cursor: "pointer",
            }}>{b.l}</button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div ref={containerRef} style={{ borderRadius: 6, overflow: "hidden", background: "transparent", minHeight: 180 }} />
      {allTrades.length === 0 && (
        <div style={{ color: "#333", textAlign: "center", padding: 30, fontSize: 11 }}>No trades yet — chart will appear after first trade</div>
      )}

      {/* Trade list */}
      {displayTrades.length > 0 && (
        <div style={{ maxHeight: 200, overflowY: "auto", marginTop: 10 }}>
          {displayTrades.map((t, i) => {
            const pnl = t.pnl_rupees || 0;
            const isOpen = t.status === "OPEN";
            return (
              <div key={i} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "6px 10px", borderBottom: `1px solid ${BORDER}11`, fontSize: 11,
              }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{
                    color: t.action?.includes("CE") ? GREEN : RED,
                    fontWeight: 700, fontSize: 9, padding: "1px 5px",
                    background: (t.action?.includes("CE") ? GREEN : RED) + "22", borderRadius: 3,
                  }}>{t.action}</span>
                  <span style={{ color: "#888" }}>{t.idx} {t.strike}</span>
                  <span style={{ color: "#333", fontSize: 9 }}>{(t.exit_time || t.entry_time)?.slice(5, 16)}</span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {isOpen && <span style={{ color: YELLOW, fontSize: 8, fontWeight: 700, padding: "1px 4px", background: YELLOW + "15", borderRadius: 3 }}>OPEN</span>}
                  <span style={{ color: pnl >= 0 ? GREEN : RED, fontWeight: 700 }}>₹{fmt(pnl)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function MiniSparkline({ values, color = ACCENT, width = 100, height = 30 }) {
  if (!values || values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values.map((v, i) => {
    const x = (i / (values.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}
