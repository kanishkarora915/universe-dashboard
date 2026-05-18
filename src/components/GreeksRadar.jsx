import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT } from "../theme";

/**
 * SVG radar chart comparing Greeks across strikes.
 * Normalizes each Greek to 0-1 for radar display.
 */

const AXES = [
  { key: "delta", label: "Delta", max: 1 },
  { key: "gamma", label: "Gamma", max: 0.03 },
  { key: "theta", label: "Theta", max: 10, abs: true },
  { key: "vega", label: "Vega", max: 20 },
  { key: "oi", label: "OI", max: 1000000 },
  { key: "volume", label: "Vol", max: 500000 },
];

export default function GreeksRadar({ strikes = [], size = 260 }) {
  const { theme } = useTheme();
  if (!strikes.length) return null;

  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 40;

  // Build normalized values
  const COLORS = [theme.ACCENT, theme.PURPLE, theme.AMBER, theme.CYAN];
  const data = strikes.slice(0, 4).map((s, idx) => {
    const greeks = s.greeks || {};
    // Use CE side by default
    const values = AXES.map((ax) => {
      let raw = 0;
      if (ax.key === "delta") raw = Math.abs(greeks.deltaCE || 0);
      else if (ax.key === "gamma") raw = greeks.gammaCE || 0;
      else if (ax.key === "theta") raw = ax.abs ? Math.abs(greeks.thetaCE || 0) : (greeks.thetaCE || 0);
      else if (ax.key === "vega") raw = greeks.vegaCE || 0;
      else if (ax.key === "oi") raw = s.ceOI || 0;
      else if (ax.key === "volume") raw = s.ceVol || 0;
      return Math.max(0, Math.min(1, raw / ax.max));
    });
    return { label: `${s.strike}`, values, color: COLORS[idx] };
  });

  const getPoint = (axisIdx, value) => {
    const angle = (Math.PI * 2 * axisIdx) / AXES.length - Math.PI / 2;
    const r = radius * value;
    return { x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r };
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
      <svg width={size} height={size}>
        {/* Rings */}
        {[0.25, 0.5, 0.75, 1].map((frac) => (
          <polygon
            key={frac}
            points={AXES.map((_, i) => {
              const p = getPoint(i, frac);
              return `${p.x},${p.y}`;
            }).join(" ")}
            fill="none"
            stroke={theme.BORDER}
            strokeWidth={0.6}
            opacity={0.6}
          />
        ))}

        {/* Axes */}
        {AXES.map((ax, i) => {
          const outer = getPoint(i, 1);
          const labelP = getPoint(i, 1.18);
          return (
            <g key={ax.key}>
              <line x1={cx} y1={cy} x2={outer.x} y2={outer.y} stroke={theme.BORDER} strokeWidth={0.6} />
              <text
                x={labelP.x}
                y={labelP.y}
                fill={theme.TEXT_DIM}
                fontSize={9}
                fontFamily={FONT.UI}
                fontWeight={TEXT_WEIGHT.BOLD}
                textAnchor={labelP.x > cx + 1 ? "start" : labelP.x < cx - 1 ? "end" : "middle"}
                dominantBaseline="middle"
              >
                {ax.label}
              </text>
            </g>
          );
        })}

        {/* Data polygons */}
        {data.map((d, i) => {
          const pts = d.values.map((v, idx) => {
            const p = getPoint(idx, v);
            return `${p.x},${p.y}`;
          }).join(" ");
          return (
            <g key={i}>
              <polygon points={pts} fill={d.color} fillOpacity={0.15} stroke={d.color} strokeWidth={2} />
              {d.values.map((v, idx) => {
                const p = getPoint(idx, v);
                return <circle key={idx} cx={p.x} cy={p.y} r={2.5} fill={d.color} />;
              })}
            </g>
          );
        })}
      </svg>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center" }}>
        {data.map((d, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 12, height: 3, background: d.color, borderRadius: 2 }} />
            <span style={{ color: theme.TEXT, fontSize: TEXT_SIZE.MICRO, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>
              {d.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
