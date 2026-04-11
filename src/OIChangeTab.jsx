import { useState, useEffect, useCallback } from "react";
import { exportOIToPDF } from "./pdfExport";
import { fetchExpiries, fetchExpiryChain } from "./api";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const CARD = "#111118";
const BORDER = "#1E1E2E";

const fmt = (n) => n ? Math.round(n).toLocaleString("en-IN") : "0";
const fmtL = (n) => n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0";

export default function OIChangeTab({ oiData }) {
  const [niftyExpiries, setNiftyExpiries] = useState([]);
  const [bnExpiries, setBnExpiries] = useState([]);
  const [selectedNiftyExpiry, setSelectedNiftyExpiry] = useState("");
  const [selectedBnExpiry, setSelectedBnExpiry] = useState("");
  const [niftyExpiryData, setNiftyExpiryData] = useState(null);
  const [bnExpiryData, setBnExpiryData] = useState(null);
  const [loading, setLoading] = useState({});
  const [timeline, setTimeline] = useState(null);

  // Fetch available expiries on mount
  useEffect(() => {
    fetchExpiries("NIFTY").then(data => {
      if (Array.isArray(data) && data.length > 0) {
        setNiftyExpiries(data);
        const current = data.find(e => e.isCurrent);
        if (current) setSelectedNiftyExpiry(current.date);
      }
    }).catch(() => {});
    fetchExpiries("BANKNIFTY").then(data => {
      if (Array.isArray(data) && data.length > 0) {
        setBnExpiries(data);
        const current = data.find(e => e.isCurrent);
        if (current) setSelectedBnExpiry(current.date);
      }
    }).catch(() => {});
  }, []);

  // Fetch expiry chain when selection changes
  const loadExpiryChain = useCallback(async (index, expiry) => {
    if (!expiry) return;
    const key = index.toLowerCase();
    // If current expiry, use live oiData
    const expiries = key === "nifty" ? niftyExpiries : bnExpiries;
    const currentExp = expiries.find(e => e.isCurrent);
    if (currentExp && expiry === currentExp.date) {
      if (key === "nifty") setNiftyExpiryData(null);
      else setBnExpiryData(null);
      return;
    }
    setLoading(prev => ({ ...prev, [key]: true }));
    try {
      const data = await fetchExpiryChain(index, expiry);
      if (data && !data.error) {
        if (key === "nifty") setNiftyExpiryData(data);
        else setBnExpiryData(data);
      }
    } catch {}
    setLoading(prev => ({ ...prev, [key]: false }));
  }, [niftyExpiries, bnExpiries]);

  useEffect(() => { loadExpiryChain("NIFTY", selectedNiftyExpiry); }, [selectedNiftyExpiry, loadExpiryChain]);
  useEffect(() => { loadExpiryChain("BANKNIFTY", selectedBnExpiry); }, [selectedBnExpiry, loadExpiryChain]);

  // Fetch OI timeline every 30s
  useEffect(() => {
    const load = () => fetch("/api/oi-timeline").then(r => r.ok ? r.json() : null).then(d => d && setTimeline(d)).catch(() => {});
    load();
    const iv = setInterval(load, 30000);
    return () => clearInterval(iv);
  }, []);

  if (!oiData && !niftyExpiryData && !bnExpiryData) {
    return (
      <div style={{ textAlign: "center", padding: 60, color: "#555" }}>
        <div style={{ fontSize: 40, marginBottom: 12 }}>📈</div>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8, color: "#888" }}>No OI Data</div>
        <div style={{ fontSize: 12 }}>Login to Kite for real-time OI change analysis</div>
      </div>
    );
  }

  const renderIndex = (key, expiries, selectedExpiry, setSelectedExpiry, expiryData, isLoading) => {
    // Use expiry-specific data if non-current expiry selected, else use live oiData
    const isCurrentExpiry = !expiryData;
    const d = isCurrentExpiry ? oiData?.[key] : expiryData;
    if (!d) return null;
    const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";

    return (
      <div key={key}>
        {/* Header + Expiry Selector + Export */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ color: ACCENT, fontWeight: 900, fontSize: 16, letterSpacing: 1 }}>{label} OI CHANGE</span>
            <span style={{ color: "#444", fontSize: 11 }}>LTP: {fmt(d.ltp)} | ATM: {fmt(d.atm)}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {/* Expiry Selector */}
            {expiries.length > 0 && (
              <select
                value={selectedExpiry}
                onChange={(e) => setSelectedExpiry(e.target.value)}
                style={{
                  background: "#0D0D15", color: ORANGE, border: `1px solid ${ORANGE}44`,
                  borderRadius: 8, padding: "5px 10px", fontSize: 11, fontWeight: 700,
                  cursor: "pointer", outline: "none",
                }}
              >
                {expiries.map(exp => (
                  <option key={exp.date} value={exp.date}>
                    {exp.isCurrent ? `${exp.date} (Live)` : exp.date}
                  </option>
                ))}
              </select>
            )}
            <span style={{ color: "#444", fontSize: 10 }}>{d.timestamp}</span>
            <button onClick={() => exportOIToPDF(oiData || {[key]: d}, label)} style={{
              background: ACCENT + "22", color: ACCENT, border: `1px solid ${ACCENT}44`,
              borderRadius: 8, padding: "5px 14px", cursor: "pointer", fontSize: 11, fontWeight: 700,
            }}>Export</button>
          </div>
        </div>

        {isLoading ? (
          <div style={{ textAlign: "center", padding: 40, color: ORANGE }}>Loading expiry data...</div>
        ) : (
          <>
            {/* Expiry badge */}
            {!isCurrentExpiry && (
              <div style={{ background: ORANGE + "11", border: `1px solid ${ORANGE}33`, borderRadius: 8, padding: "6px 12px", marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: ORANGE, fontWeight: 900, fontSize: 11 }}>EXPIRY: {d.expiry}</span>
                <span style={{ color: "#888", fontSize: 10 }}>Data fetched via REST API (snapshot, not live tick)</span>
              </div>
            )}

            {/* Summary Cards */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 10, marginBottom: 12 }}>
              <SumCard label="Total CE OI" value={fmtL(d.totalCEOI)} color="#ccc" />
              <SumCard label="Total PE OI" value={fmtL(d.totalPEOI)} color="#ccc" />
              <SumCard label="+ OI (CE+PE)" value={`+${fmtL((d.ceOIChangePos || 0) + (d.peOIChangePos || 0))}`} color={GREEN} />
              <SumCard label="- OI (CE+PE)" value={fmtL((d.ceOIChangeNeg || 0) + (d.peOIChangeNeg || 0))} color={RED} />
              <SumCard label="PCR" value={d.pcr} color={d.pcr > 1.15 ? GREEN : d.pcr < 0.8 ? RED : YELLOW} />
            </div>

            {/* OI Table */}
            <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, overflow: "hidden", marginBottom: 20 }}>
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
              {(d.strikes || []).map((s, i) => (
                <div key={s.strike} style={{
                  display: "grid", gridTemplateColumns: "80px 1fr 1fr 1fr 1fr 1fr 1fr", gap: 0,
                  padding: "6px 12px", fontSize: 12, borderTop: `1px solid ${BORDER}`,
                  background: s.isATM ? ACCENT + "11" : i % 2 === 0 ? "transparent" : "#0A0A12",
                }}>
                  <div style={{ color: s.isATM ? ACCENT : "#888", fontWeight: s.isATM ? 900 : 400 }}>
                    {fmt(s.strike)} {s.isATM && "\u25C6"}
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
          </>
        )}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {renderIndex("nifty", niftyExpiries, selectedNiftyExpiry, setSelectedNiftyExpiry, niftyExpiryData, loading.nifty)}
      {renderIndex("banknifty", bnExpiries, selectedBnExpiry, setSelectedBnExpiry, bnExpiryData, loading.banknifty)}

      {/* ── 15-MIN OI TIMELINE + NEXT DAY PREDICTION ── */}
      {timeline && ["nifty", "banknifty"].map(key => {
        const t = timeline[key];
        if (!t) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
        const pred = t.prediction || {};
        const predColor = pred.direction?.includes("UP") || pred.direction?.includes("BULLISH") ? GREEN : pred.direction?.includes("DOWN") || pred.direction?.includes("BEARISH") ? RED : YELLOW;

        return (
          <div key={key} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {/* Current OI Totals */}
            <div style={{ background: "#0D0D15", borderRadius: 10, padding: "12px", border: `1px solid ${BORDER}` }}>
              <div style={{ color: ACCENT, fontWeight: 900, fontSize: 13, marginBottom: 10 }}>{label} — TOTAL OI</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
                <SumCard label="TOTAL CE OI" value={fmtL(t.current?.ceOI)} color={RED} />
                <SumCard label="TOTAL PE OI" value={fmtL(t.current?.peOI)} color={GREEN} />
                <SumCard label="TOTAL OI" value={fmtL(t.current?.total)} color="#ccc" />
                <SumCard label="PCR" value={t.current?.pcr} color={t.current?.pcr > 1.2 ? GREEN : t.current?.pcr < 0.8 ? RED : YELLOW} />
              </div>
            </div>

            {/* 15-Min Timeline Table */}
            {t.timeline?.length > 0 && (
              <div style={{ background: "#0D0D15", borderRadius: 10, padding: "12px", border: `1px solid ${PURPLE}33` }}>
                <div style={{ color: PURPLE, fontWeight: 900, fontSize: 13, marginBottom: 10 }}>{label} — 15-MIN OI TIMELINE</div>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
                    <thead>
                      <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                        <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Time</th>
                        <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE OI</th>
                        <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE Chg</th>
                        <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE OI</th>
                        <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE Chg</th>
                        <th style={{ padding: "5px 6px", color: "#ccc", textAlign: "right" }}>Total</th>
                        <th style={{ padding: "5px 6px", color: "#ccc", textAlign: "right" }}>Net Chg</th>
                        <th style={{ padding: "5px 6px", color: YELLOW, textAlign: "right" }}>PCR</th>
                        <th style={{ padding: "5px 6px", color: "#ccc", textAlign: "right" }}>Spot</th>
                      </tr>
                    </thead>
                    <tbody>
                      {t.timeline.map((row, i) => (
                        <tr key={i} style={{ borderBottom: `1px solid ${BORDER}33` }}>
                          <td style={{ padding: "4px 6px", color: ACCENT, fontWeight: 700 }}>{row.time}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: "#ccc" }}>{fmtL(row.ceOI)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: row.ceChange > 0 ? GREEN : row.ceChange < 0 ? RED : "#555", fontWeight: 700 }}>{row.ceChange > 0 ? "+" : ""}{fmtL(row.ceChange)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: "#ccc" }}>{fmtL(row.peOI)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: row.peChange > 0 ? GREEN : row.peChange < 0 ? RED : "#555", fontWeight: 700 }}>{row.peChange > 0 ? "+" : ""}{fmtL(row.peChange)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: "#888" }}>{fmtL(row.total)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: row.netChange > 0 ? GREEN : row.netChange < 0 ? RED : "#555", fontWeight: 700 }}>{row.netChange > 0 ? "+" : ""}{fmtL(row.netChange)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: row.pcr > 1.2 ? GREEN : row.pcr < 0.8 ? RED : YELLOW }}>{row.pcr}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: "#888" }}>{row.spot?.toLocaleString("en-IN")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* OI Behaviors */}
            {t.behaviors?.length > 0 && (
              <div style={{ background: ORANGE + "08", borderRadius: 10, padding: "10px 12px", border: `1px solid ${ORANGE}33` }}>
                <div style={{ color: ORANGE, fontWeight: 700, fontSize: 12, marginBottom: 6 }}>OI BEHAVIORS DETECTED</div>
                {t.behaviors.map((b, i) => (
                  <div key={i} style={{ color: "#ccc", fontSize: 11, marginBottom: 3 }}>{"\u26A0"} {b}</div>
                ))}
              </div>
            )}

            {/* Next Day Prediction */}
            {pred.direction && pred.direction !== "UNKNOWN" && (
              <div style={{ background: predColor + "08", borderRadius: 10, padding: "12px 14px", border: `1px solid ${predColor}44` }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div>
                    <div style={{ color: "#888", fontSize: 10, fontWeight: 700 }}>NEXT DAY PREDICTION</div>
                    <div style={{ color: predColor, fontSize: 18, fontWeight: 900, marginTop: 2 }}>{pred.direction}</div>
                  </div>
                  <span style={{ background: predColor + "22", color: predColor, padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{pred.confidence}</span>
                </div>
                {pred.reasons?.map((r, i) => (
                  <div key={i} style={{ color: "#ccc", fontSize: 11, marginBottom: 3, paddingLeft: 8 }}>{i + 1}. {r}</div>
                ))}
              </div>
            )}
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
