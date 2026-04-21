/**
 * OIHeatmap — Live OI distribution with running spot LTP.
 *
 * Shows for NIFTY + BANKNIFTY:
 *  - Grid of strikes (ATM±8) with CE OI (right) and PE OI (left)
 *  - Heatmap color intensity based on OI size
 *  - Live LTP line running across strikes
 *  - Max Pain marker
 *  - Big CE/PE walls highlighted
 *  - OI change indicators (green = building, red = unwinding)
 *  - Live refresh every 5 seconds
 */

import React, { useEffect, useState, useMemo } from "react";
import { SPACE, RADIUS, FONT } from "../theme";

const GREEN = "#10b981";
const RED = "#ef4444";
const YELLOW = "#f59e0b";
const BLUE = "#3b82f6";
const GRAY = "#6b7280";
const PURPLE = "#a855f7";

function useChainData(index) {
  const [chain, setChain] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`/api/option-chain/${index}`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled && Array.isArray(data)) setChain(data);
      } catch {}
      if (!cancelled) setLoading(false);
    };
    load();
    const iv = setInterval(load, 5000); // 5 sec refresh for live feel
    return () => { cancelled = true; clearInterval(iv); };
  }, [index]);

  return { chain, loading };
}

function HeatmapRow({ row, maxCE, maxPE, spot, atm, maxPain, bigCEWall, bigPEWall, strikeGap }) {
  const isATM = Math.abs(row.strike - atm) < strikeGap / 2;
  const isMaxPain = Math.abs(row.strike - maxPain) < strikeGap / 2;
  const isBigCE = row.strike === bigCEWall;
  const isBigPE = row.strike === bigPEWall;

  const ceIntensity = maxCE > 0 ? Math.min(1, row.ce_oi / maxCE) : 0;
  const peIntensity = maxPE > 0 ? Math.min(1, row.pe_oi / maxPE) : 0;

  const ceUp = row.ce_oi_change > 0;
  const peUp = row.pe_oi_change > 0;

  const ceOI = (row.ce_oi / 100000).toFixed(1);
  const peOI = (row.pe_oi / 100000).toFixed(1);
  const ceChg = row.ce_oi_change !== 0 ? ((row.ce_oi_change > 0 ? "+" : "") + (row.ce_oi_change / 100000).toFixed(1)) : "";
  const peChg = row.pe_oi_change !== 0 ? ((row.pe_oi_change > 0 ? "+" : "") + (row.pe_oi_change / 100000).toFixed(1)) : "";

  const spotCrossing = Math.abs(spot - row.strike) < strikeGap / 2;

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "1fr 90px 1fr",
      gap: 4,
      alignItems: "stretch",
      position: "relative",
    }}>
      {spotCrossing && (
        <div style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: "50%",
          height: 2,
          background: YELLOW,
          zIndex: 2,
          boxShadow: `0 0 8px ${YELLOW}`,
          pointerEvents: "none",
        }}>
          <div style={{
            position: "absolute",
            right: 2,
            top: -8,
            padding: "1px 5px",
            background: YELLOW,
            color: "#000",
            fontSize: 9,
            fontWeight: 700,
            borderRadius: 2,
            fontFamily: FONT.MONO || "monospace",
          }}>
            SPOT {spot.toFixed(1)}
          </div>
        </div>
      )}

      {/* PE side (left) */}
      <div style={{
        padding: "4px 8px",
        background: `linear-gradient(to left, rgba(16, 185, 129, ${0.05 + peIntensity * 0.35}), transparent)`,
        borderRadius: RADIUS.SM,
        display: "flex",
        justifyContent: "flex-end",
        alignItems: "center",
        gap: 6,
        fontSize: 11,
        fontFamily: FONT.MONO || "monospace",
      }}>
        {peChg && (
          <span style={{ fontSize: 9, color: peUp ? GREEN : RED, opacity: 0.8 }}>
            {peChg}L
          </span>
        )}
        <span style={{ color: isBigPE ? GREEN : "var(--text-primary, #fff)", fontWeight: isBigPE ? 800 : 600 }}>
          {peOI}L
        </span>
        {isBigPE && <span style={{ fontSize: 10, color: GREEN }}>🛡</span>}
      </div>

      {/* Strike (center) */}
      <div style={{
        padding: "4px 6px",
        background: isATM ? "rgba(168, 85, 247, 0.15)" :
                    isMaxPain ? "rgba(245, 158, 11, 0.1)" :
                    "rgba(255,255,255,0.02)",
        border: isATM ? `1px solid ${PURPLE}` :
                isMaxPain ? `1px solid ${YELLOW}66` :
                "1px solid transparent",
        borderRadius: RADIUS.SM,
        textAlign: "center",
        fontFamily: FONT.MONO || "monospace",
        fontSize: 12,
        fontWeight: 700,
        color: isATM ? PURPLE : isMaxPain ? YELLOW : "var(--text-primary, #fff)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 4,
      }}>
        {isMaxPain && <span style={{ fontSize: 9 }}>🎯</span>}
        {row.strike.toFixed(0)}
        {isATM && <span style={{ fontSize: 8, color: PURPLE }}>ATM</span>}
      </div>

      {/* CE side (right) */}
      <div style={{
        padding: "4px 8px",
        background: `linear-gradient(to right, rgba(239, 68, 68, ${0.05 + ceIntensity * 0.35}), transparent)`,
        borderRadius: RADIUS.SM,
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
        gap: 6,
        fontSize: 11,
        fontFamily: FONT.MONO || "monospace",
      }}>
        {isBigCE && <span style={{ fontSize: 10, color: RED }}>🧱</span>}
        <span style={{ color: isBigCE ? RED : "var(--text-primary, #fff)", fontWeight: isBigCE ? 800 : 600 }}>
          {ceOI}L
        </span>
        {ceChg && (
          <span style={{ fontSize: 9, color: ceUp ? RED : GREEN, opacity: 0.8 }}>
            {ceChg}L
          </span>
        )}
      </div>
    </div>
  );
}

function IndexHeatmap({ index, live }) {
  const { chain, loading } = useChainData(index);
  const idxLive = live?.[index.toLowerCase()] || {};
  const spot = idxLive.ltp || 0;
  const maxPain = idxLive.maxPain || 0;
  const bigCEWall = idxLive.bigCallStrike || 0;
  const bigPEWall = idxLive.bigPutStrike || 0;
  const changePct = idxLive.changePct || 0;
  const strikeGap = index === "NIFTY" ? 50 : 100;

  const atm = useMemo(() => {
    return spot > 0 ? Math.round(spot / strikeGap) * strikeGap : 0;
  }, [spot, strikeGap]);

  const displayChain = useMemo(() => {
    if (!chain.length || !atm) return [];
    return chain
      .filter(r => Math.abs(r.strike - atm) <= strikeGap * 8)
      .sort((a, b) => b.strike - a.strike);
  }, [chain, atm, strikeGap]);

  const maxCE = useMemo(() => Math.max(...displayChain.map(r => r.ce_oi), 1), [displayChain]);
  const maxPE = useMemo(() => Math.max(...displayChain.map(r => r.pe_oi), 1), [displayChain]);

  if (loading && displayChain.length === 0) {
    return (
      <div style={{ padding: SPACE.MD, textAlign: "center", color: "var(--text-secondary, #888)", fontSize: 12 }}>
        Loading {index} OI heatmap...
      </div>
    );
  }

  const totalCE = displayChain.reduce((s, r) => s + r.ce_oi, 0);
  const totalPE = displayChain.reduce((s, r) => s + r.pe_oi, 0);
  const pcr = totalCE > 0 ? (totalPE / totalCE).toFixed(2) : 0;

  return (
    <div style={{
      padding: SPACE.MD,
      background: "rgba(59, 130, 246, 0.04)",
      border: `1px solid ${BLUE}33`,
      borderRadius: RADIUS.LG,
      display: "flex",
      flexDirection: "column",
      gap: SPACE.SM,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: SPACE.SM }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-secondary, #888)", letterSpacing: 1 }}>
            {index} · LIVE OI HEATMAP
          </div>
          <div style={{ fontSize: 20, fontWeight: 800, fontFamily: FONT.MONO || "monospace", marginTop: 2 }}>
            {spot.toFixed(2)}
            <span style={{ fontSize: 12, marginLeft: 8, color: changePct >= 0 ? GREEN : RED }}>
              {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <MiniPill label="ATM" value={atm} color={PURPLE} />
          <MiniPill label="Max Pain" value={maxPain} color={YELLOW} />
          <MiniPill label="PCR" value={pcr} color={pcr > 1 ? GREEN : RED} />
        </div>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "1fr 90px 1fr",
        gap: 4,
        fontSize: 10,
        color: "var(--text-secondary, #888)",
        letterSpacing: 1,
        marginBottom: 2,
      }}>
        <div style={{ textAlign: "right", color: GREEN }}>◀ PE OI (Support)</div>
        <div style={{ textAlign: "center" }}>STRIKE</div>
        <div style={{ textAlign: "left", color: RED }}>CE OI (Resistance) ▶</div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {displayChain.map(row => (
          <HeatmapRow
            key={row.strike}
            row={row}
            maxCE={maxCE}
            maxPE={maxPE}
            spot={spot}
            atm={atm}
            maxPain={maxPain}
            bigCEWall={bigCEWall}
            bigPEWall={bigPEWall}
            strikeGap={strikeGap}
          />
        ))}
      </div>

      <div style={{
        display: "flex",
        gap: SPACE.SM,
        fontSize: 9,
        color: "var(--text-secondary, #888)",
        flexWrap: "wrap",
        paddingTop: SPACE.XS,
        borderTop: "1px solid rgba(255,255,255,0.06)",
      }}>
        <span>🛡 PE Wall</span>
        <span>🧱 CE Wall</span>
        <span>🎯 Max Pain</span>
        <span>◀ Darker = More OI</span>
        <span style={{ color: YELLOW }}>— Live Spot</span>
      </div>

      <div style={{
        display: "flex",
        justifyContent: "space-between",
        fontSize: 11,
        color: "var(--text-secondary, #888)",
        padding: SPACE.XS,
        background: "rgba(255,255,255,0.02)",
        borderRadius: RADIUS.SM,
      }}>
        <span>Total CE: <b style={{ color: RED }}>{(totalCE / 10000000).toFixed(1)}Cr</b></span>
        <span>Total PE: <b style={{ color: GREEN }}>{(totalPE / 10000000).toFixed(1)}Cr</b></span>
        <span>Bias: <b style={{ color: pcr > 1 ? GREEN : RED }}>
          {pcr > 1.2 ? "BULL (PE heavy)" : pcr < 0.8 ? "BEAR (CE heavy)" : "NEUTRAL"}
        </b></span>
      </div>
    </div>
  );
}

function MiniPill({ label, value, color }) {
  return (
    <div style={{
      padding: "2px 8px",
      background: color + "22",
      border: `1px solid ${color}44`,
      borderRadius: RADIUS.SM,
      fontSize: 10,
      fontFamily: FONT.MONO || "monospace",
    }}>
      <span style={{ color: "var(--text-secondary, #999)" }}>{label}:</span>{" "}
      <b style={{ color }}>{typeof value === "number" ? value.toFixed(0) : value}</b>
    </div>
  );
}

export default function OIHeatmap({ live }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SPACE.SM }}>
      <div style={{
        fontSize: 12,
        fontWeight: 700,
        color: "var(--text-secondary, #888)",
        letterSpacing: 1.5,
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}>
        🔥 LIVE OI HEATMAP
        <span style={{
          padding: "2px 6px",
          background: BLUE + "22",
          color: BLUE,
          borderRadius: 4,
          fontSize: 9,
          letterSpacing: 1,
        }}>
          REFRESHES 5s
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
        <IndexHeatmap index="NIFTY" live={live} />
        <IndexHeatmap index="BANKNIFTY" live={live} />
      </div>
    </div>
  );
}
