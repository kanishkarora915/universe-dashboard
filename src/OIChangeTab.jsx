import { exportOIToPDF } from "./pdfExport";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const CARD = "#111118";
const BORDER = "#1E1E2E";

const fmt = (n) => n ? Math.round(n).toLocaleString("en-IN") : "0";
const fmtL = (n) => n ? `${(n / 100000).toFixed(1)}L` : "0";

export default function OIChangeTab({ oiData }) {
  if (!oiData || (!oiData.nifty && !oiData.banknifty)) {
    return (
      <div style={{ textAlign: "center", padding: 60, color: "#555" }}>
        <div style={{ fontSize: 40, marginBottom: 12 }}>📈</div>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8, color: "#888" }}>No OI Data</div>
        <div style={{ fontSize: 12 }}>Login to Kite for real-time OI change analysis</div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {["nifty", "banknifty"].map((key) => {
        const d = oiData[key];
        if (!d) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";

        return (
          <div key={key}>
            {/* Header + Export */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div>
                <span style={{ color: ACCENT, fontWeight: 900, fontSize: 16, letterSpacing: 1 }}>{label} OI CHANGE</span>
                <span style={{ color: "#444", fontSize: 11, marginLeft: 10 }}>LTP: {fmt(d.ltp)} | ATM: {fmt(d.atm)} | {d.timestamp}</span>
              </div>
              <button onClick={() => exportOIToPDF(oiData, label)} style={{
                background: ACCENT + "22", color: ACCENT, border: `1px solid ${ACCENT}44`,
                borderRadius: 8, padding: "5px 14px", cursor: "pointer", fontSize: 11, fontWeight: 700,
              }}>Export PDF</button>
            </div>

            {/* Summary Cards */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 10, marginBottom: 12 }}>
              <SumCard label="Total CE OI" value={fmtL(d.totalCEOI)} color="#ccc" />
              <SumCard label="Total PE OI" value={fmtL(d.totalPEOI)} color="#ccc" />
              <SumCard label="+ OI (CE+PE)" value={`+${fmtL(d.ceOIChangePos + d.peOIChangePos)}`} color={GREEN} />
              <SumCard label="- OI (CE+PE)" value={fmtL(d.ceOIChangeNeg + d.peOIChangeNeg)} color={RED} />
              <SumCard label="PCR" value={d.pcr} color={d.pcr > 1.15 ? GREEN : d.pcr < 0.8 ? RED : YELLOW} />
            </div>

            {/* OI Table */}
            <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, overflow: "hidden" }}>
              {/* Table Header */}
              <div style={{ display: "grid", gridTemplateColumns: "80px 1fr 1fr 1fr 1fr 1fr 1fr", gap: 0,
                background: "#0D0D15", padding: "8px 12px", fontSize: 10, fontWeight: 700, color: "#555", letterSpacing: 1 }}>
                <div>STRIKE</div>
                <div style={{ color: RED }}>CE OI</div>
                <div style={{ color: RED }}>CE CHG</div>
                <div style={{ color: RED }}>CE LTP</div>
                <div style={{ color: GREEN }}>PE LTP</div>
                <div style={{ color: GREEN }}>PE CHG</div>
                <div style={{ color: GREEN }}>PE OI</div>
              </div>

              {/* Table Rows */}
              {d.strikes.map((s, i) => (
                <div key={s.strike} style={{
                  display: "grid", gridTemplateColumns: "80px 1fr 1fr 1fr 1fr 1fr 1fr", gap: 0,
                  padding: "6px 12px", fontSize: 12, borderTop: `1px solid ${BORDER}`,
                  background: s.isATM ? ACCENT + "11" : i % 2 === 0 ? "transparent" : "#0A0A12",
                }}>
                  <div style={{ color: s.isATM ? ACCENT : "#888", fontWeight: s.isATM ? 900 : 400 }}>
                    {fmt(s.strike)} {s.isATM && "◆"}
                  </div>
                  <div style={{ color: "#ccc" }}>{fmtL(s.ceOI)}</div>
                  <OIChangeCell value={s.ceOIChange} />
                  <div style={{ color: RED }}>{s.ceLTP?.toFixed(1) || "-"}</div>
                  <div style={{ color: GREEN }}>{s.peLTP?.toFixed(1) || "-"}</div>
                  <OIChangeCell value={s.peOIChange} />
                  <div style={{ color: "#ccc" }}>{fmtL(s.peOI)}</div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SumCard({ label, value, color }) {
  return (
    <div style={{ background: "#0D0D15", borderRadius: 8, padding: "8px 12px", textAlign: "center" }}>
      <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
      <div style={{ color, fontWeight: 700, fontSize: 14 }}>{value}</div>
    </div>
  );
}

function OIChangeCell({ value }) {
  if (!value || value === 0) return <div style={{ color: "#333" }}>-</div>;
  const isPos = value > 0;
  return (
    <div style={{ color: isPos ? GREEN : RED, fontWeight: 600 }}>
      {isPos ? "+" : ""}{fmtL(value)}
    </div>
  );
}
