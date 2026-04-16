const ACCENT = "#0A84FF";
const BORDER = "#1E1E2E";

const fmt = (n) => (n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0");

function getIntensity(value, max) {
  if (!max || !value) return 0;
  return Math.min(Math.abs(value) / max, 1);
}

export default function OIHeatmap({ oiData, index = "nifty" }) {
  const data = oiData?.[index];
  if (!data || !data.strikes) return null;

  const strikes = data.strikes || [];
  const maxCE = Math.max(...strikes.map((s) => s.ceOI || 0), 1);
  const maxPE = Math.max(...strikes.map((s) => s.peOI || 0), 1);

  return (
    <div style={{ overflowX: "auto" }}>
      <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", marginBottom: 6 }}>
        OI HEATMAP — {index.toUpperCase()}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {/* Header */}
        <div style={{ display: "flex", gap: 1, fontSize: 9, color: "#555", fontWeight: 700 }}>
          <div style={{ width: 60, textAlign: "center", padding: 4 }}>CE OI</div>
          <div style={{ width: 50, textAlign: "center", padding: 4 }}>CE Chg</div>
          <div style={{ width: 65, textAlign: "center", padding: 4, color: ACCENT }}>STRIKE</div>
          <div style={{ width: 50, textAlign: "center", padding: 4 }}>PE Chg</div>
          <div style={{ width: 60, textAlign: "center", padding: 4 }}>PE OI</div>
        </div>

        {strikes.map((s) => {
          const ceInt = getIntensity(s.ceOI, maxCE);
          const peInt = getIntensity(s.peOI, maxPE);
          const ceChgPos = (s.ceOIChange || 0) > 0;
          const peChgPos = (s.peOIChange || 0) > 0;

          return (
            <div
              key={s.strike}
              style={{
                display: "flex", gap: 1,
                background: s.isATM ? ACCENT + "11" : "transparent",
                borderRadius: 2,
              }}
            >
              {/* CE OI — Red shades (resistance) */}
              <div style={{
                width: 60, textAlign: "center", padding: "3px 4px", fontSize: 10,
                background: `rgba(255, 69, 58, ${ceInt * 0.4})`,
                color: ceInt > 0.5 ? "#fff" : "#888",
                fontWeight: ceInt > 0.6 ? 700 : 400,
                borderRadius: 2,
              }}>
                {fmt(s.ceOI)}
              </div>

              {/* CE Change */}
              <div style={{
                width: 50, textAlign: "center", padding: "3px 4px", fontSize: 10,
                color: ceChgPos ? "#FF453A" : "#30D158",
                fontWeight: Math.abs(s.ceOIChange || 0) > 100000 ? 700 : 400,
                background: ceChgPos ? "rgba(255,69,58,0.08)" : "rgba(48,209,88,0.08)",
                borderRadius: 2,
              }}>
                {s.ceOIChange ? `${s.ceOIChange > 0 ? "+" : ""}${fmt(s.ceOIChange)}` : "—"}
              </div>

              {/* Strike */}
              <div style={{
                width: 65, textAlign: "center", padding: "3px 4px", fontSize: 11,
                fontWeight: s.isATM ? 900 : 600,
                color: s.isATM ? ACCENT : "#ccc",
                borderRadius: 2,
              }}>
                {s.strike} {s.isATM ? "★" : ""}
              </div>

              {/* PE Change */}
              <div style={{
                width: 50, textAlign: "center", padding: "3px 4px", fontSize: 10,
                color: peChgPos ? "#30D158" : "#FF453A",
                fontWeight: Math.abs(s.peOIChange || 0) > 100000 ? 700 : 400,
                background: peChgPos ? "rgba(48,209,88,0.08)" : "rgba(255,69,58,0.08)",
                borderRadius: 2,
              }}>
                {s.peOIChange ? `${s.peOIChange > 0 ? "+" : ""}${fmt(s.peOIChange)}` : "—"}
              </div>

              {/* PE OI — Green shades (support) */}
              <div style={{
                width: 60, textAlign: "center", padding: "3px 4px", fontSize: 10,
                background: `rgba(48, 209, 88, ${peInt * 0.4})`,
                color: peInt > 0.5 ? "#fff" : "#888",
                fontWeight: peInt > 0.6 ? 700 : 400,
                borderRadius: 2,
              }}>
                {fmt(s.peOI)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
