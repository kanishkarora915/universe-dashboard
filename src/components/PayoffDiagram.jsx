import { useState, useMemo } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS } from "../theme";

/**
 * Interactive SVG payoff diagram for option strategies.
 * Shows P&L curve across spot prices with hover tooltip + live spot marker.
 *
 * Props:
 *   strikes: array of {strike, ceLTP, peLTP, type: 'CE' or 'PE'}
 *   spot: current spot price
 *   strategy: {type: 'LONG_CALL'|'LONG_PUT'|'LONG_STRADDLE'|'LONG_STRANGLE', ...}
 *   lotSize: lot size for scaling (default 75 for NIFTY)
 */

function computePayoff(strategy, spotPrice) {
  // Returns P&L at given spot price for a strategy
  if (!strategy) return 0;
  const s = spotPrice;

  if (strategy.type === "LONG_CALL") {
    // max(spot - strike, 0) - premium
    return Math.max(s - strategy.strike, 0) - strategy.cost;
  }
  if (strategy.type === "LONG_PUT") {
    return Math.max(strategy.strike - s, 0) - strategy.cost;
  }
  if (strategy.type === "LONG_STRADDLE") {
    return Math.max(s - strategy.strike, 0) + Math.max(strategy.strike - s, 0) - strategy.cost;
  }
  if (strategy.type === "LONG_STRANGLE") {
    const [lowK, highK] = strategy.strikes || [strategy.strike, strategy.strike];
    return Math.max(s - highK, 0) + Math.max(lowK - s, 0) - strategy.cost;
  }
  return 0;
}

export default function PayoffDiagram({ strategies = [], spot = 0, lotSize = 75, height = 260 }) {
  const { theme } = useTheme();
  const [hoverX, setHoverX] = useState(null);

  const primary = strategies[0];
  if (!primary || !spot) {
    return (
      <div style={{ color: theme.TEXT_DIM, textAlign: "center", padding: SPACE.XXL }}>
        No strategy to plot
      </div>
    );
  }

  // Compute range: ±5% of spot
  const range = spot * 0.05;
  const minSpot = Math.floor((spot - range) / 10) * 10;
  const maxSpot = Math.ceil((spot + range) / 10) * 10;
  const steps = 100;
  const stepSize = (maxSpot - minSpot) / steps;

  // Generate points for each strategy
  const seriesData = strategies.slice(0, 4).map((st, i) => {
    const pts = [];
    for (let k = 0; k <= steps; k++) {
      const price = minSpot + k * stepSize;
      const pnl = computePayoff(st, price) * lotSize;
      pts.push({ spot: price, pnl });
    }
    return { strategy: st, pts, color: [theme.ACCENT, theme.PURPLE, theme.AMBER, theme.CYAN][i] };
  });

  // Find global P&L range for y-axis — guard against zero range
  const allPnls = seriesData.flatMap((s) => s.pts.map((p) => p.pnl));
  const minPnl = Math.min(...allPnls, 0);
  const maxPnl = Math.max(...allPnls, 0);
  const padPnl = Math.max(Math.abs(minPnl), Math.abs(maxPnl), 1) * 0.1;
  let yMin = minPnl - padPnl;
  let yMax = maxPnl + padPnl;
  // Division-by-zero guard: if range collapses to zero, force non-zero span
  if (yMax === yMin) {
    yMax = yMin + 1;
  }

  // SVG dimensions
  const w = 600;
  const h = height;
  const pad = { top: 20, right: 50, bottom: 40, left: 70 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  const xScale = (s) => pad.left + ((s - minSpot) / (maxSpot - minSpot)) * plotW;
  const yScale = (p) => pad.top + ((yMax - p) / (yMax - yMin)) * plotH;

  const zeroY = yScale(0);

  // Hover data
  const hoverSpot = hoverX != null ? minSpot + (hoverX / plotW) * (maxSpot - minSpot) : null;

  return (
    <div style={{ width: "100%" }}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: "auto", display: "block" }}>
        {/* Grid */}
        {[0.25, 0.5, 0.75].map((frac) => (
          <line
            key={frac}
            x1={pad.left}
            y1={pad.top + plotH * frac}
            x2={w - pad.right}
            y2={pad.top + plotH * frac}
            stroke={theme.BORDER}
            strokeWidth={0.5}
            strokeDasharray="2 4"
          />
        ))}

        {/* Zero line */}
        <line
          x1={pad.left}
          y1={zeroY}
          x2={w - pad.right}
          y2={zeroY}
          stroke={theme.TEXT_DIM}
          strokeWidth={1}
          strokeDasharray="4 4"
        />

        {/* Strategy curves */}
        {seriesData.map((s, i) => {
          const path = s.pts
            .map((p, idx) => `${idx === 0 ? "M" : "L"}${xScale(p.spot)},${yScale(p.pnl)}`)
            .join(" ");
          return (
            <g key={i}>
              <path d={path} fill="none" stroke={s.color} strokeWidth={2.5} opacity={0.9} />
              {/* Fill zones */}
              {s.pts.length > 0 && (
                <path
                  d={`${path} L${xScale(s.pts[s.pts.length - 1].spot)},${zeroY} L${xScale(s.pts[0].spot)},${zeroY} Z`}
                  fill={s.color}
                  opacity={0.08}
                />
              )}
            </g>
          );
        })}

        {/* Current spot marker */}
        <line
          x1={xScale(spot)}
          y1={pad.top}
          x2={xScale(spot)}
          y2={pad.top + plotH}
          stroke={theme.GREEN}
          strokeWidth={1.5}
          opacity={0.8}
        />
        <text
          x={xScale(spot)}
          y={pad.top + plotH + 28}
          fill={theme.GREEN}
          fontSize={10}
          fontWeight={700}
          fontFamily={FONT.MONO}
          textAnchor="middle"
        >
          SPOT {spot.toFixed(0)}
        </text>

        {/* Axis labels */}
        <text x={pad.left - 5} y={pad.top} fill={theme.TEXT_DIM} fontSize={10} fontFamily={FONT.MONO} textAnchor="end">
          {yMax > 0 ? `+₹${Math.round(yMax / 1000)}K` : Math.round(yMax)}
        </text>
        <text x={pad.left - 5} y={zeroY + 4} fill={theme.TEXT_DIM} fontSize={10} fontFamily={FONT.MONO} textAnchor="end">
          ₹0
        </text>
        <text x={pad.left - 5} y={pad.top + plotH} fill={theme.TEXT_DIM} fontSize={10} fontFamily={FONT.MONO} textAnchor="end">
          {yMin < 0 ? `-₹${Math.round(Math.abs(yMin) / 1000)}K` : Math.round(yMin)}
        </text>
        <text x={pad.left} y={h - 6} fill={theme.TEXT_DIM} fontSize={10} fontFamily={FONT.MONO}>
          {minSpot.toFixed(0)}
        </text>
        <text x={w - pad.right} y={h - 6} fill={theme.TEXT_DIM} fontSize={10} fontFamily={FONT.MONO} textAnchor="end">
          {maxSpot.toFixed(0)}
        </text>

        {/* Hover overlay */}
        <rect
          x={pad.left}
          y={pad.top}
          width={plotW}
          height={plotH}
          fill="transparent"
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const scale = rect.width / plotW;
            const localX = (e.clientX - rect.left) / scale;
            setHoverX(Math.max(0, Math.min(plotW, localX)));
          }}
          onMouseLeave={() => setHoverX(null)}
        />

        {hoverX != null && (
          <g>
            <line
              x1={pad.left + hoverX}
              y1={pad.top}
              x2={pad.left + hoverX}
              y2={pad.top + plotH}
              stroke={theme.ACCENT}
              strokeWidth={1}
              opacity={0.5}
              strokeDasharray="3 3"
            />
          </g>
        )}
      </svg>

      {/* Legend + hover readout */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: SPACE.MD, marginTop: SPACE.SM, padding: `0 ${SPACE.MD}px` }}>
        {seriesData.map((s, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ width: 16, height: 3, background: s.color, borderRadius: 2 }} />
            <span style={{ color: theme.TEXT, fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD }}>
              {s.strategy.name}
            </span>
          </div>
        ))}
      </div>

      {hoverSpot != null && (
        <div
          style={{
            marginTop: SPACE.SM,
            padding: `${SPACE.SM}px ${SPACE.MD}px`,
            background: theme.SURFACE_HI,
            borderRadius: RADIUS.SM,
            fontFamily: FONT.MONO,
            fontSize: TEXT_SIZE.MICRO,
            color: theme.TEXT,
          }}
        >
          At spot <strong style={{ color: theme.ACCENT }}>{hoverSpot.toFixed(0)}</strong> ({((hoverSpot - spot) / spot * 100).toFixed(2)}%):
          {seriesData.map((s, i) => {
            const pnl = computePayoff(s.strategy, hoverSpot) * lotSize;
            return (
              <span key={i} style={{ color: s.color, marginLeft: SPACE.MD, fontWeight: TEXT_WEIGHT.BOLD }}>
                {s.strategy.name}: ₹{pnl > 0 ? "+" : ""}{Math.round(pnl).toLocaleString("en-IN")}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
