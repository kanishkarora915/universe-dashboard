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

// ── API calls ────────────────────────────────────────────────────────────

async function fetchReport(endpoint) {
  try {
    const res = await fetch(`/api/reports/${endpoint}`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

async function postReport(endpoint) {
  try {
    const res = await fetch(`/api/reports/${endpoint}`, { method: "POST" });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

// ── Shared UI ────────────────────────────────────────────────────────────

const Card = ({ children, style = {} }) => (
  <div
    style={{
      background: CARD,
      border: `1px solid ${BORDER}`,
      borderRadius: 12,
      padding: "16px 20px",
      ...style,
    }}
  >
    {children}
  </div>
);

const SectionTitle = ({ icon, title }) => (
  <div
    style={{
      color: ACCENT,
      fontWeight: 900,
      fontSize: 14,
      marginBottom: 12,
      display: "flex",
      alignItems: "center",
      gap: 8,
    }}
  >
    <span style={{ fontSize: 16 }}>{icon}</span>
    {title}
  </div>
);

const Badge = ({ text, color }) => (
  <span
    style={{
      background: color + "22",
      color,
      border: `1px solid ${color}44`,
      borderRadius: 6,
      padding: "2px 8px",
      fontSize: 10,
      fontWeight: 700,
    }}
  >
    {text}
  </span>
);

const Stat = ({ label, value, color = "#fff", sub }) => (
  <div
    style={{
      background: BG,
      borderRadius: 8,
      padding: "10px 14px",
      flex: 1,
      minWidth: 100,
    }}
  >
    <div style={{ color: "#555", fontSize: 10, fontWeight: 700, textTransform: "uppercase" }}>
      {label}
    </div>
    <div style={{ color, fontWeight: 700, fontSize: 15 }}>{value}</div>
    {sub && <div style={{ color: "#444", fontSize: 10 }}>{sub}</div>}
  </div>
);

// ══════════════════════════════════════════════════════════════════════════
// SECTION A — Engine Performance Dashboard
// ══════════════════════════════════════════════════════════════════════════

function EnginePerformance({ data, weightsData, onApplyWeights, onResetWeights, applying }) {
  if (!data || !weightsData) {
    return (
      <Card>
        <SectionTitle icon="⚙️" title="ENGINE PERFORMANCE" />
        <div style={{ color: "#555", textAlign: "center", padding: 20 }}>Loading engine data...</div>
      </Card>
    );
  }

  const engines = weightsData.engines || [];
  const overall = data.overall || {};

  return (
    <Card>
      <SectionTitle icon="⚙️" title="ENGINE PERFORMANCE" />

      {/* Overall accuracy stats */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat
          label="15min Win Rate"
          value={`${overall["15min"]?.rate || 0}%`}
          color={overall["15min"]?.rate >= 55 ? GREEN : RED}
          sub={`${overall["15min"]?.wins || 0}/${overall["15min"]?.total || 0}`}
        />
        <Stat
          label="30min Win Rate"
          value={`${overall["30min"]?.rate || 0}%`}
          color={overall["30min"]?.rate >= 55 ? GREEN : RED}
          sub={`${overall["30min"]?.wins || 0}/${overall["30min"]?.total || 0}`}
        />
        <Stat
          label="1hr Win Rate"
          value={`${overall["1hr"]?.rate || 0}%`}
          color={overall["1hr"]?.rate >= 55 ? GREEN : RED}
          sub={`${overall["1hr"]?.wins || 0}/${overall["1hr"]?.total || 0}`}
        />
        <Stat
          label="Data Points"
          value={data.total || 0}
          color={ACCENT}
          sub={`Last ${data.days || 30} days`}
        />
      </div>

      {/* Trend indicator */}
      {data.trend && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 14,
            padding: "8px 12px",
            background: data.trend.improving ? GREEN + "11" : RED + "11",
            borderRadius: 8,
            border: `1px solid ${data.trend.improving ? GREEN : RED}33`,
          }}
        >
          <span style={{ fontSize: 14 }}>{data.trend.improving ? "↑" : "↓"}</span>
          <span style={{ color: data.trend.improving ? GREEN : RED, fontSize: 12, fontWeight: 700 }}>
            7-Day: {data.trend.recent7d}%
          </span>
          <span style={{ color: "#555", fontSize: 11 }}>vs Prior: {data.trend.older}%</span>
          <Badge
            text={data.trend.improving ? "IMPROVING" : "DECLINING"}
            color={data.trend.improving ? GREEN : RED}
          />
        </div>
      )}

      {/* Engine weights table */}
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
              <th style={{ textAlign: "left", padding: "8px 6px", color: "#555", fontWeight: 700 }}>
                ENGINE
              </th>
              <th style={{ textAlign: "center", padding: "8px 6px", color: "#555", fontWeight: 700 }}>
                DEFAULT
              </th>
              <th style={{ textAlign: "center", padding: "8px 6px", color: "#555", fontWeight: 700 }}>
                CURRENT
              </th>
              <th style={{ textAlign: "center", padding: "8px 6px", color: "#555", fontWeight: 700 }}>
                ACCURACY
              </th>
              <th style={{ textAlign: "center", padding: "8px 6px", color: "#555", fontWeight: 700 }}>
                RECOMMENDED
              </th>
              <th style={{ textAlign: "center", padding: "8px 6px", color: "#555", fontWeight: 700 }}>
                DIFF
              </th>
            </tr>
          </thead>
          <tbody>
            {engines.map((eng) => (
              <tr key={eng.name} style={{ borderBottom: `1px solid ${BORDER}22` }}>
                <td style={{ padding: "8px 6px" }}>
                  <div style={{ color: "#fff", fontWeight: 600, fontSize: 11 }}>
                    {eng.name.replace(/_/g, " ").toUpperCase()}
                  </div>
                  <div style={{ color: "#444", fontSize: 9 }}>{eng.description}</div>
                </td>
                <td style={{ textAlign: "center", padding: "8px 6px", color: "#666" }}>
                  {eng.default}
                </td>
                <td style={{ textAlign: "center", padding: "8px 6px", color: ACCENT, fontWeight: 700 }}>
                  {eng.current}
                </td>
                <td style={{ textAlign: "center", padding: "8px 6px" }}>
                  {eng.hasRealData ? (
                    <span style={{
                      color: eng.accuracy >= 60 ? GREEN : eng.accuracy >= 45 ? YELLOW : RED,
                      fontWeight: 700, fontSize: 12,
                    }}>
                      {eng.accuracy}%
                      <span style={{ color: "#444", fontSize: 9, fontWeight: 400 }}>
                        {" "}({eng.dataPoints})
                      </span>
                    </span>
                  ) : (
                    <span style={{ color: "#333", fontSize: 10 }}>collecting...</span>
                  )}
                </td>
                <td style={{ textAlign: "center", padding: "8px 6px", color: PURPLE, fontWeight: 700 }}>
                  {eng.recommended}
                </td>
                <td style={{ textAlign: "center", padding: "8px 6px" }}>
                  {eng.diff !== 0 && (
                    <span
                      style={{
                        color: eng.diff > 0 ? GREEN : RED,
                        fontWeight: 700,
                        fontSize: 11,
                      }}
                    >
                      {eng.diff > 0 ? "+" : ""}
                      {eng.diff}
                    </span>
                  )}
                  {eng.diff === 0 && <span style={{ color: "#333" }}>—</span>}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: `1px solid ${BORDER}` }}>
              <td style={{ padding: "8px 6px", color: "#888", fontWeight: 700 }}>TOTAL</td>
              <td style={{ textAlign: "center", padding: "8px 6px", color: "#666" }}>
                {weightsData.totalDefault}
              </td>
              <td style={{ textAlign: "center", padding: "8px 6px", color: ACCENT, fontWeight: 700 }}>
                {weightsData.totalCurrent}
              </td>
              <td style={{ textAlign: "center", padding: "8px 6px", color: PURPLE, fontWeight: 700 }}>
                {weightsData.totalRecommended}
              </td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>

      {/* Action buttons */}
      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button
          onClick={onApplyWeights}
          disabled={applying}
          style={{
            background: ACCENT,
            color: "#fff",
            border: "none",
            borderRadius: 8,
            padding: "8px 16px",
            fontSize: 12,
            fontWeight: 700,
            cursor: applying ? "not-allowed" : "pointer",
            opacity: applying ? 0.5 : 1,
          }}
        >
          {applying ? "Applying..." : "Apply Recommended Weights"}
        </button>
        <button
          onClick={onResetWeights}
          disabled={applying}
          style={{
            background: "transparent",
            color: "#666",
            border: `1px solid ${BORDER}`,
            borderRadius: 8,
            padding: "8px 16px",
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
          }}
        >
          Reset to Default
        </button>
      </div>
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION B — Trading Windows
// ══════════════════════════════════════════════════════════════════════════

function TradingWindows({ data }) {
  if (!data || !data.windows) {
    return (
      <Card>
        <SectionTitle icon="⏰" title="TRADING WINDOWS" />
        <div style={{ color: "#555", textAlign: "center", padding: 20 }}>Loading window data...</div>
      </Card>
    );
  }

  const typeColor = { BLAST: ORANGE, TRENDING: GREEN, SIDEWAYS: YELLOW };
  const typeIcon = { BLAST: "💥", TRENDING: "📈", SIDEWAYS: "➡️" };

  return (
    <Card>
      <SectionTitle icon="⏰" title="TRADING WINDOWS" />

      {/* Best/Worst callouts */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        {data.bestWindow && (
          <div
            style={{
              flex: 1,
              minWidth: 150,
              background: GREEN + "11",
              border: `1px solid ${GREEN}33`,
              borderRadius: 8,
              padding: "10px 14px",
            }}
          >
            <div style={{ color: "#555", fontSize: 10, fontWeight: 700 }}>BEST WINDOW</div>
            <div style={{ color: GREEN, fontWeight: 900, fontSize: 18 }}>
              {data.bestWindow.label}
            </div>
            <div style={{ color: GREEN, fontSize: 11 }}>
              {data.bestWindow.winRate}% win rate ({data.bestWindow.trades} trades)
            </div>
          </div>
        )}
        {data.worstWindow && (
          <div
            style={{
              flex: 1,
              minWidth: 150,
              background: RED + "11",
              border: `1px solid ${RED}33`,
              borderRadius: 8,
              padding: "10px 14px",
            }}
          >
            <div style={{ color: "#555", fontSize: 10, fontWeight: 700 }}>WORST WINDOW</div>
            <div style={{ color: RED, fontWeight: 900, fontSize: 18 }}>
              {data.worstWindow.label}
            </div>
            <div style={{ color: RED, fontSize: 11 }}>
              {data.worstWindow.winRate}% win rate ({data.worstWindow.trades} trades)
            </div>
          </div>
        )}
      </div>

      {/* Hour-by-hour heat map table */}
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
              {["HOUR", "TRADES", "WIN RATE", "AVG P&L", "TYPE"].map((h) => (
                <th
                  key={h}
                  style={{
                    textAlign: h === "HOUR" ? "left" : "center",
                    padding: "8px 6px",
                    color: "#555",
                    fontWeight: 700,
                    fontSize: 10,
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.windows.map((h) => {
              const wrColor =
                h.winRate >= 65 ? GREEN : h.winRate >= 50 ? YELLOW : h.winRate > 0 ? RED : "#333";
              const bgTint =
                h.trades === 0
                  ? "transparent"
                  : h.winRate >= 65
                  ? GREEN + "08"
                  : h.winRate < 45 && h.trades >= 3
                  ? RED + "08"
                  : "transparent";

              return (
                <tr
                  key={h.hour}
                  style={{ borderBottom: `1px solid ${BORDER}11`, background: bgTint }}
                >
                  <td style={{ padding: "8px 6px", fontWeight: 700, color: "#ccc" }}>{h.label}</td>
                  <td style={{ textAlign: "center", padding: "8px 6px", color: "#888" }}>
                    {h.trades || "—"}
                  </td>
                  <td
                    style={{
                      textAlign: "center",
                      padding: "8px 6px",
                      color: wrColor,
                      fontWeight: 700,
                    }}
                  >
                    {h.trades > 0 ? `${h.winRate}%` : "—"}
                  </td>
                  <td
                    style={{
                      textAlign: "center",
                      padding: "8px 6px",
                      color: h.avgPnl > 0 ? GREEN : h.avgPnl < 0 ? RED : "#555",
                    }}
                  >
                    {h.avgPnl !== 0 ? `Rs.${fmt(h.avgPnl)}` : "—"}
                  </td>
                  <td style={{ textAlign: "center", padding: "8px 6px" }}>
                    {h.trades > 0 && (
                      <Badge text={`${typeIcon[h.marketType] || ""} ${h.marketType}`} color={typeColor[h.marketType] || "#555"} />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Recommendations */}
      {data.recommendation && data.recommendation.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 6 }}>
            RECOMMENDATIONS
          </div>
          {data.recommendation.map((r, i) => (
            <div
              key={i}
              style={{
                color: r.startsWith("AVOID")
                  ? RED
                  : r.startsWith("AGGRESSIVE")
                  ? GREEN
                  : "#888",
                fontSize: 11,
                padding: "3px 0",
              }}
            >
              {r.startsWith("AVOID") ? "🚫" : r.startsWith("AGGRESSIVE") ? "🎯" : "✅"} {r}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION C — Pattern Analysis
// ══════════════════════════════════════════════════════════════════════════

function PatternAnalysis({ data }) {
  if (!data) {
    return (
      <Card>
        <SectionTitle icon="🔬" title="PATTERN ANALYSIS" />
        <div style={{ color: "#555", textAlign: "center", padding: 20 }}>Loading patterns...</div>
      </Card>
    );
  }

  const renderPatternList = (patterns, label, emptyMsg) => (
    <div style={{ flex: 1, minWidth: 200 }}>
      <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 8 }}>{label}</div>
      {patterns && patterns.length > 0 ? (
        patterns.map((p, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "6px 10px",
              background: BG,
              borderRadius: 6,
              marginBottom: 4,
            }}
          >
            <span style={{ color: "#ccc", fontSize: 11 }}>{p.label}</span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ color: "#555", fontSize: 10 }}>{p.count} trades</span>
              <span
                style={{
                  color: p.winRate >= 60 ? GREEN : p.winRate < 45 ? RED : YELLOW,
                  fontWeight: 700,
                  fontSize: 12,
                }}
              >
                {p.winRate}%
              </span>
            </div>
          </div>
        ))
      ) : (
        <div style={{ color: "#333", fontSize: 11, padding: 8 }}>{emptyMsg}</div>
      )}
    </div>
  );

  const bands = data.probabilityBands || {};
  const sessions = data.sessions || {};

  return (
    <Card>
      <SectionTitle icon="🔬" title="PATTERN ANALYSIS" />

      {/* Probability Bands */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 8 }}>
          ACCURACY BY PROBABILITY BAND
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(bands).map(([key, val]) => (
            <Stat
              key={key}
              label={val.label || key}
              value={val.count > 0 ? `${val.winRate}%` : "—"}
              color={val.winRate >= 60 ? GREEN : val.winRate >= 45 ? YELLOW : val.count > 0 ? RED : "#333"}
              sub={val.count > 0 ? `${val.count} trades` : "No data"}
            />
          ))}
        </div>
      </div>

      {/* Session Performance */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 8 }}>
          ACCURACY BY SESSION
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(sessions).map(([key, val]) => (
            <Stat
              key={key}
              label={val.label || key}
              value={val.count > 0 ? `${val.winRate}%` : "—"}
              color={val.winRate >= 60 ? GREEN : val.winRate >= 45 ? YELLOW : val.count > 0 ? RED : "#333"}
              sub={val.count > 0 ? `${val.count} trades` : "No data"}
            />
          ))}
        </div>
      </div>

      {/* Winning & Losing Patterns */}
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
        {renderPatternList(data.winningPatterns, "TOP WINNING PATTERNS", "Not enough data yet")}
        {renderPatternList(data.losingPatterns, "TOP LOSING PATTERNS", "Not enough data yet")}
      </div>
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION D — Weekly Training Report
// ══════════════════════════════════════════════════════════════════════════

function WeeklyReport({ data }) {
  if (!data) {
    return (
      <Card>
        <SectionTitle icon="📋" title="WEEKLY TRAINING REPORT" />
        <div style={{ color: "#555", textAlign: "center", padding: 20 }}>Loading report...</div>
      </Card>
    );
  }

  const summary = data.summary || {};
  const trades = data.trades || {};
  const byDir = data.byDirection || {};

  return (
    <Card>
      <SectionTitle icon="📋" title="WEEKLY TRAINING REPORT" />

      {/* Period */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 14,
        }}
      >
        <span style={{ color: "#555", fontSize: 11 }}>
          {data.period?.from} to {data.period?.to}
        </span>
        <span style={{ color: "#333", fontSize: 10 }}>Generated: {data.generatedAt}</span>
      </div>

      {/* Summary Stats */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat label="Total Verdicts" value={summary.totalVerdicts || 0} color={ACCENT} />
        <Stat
          label="Win Rate (30m)"
          value={`${summary.winRate30m || 0}%`}
          color={summary.winRate30m >= 55 ? GREEN : RED}
        />
        <Stat
          label="vs Last Week"
          value={`${summary.improvement > 0 ? "+" : ""}${summary.improvement || 0}%`}
          color={summary.improving ? GREEN : RED}
          sub={`Was ${summary.prevWeekRate || 0}%`}
        />
      </div>

      {/* Trade P&L */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat label="Trades Closed" value={trades.total || 0} color={ACCENT} />
        <Stat
          label="P&L"
          value={`Rs.${fmt(trades.pnl || 0)}`}
          color={trades.pnl > 0 ? GREEN : trades.pnl < 0 ? RED : "#555"}
        />
        <Stat label="Wins" value={trades.wins || 0} color={GREEN} />
        <Stat label="Losses" value={trades.losses || 0} color={RED} />
      </div>

      {/* Direction Performance */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat
          label="CE Accuracy"
          value={`${byDir.CE?.rate || 0}%`}
          color={byDir.CE?.rate >= 55 ? GREEN : RED}
          sub={`${byDir.CE?.total || 0} trades`}
        />
        <Stat
          label="PE Accuracy"
          value={`${byDir.PE?.rate || 0}%`}
          color={byDir.PE?.rate >= 55 ? GREEN : RED}
          sub={`${byDir.PE?.total || 0} trades`}
        />
      </div>

      {/* Insights */}
      {data.insights && data.insights.length > 0 && (
        <div
          style={{
            background: BG,
            borderRadius: 8,
            padding: "12px 14px",
          }}
        >
          <div style={{ color: ACCENT, fontSize: 10, fontWeight: 700, marginBottom: 8 }}>
            KEY INSIGHTS
          </div>
          {data.insights.map((insight, i) => (
            <div key={i} style={{ color: "#ccc", fontSize: 12, padding: "3px 0" }}>
              {insight.includes("+") || insight.includes("improved") || insight.includes("Best")
                ? "✅"
                : insight.includes("Avoid") || insight.includes("declined") || insight.includes("-")
                ? "⚠️"
                : "📊"}{" "}
              {insight}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION E — Direction Accuracy by Probability
// ══════════════════════════════════════════════════════════════════════════

function DirectionBreakdown({ data }) {
  if (!data) return null;

  const byProb = data.byProbability || {};

  return (
    <Card>
      <SectionTitle icon="📊" title="PROBABILITY CALIBRATION" />
      <div style={{ color: "#666", fontSize: 11, marginBottom: 12 }}>
        Are higher probability verdicts actually more accurate?
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {Object.entries(byProb).map(([band, info]) => {
          const barWidth = Math.max(info.rate || 0, 5);
          return (
            <div key={band} style={{ flex: 1, minWidth: 120 }}>
              <div style={{ color: "#888", fontSize: 10, fontWeight: 700, marginBottom: 4 }}>
                {band}%
              </div>
              <div
                style={{
                  background: BG,
                  borderRadius: 4,
                  height: 24,
                  position: "relative",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${barWidth}%`,
                    height: "100%",
                    background:
                      info.rate >= 60
                        ? GREEN + "66"
                        : info.rate >= 45
                        ? YELLOW + "66"
                        : RED + "66",
                    borderRadius: 4,
                    transition: "width 0.3s",
                  }}
                />
                <span
                  style={{
                    position: "absolute",
                    top: "50%",
                    left: 8,
                    transform: "translateY(-50%)",
                    color: "#fff",
                    fontSize: 10,
                    fontWeight: 700,
                  }}
                >
                  {info.rate}% ({info.count})
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// MAIN TAB COMPONENT
// ══════════════════════════════════════════════════════════════════════════

export default function ReportsTab() {
  const [accuracy, setAccuracy] = useState(null);
  const [weights, setWeights] = useState(null);
  const [windows, setWindows] = useState(null);
  const [patterns, setPatterns] = useState(null);
  const [weekly, setWeekly] = useState(null);
  const [trainStatus, setTrainStatus] = useState(null);
  const [applying, setApplying] = useState(false);
  const [training, setTraining] = useState(false);
  const [days, setDays] = useState(30);
  const [lastRefresh, setLastRefresh] = useState(null);

  const loadAll = useCallback(() => {
    fetchReport(`engine-accuracy?days=${days}`).then(setAccuracy);
    fetchReport("weights").then(setWeights);
    fetchReport(`trading-windows?days=${days}`).then(setWindows);
    fetchReport(`patterns?days=${days}`).then(setPatterns);
    fetchReport("weekly").then(setWeekly);
    fetchReport("auto-train-status").then(setTrainStatus);
    setLastRefresh(new Date().toLocaleTimeString("en-IN"));
  }, [days]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleApplyWeights = async () => {
    setApplying(true);
    const result = await postReport("apply-weights");
    if (result && !result.error) {
      fetchReport("weights").then(setWeights);
    }
    setApplying(false);
  };

  const handleResetWeights = async () => {
    setApplying(true);
    const result = await postReport("reset-weights");
    if (result && !result.error) {
      fetchReport("weights").then(setWeights);
    }
    setApplying(false);
  };

  const handleRunTrain = async () => {
    setTraining(true);
    const result = await postReport("run-train");
    if (result) {
      // Refresh all data after training
      fetchReport("weights").then(setWeights);
      fetchReport(`engine-accuracy?days=${days}`).then(setAccuracy);
      fetchReport("auto-train-status").then(setTrainStatus);
    }
    setTraining(false);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header Controls */}
      <Card>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 8,
          }}
        >
          <div>
            <div style={{ color: ACCENT, fontWeight: 900, fontSize: 15 }}>
              REPORTS & DATA — ML FEEDBACK LOOP
            </div>
            <div style={{ color: "#555", fontSize: 11 }}>
              Self-learning system. Engines that win get more voting power.
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {/* Period selector */}
            {[7, 30, 60].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                style={{
                  background: days === d ? ACCENT : "transparent",
                  color: days === d ? "#fff" : "#555",
                  border: `1px solid ${days === d ? ACCENT : BORDER}`,
                  borderRadius: 6,
                  padding: "4px 10px",
                  fontSize: 11,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                {d}D
              </button>
            ))}
            <button
              onClick={loadAll}
              style={{
                background: "transparent",
                color: ACCENT,
                border: `1px solid ${ACCENT}44`,
                borderRadius: 6,
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              Refresh
            </button>
          </div>
        </div>
        {lastRefresh && (
          <div style={{ color: "#333", fontSize: 10, marginTop: 4 }}>
            Last updated: {lastRefresh}
          </div>
        )}
      </Card>

      {/* Section D — Weekly Report (first for overview) */}
      <WeeklyReport data={weekly} />

      {/* Section A — Engine Performance */}
      <EnginePerformance
        data={accuracy}
        weightsData={weights}
        onApplyWeights={handleApplyWeights}
        onResetWeights={handleResetWeights}
        applying={applying}
      />

      {/* Section E — Probability Calibration */}
      <DirectionBreakdown data={accuracy} />

      {/* Section B — Trading Windows */}
      <TradingWindows data={windows} />

      {/* Section C — Pattern Analysis */}
      <PatternAnalysis data={patterns} />

      {/* Section F — Auto-Train System */}
      <AutoTrainSection
        data={trainStatus}
        onRunTrain={handleRunTrain}
        training={training}
      />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION F — Auto-Train System
// ══════════════════════════════════════════════════════════════════════════

function AutoTrainSection({ data, onRunTrain, training }) {
  return (
    <Card>
      <SectionTitle icon="🤖" title="AUTO-TRAIN SYSTEM" />
      <div style={{ color: "#666", fontSize: 11, marginBottom: 12 }}>
        Self-learning engine. Trains every Sunday 8 PM IST using per-engine accuracy data.
      </div>

      {/* Status row */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat
          label="Last Trained"
          value={data?.lastTrainAgo || "Never"}
          color={data?.lastTrain ? ACCENT : "#555"}
        />
        <Stat
          label="Next Train"
          value={data?.nextTrainIn || "—"}
          color={PURPLE}
          sub={data?.nextTrain || ""}
        />
        <Stat
          label="Total Runs"
          value={data?.totalRuns || 0}
          color={ACCENT}
          sub={data?.schedule || ""}
        />
      </div>

      {/* Manual train button */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <button
          onClick={onRunTrain}
          disabled={training}
          style={{
            background: PURPLE,
            color: "#fff",
            border: "none",
            borderRadius: 8,
            padding: "8px 16px",
            fontSize: 12,
            fontWeight: 700,
            cursor: training ? "not-allowed" : "pointer",
            opacity: training ? 0.5 : 1,
          }}
        >
          {training ? "Training..." : "Run Training Now"}
        </button>
        <div style={{ color: "#444", fontSize: 10, alignSelf: "center" }}>
          Needs 10+ backtest data points with engine scores
        </div>
      </div>

      {/* Training history */}
      {data?.recentRuns && data.recentRuns.length > 0 && (
        <div>
          <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 8 }}>
            TRAINING HISTORY
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                  {["DATE", "ACCURACY", "DATA PTS", "NOTES"].map((h) => (
                    <th
                      key={h}
                      style={{
                        textAlign: h === "NOTES" ? "left" : "center",
                        padding: "6px",
                        color: "#555",
                        fontWeight: 700,
                        fontSize: 9,
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.recentRuns.map((run, i) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${BORDER}11` }}>
                    <td style={{ textAlign: "center", padding: "6px", color: "#888" }}>
                      {run.timestamp?.split("T")[0] || "—"}
                    </td>
                    <td
                      style={{
                        textAlign: "center",
                        padding: "6px",
                        color: run.accuracyBefore >= 55 ? GREEN : RED,
                        fontWeight: 700,
                      }}
                    >
                      {run.accuracyBefore}%
                    </td>
                    <td style={{ textAlign: "center", padding: "6px", color: "#888" }}>
                      {run.dataPoints}
                    </td>
                    <td style={{ padding: "6px", color: "#666", fontSize: 10 }}>
                      {run.notes || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* No history */}
      {(!data?.recentRuns || data.recentRuns.length === 0) && (
        <div style={{ color: "#333", textAlign: "center", padding: 12, fontSize: 11 }}>
          No training runs yet. System will auto-train on Sunday 8 PM once enough data is collected.
        </div>
      )}
    </Card>
  );
}
