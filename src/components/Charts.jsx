import { useEffect, useRef, useState } from "react";
import { createChart } from "lightweight-charts";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const CARD = "#111118";
const BG = "#0A0A0F";

export function PnLChart() {
  const chartRef = useRef(null);
  const containerRef = useRef(null);
  const [data, setData] = useState(null);

  useEffect(() => {
    fetch("/api/trades/closed")
      .then((r) => r.ok ? r.json() : [])
      .then(setData)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!data || !Array.isArray(data) || data.length === 0 || !containerRef.current) return;

    // Build cumulative P&L series
    let cumPnl = 0;
    const series = [];
    data.forEach((t, i) => {
      cumPnl += t.pnl_rupees || 0;
      const d = t.exit_time || t.entry_time;
      if (d) {
        try {
          const dt = new Date(d);
          series.push({
            time: dt.toISOString().split("T")[0],
            value: Math.round(cumPnl),
          });
        } catch {}
      }
    });

    if (series.length === 0) return;

    // Deduplicate by date (keep last value per day)
    const byDate = {};
    series.forEach((s) => { byDate[s.time] = s.value; });
    const uniqueSeries = Object.entries(byDate)
      .map(([time, value]) => ({ time, value }))
      .sort((a, b) => a.time.localeCompare(b.time));

    if (chartRef.current) {
      chartRef.current.remove();
    }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 200,
      layout: { background: { color: BG }, textColor: "#555" },
      grid: { vertLines: { color: "#1a1a2e" }, horzLines: { color: "#1a1a2e" } },
      rightPriceScale: { borderColor: "#1a1a2e" },
      timeScale: { borderColor: "#1a1a2e", timeVisible: false },
    });

    const lineSeries = chart.addAreaSeries({
      lineColor: uniqueSeries[uniqueSeries.length - 1]?.value >= 0 ? GREEN : RED,
      topColor: uniqueSeries[uniqueSeries.length - 1]?.value >= 0 ? GREEN + "33" : RED + "33",
      bottomColor: "transparent",
      lineWidth: 2,
    });

    lineSeries.setData(uniqueSeries);
    chart.timeScale().fitContent();
    chartRef.current = chart;

    const resize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  return (
    <div>
      <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", marginBottom: 6 }}>
        CUMULATIVE P&L
      </div>
      <div ref={containerRef} style={{ borderRadius: 8, overflow: "hidden", background: BG }} />
      {(!data || data.length === 0) && (
        <div style={{ color: "#333", textAlign: "center", padding: 20, fontSize: 11 }}>No closed trades yet</div>
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
