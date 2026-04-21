/**
 * NextDayPredictor — Tomorrow's gap prediction + key levels.
 *
 * Shows for both NIFTY and BANKNIFTY:
 *  - Predicted gap direction (UP / DOWN / FLAT) with confidence
 *  - Key OI shifts today (what drives the prediction)
 *  - Expected range + support/resistance levels
 *  - Historical accuracy so far
 *  - Big CE/PE walls for tomorrow
 */

import React, { useEffect, useState } from "react";
import { SPACE, RADIUS, FONT } from "../theme";

const GREEN = "#10b981";
const RED = "#ef4444";
const YELLOW = "#f59e0b";
const BLUE = "#3b82f6";
const GRAY = "#6b7280";
const PURPLE = "#a855f7";

function useNextDayData(index) {
  const [prediction, setPrediction] = useState(null);
  const [levels, setLevels] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [predRes, nextRes] = await Promise.all([
          fetch(`/api/autopsy/gap-prediction/${index}`).then(r => r.ok ? r.json() : null).catch(() => null),
          fetch(`/api/nextday`).then(r => r.ok ? r.json() : null).catch(() => null),
        ]);
        if (cancelled) return;
        setPrediction(predRes);
        setLevels(nextRes?.[index.toLowerCase()] || null);
      } catch {}
      if (!cancelled) setLoading(false);
    };
    load();
    const iv = setInterval(load, 60000); // Refresh every minute
    return () => { cancelled = true; clearInterval(iv); };
  }, [index]);

  return { prediction, levels, loading };
}

function GapBadge({ type, confidence }) {
  const cfg = {
    "GAP_UP": { bg: GREEN + "22", border: GREEN, emoji: "⬆️", label: "GAP UP LIKELY" },
    "GAP_DOWN": { bg: RED + "22", border: RED, emoji: "⬇️", label: "GAP DOWN LIKELY" },
    "FLAT": { bg: GRAY + "22", border: GRAY, emoji: "➡️", label: "FLAT OPEN" },
    "NEED MORE DATA": { bg: GRAY + "11", border: GRAY, emoji: "⏳", label: "NEED MORE DATA" },
    "NEED DATA": { bg: GRAY + "11", border: GRAY, emoji: "⏳", label: "NEED DATA" },
  };
  const c = cfg[type] || cfg["FLAT"];
  return (
    <div style={{
      padding: `${SPACE.SM}px ${SPACE.MD}px`,
      background: c.bg,
      border: `1px solid ${c.border}`,
      borderRadius: RADIUS.MD,
      display: "flex",
      alignItems: "center",
      gap: SPACE.SM,
    }}>
      <span style={{ fontSize: 24 }}>{c.emoji}</span>
      <div>
        <div style={{ fontSize: 13, fontWeight: 800, color: c.border }}>{c.label}</div>
        {confidence > 0 && (
          <div style={{ fontSize: 11, color: "var(--text-secondary, #999)" }}>
            Confidence: {confidence}%
          </div>
        )}
      </div>
    </div>
  );
}

function Level({ value, label, color }) {
  return (
    <div style={{
      padding: `${SPACE.XS}px ${SPACE.SM}px`,
      background: "rgba(255,255,255,0.03)",
      border: `1px solid ${color}33`,
      borderRadius: RADIUS.SM,
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      gap: SPACE.SM,
    }}>
      <span style={{ fontSize: 11, color: "var(--text-secondary, #999)" }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 700, color, fontFamily: FONT.MONO || "monospace" }}>
        {typeof value === "number" ? value.toFixed(0) : value}
      </span>
    </div>
  );
}

function IndexNextDayCard({ index, prediction, levels }) {
  const gapType = prediction?.prediction || "NEED DATA";
  const conf = prediction?.confidence || 0;
  const recent = prediction?.recentGaps || [];
  const dataPoints = prediction?.dataPoints || 0;

  const bias = levels?.bias || "NEUTRAL";
  const pivot = levels?.pivot;
  const rangeHigh = levels?.rangeHigh;
  const rangeLow = levels?.rangeLow;
  const maxPain = levels?.maxPain;
  const bigCE = levels?.bigCEWall || "";
  const bigPE = levels?.bigPEWall || "";
  const resistance = levels?.resistance || [];
  const support = levels?.support || [];
  const unusual = levels?.unusual;

  const biasColor = bias.includes("BULL") ? GREEN : bias.includes("BEAR") ? RED : GRAY;

  return (
    <div style={{
      padding: SPACE.MD,
      background: "rgba(168, 85, 247, 0.06)",
      border: `1px solid ${PURPLE}33`,
      borderRadius: RADIUS.LG,
      display: "flex",
      flexDirection: "column",
      gap: SPACE.SM,
    }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-secondary, #888)", letterSpacing: 1 }}>
            {index} · TOMORROW
          </div>
          <div style={{ fontSize: 16, fontWeight: 700, color: biasColor, marginTop: 2 }}>
            Bias: {bias}
          </div>
        </div>
        <GapBadge type={gapType} confidence={conf} />
      </div>

      {/* Expected Range */}
      {rangeHigh && rangeLow && (
        <div style={{
          padding: SPACE.SM,
          background: "rgba(255,255,255,0.03)",
          borderRadius: RADIUS.MD,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          border: "1px dashed rgba(168,85,247,0.3)",
        }}>
          <div>
            <div style={{ fontSize: 10, color: "var(--text-secondary, #888)" }}>EXPECTED RANGE</div>
            <div style={{ fontSize: 14, fontWeight: 700, fontFamily: FONT.MONO || "monospace" }}>
              <span style={{ color: RED }}>{rangeLow?.toFixed(0)}</span>
              {" — "}
              <span style={{ color: GREEN }}>{rangeHigh?.toFixed(0)}</span>
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 10, color: "var(--text-secondary, #888)" }}>PIVOT</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: PURPLE, fontFamily: FONT.MONO || "monospace" }}>
              {pivot?.toFixed(0)}
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 10, color: "var(--text-secondary, #888)" }}>MAX PAIN</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: YELLOW, fontFamily: FONT.MONO || "monospace" }}>
              {maxPain?.toFixed(0)}
            </div>
          </div>
        </div>
      )}

      {/* Support + Resistance Levels */}
      {(resistance.length > 0 || support.length > 0) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.SM }}>
          <div>
            <div style={{ fontSize: 10, color: "var(--text-secondary, #888)", marginBottom: 4 }}>
              RESISTANCE
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              {resistance.slice(0, 3).map((r, i) => (
                <Level key={i} value={r.level} label={r.reason?.split("—")[0] || `R${i+1}`} color={RED} />
              ))}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: "var(--text-secondary, #888)", marginBottom: 4 }}>
              SUPPORT
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              {support.slice(0, 3).map((s, i) => (
                <Level key={i} value={s.level} label={s.reason?.split("—")[0] || `S${i+1}`} color={GREEN} />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Big Walls */}
      <div style={{ display: "flex", gap: SPACE.SM, flexWrap: "wrap" }}>
        {bigCE && (
          <div style={{
            flex: 1,
            padding: SPACE.XS + "px " + SPACE.SM + "px",
            background: "rgba(239,68,68,0.08)",
            border: `1px solid ${RED}44`,
            borderRadius: RADIUS.SM,
            fontSize: 11,
          }}>
            <span style={{ color: RED, fontWeight: 700 }}>CE Wall:</span>{" "}
            <span style={{ color: "var(--text-primary, #fff)" }}>{bigCE.replace(" OI", "")}</span>
          </div>
        )}
        {bigPE && (
          <div style={{
            flex: 1,
            padding: SPACE.XS + "px " + SPACE.SM + "px",
            background: "rgba(16,185,129,0.08)",
            border: `1px solid ${GREEN}44`,
            borderRadius: RADIUS.SM,
            fontSize: 11,
          }}>
            <span style={{ color: GREEN, fontWeight: 700 }}>PE Wall:</span>{" "}
            <span style={{ color: "var(--text-primary, #fff)" }}>{bigPE.replace(" OI", "")}</span>
          </div>
        )}
      </div>

      {/* Unusual / Key observation */}
      {unusual && (
        <div style={{
          padding: SPACE.SM,
          background: "rgba(245,158,11,0.08)",
          borderLeft: `3px solid ${YELLOW}`,
          borderRadius: 4,
          fontSize: 11,
          color: "var(--text-primary, #fff)",
        }}>
          ⚠️ {unusual}
        </div>
      )}

      {/* Recent gap history */}
      {recent.length > 0 && (
        <div style={{ marginTop: SPACE.XS }}>
          <div style={{ fontSize: 10, color: "var(--text-secondary, #888)", marginBottom: 4 }}>
            RECENT GAPS ({dataPoints} days tracked)
          </div>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {recent.slice(0, 5).map((r, i) => {
              const c = r.gapType === "GAP_UP" ? GREEN : r.gapType === "GAP_DOWN" ? RED : GRAY;
              return (
                <div key={i} title={r.date} style={{
                  padding: "2px 6px",
                  fontSize: 10,
                  background: c + "22",
                  color: c,
                  borderRadius: 3,
                  fontFamily: FONT.MONO || "monospace",
                }}>
                  {r.gapPct > 0 ? "+" : ""}{r.gapPct?.toFixed(2)}%
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function NextDayPredictor() {
  const nifty = useNextDayData("NIFTY");
  const bn = useNextDayData("BANKNIFTY");

  if (nifty.loading && bn.loading) {
    return (
      <div style={{
        padding: SPACE.MD,
        textAlign: "center",
        color: "var(--text-secondary, #888)",
        fontSize: 12,
      }}>
        Loading next day predictions...
      </div>
    );
  }

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
        🔮 NEXT DAY PREDICTION
        <span style={{
          padding: "2px 6px",
          background: PURPLE + "22",
          color: PURPLE,
          borderRadius: 4,
          fontSize: 9,
          letterSpacing: 1,
        }}>
          OI + EOD ANALYSIS
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
        <IndexNextDayCard index="NIFTY" prediction={nifty.prediction} levels={nifty.levels} />
        <IndexNextDayCard index="BANKNIFTY" prediction={bn.prediction} levels={bn.levels} />
      </div>
    </div>
  );
}
