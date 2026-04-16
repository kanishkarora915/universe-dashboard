import { useState, useEffect, useCallback } from "react";

const ACCENT = "#0A84FF";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";
const CARD = "#111118";
const BORDER = "#1E1E2E";
const BG = "#0A0A0F";

const fmt = (n) => (n ? Math.round(n).toLocaleString("en-IN") : "0");
const fmtL = (n) => (n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0");

async function fetchAPI(endpoint) {
  try {
    const res = await fetch(`/api/autopsy/${endpoint}`);
    if (!res.ok) return null;
    return res.json();
  } catch { return null; }
}

const Card = ({ children, style = {} }) => (
  <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: "16px 20px", ...style }}>{children}</div>
);
const Label = ({ children }) => (
  <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>{children}</div>
);
const Stat = ({ label, value, color = "#fff", sub }) => (
  <div style={{ background: BG, borderRadius: 8, padding: "10px 14px", flex: 1, minWidth: 90 }}>
    <div style={{ color: "#555", fontSize: 9, fontWeight: 700, textTransform: "uppercase" }}>{label}</div>
    <div style={{ color, fontWeight: 700, fontSize: 14 }}>{value}</div>
    {sub && <div style={{ color: "#444", fontSize: 9 }}>{sub}</div>}
  </div>
);
const Badge = ({ text, color }) => (
  <span style={{ background: color + "22", color, border: `1px solid ${color}44`, borderRadius: 6, padding: "2px 8px", fontSize: 10, fontWeight: 700 }}>{text}</span>
);

// ═════════════════════════════════════════════════
// WIN/LOSS PATTERNS
// ═════════════════════════════════════════════════

function WinLossPatterns({ data }) {
  if (!data || data.error) {
    return (
      <Card>
        <Label>WIN vs LOSS PATTERNS</Label>
        <div style={{ color: "#555", textAlign: "center", padding: 20 }}>{data?.error || "Loading..."}</div>
      </Card>
    );
  }

  const wp = data.winPatterns || {};
  const lp = data.lossPatterns || {};

  return (
    <Card>
      <Label>WIN vs LOSS PATTERNS — What's different?</Label>
      <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
        <Stat label="Wins Analyzed" value={data.winEntries || 0} color={GREEN} />
        <Stat label="Losses Analyzed" value={data.lossEntries || 0} color={RED} />
      </div>

      {/* Side by side comparison */}
      {wp.count > 0 && lp.count > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, marginBottom: 14 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                <th style={{ padding: "6px", color: "#555", textAlign: "left" }}>METRIC</th>
                <th style={{ padding: "6px", color: GREEN, textAlign: "center" }}>WINS ({wp.count})</th>
                <th style={{ padding: "6px", color: RED, textAlign: "center" }}>LOSSES ({lp.count})</th>
                <th style={{ padding: "6px", color: ACCENT, textAlign: "center" }}>SIGNAL</th>
              </tr>
            </thead>
            <tbody>
              {[
                { label: "Avg PCR", w: wp.avgPCR, l: lp.avgPCR, good: "higher" },
                { label: "Premium Ratio", w: wp.avgPremiumRatio, l: lp.avgPremiumRatio, good: "higher" },
                { label: "Vol Ratio CE/PE", w: wp.volRatio, l: lp.volRatio, good: "higher" },
                { label: "CE OI Decreasing %", w: `${wp.ceDecreasingPct}%`, l: `${lp.ceDecreasingPct}%`, good: "higher" },
                { label: "PE OI Increasing %", w: `${wp.peIncreasingPct}%`, l: `${lp.peIncreasingPct}%`, good: "higher" },
                { label: "Avg CE OI Change", w: fmtL(wp.avgCEOIChange), l: fmtL(lp.avgCEOIChange), good: "lower" },
                { label: "Avg PE OI Change", w: fmtL(wp.avgPEOIChange), l: fmtL(lp.avgPEOIChange), good: "higher" },
              ].map((row, i) => (
                <tr key={i} style={{ borderBottom: `1px solid ${BORDER}11` }}>
                  <td style={{ padding: "5px 6px", color: "#888" }}>{row.label}</td>
                  <td style={{ padding: "5px 6px", textAlign: "center", color: GREEN, fontWeight: 700 }}>{row.w}</td>
                  <td style={{ padding: "5px 6px", textAlign: "center", color: RED, fontWeight: 700 }}>{row.l}</td>
                  <td style={{ padding: "5px 6px", textAlign: "center" }}>
                    {parseFloat(row.w) !== parseFloat(row.l) ? (
                      <span style={{ color: ACCENT, fontSize: 10 }}>
                        {parseFloat(row.w) > parseFloat(row.l) ? "Wins higher" : "Losses higher"}
                      </span>
                    ) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Insights */}
      {data.insights && data.insights.length > 0 && (
        <div style={{ background: BG, borderRadius: 8, padding: "12px 14px" }}>
          <div style={{ color: ACCENT, fontSize: 10, fontWeight: 700, marginBottom: 8 }}>KEY INSIGHTS</div>
          {data.insights.map((insight, i) => (
            <div key={i} style={{ color: "#ccc", fontSize: 11, padding: "3px 0" }}>
              {insight.includes("only") || insight.includes("DON'T") ? "❌" : "✅"} {insight}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ═════════════════════════════════════════════════
// GAP PREDICTION
// ═════════════════════════════════════════════════

function GapPrediction({ data }) {
  if (!data) {
    return (
      <Card>
        <Label>GAP PREDICTION — Tomorrow's Open</Label>
        <div style={{ color: "#555", textAlign: "center", padding: 20 }}>Loading...</div>
      </Card>
    );
  }

  const predColor = data.prediction === "GAP UP" ? GREEN : data.prediction === "GAP DOWN" ? RED : YELLOW;
  const predIcon = data.prediction === "GAP UP" ? "📈" : data.prediction === "GAP DOWN" ? "📉" : "➡️";

  return (
    <Card style={{ borderColor: data.confidence > 60 ? predColor + "44" : BORDER }}>
      <Label>GAP PREDICTION — Tomorrow's Open</Label>

      {/* Prediction banner */}
      <div style={{ textAlign: "center", padding: "12px 0", marginBottom: 14 }}>
        <div style={{ fontSize: 24, fontWeight: 900, color: predColor }}>
          {predIcon} {data.prediction}
        </div>
        <div style={{ color: predColor, fontSize: 14 }}>Confidence: {data.confidence}%</div>
        {data.prediction === "NEED MORE DATA" && (
          <div style={{ color: "#555", fontSize: 11, marginTop: 4 }}>{data.message}</div>
        )}
      </div>

      {/* Reasons */}
      {data.reasons && data.reasons.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 6 }}>REASONS</div>
          {data.reasons.map((r, i) => (
            <div key={i} style={{ color: "#ccc", fontSize: 11, padding: "2px 0" }}>
              {r.includes("bullish") || r.includes("support") || r.includes("above") ? "🟢" : "🔴"} {r}
            </div>
          ))}
        </div>
      )}

      {/* History stats */}
      {data.history && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
          <Stat label="Data Points" value={data.dataPoints} color={ACCENT} />
          <Stat label="Gap Ups" value={data.history.gapUps} color={GREEN} sub={`avg ${data.history.avgGapUp}%`} />
          <Stat label="Gap Downs" value={data.history.gapDowns} color={RED} sub={`avg ${data.history.avgGapDown}%`} />
          <Stat label="Flat Opens" value={data.history.flats} color="#888" />
        </div>
      )}

      {/* Correlations */}
      {data.correlations && data.correlations.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 6 }}>LEARNED CORRELATIONS</div>
          {data.correlations.map((c, i) => (
            <div key={i} style={{ background: BG, borderRadius: 6, padding: "8px 10px", marginBottom: 4, display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "#ccc", fontSize: 11 }}>{c.condition}</span>
              <span style={{ color: c.gapUpPct ? GREEN : RED, fontWeight: 700, fontSize: 11 }}>
                {c.gapUpPct ? `Gap Up ${c.gapUpPct}%` : `Gap Down ${c.gapDownPct}%`} ({c.count} days)
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Recent gaps */}
      {data.recentGaps && data.recentGaps.length > 0 && (
        <div>
          <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 6 }}>RECENT GAPS</div>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {data.recentGaps.map((g, i) => (
              <div key={i} style={{
                background: g.gapType === "GAP_UP" ? GREEN + "11" : g.gapType === "GAP_DOWN" ? RED + "11" : BG,
                borderRadius: 6, padding: "6px 10px", fontSize: 10, textAlign: "center", minWidth: 70,
              }}>
                <div style={{ color: "#888" }}>{g.date?.slice(5)}</div>
                <div style={{ color: g.gapType === "GAP_UP" ? GREEN : g.gapType === "GAP_DOWN" ? RED : "#555", fontWeight: 700 }}>
                  {g.gapPct > 0 ? "+" : ""}{g.gapPct}%
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

// ═════════════════════════════════════════════════
// MAIN TAB
// ═════════════════════════════════════════════════

export default function TradeAutopsyTab() {
  const [index, setIndex] = useState("NIFTY");
  const [patterns, setPatterns] = useState(null);
  const [gapPred, setGapPred] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const loadData = useCallback(() => {
    fetchAPI("patterns").then(setPatterns);
    fetchAPI(`gap-prediction/${index}`).then(setGapPred);
    setLastUpdate(new Date().toLocaleTimeString("en-IN"));
  }, [index]);

  useEffect(() => { loadData(); }, [loadData]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <div>
            <div style={{ color: ACCENT, fontWeight: 900, fontSize: 15 }}>TRADE AUTOPSY & GAP PREDICTION</div>
            <div style={{ color: "#555", fontSize: 11 }}>Learn from every trade. Predict tomorrow's gap.</div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {["NIFTY", "BANKNIFTY"].map(idx => (
              <button key={idx} onClick={() => setIndex(idx)} style={{
                background: index === idx ? ACCENT : "transparent",
                color: index === idx ? "#fff" : "#555",
                border: `1px solid ${index === idx ? ACCENT : BORDER}`,
                borderRadius: 6, padding: "5px 12px", fontSize: 11, fontWeight: 700, cursor: "pointer",
              }}>{idx}</button>
            ))}
            <button onClick={loadData} style={{
              background: "transparent", color: ACCENT, border: `1px solid ${ACCENT}44`,
              borderRadius: 6, padding: "5px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer",
            }}>Refresh</button>
          </div>
        </div>
        {lastUpdate && <div style={{ color: "#333", fontSize: 10, marginTop: 4 }}>Last: {lastUpdate}</div>}
      </Card>

      {/* Gap Prediction */}
      <GapPrediction data={gapPred} />

      {/* Win/Loss Patterns */}
      <WinLossPatterns data={patterns} />
    </div>
  );
}
