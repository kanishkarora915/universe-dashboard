/**
 * LivePositionChart
 * ─────────────────
 * Live premium chart for an open position with horizontal lines
 * for entry, SL, T1, T2 and a scrolling tick stream.
 *
 * Props:
 *   tradeId
 *   source   "MAIN" | "SCALPER"
 *   entry, sl, t1, t2, qty, action   (from trade row)
 *   currentLtp                       (live ltp from parent)
 */

import { useEffect, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";

const API = import.meta.env.VITE_API_URL || "";
const REFRESH_MS = 5000;

export default function LivePositionChart({
  tradeId, source = "MAIN", entry, sl, t1, t2, qty, action, currentLtp,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const [ticks, setTicks] = useState([]);
  const [error, setError] = useState(null);

  // Fetch ticks
  useEffect(() => {
    let alive = true;
    const fetchTicks = async () => {
      try {
        const r = await fetch(`${API}/api/positions/ticks/${tradeId}?source=${source}`);
        if (!r.ok) {
          setError(`API ${r.status}`);
          return;
        }
        const j = await r.json();
        if (!alive) return;
        const t = Array.isArray(j.ticks) ? j.ticks : [];
        setTicks(t);
        setError(null);
      } catch (e) {
        setError("network");
      }
    };
    fetchTicks();
    const id = setInterval(fetchTicks, REFRESH_MS);
    return () => { alive = false; clearInterval(id); };
  }, [tradeId, source]);

  // Init chart
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { color: "transparent" }, textColor: "#888" },
      grid: { vertLines: { color: "#1E1E2E" }, horzLines: { color: "#1E1E2E" } },
      timeScale: { timeVisible: true, secondsVisible: true, borderColor: "#1E1E2E" },
      rightPriceScale: { borderColor: "#1E1E2E" },
      width: containerRef.current.clientWidth,
      height: 220,
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;

    const series = chart.addSeries(LineSeries, {
      color: "#0A84FF",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    seriesRef.current = series;

    // Horizontal levels — entry/SL/T1/T2
    if (entry > 0) series.createPriceLine({
      price: entry, color: "#aaa", lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title: "Entry",
    });
    if (sl > 0) series.createPriceLine({
      price: sl, color: "#FF453A", lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title: "SL",
    });
    if (t1 > 0) series.createPriceLine({
      price: t1, color: "#30D158", lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title: "T1",
    });
    if (t2 > 0) series.createPriceLine({
      price: t2, color: "#A0DC5A", lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title: "T2",
    });

    const onResize = () => {
      if (!containerRef.current) return;
      chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, [entry, sl, t1, t2]);

  // Apply ticks to series
  useEffect(() => {
    if (!seriesRef.current) return;
    if (ticks.length === 0) return;
    // Normalise ts in seconds for lightweight-charts
    const data = ticks
      .map(t => ({
        time: Math.floor((t.ts || 0) / 1000),
        value: t.premium || t.ltp || 0,
      }))
      .filter(d => d.time > 0 && d.value > 0)
      // dedupe + sort
      .sort((a, b) => a.time - b.time);
    // dedupe by time (keep latest)
    const seen = new Set();
    const cleaned = [];
    for (let i = data.length - 1; i >= 0; i--) {
      if (!seen.has(data[i].time)) {
        seen.add(data[i].time);
        cleaned.unshift(data[i]);
      }
    }
    if (cleaned.length === 0) return;
    seriesRef.current.setData(cleaned);
    // Append a live point if currentLtp differs from last
    if (currentLtp > 0) {
      const last = cleaned[cleaned.length - 1];
      const nowSec = Math.floor(Date.now() / 1000);
      if (nowSec > last.time) {
        seriesRef.current.update({ time: nowSec, value: currentLtp });
      }
    }
  }, [ticks, currentLtp]);

  const isCE = (action || "").includes("CE");
  const profitPct = entry > 0 && currentLtp > 0
    ? ((currentLtp - entry) / entry * 100).toFixed(2)
    : null;
  const pnlRupees = entry > 0 && currentLtp > 0 && qty > 0
    ? Math.round((currentLtp - entry) * qty)
    : null;

  return (
    <div style={{
      background: "#0A0A0F", border: "1px solid #1E1E2E",
      borderRadius: 8, padding: "10px 12px", marginTop: 10,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 8,
      }}>
        <div style={{
          color: "#0A84FF", fontSize: 11, fontWeight: 700,
          textTransform: "uppercase", letterSpacing: 0.6,
        }}>
          📈 Live Premium · {source} · {ticks.length} ticks
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {profitPct != null && (
            <span style={{
              fontSize: 12, fontWeight: 700,
              color: profitPct >= 0 ? "#30D158" : "#FF453A",
            }}>
              {profitPct >= 0 ? "+" : ""}{profitPct}%
              {pnlRupees != null && (
                <span style={{ fontSize: 10, marginLeft: 6, color: "#aaa" }}>
                  ({pnlRupees >= 0 ? "+" : ""}₹{pnlRupees.toLocaleString("en-IN")})
                </span>
              )}
            </span>
          )}
          <LegendChip color="#aaa" label={`Entry ₹${entry?.toFixed?.(1) || entry}`} />
          <LegendChip color="#FF453A" label={`SL ₹${sl?.toFixed?.(1) || sl}`} />
          <LegendChip color="#30D158" label={`T1 ₹${t1?.toFixed?.(1) || t1}`} />
          <LegendChip color="#A0DC5A" label={`T2 ₹${t2?.toFixed?.(1) || t2}`} />
        </div>
      </div>

      <div ref={containerRef} style={{ width: "100%", height: 220 }} />

      {ticks.length === 0 && (
        <div style={{
          textAlign: "center", color: "#666", fontSize: 11,
          padding: "20px 0",
        }}>
          ⏳ Waiting for first watcher pulse — premium ticks will appear within 30s.
          {error && <div style={{ color: "#FF9F0A", marginTop: 6, fontSize: 10 }}>API: {error}</div>}
        </div>
      )}
    </div>
  );
}


function LegendChip({ color, label }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      fontSize: 10, color: "#aaa",
    }}>
      <span style={{
        width: 8, height: 2, background: color, borderRadius: 1,
      }}/>
      {label}
    </span>
  );
}
