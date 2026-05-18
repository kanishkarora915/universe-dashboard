/**
 * ReversalCockpit
 * ───────────────
 * 4-panel composable view for Scalper tab (B4.11-14):
 *
 *  ┌────────────── Capitulation Banner (B4.13) ──────────────┐
 *  │ NIFTY: BULL 6.2/STRONG ↑ · BANKNIFTY: BEAR 4.1/WATCH ↓ │
 *  └─────────────────────────────────────────────────────────┘
 *  ┌──── Writer Pressure NIFTY (B4.12) ────┬──── BANKNIFTY ──┐
 *  │ CE writers: ADDING +12% │ PE: covering│ ...            │
 *  └─────────────────────────┴─────────────┴────────────────┘
 *  ┌── Per-Strike OI Chart (B4.11) — ONE per open trade ────┐
 *  │  CE OI / PE OI lines, last 30 min                     │
 *  └────────────────────────────────────────────────────────┘
 *  ┌── Smart Money Micro-Panel (B4.14) — 3 latest patterns ─┐
 *  └────────────────────────────────────────────────────────┘
 *
 * All data fetched via SWR (deduped). No setInterval — backend
 * caches mean refresh interval determines actual fetch rate.
 */

import { useMemo } from "react";
import useSWRPoll from "../hooks/useSWRPoll";

const C = {
  bg: "#0F0F18",
  card: "#15151F",
  border: "#262636",
  text: "#E5E5E5",
  dim: "#888",
  green: "#30D158",
  red: "#FF453A",
  yellow: "#FFD60A",
  blue: "#0A84FF",
  orange: "#FF9F0A",
  purple: "#BF5AF2",
};


/* ════════════════════════════════════════════════════════════════
 * Top-level cockpit: combines all 4 panels for Scalper tab.
 * ═══════════════════════════════════════════════════════════════ */

export default function ReversalCockpit({ openTrades = [] }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 8, marginBottom: 8,
    }}>
      <CapitulationBanner />
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8,
      }}>
        <WriterPressurePanel idx="NIFTY" />
        <WriterPressurePanel idx="BANKNIFTY" />
      </div>
      {openTrades.map(t => (
        <PerStrikeOIPanel key={t.id} trade={t} />
      ))}
      <SmartMoneyMicroPanel />
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════
 * B4.13 — CapitulationBanner
 * Top-level reversal score for both indices.
 * ═══════════════════════════════════════════════════════════════ */

function CapitulationBanner() {
  // /api/reversal/live returns { results: { NIFTY: {bullish, bearish}, BANKNIFTY: {...} } }
  const { data } = useSWRPoll("/api/reversal/live", { refreshInterval: 15000 });
  const results = data?.results || {};

  return (
    <div style={{
      display: "flex", gap: 8, alignItems: "stretch",
    }}>
      {["NIFTY", "BANKNIFTY"].map(idx => {
        const r = results[idx] || {};
        const bull = r.bullish || {};
        const bear = r.bearish || {};
        const bullScore = bull.score || 0;
        const bearScore = bear.score || 0;
        const dominant = bullScore > bearScore ? "BULL" : "BEAR";
        const score = Math.max(bullScore, bearScore);
        const verdict = (dominant === "BULL" ? bull : bear).verdict || "QUIET";
        const reasons = (dominant === "BULL" ? bull : bear).reasons || [];

        const color =
          verdict === "STRONG_CAPITULATION" ? C.purple :
          verdict === "ALERT" ? (dominant === "BULL" ? C.green : C.red) :
          verdict === "WATCH" ? C.yellow :
          C.dim;
        const arrow = dominant === "BULL" ? "↑" : "↓";

        return (
          <div key={idx} style={{
            flex: 1,
            background: `${color}10`,
            border: `1px solid ${color}40`,
            borderRadius: 8,
            padding: "8px 12px",
          }}>
            <div style={{ display: "flex", alignItems: "center",
                          justifyContent: "space-between", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: C.text, fontSize: 11, fontWeight: 800,
                              letterSpacing: 0.5 }}>{idx}</span>
                <span style={{
                  color, fontSize: 11, fontWeight: 700,
                  padding: "2px 6px", borderRadius: 3,
                  background: `${color}22`,
                }}>
                  {arrow} {dominant} {score.toFixed(1)}/10
                </span>
                <span style={{ color: C.dim, fontSize: 10 }}>
                  {verdict.replace("_", " ")}
                </span>
              </div>
              <div style={{ color: C.dim, fontSize: 9, fontFamily: "ui-monospace, monospace" }}>
                bull {bullScore.toFixed(1)} · bear {bearScore.toFixed(1)}
              </div>
            </div>
            {reasons.length > 0 && (
              <div style={{ marginTop: 4, color: "#aaa", fontSize: 10, lineHeight: 1.4 }}>
                {reasons.slice(0, 2).map((r, i) => (
                  <div key={i}>• {r}</div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════
 * B4.12 — WriterPressurePanel
 * Per-index CE/PE writer activity via oi-context endpoint.
 * ═══════════════════════════════════════════════════════════════ */

function WriterPressurePanel({ idx }) {
  const { data } = useSWRPoll(
    `/api/scalper/oi-context?idx=${idx}`,
    { refreshInterval: 15000 }
  );

  if (!data) {
    return (
      <Box title={`${idx} · Writer Pressure`}>
        <div style={{ color: C.dim, fontSize: 10 }}>Loading…</div>
      </Box>
    );
  }

  const ce15 = data.ce_oi_delta_15m_pct;
  const pe15 = data.pe_oi_delta_15m_pct;
  const sigs = data.signals || {};
  const pcrNow = data.pcr_now;
  const pcrDelta = data.pcr_delta_15m;
  const mp = data.max_pain_now;
  const mpShift = data.max_pain_shift;

  // CE side interpretation
  const ceState = sigs.ce_writer_adding ? { label: "ADDING", color: C.red, hint: "Bearish ceiling" }
                : sigs.ce_writer_covering ? { label: "COVERING", color: C.green, hint: "Bullish reversal" }
                : { label: "NEUTRAL", color: C.dim, hint: "—" };
  const peState = sigs.pe_writer_adding ? { label: "ADDING", color: C.green, hint: "Bullish floor" }
                : sigs.pe_writer_covering ? { label: "COVERING", color: C.red, hint: "Bearish reversal" }
                : { label: "NEUTRAL", color: C.dim, hint: "—" };

  return (
    <Box title={`${idx} · Writer Pressure (15m)`}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        <Side label="CE Writers" delta={ce15} state={ceState} />
        <Side label="PE Writers" delta={pe15} state={peState} />
      </div>
      <div style={{
        display: "flex", justifyContent: "space-between", marginTop: 6,
        fontSize: 9, color: C.dim, fontFamily: "ui-monospace, monospace",
      }}>
        <span>PCR {pcrNow?.toFixed(2) ?? "—"} {pcrDelta != null && (
          <span style={{ color: pcrDelta > 0 ? C.green : pcrDelta < 0 ? C.red : C.dim }}>
            ({pcrDelta > 0 ? "+" : ""}{pcrDelta.toFixed(2)})
          </span>
        )}</span>
        <span>MaxPain {mp?.toLocaleString() ?? "—"} {mpShift != null && mpShift !== 0 && (
          <span style={{ color: mpShift > 0 ? C.green : C.red }}>
            ({mpShift > 0 ? "+" : ""}{mpShift})
          </span>
        )}</span>
      </div>
    </Box>
  );
}

function Side({ label, delta, state }) {
  return (
    <div style={{
      background: `${state.color}10`,
      border: `1px solid ${state.color}33`,
      borderRadius: 6, padding: "6px 8px",
    }}>
      <div style={{ color: C.dim, fontSize: 9, fontWeight: 700,
                    textTransform: "uppercase" }}>{label}</div>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "baseline", marginTop: 2 }}>
        <span style={{ color: state.color, fontSize: 12, fontWeight: 800 }}>
          {state.label}
        </span>
        <span style={{ color: delta != null && delta >= 0 ? C.green
                              : delta != null ? C.red : C.dim,
                       fontSize: 11, fontWeight: 700,
                       fontFamily: "ui-monospace, monospace" }}>
          {delta != null ? `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%` : "—"}
        </span>
      </div>
      <div style={{ color: "#aaa", fontSize: 9, marginTop: 2 }}>{state.hint}</div>
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════
 * B4.11 — PerStrikeOIPanel
 * Per-trade CE/PE OI mini-chart for the last 30 min at YOUR strike.
 * Pure SVG (no charting lib) — keeps bundle small.
 * ═══════════════════════════════════════════════════════════════ */

function PerStrikeOIPanel({ trade }) {
  const { idx, strike, action, id } = trade || {};
  const { data } = useSWRPoll(
    strike ? `/api/scalper/strike-history?idx=${idx}&strike=${strike}&minutes=30` : null,
    { refreshInterval: 30000 }  // OI snapshots are 60s apart anyway
  );

  const history = data?.history || [];
  if (history.length < 3) {
    return (
      <Box title={`#${id} ${idx} ${strike} ${action} · OI History`}>
        <div style={{ color: C.dim, fontSize: 10 }}>
          {history.length === 0 ? "No data yet — waiting for next minute capture" : "Building history…"}
        </div>
      </Box>
    );
  }

  // Compute series
  const ts = history.map(h => h.ts);
  const ceOI = history.map(h => h.ce_oi);
  const peOI = history.map(h => h.pe_oi);
  const minTs = Math.min(...ts);
  const maxTs = Math.max(...ts);
  const allOI = [...ceOI, ...peOI].filter(v => v > 0);
  const minOI = Math.min(...allOI);
  const maxOI = Math.max(...allOI);
  const range = (maxOI - minOI) || 1;

  // SVG geometry
  const W = 360;
  const H = 70;
  const padX = 4;
  const padY = 6;

  const xFor = (t) => padX + (W - 2 * padX) * (maxTs === minTs ? 0.5 : (t - minTs) / (maxTs - minTs));
  const yFor = (v) => padY + (H - 2 * padY) * (1 - (v - minOI) / range);

  const cePath = history.map((h, i) =>
    `${i === 0 ? "M" : "L"}${xFor(h.ts).toFixed(1)},${yFor(h.ce_oi).toFixed(1)}`
  ).join(" ");
  const pePath = history.map((h, i) =>
    `${i === 0 ? "M" : "L"}${xFor(h.ts).toFixed(1)},${yFor(h.pe_oi).toFixed(1)}`
  ).join(" ");

  // Last values + delta-from-first for quick read
  const ceFirst = ceOI[0];
  const peFirst = peOI[0];
  const ceLast = ceOI[ceOI.length - 1];
  const peLast = peOI[peOI.length - 1];
  const ceDelta = ceFirst > 0 ? ((ceLast - ceFirst) / ceFirst * 100) : 0;
  const peDelta = peFirst > 0 ? ((peLast - peFirst) / peFirst * 100) : 0;

  const isCE = action?.includes("CE");
  const ourSideDelta = isCE ? ceDelta : peDelta;
  const oppSideDelta = isCE ? peDelta : ceDelta;

  return (
    <Box title={`#${id} ${idx} ${strike} ${action} · OI 30m (${history.length}m samples)`}>
      <div style={{ position: "relative" }}>
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
          <path d={cePath} stroke={C.green} strokeWidth="1.5" fill="none" />
          <path d={pePath} stroke={C.red} strokeWidth="1.5" fill="none" />
        </svg>
        <div style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 9, fontFamily: "ui-monospace, monospace",
          color: C.dim, marginTop: 2,
        }}>
          <span>
            <span style={{ color: C.green }}>━ CE</span>{" "}
            {ceLast.toLocaleString()} ({ceDelta > 0 ? "+" : ""}{ceDelta.toFixed(1)}%)
          </span>
          <span>
            <span style={{ color: C.red }}>━ PE</span>{" "}
            {peLast.toLocaleString()} ({peDelta > 0 ? "+" : ""}{peDelta.toFixed(1)}%)
          </span>
        </div>
        <div style={{
          marginTop: 4, padding: "4px 6px",
          background: ourSideDelta > 0 ? `${C.green}10` : `${C.red}10`,
          border: `1px solid ${ourSideDelta > 0 ? C.green : C.red}33`,
          borderRadius: 4, fontSize: 10,
        }}>
          {isCE ? (
            <>Your strike: CE OI {ceDelta > 0 ? `↑ +${ceDelta.toFixed(1)}% (writers stacking — ` : `↓ ${ceDelta.toFixed(1)}% (writers covering — `}
            {ceDelta > 5 ? "bearish for CE buy" : ceDelta < -3 ? "bullish, GOOD" : "neutral"})</>
          ) : (
            <>Your strike: PE OI {peDelta > 0 ? `↑ +${peDelta.toFixed(1)}% (writers stacking — ` : `↓ ${peDelta.toFixed(1)}% (writers covering — `}
            {peDelta > 5 ? "bullish for PE buy" : peDelta < -3 ? "bearish, GOOD" : "neutral"})</>
          )}
        </div>
      </div>
    </Box>
  );
}


/* ════════════════════════════════════════════════════════════════
 * B4.14 — SmartMoneyMicroPanel
 * Last 3 institutional pattern alerts.
 * ═══════════════════════════════════════════════════════════════ */

function SmartMoneyMicroPanel() {
  const { data } = useSWRPoll("/api/smart-money/live", { refreshInterval: 30000 });
  const events = useMemo(() => {
    const arr = data?.recent || data?.events || data || [];
    return Array.isArray(arr) ? arr.slice(0, 3) : [];
  }, [data]);

  if (events.length === 0) {
    return (
      <Box title="Smart Money · Latest Patterns">
        <div style={{ color: C.dim, fontSize: 10 }}>No patterns detected (recent)</div>
      </Box>
    );
  }

  return (
    <Box title="Smart Money · Latest Patterns">
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {events.map((e, i) => {
          const pattern = e.pattern || e.type || "—";
          const isWriterDrip = pattern.includes("WRITER_DRIP");
          const isBuyerDrip = pattern.includes("BUYER_DRIP");
          const color = isBuyerDrip ? C.green : isWriterDrip ? C.red : C.yellow;
          return (
            <div key={i} style={{
              display: "flex", justifyContent: "space-between",
              padding: "4px 8px",
              background: `${color}10`, border: `1px solid ${color}33`,
              borderRadius: 4, fontSize: 10,
            }}>
              <span style={{ color, fontWeight: 700 }}>{pattern}</span>
              <span style={{ color: C.dim }}>
                {e.idx} {e.strike} · {e.detail || e.note || ""}
              </span>
            </div>
          );
        })}
      </div>
    </Box>
  );
}


/* ════════════════════════════════════════════════════════════════
 * Reusable card box
 * ═══════════════════════════════════════════════════════════════ */

function Box({ title, children }) {
  return (
    <div style={{
      background: C.card, border: `1px solid ${C.border}`,
      borderRadius: 8, padding: "8px 10px",
    }}>
      <div style={{
        color: C.dim, fontSize: 9, fontWeight: 700,
        letterSpacing: 0.6, textTransform: "uppercase",
        marginBottom: 6,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}
