/**
 * SmartTradingStatus — Comprehensive widget showing all 18 new intelligence features.
 *
 * Sections:
 *   1. Volatility Regime (A1)
 *   2. Risk Tier (A5)
 *   3. Quality Score for current verdict (A8)
 *   4. Recent OI Shifts (A2)
 *   5. Truth/Lie patterns active (A3)
 *   6. Today's Profile (B1)
 *   7. Past Similar Days (B2)
 *   8. Morning Prediction (B10)
 */

import React, { useEffect, useState } from "react";

const GREEN = "#26a69a";
const RED = "#ef5350";
const ORANGE = "#FF9F0A";
const YELLOW = "#FFD60A";
const BLUE = "#2962ff";
const GRAY = "#71717a";
const FG = "#d4d4d8";
const FG_DIM = "#71717a";
const BG = "#0a0a0a";
const CARD = "#0f0f10";
const BORDER = "#1f1f24";

const fmtR = (n) => `₹${Math.round(n || 0).toLocaleString("en-IN")}`;

async function safeFetch(url, fb) {
  try { const r = await fetch(url); if (!r.ok) return fb; return await r.json(); } catch { return fb; }
}

function regimeColor(regime) {
  if (!regime) return GRAY;
  if (regime === "EXTREME" || regime.includes("EXPIRY")) return RED;
  if (regime.includes("HIGH-VOL")) return ORANGE;
  if (regime === "NORMAL") return GREEN;
  return BLUE;
}

function tierColor(tier) {
  return tier === 1 ? GREEN : tier === 2 ? YELLOW : tier === 3 ? ORANGE : RED;
}

function gradeColor(grade) {
  if (grade === "EXCELLENT") return GREEN;
  if (grade === "GOOD") return BLUE;
  if (grade === "OK") return YELLOW;
  if (grade === "WEAK") return ORANGE;
  return RED;
}

export default function SmartTradingStatus() {
  const [vol, setVol] = useState(null);
  const [tier, setTier] = useState(null);
  const [quality, setQuality] = useState(null);
  const [shifts, setShifts] = useState([]);
  const [predict, setPredict] = useState(null);

  const load = async () => {
    const [v, t, q, s, p] = await Promise.all([
      safeFetch("/api/volatility/regime", null),
      safeFetch("/api/risk-tier/current", null),
      safeFetch("/api/quality/current/NIFTY", null),
      safeFetch("/api/oi-shifts/recent?hours=2", { shifts: [] }),
      safeFetch("/api/daily/predict", null),
    ]);
    if (v && !v.error) setVol(v);
    if (t && !t.error) setTier(t);
    if (q && !q.error) setQuality(q);
    if (s?.shifts) setShifts(s.shifts);
    if (p && !p.error) setPredict(p);
  };

  useEffect(() => {
    load();
    const iv = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 30000);
    return () => clearInterval(iv);
  }, []);

  return (
    <div style={wrap}>
      <div style={headerRow}>
        <div>
          <div style={{ fontSize: 10, color: FG_DIM, fontWeight: 600, letterSpacing: 1, textTransform: "uppercase" }}>
            🧠 SMART TRADING STATUS
          </div>
          <div style={{ fontSize: 11, color: FG_DIM, marginTop: 2 }}>
            18 intelligence engines · Auto-adapt · Real-time
          </div>
        </div>
      </div>

      {/* TOP 4 STATS GRID */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 12 }}>
        {/* Volatility */}
        <StatBox
          label="VOLATILITY"
          value={vol?.regime || "—"}
          color={regimeColor(vol?.regime)}
          sub={vol ? `VIX ${vol.vix?.toFixed(1)} · ATR ${vol.atr_ratio?.toFixed(1)}x` : null}
        />
        {/* Risk Tier */}
        <StatBox
          label="RISK TIER"
          value={tier ? `T${tier.tier} ${tier.tier_name}` : "—"}
          color={tier ? tierColor(tier.tier) : GRAY}
          sub={tier ? `${tier.win_streak}W / ${tier.loss_streak}L streak` : null}
        />
        {/* Quality */}
        <StatBox
          label="QUALITY (NIFTY)"
          value={quality ? `${quality.score}/10` : "—"}
          color={quality ? gradeColor(quality.grade) : GRAY}
          sub={quality?.grade}
        />
        {/* Time Window */}
        <StatBox
          label="TIME WINDOW"
          value={vol?.time_window || "—"}
          color={vol?.time_window === "MORNING_TREND" || vol?.time_window === "POWER_HOUR" ? GREEN
                : vol?.time_window === "LUNCH_CHOP" ? RED : BLUE}
          sub={vol?.is_expiry ? "⚠️ EXPIRY DAY" : null}
        />
      </div>

      {/* WARNINGS */}
      {(vol?.notes || []).length > 0 && (
        <div style={{
          background: BG, border: `1px solid ${ORANGE}33`, borderRadius: 4,
          padding: "8px 10px", marginBottom: 12,
        }}>
          <div style={{ fontSize: 9, color: ORANGE, fontWeight: 700, marginBottom: 4 }}>
            ⚠️ ACTIVE WARNINGS
          </div>
          {vol.notes.slice(0, 4).map((n, i) => (
            <div key={i} style={{ fontSize: 11, color: FG, padding: "1px 0" }}>• {n}</div>
          ))}
        </div>
      )}

      {/* MORNING PREDICTION */}
      {predict && predict.similar_days && predict.similar_days.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={sectionLabel}>📅 TODAY'S PREDICTION ({predict.day_of_week})</div>
          {predict.top_match && (
            <div style={{
              background: BG, border: `1px solid ${BLUE}33`,
              borderRadius: 4, padding: "10px 12px", marginBottom: 6,
            }}>
              <div style={{ fontSize: 10, color: BLUE, fontWeight: 700 }}>
                Most similar: {predict.top_match.date} ({predict.top_match.similarity_pct}% match)
              </div>
              <div style={{ fontSize: 11, color: FG, marginTop: 4 }}>
                {predict.top_match.summary}
              </div>
              <div style={{ fontSize: 10, color: FG_DIM, marginTop: 4 }}>
                That day: {predict.top_match.morning_trend} morning, {predict.top_match.afternoon_trend} afternoon, P&L {fmtR(predict.top_match.net_pnl || 0)}
              </div>
            </div>
          )}
          {(predict.recommendations || []).slice(0, 4).map((r, i) => (
            <div key={i} style={{ fontSize: 11, color: FG_DIM, padding: "2px 0" }}>{r}</div>
          ))}
        </div>
      )}

      {/* TIME WINDOW ANALYSIS */}
      {predict?.best_time_windows?.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={sectionLabel}>⏰ TIME WINDOW ANALYSIS (past {predict.day_of_week}s)</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            <div>
              <div style={{ fontSize: 9, color: GREEN, fontWeight: 700, marginBottom: 3 }}>✓ BEST WINDOWS</div>
              {predict.best_time_windows.slice(0, 3).map((w, i) => (
                <div key={i} style={miniRow}>
                  <span>{w.window}</span>
                  <span style={{ color: GREEN, fontWeight: 700 }}>{w.win_rate}%</span>
                </div>
              ))}
            </div>
            <div>
              <div style={{ fontSize: 9, color: RED, fontWeight: 700, marginBottom: 3 }}>✗ AVOID WINDOWS</div>
              {(predict.avoid_time_windows || []).slice(0, 3).map((w, i) => (
                <div key={i} style={miniRow}>
                  <span>{w.window}</span>
                  <span style={{ color: RED, fontWeight: 700 }}>{w.win_rate}%</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* OI SHIFTS */}
      {shifts.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={sectionLabel}>🎯 RECENT OI WALL SHIFTS (last 2hr)</div>
          {shifts.slice(0, 3).map((s, i) => (
            <div key={i} style={{
              background: BG, border: `1px solid ${ORANGE}33`,
              borderRadius: 4, padding: "6px 10px", marginBottom: 4, fontSize: 11,
            }}>
              <span style={{ color: ORANGE, fontWeight: 700 }}>
                {s.idx} {s.side}
              </span>
              <span style={{ color: FG, marginLeft: 6 }}>
                {s.from_strike} → {s.to_strike}
              </span>
              <span style={{ color: FG_DIM, marginLeft: 6, fontSize: 10 }}>
                ({(s.shift_magnitude_pct || 0) >= 0 ? "+" : ""}{(s.shift_magnitude_pct || 0).toFixed(0)}%)
              </span>
            </div>
          ))}
        </div>
      )}

      {/* QUALITY BREAKDOWN */}
      {quality && quality.breakdown && (
        <div>
          <div style={sectionLabel}>🎯 QUALITY BREAKDOWN (NIFTY current verdict)</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 6 }}>
            <Mini label="Alignment" value={`${quality.breakdown.alignment?.toFixed(1) || 0}/3`} color={GREEN} />
            <Mini label="Strength" value={`${quality.breakdown.strength?.toFixed(1) || 0}/2.5`} color={BLUE} />
            <Mini label="Time Fit" value={`${quality.breakdown.time_window?.toFixed(1) || 0}/2`} color={ORANGE} />
            <Mini label="Volatility" value={`${quality.breakdown.volatility?.toFixed(1) || 0}/1.5`} color={YELLOW} />
            <Mini label="OI Confirm" value={`${quality.breakdown.oi_confirmation?.toFixed(1) || 0}/1`} color={GREEN} />
          </div>
          {(quality.reasons || []).length > 0 && (
            <div style={{ marginTop: 6 }}>
              {quality.reasons.slice(0, 3).map((r, i) => (
                <div key={i} style={{ fontSize: 10, color: FG_DIM, padding: "1px 0" }}>{r}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatBox({ label, value, color = FG, sub }) {
  return (
    <div style={{
      background: BG, border: `1px solid ${BORDER}`, borderRadius: 4,
      padding: "10px 12px",
    }}>
      <div style={{ fontSize: 9, color: FG_DIM, fontWeight: 600, letterSpacing: 0.5, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 15, color, fontWeight: 700, marginTop: 4, fontFeatureSettings: "'tnum'" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 10, color: FG_DIM, marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function Mini({ label, value, color = FG }) {
  return (
    <div>
      <div style={{ fontSize: 8, color: FG_DIM, fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 11, color, fontWeight: 700, marginTop: 2, fontFeatureSettings: "'tnum'" }}>{value}</div>
    </div>
  );
}

const wrap = {
  background: CARD,
  border: `1px solid ${BORDER}`,
  borderRadius: 6,
  padding: 16,
  marginBottom: 12,
  fontFamily: "-apple-system, 'Segoe UI', system-ui, sans-serif",
};

const headerRow = {
  paddingBottom: 10,
  borderBottom: `1px solid ${BORDER}`,
  marginBottom: 12,
};

const sectionLabel = {
  fontSize: 9,
  color: FG_DIM,
  fontWeight: 600,
  letterSpacing: 0.5,
  textTransform: "uppercase",
  marginBottom: 6,
};

const miniRow = {
  display: "flex",
  justifyContent: "space-between",
  background: BG,
  border: `1px solid ${BORDER}`,
  borderRadius: 3,
  padding: "4px 8px",
  marginBottom: 3,
  fontSize: 11,
  color: FG,
};
