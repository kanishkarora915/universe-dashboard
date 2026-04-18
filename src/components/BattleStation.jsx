import { useState, useEffect, useMemo } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";
import PayoffDiagram from "./PayoffDiagram";
import GreeksRadar from "./GreeksRadar";
import {
  SimpleVerdict,
  PriceJourney,
  BreakevenAnalysis,
  ThetaBurn,
  IVContext,
  EventRisk,
  SupportResistance,
} from "./BattleDeepAnalysis";

/**
 * BATTLE STATION — God-mode strike comparison.
 * Features:
 * - 20+ metric comparison table with winner highlighting
 * - 4 strategies auto-computed (Long Call, Long Put, Straddle, Strangle)
 * - Payoff diagram overlay
 * - Greeks radar
 * - Scenario stress test (spot ±2%, IV ±10%, 1 day theta)
 * - Claude AI verdict with entry/SL/T1/T2/reasoning
 */

const LOT_SIZE = { NIFTY: 75, BANKNIFTY: 30 };

function Section({ title, children, accent, theme }) {
  return (
    <div
      style={{
        background: theme.SURFACE,
        border: `1px solid ${theme.BORDER}`,
        borderLeft: `2px solid ${accent || theme.ACCENT}`,
        borderRadius: RADIUS.LG,
        padding: SPACE.LG,
        marginBottom: SPACE.MD,
      }}
    >
      <div
        style={{
          color: accent || theme.ACCENT,
          fontSize: TEXT_SIZE.MICRO,
          fontWeight: TEXT_WEIGHT.BOLD,
          letterSpacing: 2,
          textTransform: "uppercase",
          marginBottom: SPACE.MD,
          fontFamily: FONT.UI,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  );
}

function MetricCell({ value, isWinner, color, theme }) {
  return (
    <td
      style={{
        padding: `6px ${SPACE.SM}px`,
        color: isWinner ? theme.GREEN : color || theme.TEXT,
        fontWeight: isWinner ? TEXT_WEIGHT.BLACK : TEXT_WEIGHT.BOLD,
        fontFamily: FONT.MONO,
        fontSize: TEXT_SIZE.BODY,
        textAlign: "center",
        background: isWinner ? theme.GREEN + "0F" : "transparent",
        transition: TRANSITION.FAST,
      }}
    >
      {value}
      {isWinner && <span style={{ marginLeft: 4, fontSize: 10 }}>●</span>}
    </td>
  );
}

function findWinner(strikes, fn, higherIsBetter = true) {
  if (!strikes.length) return null;
  const values = strikes.map(fn);
  const valid = values.filter((v) => v != null && !isNaN(v));
  if (!valid.length) return null;
  const winner = higherIsBetter ? Math.max(...valid) : Math.min(...valid);
  return winner;
}

function computeNetGreeks(strikes) {
  // Sum the CE greeks (as if buying all CEs)
  return strikes.reduce(
    (acc, s) => {
      const g = s.greeks || {};
      acc.delta += g.deltaCE || 0;
      acc.gamma += g.gammaCE || 0;
      acc.theta += g.thetaCE || 0;
      acc.vega += g.vegaCE || 0;
      acc.cost += s.ceLTP || 0;
      return acc;
    },
    { delta: 0, gamma: 0, theta: 0, vega: 0, cost: 0 }
  );
}

function computeStressTest(strikes, spot) {
  // For each scenario, compute portfolio P&L if we buy all CEs
  const scenarios = [
    { label: "Spot -2%", spotDelta: -0.02, iv: 1.0, theta: 1 },
    { label: "Spot -1%", spotDelta: -0.01, iv: 1.0, theta: 1 },
    { label: "Flat", spotDelta: 0, iv: 1.0, theta: 1 },
    { label: "Spot +1%", spotDelta: 0.01, iv: 1.0, theta: 1 },
    { label: "Spot +2%", spotDelta: 0.02, iv: 1.0, theta: 1 },
    { label: "IV crush -20%", spotDelta: 0, iv: 0.8, theta: 1 },
    { label: "IV spike +20%", spotDelta: 0, iv: 1.2, theta: 1 },
  ];

  return scenarios.map((sc) => {
    let totalPnl = 0;
    strikes.forEach((s) => {
      const g = s.greeks || {};
      const ltp = s.ceLTP || 0;
      if (!ltp) return;
      // Delta-based price change
      const spotChange = spot * sc.spotDelta;
      const deltaPnl = (g.deltaCE || 0) * spotChange;
      // Gamma convexity (small)
      const gammaPnl = 0.5 * (g.gammaCE || 0) * Math.pow(spotChange, 2);
      // Theta
      const thetaPnl = (g.thetaCE || 0) * sc.theta;
      // Vega
      const vegaPnl = (g.vegaCE || 0) * (sc.iv - 1) * 100;
      totalPnl += deltaPnl + gammaPnl + thetaPnl + vegaPnl;
    });
    return { ...sc, pnl: Math.round(totalPnl * (LOT_SIZE[strikes[0]?.index] || 75)) };
  });
}

export default function BattleStation({ isOpen, onClose, pinnedStrikes = [], onRemoveStrike }) {
  const { theme } = useTheme();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [fetchError, setFetchError] = useState(null);
  const [liveData, setLiveData] = useState(null);
  const [trapVerdict, setTrapVerdict] = useState(null);

  // Fetch live spot + engine verdict alongside battle data (for deep analysis cards)
  useEffect(() => {
    if (!isOpen) return;
    const fetchExtras = () => {
      fetch("/api/live").then(r => r.ok ? r.json() : null).then(d => { if (d) setLiveData(d); }).catch(() => {});
      fetch("/api/trap/verdict").then(r => r.ok ? r.json() : null).then(d => { if (d) setTrapVerdict(d); }).catch(() => {});
    };
    fetchExtras();
    const iv = setInterval(fetchExtras, 10000);
    return () => clearInterval(iv);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || pinnedStrikes.length < 1) return;
    setLoading(true);
    setFetchError(null);
    // Fetch comparison (no AI — fast)
    fetch("/api/battle/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strikes: pinnedStrikes.slice(0, 4) }),
    })
      .then((r) => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then((d) => {
        if (d.error) {
          setFetchError(d.error);
        } else {
          setData(d);
        }
        setLoading(false);
      })
      .catch((e) => {
        setFetchError(e?.message || String(e) || "Failed to load comparison data");
        setLoading(false);
      });
  }, [isOpen, pinnedStrikes]);

  const fetchAIVerdict = () => {
    if (!data || !data.strikes) return;
    setAiLoading(true);
    fetch("/api/battle/verdict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strikes: pinnedStrikes.slice(0, 4) }),
    })
      .then((r) => r.json())
      .then((d) => {
        setData((prev) => ({ ...prev, verdict: d.verdict }));
        setAiLoading(false);
      })
      .catch(() => setAiLoading(false));
  };

  if (!isOpen) return null;

  const strikes = data?.strikes || [];
  const strategies = data?.strategies || [];
  const spot = strikes[0]?.spot || 0;
  const lotSize = LOT_SIZE[strikes[0]?.index] || 75;
  const netGreeks = strikes.length ? computeNetGreeks(strikes) : null;
  const stressTest = strikes.length ? computeStressTest(strikes, spot) : [];

  // Winners for each metric
  const cheapestCE = findWinner(strikes, (s) => s.ceLTP, false);
  const cheapestPE = findWinner(strikes, (s) => s.peLTP, false);
  const mostOI = findWinner(strikes, (s) => Math.max(s.ceOI || 0, s.peOI || 0));
  const mostVol = findWinner(strikes, (s) => Math.max(s.ceVol || 0, s.peVol || 0));

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: theme.OVERLAY,
        zIndex: Z.MODAL,
        overflowY: "auto",
        padding: SPACE.LG,
        backdropFilter: "blur(6px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: 1280,
          margin: "0 auto",
          background: theme.BG,
          border: `1px solid ${theme.BORDER_HI}`,
          borderRadius: RADIUS.LG,
          boxShadow: theme.SHADOW_HI,
          padding: SPACE.LG,
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: SPACE.LG,
            paddingBottom: SPACE.MD,
            borderBottom: `1px solid ${theme.BORDER}`,
          }}
        >
          <div>
            <div
              style={{
                color: theme.PURPLE,
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 3,
                textTransform: "uppercase",
              }}
            >
              ⚔ Battle Station
            </div>
            <div
              style={{
                color: theme.TEXT,
                fontSize: 24,
                fontWeight: TEXT_WEIGHT.BLACK,
                fontFamily: FONT.MONO,
                letterSpacing: 1,
                marginTop: 2,
              }}
            >
              {strikes.length === 1 ? "DEEP ANALYSIS" : `${strikes.length} STRIKES COMPARED`}
            </div>
            {spot > 0 && (
              <div
                style={{
                  color: theme.TEXT_MUTED,
                  fontSize: TEXT_SIZE.BODY,
                  marginTop: 4,
                  fontFamily: FONT.UI,
                }}
              >
                Spot: <span style={{ color: theme.GREEN, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>{spot.toFixed(2)}</span>
                {" · "}Lot size: {lotSize}
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: SPACE.SM }}>
            <button
              onClick={fetchAIVerdict}
              disabled={aiLoading}
              style={{
                background: theme.PURPLE,
                color: "#fff",
                border: "none",
                borderRadius: RADIUS.SM,
                padding: "6px 16px",
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 1,
                textTransform: "uppercase",
                cursor: aiLoading ? "not-allowed" : "pointer",
                opacity: aiLoading ? 0.6 : 1,
              }}
            >
              {aiLoading ? "Thinking..." : "🧠 Ask AI"}
            </button>
            <button
              onClick={onClose}
              style={{
                background: "transparent",
                color: theme.TEXT_MUTED,
                border: `1px solid ${theme.BORDER}`,
                borderRadius: RADIUS.SM,
                padding: "6px 14px",
                cursor: "pointer",
                fontSize: 14,
              }}
            >
              ×
            </button>
          </div>
        </div>

        {loading && (
          <div style={{ textAlign: "center", padding: SPACE.XXXL, color: theme.TEXT_DIM }}>
            Loading comparison data...
          </div>
        )}

        {!loading && pinnedStrikes.length < 1 && (
          <div style={{ textAlign: "center", padding: SPACE.XXXL, color: theme.TEXT_DIM, fontSize: TEXT_SIZE.BODY }}>
            Pin a strike to analyze it.
            <br />
            <span style={{ fontSize: TEXT_SIZE.MICRO, color: theme.TEXT_DIM }}>
              (Open search with ⌘K, pin with ☆. Pin 2+ for comparison mode.)
            </span>
          </div>
        )}

        {/* Backend error OR empty response despite pinned strikes */}
        {!loading && pinnedStrikes.length >= 1 && (fetchError || strikes.length < 1) && (
          <div
            role="alert"
            style={{
              padding: SPACE.LG,
              margin: `${SPACE.MD}px 0`,
              background: theme.RED_DIM,
              border: `1px solid ${theme.RED}44`,
              borderLeft: `3px solid ${theme.RED}`,
              borderRadius: RADIUS.LG,
              color: theme.TEXT,
            }}
          >
            <div style={{ color: theme.RED, fontSize: 10, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1.5, textTransform: "uppercase", marginBottom: SPACE.SM }}>
              Could not load comparison
            </div>
            <div style={{ fontSize: TEXT_SIZE.BODY, marginBottom: SPACE.SM }}>
              {fetchError || "Backend returned empty strike data. Engine may not have these strikes subscribed."}
            </div>
            <div style={{ fontSize: TEXT_SIZE.MICRO, color: theme.TEXT_MUTED }}>
              Pinned: {pinnedStrikes.map(s => `${s.index} ${s.strike}${s.type || ''}`).join(", ")}
            </div>
            <button
              onClick={() => {
                setLoading(true);
                setFetchError(null);
                fetch("/api/battle/compare", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ strikes: pinnedStrikes.slice(0, 4) }),
                })
                  .then((r) => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
                  .then((d) => { if (d.error) setFetchError(d.error); else setData(d); setLoading(false); })
                  .catch((e) => { setFetchError(e?.message || String(e)); setLoading(false); });
              }}
              style={{
                marginTop: SPACE.SM,
                background: theme.ACCENT, color: "#fff",
                border: "none", borderRadius: RADIUS.SM,
                padding: "6px 14px", cursor: "pointer",
                fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD,
              }}
            >
              Retry
            </button>
          </div>
        )}

        {!loading && strikes.length >= 1 && (
          <>
            {/* ── DEEP ANALYSIS (new) ── */}

            {/* BUY / DON'T BUY quick verdict — big decision card on top */}
            <SimpleVerdict strike={strikes[0]} spot={spot} verdict={trapVerdict} theme={theme} />

            {/* Price Journey — where did spot come from today */}
            <PriceJourney index={strikes[0].index} liveData={liveData} theme={theme} />

            {/* Breakeven with probability estimate */}
            <BreakevenAnalysis strike={strikes[0]} spot={spot} theme={theme} />

            {/* Theta burn live timer */}
            <ThetaBurn strike={strikes[0]} lotSize={lotSize} theme={theme} />

            {/* IV cheap/expensive context */}
            <IVContext strike={strikes[0]} theme={theme} />

            {/* Event / timing risks */}
            <EventRisk strike={strikes[0]} spot={spot} theme={theme} />

            {/* Market structure (PCR, max pain) */}
            <SupportResistance strikes={strikes} spot={spot} theme={theme} />

            {/* ── CLAUDE AI VERDICT (deeper, clickable) ── */}
            {!data?.verdict && (
              <Section title="🧠 AI Verdict" accent={theme.PURPLE} theme={theme}>
                <div style={{ textAlign: "center", padding: SPACE.MD, color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.BODY }}>
                  Click <strong style={{ color: theme.PURPLE }}>🧠 Ask AI</strong> button in header to get Claude's analysis:
                  winner pick, entry/SL/T1/T2, reasoning, dangers, strategies to avoid.
                </div>
              </Section>
            )}
            {data?.verdict && (
              <Section title="🧠 AI Verdict" accent={theme.PURPLE} theme={theme}>
                {data.verdict.winner && (
                  <div
                    style={{
                      color: theme.GREEN,
                      fontSize: 26,
                      fontWeight: TEXT_WEIGHT.BLACK,
                      fontFamily: FONT.MONO,
                      letterSpacing: 1.5,
                      marginBottom: SPACE.SM,
                    }}
                  >
                    {data.verdict.winner}
                    {data.verdict.confidence && (
                      <span style={{ color: theme.ACCENT, fontSize: 16, marginLeft: SPACE.MD }}>
                        {data.verdict.confidence}%
                      </span>
                    )}
                  </div>
                )}
                {data.verdict.reasoning && (
                  <div style={{ marginBottom: SPACE.MD }}>
                    {data.verdict.reasoning.map((r, i) => (
                      <div key={i} style={{ color: theme.TEXT, fontSize: TEXT_SIZE.BODY, padding: "4px 0" }}>
                        <span style={{ color: theme.PURPLE, marginRight: 8 }}>›</span>
                        {r}
                      </div>
                    ))}
                  </div>
                )}
                {(data.verdict.entry || data.verdict.sl || data.verdict.target1) && (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: SPACE.SM }}>
                    {[
                      { l: "ENTRY", v: data.verdict.entry, c: theme.ACCENT },
                      { l: "SL", v: data.verdict.sl, c: theme.RED },
                      { l: "T1", v: data.verdict.target1, c: theme.GREEN },
                      { l: "T2", v: data.verdict.target2, c: theme.GREEN },
                      { l: "R:R", v: data.verdict.riskReward, c: theme.PURPLE },
                      { l: "HOLD", v: data.verdict.holdTime, c: theme.CYAN },
                    ].filter((x) => x.v).map((p, i) => (
                      <div key={i} style={{
                        background: p.c + "15", border: `1px solid ${p.c}33`,
                        borderRadius: RADIUS.SM, padding: "6px 10px",
                      }}>
                        <div style={{ color: p.c, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1 }}>{p.l}</div>
                        <div style={{ color: theme.TEXT, fontSize: 14, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>{p.v}</div>
                      </div>
                    ))}
                  </div>
                )}
                {data.verdict.avoid && data.verdict.avoid.length > 0 && (
                  <div style={{ marginTop: SPACE.MD }}>
                    <div style={{ color: theme.RED, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, marginBottom: 4 }}>AVOID</div>
                    {data.verdict.avoid.map((a, i) => (
                      <div key={i} style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, padding: "2px 0" }}>✗ {a}</div>
                    ))}
                  </div>
                )}
                {data.verdict.dangers && data.verdict.dangers.length > 0 && (
                  <div style={{ marginTop: SPACE.SM, display: "flex", gap: SPACE.XS, flexWrap: "wrap" }}>
                    {data.verdict.dangers.map((d, i) => (
                      <span key={i} style={{
                        background: theme.AMBER + "22", color: theme.AMBER,
                        border: `1px solid ${theme.AMBER}44`,
                        padding: "2px 8px", borderRadius: RADIUS.XS,
                        fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD,
                      }}>⚠ {d}</span>
                    ))}
                  </div>
                )}
              </Section>
            )}

            {/* Strategies */}
            <Section title="🎯 Strategies" theme={theme}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: SPACE.SM }}>
                {strategies.map((st, i) => {
                  const be = typeof st.breakeven === "string" ? st.breakeven : Math.round(st.breakeven).toLocaleString("en-IN");
                  return (
                    <div
                      key={i}
                      style={{
                        background: theme.SURFACE_HI,
                        border: `1px solid ${theme.BORDER}`,
                        borderRadius: RADIUS.MD,
                        padding: SPACE.MD,
                      }}
                    >
                      <div style={{
                        color: theme.ACCENT, fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD,
                        letterSpacing: 1, marginBottom: 6, fontFamily: FONT.UI,
                      }}>{st.type?.replace(/_/g, " ")}</div>
                      <div style={{
                        color: theme.TEXT, fontSize: 14, fontWeight: TEXT_WEIGHT.BOLD,
                        fontFamily: FONT.MONO, marginBottom: 8,
                      }}>{st.name}</div>
                      <div style={{ fontSize: TEXT_SIZE.MICRO, color: theme.TEXT_MUTED, lineHeight: 1.7 }}>
                        <div>Cost: <span style={{ color: theme.TEXT, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>₹{Math.round(st.cost)}</span></div>
                        <div>Max Loss: <span style={{ color: theme.RED, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>₹{Math.round(st.maxLoss)}</span></div>
                        <div>Max Profit: <span style={{ color: theme.GREEN, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>{typeof st.maxProfit === "string" ? st.maxProfit : `₹${Math.round(st.maxProfit).toLocaleString("en-IN")}`}</span></div>
                        <div>Breakeven: <span style={{ color: theme.AMBER, fontFamily: FONT.MONO }}>{be}</span></div>
                        <div style={{ marginTop: 6, color: theme.CYAN }}>{st.bestWhen}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </Section>

            {/* Payoff Diagram */}
            <Section title="📊 Payoff Diagram" theme={theme}>
              <PayoffDiagram strategies={strategies} spot={spot} lotSize={lotSize} />
            </Section>

            {/* Comparison Table */}
            <Section title="📋 Full Metric Comparison" theme={theme}>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: TEXT_SIZE.BODY }}>
                  <thead>
                    <tr style={{ borderBottom: `2px solid ${theme.BORDER_HI}` }}>
                      <th style={{ padding: SPACE.SM, color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, textAlign: "left", letterSpacing: 1, fontWeight: TEXT_WEIGHT.BOLD, textTransform: "uppercase" }}>METRIC</th>
                      {strikes.map((s, i) => (
                        <th key={i} style={{ padding: SPACE.SM, color: theme.TEXT, fontSize: TEXT_SIZE.BODY, textAlign: "center", fontWeight: TEXT_WEIGHT.BLACK, fontFamily: FONT.MONO }}>
                          {s.index} {s.strike}
                          <div style={{ color: theme.TEXT_DIM, fontSize: 9, fontWeight: TEXT_WEIGHT.MED, marginTop: 2 }}>
                            {s.expiry?.slice(5) || ""}
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { label: "CE LTP", fn: (s) => s.ceLTP, fmt: (v) => v ? `₹${v}` : "—", winnerFn: (s) => s.ceLTP, higher: false, color: (s) => theme.GREEN },
                      { label: "PE LTP", fn: (s) => s.peLTP, fmt: (v) => v ? `₹${v}` : "—", winnerFn: (s) => s.peLTP, higher: false, color: (s) => theme.RED },
                      { label: "CE OI", fn: (s) => s.ceOI, fmt: (v) => v ? `${(v / 100000).toFixed(1)}L` : "—", winnerFn: (s) => s.ceOI, higher: true },
                      { label: "PE OI", fn: (s) => s.peOI, fmt: (v) => v ? `${(v / 100000).toFixed(1)}L` : "—", winnerFn: (s) => s.peOI, higher: true },
                      { label: "CE Vol", fn: (s) => s.ceVol, fmt: (v) => v ? `${(v / 100000).toFixed(1)}L` : "—", winnerFn: (s) => s.ceVol, higher: true },
                      { label: "PE Vol", fn: (s) => s.peVol, fmt: (v) => v ? `${(v / 100000).toFixed(1)}L` : "—", winnerFn: (s) => s.peVol, higher: true },
                      { label: "Delta CE", fn: (s) => s.greeks?.deltaCE, fmt: (v) => v?.toFixed(2) || "—" },
                      { label: "Delta PE", fn: (s) => s.greeks?.deltaPE, fmt: (v) => v?.toFixed(2) || "—" },
                      { label: "Gamma", fn: (s) => s.greeks?.gammaCE, fmt: (v) => v?.toFixed(4) || "—", winnerFn: (s) => s.greeks?.gammaCE, higher: true },
                      { label: "Theta", fn: (s) => s.greeks?.thetaCE, fmt: (v) => v?.toFixed(2) || "—", winnerFn: (s) => Math.abs(s.greeks?.thetaCE || 0), higher: false, color: () => theme.AMBER },
                      { label: "Vega", fn: (s) => s.greeks?.vegaCE, fmt: (v) => v?.toFixed(2) || "—" },
                      { label: "IV", fn: (s) => s.iv, fmt: (v) => v ? `${v.toFixed(1)}%` : "—" },
                      { label: "PCR", fn: (s) => s.pcr, fmt: (v) => v?.toFixed(2) || "—" },
                      { label: "Moneyness", fn: (s) => s.moneyness, fmt: (v) => v != null ? `${v > 0 ? "+" : ""}${v}%` : "—" },
                      { label: "Distance ATM", fn: (s) => s.atmDistance, fmt: (v) => v != null ? `${v > 0 ? "+" : ""}${v}` : "—" },
                    ].map((row, rowIdx) => {
                      const winner = row.winnerFn ? findWinner(strikes, row.winnerFn, row.higher !== false) : null;
                      return (
                        <tr key={rowIdx} style={{ borderBottom: `1px solid ${theme.BORDER}44` }}>
                          <td style={{ padding: `6px ${SPACE.SM}px`, color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD }}>
                            {row.label}
                          </td>
                          {strikes.map((s, i) => {
                            const val = row.fn(s);
                            const isWinner = winner != null && val === winner && val != null && val !== 0;
                            return (
                              <MetricCell
                                key={i}
                                value={row.fmt(val)}
                                isWinner={isWinner}
                                color={row.color ? row.color(s) : undefined}
                                theme={theme}
                              />
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Section>

            {/* Greeks Radar + Net Greeks side-by-side */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD, marginBottom: SPACE.MD }}>
              <Section title="🎯 Greeks Radar" theme={theme}>
                <GreeksRadar strikes={strikes} />
              </Section>

              {netGreeks && (
                <Section title="📦 Net Position (if buying all CEs)" theme={theme}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
                    {[
                      { l: "Net Delta", v: netGreeks.delta.toFixed(2), c: netGreeks.delta > 0 ? theme.GREEN : theme.RED, sub: netGreeks.delta > 0 ? "Bullish exposure" : "Bearish exposure" },
                      { l: "Net Gamma", v: netGreeks.gamma.toFixed(4), c: theme.ACCENT, sub: "Delta acceleration" },
                      { l: "Net Theta", v: `₹${Math.round(netGreeks.theta * lotSize).toLocaleString("en-IN")}/day`, c: theme.AMBER, sub: "Daily time decay" },
                      { l: "Net Vega", v: netGreeks.vega.toFixed(2), c: theme.PURPLE, sub: "Per 1% IV change" },
                      { l: "Total Cost", v: `₹${Math.round(netGreeks.cost * lotSize).toLocaleString("en-IN")}`, c: theme.CYAN, sub: "Capital required" },
                    ].map((x, i) => (
                      <div key={i}>
                        <div style={{ color: theme.TEXT_DIM, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase" }}>{x.l}</div>
                        <div style={{ color: x.c, fontSize: 18, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>{x.v}</div>
                        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>{x.sub}</div>
                      </div>
                    ))}
                  </div>
                </Section>
              )}
            </div>

            {/* Stress Test */}
            <Section title="🧪 Scenario Stress Test" theme={theme}>
              <div style={{ fontSize: TEXT_SIZE.MICRO, color: theme.TEXT_DIM, marginBottom: SPACE.SM }}>
                Assumes you buy all CEs. Shows expected P&L after 1 day in each scenario.
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: SPACE.XS }}>
                {stressTest.map((sc, i) => {
                  const isProfit = sc.pnl > 0;
                  return (
                    <div
                      key={i}
                      style={{
                        background: isProfit ? theme.GREEN_DIM : theme.RED_DIM,
                        border: `1px solid ${isProfit ? theme.GREEN : theme.RED}44`,
                        borderRadius: RADIUS.SM,
                        padding: SPACE.SM,
                        textAlign: "center",
                      }}
                    >
                      <div style={{ color: theme.TEXT_MUTED, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase" }}>
                        {sc.label}
                      </div>
                      <div style={{
                        color: isProfit ? theme.GREEN : theme.RED,
                        fontSize: 14,
                        fontWeight: TEXT_WEIGHT.BLACK,
                        fontFamily: FONT.MONO,
                        marginTop: 2,
                      }}>
                        ₹{sc.pnl > 0 ? "+" : ""}{sc.pnl.toLocaleString("en-IN")}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Section>

            {/* Pinned strike chips (remove to narrow comparison) */}
            <Section title="🗂 Pinned Strikes" theme={theme}>
              <div style={{ display: "flex", gap: SPACE.XS, flexWrap: "wrap" }}>
                {strikes.map((s, i) => (
                  <div key={i} style={{
                    background: theme.SURFACE_HI,
                    border: `1px solid ${theme.BORDER}`,
                    borderRadius: RADIUS.PILL,
                    padding: "4px 12px",
                    fontSize: TEXT_SIZE.MICRO,
                    fontFamily: FONT.MONO,
                    fontWeight: TEXT_WEIGHT.BOLD,
                    color: theme.TEXT,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}>
                    {s.index} {s.strike} · {s.expiry?.slice(5)}
                    {onRemoveStrike && (
                      <button
                        onClick={() => onRemoveStrike(pinnedStrikes[i])}
                        style={{
                          background: "transparent",
                          border: "none",
                          color: theme.TEXT_DIM,
                          cursor: "pointer",
                          padding: 0,
                          fontSize: 12,
                          lineHeight: 1,
                        }}
                      >×</button>
                    )}
                  </div>
                ))}
                {pinnedStrikes.length < 4 && (
                  <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, padding: "4px 12px" }}>
                    Pin {4 - pinnedStrikes.length} more from search
                  </div>
                )}
              </div>
            </Section>
          </>
        )}
      </div>
    </div>
  );
}
