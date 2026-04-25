/**
 * ZonesTab — Rejection Zone Engine.
 *
 * Live chart with rejection zones overlaid (red above, green below) +
 * deep OI analysis per zone + hidden activity feed + final verdict.
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { createChart, LineSeries } from "lightweight-charts";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const CARD = "#111118";
const BG = "#0A0A0F";
const BORDER = "#1E1E2E";

const fmtL = (n) => `${(Math.abs(n) / 100000).toFixed(1)}L`;
const fmtLSigned = (n) => `${n >= 0 ? "+" : "−"}${(Math.abs(n) / 100000).toFixed(2)}L`;

const strengthColor = {
  MEGA: "#ff006e",
  STRONG: "#fb5607",
  MEDIUM: "#ffbe0b",
  WEAK: "#999",
};

const sigColor = {
  STRENGTHENING: GREEN,
  WEAKENING: RED,
  HOLDING: YELLOW,
  NEUTRAL: "#888",
};

async function safeFetch(url, fb) {
  try { const r = await fetch(url); if (!r.ok) return fb; return await r.json(); } catch { return fb; }
}

// ─── Live Chart with rejection zone overlays ───
function ZoneChart({ chartData, idx }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const lineRefs = useRef([]);

  useEffect(() => {
    if (!containerRef.current || !chartData) return;
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }

    const chart = createChart(containerRef.current, {
      layout: { background: { color: BG }, textColor: "#888", fontSize: 11 },
      grid: { vertLines: { color: "#1a1a22" }, horzLines: { color: "#1a1a22" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: BORDER },
      rightPriceScale: { borderColor: BORDER },
      crosshair: { mode: 0 },
      width: containerRef.current.clientWidth,
      height: 360,
    });
    chartRef.current = chart;

    const series = chart.addSeries(LineSeries, {
      color: ACCENT,
      lineWidth: 2,
      priceLineVisible: true,
    });

    if (Array.isArray(chartData.series) && chartData.series.length > 0) {
      // Sort and dedupe
      const seen = new Set();
      const data = [];
      chartData.series
        .sort((a, b) => a.time - b.time)
        .forEach((p) => {
          if (!seen.has(p.time)) {
            seen.add(p.time);
            data.push(p);
          }
        });
      series.setData(data);
    }

    // Add horizontal price lines for each zone
    const lines = [];
    (chartData.upside_levels || []).forEach((z) => {
      const line = series.createPriceLine({
        price: z.price,
        color: strengthColor[z.strength] || RED,
        lineWidth: z.strength === "MEGA" ? 3 : z.strength === "STRONG" ? 2 : 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `↑ ${z.strike} (${z.strength})`,
      });
      lines.push(line);
    });
    (chartData.downside_levels || []).forEach((z) => {
      const line = series.createPriceLine({
        price: z.price,
        color: strengthColor[z.strength] || GREEN,
        lineWidth: z.strength === "MEGA" ? 3 : z.strength === "STRONG" ? 2 : 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `↓ ${z.strike} (${z.strength})`,
      });
      lines.push(line);
    });
    lineRefs.current = lines;

    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (chart && containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [chartData, idx]);

  return (
    <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ color: "#ccc", fontWeight: 700, fontSize: 13 }}>{idx} Live Chart with Rejection Zones</div>
        <div style={{ display: "flex", gap: 8, fontSize: 10, color: "#777" }}>
          {Object.entries(strengthColor).map(([k, v]) => (
            <span key={k}>
              <span style={{ display: "inline-block", width: 10, height: 2, background: v, marginRight: 4, verticalAlign: "middle" }}></span>
              {k}
            </span>
          ))}
        </div>
      </div>
      <div ref={containerRef} style={{ width: "100%", height: 360 }} />
    </div>
  );
}

// ─── Zone Card ───
function ZoneCard({ zone }) {
  const isUp = zone.side === "UPSIDE";
  const sColor = strengthColor[zone.strength] || "#888";
  const sigC = sigColor[zone.signal] || "#888";

  const today_ce = zone.oi?.today_ce_change || 0;
  const today_pe = zone.oi?.today_pe_change || 0;
  const total_ce = zone.oi?.total_ce_oi || 0;
  const total_pe = zone.oi?.total_pe_oi || 0;

  return (
    <div style={{
      background: CARD,
      borderLeft: `3px solid ${isUp ? RED : GREEN}`,
      border: `1px solid ${BORDER}`,
      borderRadius: 8,
      padding: 12,
      marginBottom: 8,
    }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 16, color: isUp ? RED : GREEN, fontWeight: 800 }}>
            {isUp ? "↑" : "↓"} {zone.strike}
          </span>
          <span style={{
            background: sColor + "22", color: sColor,
            padding: "2px 8px", borderRadius: 4,
            fontSize: 10, fontWeight: 800, letterSpacing: 0.5,
          }}>
            {zone.strength}
          </span>
          <span style={{ color: "#666", fontSize: 10 }}>
            {zone.touches}× touches · last {zone.last_seen?.slice(5)}
          </span>
        </div>
        <span style={{
          background: sigC + "22", color: sigC,
          padding: "3px 10px", borderRadius: 4,
          fontSize: 11, fontWeight: 700,
        }}>
          {zone.signal}
        </span>
      </div>

      {/* Reason */}
      <div style={{ color: "#aaa", fontSize: 11, marginBottom: 10, lineHeight: 1.4 }}>
        {zone.reason}
      </div>

      {/* OI Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6, marginBottom: 10 }}>
        <Stat label="Today CE Δ" value={fmtLSigned(today_ce)} color={today_ce >= 0 ? RED : GREEN} />
        <Stat label="Today PE Δ" value={fmtLSigned(today_pe)} color={today_pe >= 0 ? GREEN : RED} />
        <Stat label="Total CE" value={fmtL(total_ce)} color="#888" />
        <Stat label="Total PE" value={fmtL(total_pe)} color="#888" />
      </div>

      {/* Hidden events on this zone */}
      {zone.hidden_count > 0 && (
        <div style={{ marginTop: 8, padding: 8, background: BG, borderRadius: 6 }}>
          <div style={{ color: PURPLE, fontSize: 10, fontWeight: 700, marginBottom: 4 }}>
            🐋 {zone.hidden_count} HIDDEN EVENT{zone.hidden_count > 1 ? "S" : ""} (last 4 hrs)
          </div>
          {(zone.hidden_events || []).slice(0, 3).map((h, i) => (
            <div key={i} style={{ fontSize: 10, color: "#bbb", padding: "2px 0" }}>
              <span style={{ color: ORANGE, fontWeight: 600 }}>{h.event_type}</span>
              <span style={{ color: "#666" }}> · {h.time?.slice(11, 16)} · </span>
              {h.description}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div style={{ background: BG, borderRadius: 5, padding: "6px 8px" }}>
      <div style={{ color: "#666", fontSize: 9, fontWeight: 700, textTransform: "uppercase" }}>{label}</div>
      <div style={{ color, fontSize: 12, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

// ─── Verdict Banner ───
function VerdictBanner({ verdict, spot }) {
  if (!verdict) return null;
  const sig = verdict.signal;
  const c = sig === "BUY CE" ? GREEN : sig === "BUY PE" ? RED : YELLOW;

  return (
    <div style={{
      background: `linear-gradient(90deg, ${c}22, ${BG})`,
      border: `1px solid ${c}66`,
      borderRadius: 12,
      padding: "16px 20px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ color: "#666", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
            🎯 Final Verdict (Rejection Zone Engine)
          </div>
          <div style={{ color: c, fontSize: 24, fontWeight: 900, marginTop: 4 }}>
            {sig}
          </div>
          <div style={{ color: "#888", fontSize: 11 }}>
            Confidence {verdict.confidence}% · Spot {spot} · Bull {verdict.bull_score} vs Bear {verdict.bear_score}
          </div>
        </div>
        {(verdict.target || verdict.sl) && (
          <div style={{ display: "flex", gap: 12 }}>
            {verdict.target && (
              <div style={{ textAlign: "center" }}>
                <div style={{ color: "#666", fontSize: 9, fontWeight: 700 }}>TARGET</div>
                <div style={{ color: GREEN, fontSize: 16, fontWeight: 800 }}>{verdict.target}</div>
              </div>
            )}
            {verdict.sl && (
              <div style={{ textAlign: "center" }}>
                <div style={{ color: "#666", fontSize: 9, fontWeight: 700 }}>SL</div>
                <div style={{ color: RED, fontSize: 16, fontWeight: 800 }}>{verdict.sl}</div>
              </div>
            )}
          </div>
        )}
      </div>

      {verdict.reasons && verdict.reasons.length > 0 && (
        <div style={{ marginTop: 12, padding: 10, background: BG, borderRadius: 6 }}>
          <div style={{ color: "#777", fontSize: 9, fontWeight: 700, marginBottom: 6 }}>REASONS</div>
          {verdict.reasons.map((r, i) => (
            <div key={i} style={{ color: "#ccc", fontSize: 11, padding: "2px 0" }}>{r}</div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Hidden Activity Feed ───
function HiddenFeed({ events }) {
  if (!Array.isArray(events) || events.length === 0) {
    return (
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12 }}>
        <div style={{ color: "#777", fontSize: 11, fontWeight: 700, marginBottom: 6 }}>🐋 HIDDEN ACTIVITY (last 2 hrs)</div>
        <div style={{ color: "#555", fontSize: 11, textAlign: "center", padding: 10 }}>
          No big moves detected yet.
        </div>
      </div>
    );
  }

  return (
    <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12 }}>
      <div style={{ color: "#aaa", fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
        🐋 HIDDEN ACTIVITY FEED · {events.length} events
      </div>
      <div style={{ maxHeight: 280, overflowY: "auto" }}>
        {events.map((e, i) => {
          const isBullish = (e.event_type === "MASS BUY ENTRY" && e.side === "CE")
            || (e.event_type === "MASS COVER" && e.side === "CE")
            || (e.event_type === "MASS WRITE" && e.side === "PE");
          const c = isBullish ? GREEN : RED;
          return (
            <div key={i} style={{
              padding: "6px 8px",
              borderBottom: `1px solid ${BORDER}33`,
              fontSize: 11,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                <span style={{ color: c, fontWeight: 700 }}>{e.event_type}</span>
                <span style={{ color: "#555", fontSize: 9 }}>{e.time?.slice(11, 16)}</span>
              </div>
              <div style={{ color: "#bbb", marginTop: 2 }}>
                {e.idx} {e.strike} {e.side}
                {e.lots_moved ? ` · ${e.lots_moved} lots` : ""}
              </div>
              <div style={{ color: "#888", fontSize: 10, marginTop: 2 }}>{e.description}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Main Tab ───
export default function ZonesTab() {
  const [idx, setIdx] = useState("NIFTY");
  const [zones, setZones] = useState(null);
  const [chart, setChart] = useState(null);
  const [hidden, setHidden] = useState([]);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [capturing, setCapturing] = useState(false);

  const load = useCallback(async () => {
    const [z, c, h] = await Promise.all([
      safeFetch(`/api/zones/${idx}`, null),
      safeFetch(`/api/zones/chart/${idx}`, null),
      safeFetch(`/api/zones/hidden-events?idx=${idx}&hours=2&limit=30`, []),
    ]);
    if (z && !z.error) setZones(z);
    if (c && !c.error) setChart(c);
    if (Array.isArray(h)) setHidden(h);
    setLastUpdate(new Date().toLocaleTimeString("en-IN"));
  }, [idx]);

  useEffect(() => {
    load();
    const iv = setInterval(load, 30_000);
    return () => clearInterval(iv);
  }, [load]);

  const captureNow = async () => {
    setCapturing(true);
    await fetch("/api/zones/capture-now", { method: "POST" }).catch(() => {});
    setTimeout(() => { load(); setCapturing(false); }, 1500);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header */}
      <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <div>
            <div style={{ color: ACCENT, fontWeight: 900, fontSize: 15 }}>🎯 REJECTION ZONE ENGINE</div>
            <div style={{ color: "#666", fontSize: 11, marginTop: 2 }}>
              Institutional levels · Today's OI deep-dive · Hidden 100+ lot activity · Live chart with zones
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {["NIFTY", "BANKNIFTY"].map((i) => (
              <button key={i} onClick={() => setIdx(i)} style={{
                background: idx === i ? ACCENT : "transparent",
                color: idx === i ? "#fff" : "#666",
                border: `1px solid ${idx === i ? ACCENT : BORDER}`,
                borderRadius: 6, padding: "6px 14px", fontSize: 11, fontWeight: 700, cursor: "pointer",
              }}>{i}</button>
            ))}
            <button onClick={captureNow} disabled={capturing} style={{
              background: capturing ? "#222" : "transparent",
              color: capturing ? "#555" : ACCENT,
              border: `1px solid ${ACCENT}44`,
              borderRadius: 6, padding: "6px 12px", fontSize: 11, fontWeight: 700,
              cursor: capturing ? "wait" : "pointer",
            }}>
              {capturing ? "Capturing…" : "Capture Now"}
            </button>
          </div>
        </div>
        {lastUpdate && (
          <div style={{ color: "#444", fontSize: 10, marginTop: 6 }}>Last: {lastUpdate}</div>
        )}
      </div>

      {/* Verdict Banner */}
      {zones?.verdict && <VerdictBanner verdict={zones.verdict} spot={zones.spot} />}

      {/* Live Chart */}
      {chart && <ZoneChart chartData={chart} idx={idx} />}

      {/* Two columns: Upside | Downside */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {/* UPSIDE */}
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: 14 }}>
          <div style={{ color: RED, fontWeight: 800, fontSize: 13, marginBottom: 10 }}>
            ↑ UPSIDE REJECTIONS (Resistance)
          </div>
          {zones?.upside_zones?.length > 0 ? (
            zones.upside_zones.map((z) => <ZoneCard key={`u-${z.strike}`} zone={z} />)
          ) : (
            <div style={{ color: "#555", fontSize: 11, padding: 20, textAlign: "center" }}>
              No upside rejection zones detected. Need 3-5 days of EOD data.
            </div>
          )}
        </div>

        {/* DOWNSIDE */}
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: 14 }}>
          <div style={{ color: GREEN, fontWeight: 800, fontSize: 13, marginBottom: 10 }}>
            ↓ DOWNSIDE REJECTIONS (Support)
          </div>
          {zones?.downside_zones?.length > 0 ? (
            zones.downside_zones.map((z) => <ZoneCard key={`d-${z.strike}`} zone={z} />)
          ) : (
            <div style={{ color: "#555", fontSize: 11, padding: 20, textAlign: "center" }}>
              No downside rejection zones detected. Need 3-5 days of EOD data.
            </div>
          )}
        </div>
      </div>

      {/* Hidden Activity Feed */}
      <HiddenFeed events={hidden} />
    </div>
  );
}
