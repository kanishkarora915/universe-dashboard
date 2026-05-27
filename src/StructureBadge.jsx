/**
 * StructureBadge — live market structure indicator
 *
 * Phase 6 of Option Y (2026-05-27).
 *
 * Shows per-index trend (UPTREND/DOWNTREND/CHOP) with per-timeframe
 * alignment indicators (5m/15m/1h). Polls /api/structure/state every
 * 30s. Color-coded: green=UPTREND, red=DOWNTREND, orange=CHOP, grey=unknown.
 *
 * Usage:
 *   <StructureBadge indices={["NIFTY", "BANKNIFTY"]} />
 */

import { useEffect, useState } from "react";

const COLORS = {
  UPTREND: "#1a8a2e",
  DOWNTREND: "#cc2020",
  CHOP: "#FF9F0A",
  UNKNOWN: "#777",
};

const SHORT = {
  UPTREND: "UP",
  DOWNTREND: "DN",
  CHOP: "CHOP",
  UNKNOWN: "—",
};

function _colorFor(verdict) {
  return COLORS[verdict] || COLORS.UNKNOWN;
}

function _short(verdict) {
  return SHORT[verdict] || SHORT.UNKNOWN;
}

export default function StructureBadge({ indices = ["NIFTY", "BANKNIFTY"] }) {
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const fetchAll = async () => {
      const out = {};
      for (const idx of indices) {
        try {
          const r = await fetch(`/api/structure/state?idx=${idx}`);
          if (r.ok) {
            const j = await r.json();
            if (!j.error) out[idx] = j;
          }
        } catch (e) {
          // silent — fall back to empty
        }
      }
      if (alive) {
        setData(out);
        setLoading(false);
      }
    };
    fetchAll();
    // 30s poll — structure changes slowly (5m+ candles)
    const iv = setInterval(() => {
      if (document.visibilityState === "visible") fetchAll();
    }, 30000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return (
      <div style={{
        padding: "6px 10px", fontSize: 11, color: "#777",
        fontStyle: "italic",
      }}>
        Loading structure…
      </div>
    );
  }

  if (Object.keys(data).length === 0) {
    return null; // silent — not configured / off / no data
  }

  return (
    <div style={{
      display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center",
      padding: "4px 0",
    }}>
      {indices.map((idx) => {
        const d = data[idx];
        if (!d || !d.structures) return null;
        const tf5 = d.structures["5m"]?.verdict || "UNKNOWN";
        const tf15 = d.structures["15m"]?.verdict || "UNKNOWN";
        const tf1h = d.structures["1h"]?.verdict || "UNKNOWN";
        const align = d.alignment || {};
        const direction = align.direction || "MIXED";
        const conviction = align.conviction || "LOW";
        const mainColor =
          direction === "BULL" ? COLORS.UPTREND :
          direction === "BEAR" ? COLORS.DOWNTREND :
          "#777";

        return (
          <div
            key={idx}
            title={align.reason || ""}
            style={{
              display: "flex", flexDirection: "column", gap: 2,
              padding: "6px 10px",
              background: `${mainColor}11`,
              border: `1px solid ${mainColor}55`,
              borderRadius: 8,
              minWidth: 130,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 10, color: "#888", letterSpacing: 0.5, fontWeight: 700 }}>
                {idx}
              </span>
              <span style={{
                fontSize: 11, fontWeight: 900, color: mainColor,
                letterSpacing: 0.5,
              }}>
                {direction === "BULL" ? "▲ BULL" : direction === "BEAR" ? "▼ BEAR" : "— MIXED"}
                {" "}
                <span style={{ fontSize: 9, color: "#888", fontWeight: 500 }}>
                  ({conviction})
                </span>
              </span>
            </div>
            <div style={{ display: "flex", gap: 4, marginTop: 2 }}>
              {[
                ["5m", tf5], ["15m", tf15], ["1h", tf1h],
              ].map(([tf, verdict]) => (
                <span
                  key={tf}
                  title={`${tf}: ${verdict}`}
                  style={{
                    fontSize: 9, padding: "2px 6px",
                    background: `${_colorFor(verdict)}22`,
                    color: _colorFor(verdict),
                    borderRadius: 3, fontWeight: 700,
                    letterSpacing: 0.3,
                  }}
                >
                  {tf}:{_short(verdict)}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
