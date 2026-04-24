import { useState, useEffect, useCallback } from "react";
import AutopsyMindWidget from "./components/AutopsyMindWidget";

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
  if (!data) {
    return (
      <Card>
        <Label>WIN vs LOSS PATTERNS</Label>
        <div style={{ color: "#555", textAlign: "center", padding: 20 }}>Loading...</div>
      </Card>
    );
  }
  if (data.error) {
    return (
      <Card>
        <Label>WIN vs LOSS PATTERNS</Label>
        <div style={{ color: YELLOW, textAlign: "center", padding: 20, fontSize: 12 }}>{data.error}</div>
        <div style={{ color: "#555", textAlign: "center", fontSize: 10, marginTop: 6 }}>
          Patterns will appear after trades close with snapshots captured.
        </div>
      </Card>
    );
  }

  // Backend returns arrays when empty, objects when populated — normalize
  const wp = (data.winPatterns && !Array.isArray(data.winPatterns)) ? data.winPatterns : {};
  const lp = (data.lossPatterns && !Array.isArray(data.lossPatterns)) ? data.lossPatterns : {};

  if (!wp.count && !lp.count) {
    return (
      <Card>
        <Label>WIN vs LOSS PATTERNS</Label>
        <div style={{ color: "#555", textAlign: "center", padding: 20, fontSize: 12 }}>
          No entry snapshots captured yet.
        </div>
        <div style={{ color: "#444", textAlign: "center", fontSize: 10, marginTop: 6 }}>
          Wins: {data.totalWins || 0} closed · Losses: {data.totalLosses || 0} closed · Snapshots: {data.winEntries + data.lossEntries || 0}
        </div>
      </Card>
    );
  }

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

async function fetchShadow(endpoint) {
  try {
    const res = await fetch(`/api/shadow/${endpoint}`);
    if (!res.ok) return null;
    return res.json();
  } catch { return null; }
}

function LiveTradesTable({ trades }) {
  const [filter, setFilter] = useState("ALL"); // ALL | NIFTY | BANKNIFTY | OPEN | WINS | LOSSES
  const [side, setSide] = useState("ALL"); // ALL | CE | PE

  let filtered = [...trades];
  if (filter === "NIFTY") filtered = filtered.filter((t) => t.idx === "NIFTY");
  if (filter === "BANKNIFTY") filtered = filtered.filter((t) => t.idx === "BANKNIFTY");
  if (filter === "OPEN") filtered = filtered.filter((t) => t.status === "OPEN");
  if (filter === "WINS") filtered = filtered.filter((t) => (t.pnl_pct || 0) > 0);
  if (filter === "LOSSES") filtered = filtered.filter((t) => (t.pnl_pct || 0) < 0);
  if (side !== "ALL") filtered = filtered.filter((t) => t.side === side);

  // Sort: NIFTY first, then by offset (ATM center out)
  filtered.sort((a, b) => {
    if (a.idx !== b.idx) return a.idx === "NIFTY" ? -1 : 1;
    if (a.side !== b.side) return a.side === "CE" ? -1 : 1;
    return (a.offset || 0) - (b.offset || 0);
  });

  const btn = (label, active, onClick) => (
    <button
      onClick={onClick}
      style={{
        background: active ? ACCENT : "transparent",
        color: active ? "#fff" : "#888",
        border: `1px solid ${active ? ACCENT : BORDER}`,
        borderRadius: 6,
        padding: "3px 10px",
        fontSize: 10,
        fontWeight: 700,
        cursor: "pointer",
        marginRight: 4,
      }}
    >
      {label}
    </button>
  );

  return (
    <div style={{ marginTop: 14, marginBottom: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 6 }}>
        <Label>LIVE SHADOW TRADES — updating every 60s</Label>
        <div style={{ color: "#555", fontSize: 10 }}>
          {filtered.length} of {trades.length} trades
        </div>
      </div>

      {/* Filters */}
      <div style={{ marginBottom: 8 }}>
        {["ALL", "NIFTY", "BANKNIFTY", "OPEN", "WINS", "LOSSES"].map((f) => btn(f, filter === f, () => setFilter(f)))}
        <span style={{ margin: "0 6px", color: "#333" }}>·</span>
        {["ALL", "CE", "PE"].map((s) => btn(s, side === s, () => setSide(s)))}
      </div>

      {/* Table */}
      <div style={{ overflowX: "auto", maxHeight: 420, overflowY: "auto", border: `1px solid ${BORDER}`, borderRadius: 8 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead style={{ position: "sticky", top: 0, background: CARD, zIndex: 1 }}>
            <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
              <th style={th}>IDX</th>
              <th style={th}>STRIKE</th>
              <th style={th}>SIDE</th>
              <th style={{ ...th, textAlign: "right" }}>ENTRY</th>
              <th style={{ ...th, textAlign: "right" }}>LIVE</th>
              <th style={{ ...th, textAlign: "right" }}>PEAK</th>
              <th style={{ ...th, textAlign: "right" }}>TROUGH</th>
              <th style={{ ...th, textAlign: "right" }}>QTY</th>
              <th style={{ ...th, textAlign: "right" }}>P&L ₹</th>
              <th style={{ ...th, textAlign: "right" }}>P&L %</th>
              <th style={{ ...th, textAlign: "right" }}>OI Δ</th>
              <th style={{ ...th, textAlign: "center" }}>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => {
              const entry = Number(t.entry_ltp || 0);
              const cur = Number(t.current_ltp || entry);
              const peak = Number(t.peak_ltp || cur);
              const trough = Number(t.trough_ltp || cur);
              const qty = Number(t.qty || 0);
              const pnlR = t.status === "CLOSED"
                ? Number(t.pnl_rupees || 0)
                : (cur - entry) * qty;
              const pnlP = entry > 0 ? ((cur - entry) / entry) * 100 : 0;
              const oiCh = Number(t.oi_change || 0);
              const pnlColor = pnlR >= 0 ? GREEN : RED;
              const isOpen = t.status === "OPEN";

              return (
                <tr key={t.id} style={{ borderBottom: `1px solid ${BORDER}22` }}>
                  <td style={td}>
                    <span style={{ color: t.idx === "NIFTY" ? ACCENT : PURPLE, fontWeight: 700 }}>
                      {t.idx === "NIFTY" ? "N" : "BN"}
                    </span>
                  </td>
                  <td style={{ ...td, fontWeight: 600 }}>
                    {t.strike}
                    {t.offset === 0 && <span style={{ color: YELLOW, fontSize: 9, marginLeft: 4 }}>ATM</span>}
                  </td>
                  <td style={td}>
                    <span style={{ color: t.side === "CE" ? GREEN : RED, fontWeight: 700 }}>{t.side}</span>
                  </td>
                  <td style={{ ...td, textAlign: "right", color: "#999" }}>{entry.toFixed(2)}</td>
                  <td style={{ ...td, textAlign: "right", color: "#fff", fontWeight: 700 }}>{cur.toFixed(2)}</td>
                  <td style={{ ...td, textAlign: "right", color: GREEN }}>{peak.toFixed(2)}</td>
                  <td style={{ ...td, textAlign: "right", color: RED }}>{trough.toFixed(2)}</td>
                  <td style={{ ...td, textAlign: "right", color: "#888" }}>{qty.toLocaleString("en-IN")}</td>
                  <td style={{ ...td, textAlign: "right", color: pnlColor, fontWeight: 700 }}>
                    {pnlR >= 0 ? "+" : ""}₹{Math.round(pnlR).toLocaleString("en-IN")}
                  </td>
                  <td style={{ ...td, textAlign: "right", color: pnlColor, fontWeight: 700 }}>
                    {pnlP >= 0 ? "+" : ""}{pnlP.toFixed(2)}%
                  </td>
                  <td style={{ ...td, textAlign: "right", color: oiCh >= 0 ? GREEN : RED, fontSize: 10 }}>
                    {oiCh >= 0 ? "+" : ""}{fmtL(oiCh)}
                  </td>
                  <td style={{ ...td, textAlign: "center" }}>
                    {isOpen ? (
                      <span style={{ color: YELLOW, fontSize: 9, fontWeight: 700 }}>● LIVE</span>
                    ) : (
                      <span style={{ color: t.result === "WIN" || t.result === "BIG_WIN" ? GREEN : t.result === "FLAT" ? "#888" : RED, fontSize: 9, fontWeight: 700 }}>
                        {t.result || "CLOSED"}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={12} style={{ textAlign: "center", padding: 20, color: "#555", fontSize: 11 }}>
                  No trades matching filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const th = { padding: "7px 8px", color: "#555", textAlign: "left", fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 };
const td = { padding: "6px 8px", color: "#ccc" };

function ShadowAutopsySection({ shadow, history, onTrigger }) {
  const today = shadow || {};
  const trades = Array.isArray(today.trades) ? today.trades : [];
  const openCount = trades.filter((t) => t.status === "OPEN").length;
  const closedTrades = trades.filter((t) => t.status === "CLOSED");
  const wins = closedTrades.filter((t) => (t.pnl_pct || 0) > 0).length;
  const losses = closedTrades.length - wins;
  const bestTrade = closedTrades.slice().sort((a, b) => (b.pnl_pct || 0) - (a.pnl_pct || 0))[0];
  const worstTrade = closedTrades.slice().sort((a, b) => (a.pnl_pct || 0) - (b.pnl_pct || 0))[0];

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <Label>SHADOW AUTOPSY — 9:20 AM ATM±6 Paper Trades</Label>
          <div style={{ color: "#555", fontSize: 10 }}>
            52 simulated trades daily · learn WHICH strike wins, not just direction
          </div>
        </div>
        <button
          onClick={onTrigger}
          style={{
            background: "transparent",
            color: ACCENT,
            border: `1px solid ${ACCENT}44`,
            borderRadius: 6,
            padding: "5px 10px",
            fontSize: 11,
            fontWeight: 700,
            cursor: "pointer",
          }}
        >
          Trigger Now
        </button>
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
        <Stat label="Total Today" value={trades.length} color={ACCENT} />
        <Stat label="Open" value={openCount} color={YELLOW} />
        <Stat label="Wins" value={wins} color={GREEN} />
        <Stat label="Losses" value={losses} color={RED} />
        <Stat
          label="Win Rate"
          value={closedTrades.length > 0 ? `${Math.round((wins / closedTrades.length) * 100)}%` : "—"}
          color={wins > losses ? GREEN : wins < losses ? RED : "#888"}
        />
      </div>

      {/* Investment + Live PnL aggregate */}
      {(today.investment_total || today.live_pnl_total !== undefined) && (
        <div style={{
          display: "flex",
          gap: 8,
          marginBottom: 12,
          padding: "12px 14px",
          background: BG,
          borderRadius: 8,
          border: `1px solid ${BORDER}`,
          flexWrap: "wrap",
        }}>
          <Stat
            label="Total Invested"
            value={`₹${Math.round(today.investment_total || 0).toLocaleString("en-IN")}`}
            color="#fff"
            sub="1625×NIFTY + 600×BN"
          />
          <Stat
            label="Live P&L"
            value={`${(today.live_pnl_total || 0) >= 0 ? "+" : ""}₹${Math.round(today.live_pnl_total || 0).toLocaleString("en-IN")}`}
            color={(today.live_pnl_total || 0) >= 0 ? GREEN : RED}
            sub={`${(today.pnl_pct_on_invest || 0) >= 0 ? "+" : ""}${(today.pnl_pct_on_invest || 0).toFixed(2)}%`}
          />
          <Stat
            label="Realized"
            value={`₹${Math.round(today.realized_pnl_total || 0).toLocaleString("en-IN")}`}
            color={(today.realized_pnl_total || 0) >= 0 ? GREEN : RED}
          />
          <Stat
            label="Unrealized"
            value={`₹${Math.round(today.unrealized_pnl_total || 0).toLocaleString("en-IN")}`}
            color={(today.unrealized_pnl_total || 0) >= 0 ? GREEN : RED}
          />
        </div>
      )}

      {/* Per-index breakdown */}
      {today.by_index && Object.keys(today.by_index).length > 0 && (
        <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          {Object.entries(today.by_index).map(([ix, v]) => (
            <div key={ix} style={{
              flex: 1,
              minWidth: 140,
              padding: "8px 12px",
              background: BG,
              borderRadius: 6,
              border: `1px solid ${BORDER}`,
            }}>
              <div style={{ color: ACCENT, fontSize: 10, fontWeight: 700 }}>{ix} · {v.qty} qty</div>
              <div style={{ color: "#888", fontSize: 10 }}>
                Invest: ₹{Math.round(v.invest).toLocaleString("en-IN")} · {v.trades} trades
              </div>
              <div style={{
                color: (v.live_pnl || 0) >= 0 ? GREEN : RED,
                fontSize: 13,
                fontWeight: 700,
                marginTop: 2,
              }}>
                {(v.live_pnl || 0) >= 0 ? "+" : ""}₹{Math.round(v.live_pnl).toLocaleString("en-IN")} ({(v.pnl_pct || 0) >= 0 ? "+" : ""}{(v.pnl_pct || 0).toFixed(2)}%)
              </div>
            </div>
          ))}
        </div>
      )}

      {bestTrade && (
        <div style={{ background: BG, borderRadius: 6, padding: "8px 10px", marginBottom: 6 }}>
          <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>🏆 BEST TRADE TODAY</div>
          <div style={{ color: GREEN, fontSize: 12, fontWeight: 700 }}>
            {bestTrade.idx} {bestTrade.strike} {bestTrade.side} — +{(bestTrade.pnl_pct || 0).toFixed(1)}%
            {bestTrade.pnl_rupees ? ` (₹${Math.round(bestTrade.pnl_rupees).toLocaleString("en-IN")})` : ""}
          </div>
        </div>
      )}

      {worstTrade && (worstTrade.pnl_pct || 0) < 0 && (
        <div style={{ background: BG, borderRadius: 6, padding: "8px 10px", marginBottom: 12 }}>
          <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>💀 WORST TRADE TODAY</div>
          <div style={{ color: RED, fontSize: 12, fontWeight: 700 }}>
            {worstTrade.idx} {worstTrade.strike} {worstTrade.side} — {(worstTrade.pnl_pct || 0).toFixed(1)}%
            {worstTrade.pnl_rupees ? ` (₹${Math.round(worstTrade.pnl_rupees).toLocaleString("en-IN")})` : ""}
          </div>
        </div>
      )}

      {/* Live Trades Table — all 52 trades with live LTP, P&L, peak/trough */}
      {trades.length > 0 && <LiveTradesTable trades={trades} />}

      {/* History */}
      {history && Array.isArray(history.daily) && history.daily.length > 0 && (
        <div>
          <div style={{ color: "#555", fontSize: 10, fontWeight: 700, marginBottom: 6 }}>LAST 7 DAYS</div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                  <th style={{ padding: "5px", color: "#555", textAlign: "left" }}>DATE</th>
                  <th style={{ padding: "5px", color: "#555", textAlign: "left" }}>IDX</th>
                  <th style={{ padding: "5px", color: "#555", textAlign: "center" }}>SIDE</th>
                  <th style={{ padding: "5px", color: "#555", textAlign: "center" }}>TOTAL</th>
                  <th style={{ padding: "5px", color: "#555", textAlign: "center" }}>WINS</th>
                  <th style={{ padding: "5px", color: "#555", textAlign: "center" }}>AVG %</th>
                  <th style={{ padding: "5px", color: "#555", textAlign: "right" }}>P&L</th>
                </tr>
              </thead>
              <tbody>
                {history.daily.slice(0, 14).map((r, i) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${BORDER}11` }}>
                    <td style={{ padding: "4px 5px", color: "#ccc" }}>{String(r.date || "").slice(5)}</td>
                    <td style={{ padding: "4px 5px", color: "#888" }}>{r.idx}</td>
                    <td style={{ padding: "4px 5px", textAlign: "center", color: r.side === "CE" ? GREEN : RED, fontWeight: 700 }}>
                      {r.side}
                    </td>
                    <td style={{ padding: "4px 5px", textAlign: "center", color: "#ccc" }}>{r.total}</td>
                    <td style={{ padding: "4px 5px", textAlign: "center", color: GREEN, fontWeight: 700 }}>{r.wins}</td>
                    <td style={{
                      padding: "4px 5px",
                      textAlign: "center",
                      color: (r.avg_pnl_pct || 0) >= 0 ? GREEN : RED,
                      fontWeight: 700,
                    }}>
                      {(r.avg_pnl_pct || 0) >= 0 ? "+" : ""}{(r.avg_pnl_pct || 0).toFixed(1)}%
                    </td>
                    <td style={{
                      padding: "4px 5px",
                      textAlign: "right",
                      color: (r.total_pnl || 0) >= 0 ? GREEN : RED,
                      fontWeight: 700,
                    }}>
                      {(r.total_pnl || 0) >= 0 ? "+" : ""}₹{Math.round(r.total_pnl || 0).toLocaleString("en-IN")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {(!trades.length && !history?.daily?.length) && (
        <div style={{ color: "#555", textAlign: "center", padding: 16, fontSize: 11 }}>
          No shadow trades yet. Runs automatically at 9:20 AM IST on market days.
        </div>
      )}
    </Card>
  );
}

export default function TradeAutopsyTab() {
  const [index, setIndex] = useState("NIFTY");
  const [patterns, setPatterns] = useState(null);
  const [gapPred, setGapPred] = useState(null);
  const [shadowToday, setShadowToday] = useState(null);
  const [shadowHistory, setShadowHistory] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);

  const loadData = useCallback(() => {
    fetchAPI("patterns").then(setPatterns);
    fetchAPI(`gap-prediction/${index}`).then(setGapPred);
    fetchShadow("today").then(setShadowToday);
    fetchShadow("history?days=14").then(setShadowHistory);
    setLastUpdate(new Date().toLocaleTimeString("en-IN"));
  }, [index]);

  const triggerShadow = useCallback(async () => {
    try {
      await fetch("/api/shadow/trigger-open", { method: "POST" });
      setTimeout(loadData, 1500);
    } catch (e) { console.error(e); }
  }, [loadData]);

  useEffect(() => {
    loadData();
    const iv = setInterval(loadData, 15_000); // live refresh every 15s
    return () => clearInterval(iv);
  }, [loadData]);

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

      {/* Smart Autopsy Mind — pattern-based prediction for selected index */}
      <AutopsyMindWidget index={index} />

      {/* Shadow Autopsy — 9:20 AM paper trades + history */}
      <ShadowAutopsySection
        shadow={shadowToday}
        history={shadowHistory}
        onTrigger={triggerShadow}
      />

      {/* Gap Prediction */}
      <GapPrediction data={gapPred} />

      {/* Win/Loss Patterns */}
      <WinLossPatterns data={patterns} />
    </div>
  );
}
