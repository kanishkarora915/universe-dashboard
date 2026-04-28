/**
 * BuyerCockpit — Clean home page for option buyers.
 *
 * Replaces cluttered dashboard with 3 sections:
 * 1. GO/NO-GO Banner (top hero — 5-second decision)
 * 2. Enhanced Verdict Cards (with full SL/T1/T2/theta math)
 * 3. Open Positions Quick View
 */

import React, { useMemo, useState, useEffect } from "react";
import { SPACE, RADIUS, TEXT_SIZE, TEXT_WEIGHT, FONT } from "../theme";
import NextDayPredictor from "./NextDayPredictor";
import OIHeatmap from "./OIHeatmap";
import AutopsyMindWidget from "./AutopsyMindWidget";
import OIInsightPanel from "./OIInsightPanel";
import BuyerModeToggle from "./BuyerModeToggle";

const GREEN = "#10b981";
const RED = "#ef4444";
const YELLOW = "#f59e0b";
const BLUE = "#3b82f6";
const GRAY = "#6b7280";

// ═══════════════════════════════════════════════════════
// 1. GO/NO-GO BANNER
// ═══════════════════════════════════════════════════════

function computeDayMode(live, niftyVerdict, bnVerdict) {
  // Determine if today is buyer-friendly
  const nifty = live?.nifty || {};
  const bn = live?.banknifty || {};

  // Chop detection: small day range
  const niftyRange = nifty.dayRange || 0;
  const bnRange = bn.dayRange || 0;
  const niftyATR = (nifty.high || 0) - (nifty.low || 0);
  const niftyChopScore =
    niftyATR > 0 && niftyRange > 0
      ? Math.min(100, Math.max(0, 100 - (niftyATR / nifty.ltp) * 100 * 200))
      : 50;

  // IVR from live data
  const ivr = nifty.ivr || 50;

  // Expiry day detection
  const now = new Date();
  const istHour = now.getUTCHours() + 5 + (now.getUTCMinutes() + 30) / 60;
  const day = now.getUTCDay(); // 0=Sun
  const isExpiryDay = day === 2; // Tuesday for NIFTY

  // Max conviction of either index
  const maxConv = Math.max(
    niftyVerdict?.winProbability || 0,
    bnVerdict?.winProbability || 0
  );

  // Classification
  let mode = "NEUTRAL";
  let reason = "";

  if (isExpiryDay) {
    mode = "EXPIRY";
    const phase =
      istHour < 12 ? "EARLY" : istHour < 13 ? "MID" : istHour < 14 ? "LATE" : "DEATH_ZONE";
    reason = `NIFTY expiry day — Phase: ${phase}`;
  } else if (ivr > 70 || niftyChopScore > 70) {
    mode = "NO_GO";
    reason = ivr > 70 ? `IVR ${ivr}% too expensive` : `Chop score ${Math.round(niftyChopScore)} too high`;
  } else if (maxConv >= 75) {
    mode = "GO";
    reason = `Strong setup — best: ${maxConv}%`;
  } else if (maxConv >= 65) {
    mode = "CAUTION";
    reason = `Moderate setup — watch for confirmation`;
  } else {
    mode = "WAIT";
    reason = `No setup yet — all signals < 65%`;
  }

  return { mode, reason, ivr, chopScore: Math.round(niftyChopScore), isExpiryDay, istHour, maxConv };
}

export function GoNoGoBanner({ live, niftyVerdict, bnVerdict }) {
  const { mode, reason, ivr, chopScore, isExpiryDay, istHour, maxConv } = useMemo(
    () => computeDayMode(live, niftyVerdict, bnVerdict),
    [live, niftyVerdict, bnVerdict]
  );

  const configs = {
    GO: { bg: "rgba(16, 185, 129, 0.1)", border: GREEN, emoji: "🟢", title: "BUYER-FRIENDLY DAY — GO" },
    CAUTION: { bg: "rgba(245, 158, 11, 0.1)", border: YELLOW, emoji: "🟡", title: "CAUTION DAY — MODERATE" },
    NO_GO: { bg: "rgba(239, 68, 68, 0.1)", border: RED, emoji: "🔴", title: "NO-TRADE DAY — SKIP" },
    WAIT: { bg: "rgba(107, 114, 128, 0.1)", border: GRAY, emoji: "⏳", title: "WAIT — NO CLEAR SETUP" },
    EXPIRY: { bg: "rgba(168, 85, 247, 0.15)", border: "#a855f7", emoji: "🔔", title: "EXPIRY DAY — PHASE MODE" },
    NEUTRAL: { bg: "rgba(107, 114, 128, 0.08)", border: GRAY, emoji: "⚪", title: "MARKET ACTIVE" },
  };

  const cfg = configs[mode] || configs.NEUTRAL;

  const niftyPct = live?.nifty?.changePct ?? 0;
  const bnPct = live?.banknifty?.changePct ?? 0;

  return (
    <div
      style={{
        padding: `${SPACE.MD}px ${SPACE.LG}px`,
        background: cfg.bg,
        border: `2px solid ${cfg.border}`,
        borderRadius: RADIUS.LG,
        display: "flex",
        gap: SPACE.LG,
        alignItems: "center",
        justifyContent: "space-between",
        flexWrap: "wrap",
      }}
    >
      {/* Left: Title + Reason */}
      <div style={{ flex: "1 1 auto", minWidth: 280 }}>
        <div
          style={{
            fontSize: TEXT_SIZE.HEADING_SM || 20,
            fontWeight: TEXT_WEIGHT.BOLD || 800,
            color: cfg.border,
            display: "flex",
            alignItems: "center",
            gap: SPACE.SM,
          }}
        >
          <span style={{ fontSize: 28 }}>{cfg.emoji}</span>
          {cfg.title}
        </div>
        <div style={{ fontSize: TEXT_SIZE.BODY || 14, color: "var(--text-secondary, #999)", marginTop: 4 }}>
          {reason}
        </div>
      </div>

      {/* Right: Metrics pills */}
      <div style={{ display: "flex", gap: SPACE.SM, flexWrap: "wrap" }}>
        <MetricPill label="IVR" value={`${ivr.toFixed?.(0) || ivr}%`} color={ivr < 50 ? GREEN : ivr < 70 ? YELLOW : RED} />
        <MetricPill label="Chop" value={`${chopScore}`} color={chopScore < 50 ? GREEN : chopScore < 70 ? YELLOW : RED} />
        <MetricPill label="NIFTY" value={`${niftyPct >= 0 ? "+" : ""}${niftyPct.toFixed(2)}%`} color={niftyPct >= 0 ? GREEN : RED} />
        <MetricPill label="BN" value={`${bnPct >= 0 ? "+" : ""}${bnPct.toFixed(2)}%`} color={bnPct >= 0 ? GREEN : RED} />
        <MetricPill label="Best" value={`${maxConv}%`} color={maxConv >= 75 ? GREEN : maxConv >= 65 ? YELLOW : GRAY} />
      </div>
    </div>
  );
}

function MetricPill({ label, value, color }) {
  return (
    <div
      style={{
        padding: `${SPACE.XS}px ${SPACE.SM}px`,
        background: "rgba(255, 255, 255, 0.04)",
        border: `1px solid ${color}44`,
        borderRadius: RADIUS.MD,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        minWidth: 64,
      }}
    >
      <div style={{ fontSize: 10, color: "var(--text-secondary, #999)", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color, fontFamily: FONT.MONO || "monospace" }}>{value}</div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════
// 2. ENHANCED VERDICT CARD
// ═══════════════════════════════════════════════════════

export function EnhancedVerdictCard({ index, verdict, reasons }) {
  // Live LTP fetcher for verdict strike
  const [liveLtp, setLiveLtp] = useState(null);
  const [verdictAge, setVerdictAge] = useState(0);

  const trade = verdict?.trade || {};
  const strike = trade.strike || verdict?.atm;
  const action = verdict?.action || "NO TRADE";

  useEffect(() => {
    if (!strike || !action || action === "NO TRADE") return;
    let cancelled = false;
    const fetchLtp = async () => {
      try {
        const res = await fetch(`/api/option-chain/${index}`);
        if (!res.ok) return;
        const chain = await res.json();
        const row = chain.find(r => Math.abs(r.strike - strike) < 0.01);
        if (row && !cancelled) {
          const ltp = action.includes("CE") ? row.ce_ltp : row.pe_ltp;
          setLiveLtp(ltp);
        }
      } catch {}
    };
    fetchLtp();
    const iv = setInterval(() => { if (document.visibilityState === "visible") fetchLtp(); }, 10000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [index, strike, action]);

  // Verdict age tracker
  useEffect(() => {
    if (!verdict?.timestamp) return;
    const updateAge = () => {
      // Verdict timestamp like "10:45:23 AM IST" — just track elapsed
      setVerdictAge(prev => prev + 1);
    };
    setVerdictAge(0);
    const iv = setInterval(updateAge, 1000);
    return () => clearInterval(iv);
  }, [verdict?.timestamp]);

  if (!verdict) {
    return (
      <div style={{ padding: SPACE.LG, background: "rgba(107,114,128,0.1)", borderRadius: RADIUS.LG, textAlign: "center", color: "#888" }}>
        <div style={{ fontSize: 14 }}>{index}</div>
        <div style={{ fontSize: 12, marginTop: 8 }}>Waiting for signal...</div>
      </div>
    );
  }

  const winProb = verdict.winProbability || 0;
  const isBuy = action.includes("BUY");
  const isCE = action.includes("CE");
  const isPE = action.includes("PE");
  const direction = isCE ? "CE" : isPE ? "PE" : "?";

  // Conviction levels (relaxed — trust engines)
  const tooLow = winProb > 0 && winProb < 50;
  const high = winProb >= 70;
  const medium = winProb >= 60 && winProb < 70;

  // Colors
  const color = tooLow ? GRAY : isCE ? GREEN : isPE ? RED : GRAY;
  const bgColor = tooLow
    ? "rgba(107, 114, 128, 0.08)"
    : isCE
    ? "rgba(16, 185, 129, 0.08)"
    : isPE
    ? "rgba(239, 68, 68, 0.08)"
    : "rgba(107, 114, 128, 0.08)";

  const entry = trade.entry || 0;
  const sl = trade.sl || 0;
  const t1 = trade.t1 || 0;
  const t2 = trade.t2 || 0;

  // Calculate P&L amounts (assume standard lot)
  const lotSize = index === "NIFTY" ? 25 : 15;
  const lots = winProb >= 90 ? 4 : winProb >= 80 ? 3 : winProb >= 70 ? 2 : 1;
  const qty = lots * lotSize;
  const maxLoss = entry && sl ? Math.round((entry - sl) * qty) : 0;
  const t1Profit = entry && t1 ? Math.round((t1 - entry) * qty) : 0;
  const t2Profit = entry && t2 ? Math.round((t2 - entry) * qty) : 0;
  const rr = entry && sl && t1 ? ((t1 - entry) / (entry - sl)).toFixed(1) : 0;

  // Theta estimate (4% of premium per hour)
  const thetaHr = entry ? Math.round(entry * 0.04) : 0;

  const predictive = verdict.predictive || {};
  const momentum = predictive.momentum || "N/A";

  return (
    <div
      style={{
        padding: SPACE.LG,
        background: bgColor,
        border: `2px solid ${color}66`,
        borderRadius: RADIUS.LG,
        display: "flex",
        flexDirection: "column",
        gap: SPACE.SM,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-secondary, #888)", letterSpacing: 1 }}>
            {index} {verdict.isExpiryDay && " · 🔔 EXPIRY"}
          </div>
          <div
            style={{
              fontSize: 28,
              fontWeight: 900,
              color,
              fontFamily: FONT.MONO || "monospace",
              marginTop: 4,
            }}
          >
            {tooLow ? "⏳ WAIT" : `${action} ${strike}`}
          </div>
        </div>
        <div
          style={{
            padding: `${SPACE.XS}px ${SPACE.SM}px`,
            background: color + "22",
            borderRadius: RADIUS.MD,
            fontWeight: 800,
            fontSize: 16,
            color,
            fontFamily: FONT.MONO || "monospace",
          }}
        >
          {winProb}%
        </div>
      </div>

      {/* Low conviction — only blocks below 50% (very rare) */}
      {tooLow && (
        <div
          style={{
            padding: SPACE.MD,
            background: "rgba(245, 158, 11, 0.1)",
            borderLeft: `3px solid ${YELLOW}`,
            borderRadius: 4,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div style={{ fontSize: 13, color: YELLOW, fontWeight: 700 }}>
            ⏳ NO CLEAR EDGE — Both sides voting equal ({winProb}%)
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary, #aaa)" }}>
            <b>Direction lean:</b> {action} but engines split
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary, #aaa)" }}>
            <b>Auto-recheck:</b> Every 60 sec — signal when one side crosses 50%
          </div>
          <div
            style={{
              marginTop: 4,
              padding: "4px 8px",
              background: "rgba(255,255,255,0.06)",
              borderRadius: 4,
              fontSize: 11,
              color: "var(--text-primary, #fff)",
            }}
          >
            👉 If you see clear chart move, take trade manually on Kite.
            Auto-trade fires at 50%+ probability.
          </div>
        </div>
      )}

      {/* Trade math (only show if valid setup) */}
      {!tooLow && entry > 0 && (
        <>
          {/* Live LTP banner with delta from entry */}
          {liveLtp !== null && liveLtp > 0 && (
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: `${SPACE.XS}px ${SPACE.SM}px`,
              background: "rgba(255,255,255,0.04)",
              borderRadius: RADIUS.SM,
              fontSize: 12,
            }}>
              <span style={{ color: "var(--text-secondary, #999)" }}>
                Live LTP <b style={{ fontFamily: FONT.MONO || "monospace", color: "var(--text-primary, #fff)" }}>₹{liveLtp.toFixed(2)}</b>
              </span>
              <span style={{
                color: liveLtp >= entry ? GREEN : RED,
                fontFamily: FONT.MONO || "monospace",
                fontWeight: 700,
              }}>
                {liveLtp >= entry ? "+" : ""}{((liveLtp - entry) / entry * 100).toFixed(2)}% from entry
              </span>
            </div>
          )}

          {/* Price row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: SPACE.XS, marginTop: SPACE.XS }}>
            <PriceBox label="ENTRY" value={`₹${entry}`} color={color} highlight />
            <PriceBox label="SL" value={`₹${sl}`} subtext={maxLoss ? `-₹${Math.abs(maxLoss).toLocaleString()}` : ""} color={RED} />
            <PriceBox label="T1" value={`₹${t1}`} subtext={t1Profit ? `+₹${t1Profit.toLocaleString()}` : ""} color={GREEN} />
            <PriceBox label="T2" value={`₹${t2}`} subtext={t2Profit ? `+₹${t2Profit.toLocaleString()}` : ""} color={GREEN} />
          </div>

          {/* Metadata row */}
          <div style={{ display: "flex", gap: SPACE.SM, flexWrap: "wrap", fontSize: 11, color: "var(--text-secondary, #888)", marginTop: SPACE.XS }}>
            <span>Size: <b style={{ color: "var(--text-primary, #fff)" }}>{lots}L × {lotSize} = {qty}qty</b></span>
            <span>·</span>
            <span>R:R <b style={{ color: rr >= 2 ? GREEN : rr >= 1.5 ? YELLOW : RED }}>1:{rr}</b></span>
            <span>·</span>
            <span>Theta <b style={{ color: YELLOW }}>-₹{thetaHr}/hr</b></span>
            <span>·</span>
            <span>Momentum <b style={{ color: momentum.includes("UP") ? GREEN : momentum.includes("DOWN") ? RED : GRAY }}>{momentum}</b></span>
          </div>

          {/* Last updated timestamp */}
          <div style={{ fontSize: 10, color: "var(--text-secondary, #777)", marginTop: 2 }}>
            Updated {verdictAge}s ago · Refreshes every 60s · Live LTP every 5s
          </div>
        </>
      )}

      {/* Top reasons */}
      {reasons && reasons.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: SPACE.XS }}>
          {reasons.slice(0, 3).map((r, i) => (
            <div key={i} style={{ fontSize: 11, color: "var(--text-secondary, #999)", paddingLeft: 12, position: "relative" }}>
              <span style={{ position: "absolute", left: 0, color }}>›</span>
              {typeof r === "string" ? r : r.text || JSON.stringify(r)}
            </div>
          ))}
        </div>
      )}

      {/* Action button */}
      {!tooLow && entry > 0 && (
        <div style={{ display: "flex", gap: SPACE.SM, marginTop: SPACE.SM }}>
          <button
            style={{
              flex: 1,
              padding: `${SPACE.SM}px ${SPACE.MD}px`,
              background: color,
              color: "#fff",
              border: "none",
              borderRadius: RADIUS.MD,
              fontWeight: 700,
              fontSize: 13,
              cursor: "pointer",
              letterSpacing: 0.5,
            }}
            onClick={() => alert(`Execute on Kite: ${action} ${strike} @ ₹${entry}\nSL: ₹${sl} | T1: ₹${t1}\nSize: ${qty} qty`)}
          >
            ✓ EXECUTE ON KITE
          </button>
          <button
            style={{
              padding: `${SPACE.SM}px ${SPACE.MD}px`,
              background: "transparent",
              color: "var(--text-primary, #fff)",
              border: `1px solid ${color}44`,
              borderRadius: RADIUS.MD,
              fontWeight: 600,
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            ⏱ SET ALERT
          </button>
        </div>
      )}
    </div>
  );
}

function PriceBox({ label, value, subtext, color, highlight }) {
  return (
    <div
      style={{
        padding: SPACE.SM,
        background: highlight ? color + "11" : "rgba(255,255,255,0.02)",
        border: `1px solid ${color}33`,
        borderRadius: RADIUS.MD,
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: 9, color: "var(--text-secondary, #888)", letterSpacing: 1 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 800, color, fontFamily: FONT.MONO || "monospace", marginTop: 2 }}>
        {value}
      </div>
      {subtext && <div style={{ fontSize: 9, color: color, marginTop: 2, fontFamily: FONT.MONO || "monospace" }}>{subtext}</div>}
    </div>
  );
}

// ═══════════════════════════════════════════════════════
// 3. OPEN POSITIONS QUICK VIEW
// ═══════════════════════════════════════════════════════

export function OpenPositionsQuick({ positions }) {
  if (!positions || positions.length === 0) {
    return (
      <div
        style={{
          padding: SPACE.MD,
          background: "rgba(107,114,128,0.06)",
          borderRadius: RADIUS.LG,
          border: "1px dashed rgba(107,114,128,0.3)",
          textAlign: "center",
          color: "#888",
          fontSize: 13,
        }}
      >
        No open positions · Auto-enters when verdict ≥ 70% conviction
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SPACE.SM }}>
      <div style={{ fontSize: 12, color: "var(--text-secondary, #888)", letterSpacing: 1 }}>
        OPEN POSITIONS ({positions.length})
      </div>
      {positions.map((t) => {
        const pnl = t.pnl_rupees || 0;
        const isProfit = pnl >= 0;
        const color = isProfit ? GREEN : RED;
        const pnlPct = t.entry_price > 0 ? ((t.current_ltp - t.entry_price) / t.entry_price * 100).toFixed(1) : 0;
        return (
          <div
            key={t.id}
            style={{
              padding: SPACE.MD,
              background: color + "0D",
              border: `1px solid ${color}44`,
              borderRadius: RADIUS.MD,
              display: "grid",
              gridTemplateColumns: "2fr 1fr 1fr 1fr",
              gap: SPACE.SM,
              alignItems: "center",
            }}
          >
            <div>
              <div style={{ fontSize: 13, fontWeight: 700 }}>
                {t.idx} {t.action} {t.strike}
              </div>
              <div style={{ fontSize: 10, color: "var(--text-secondary, #888)" }}>
                Entry ₹{t.entry_price} · LTP ₹{t.current_ltp}
              </div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 10, color: "var(--text-secondary, #888)" }}>P&L</div>
              <div style={{ fontSize: 14, fontWeight: 800, color }}>
                ₹{pnl >= 0 ? "+" : ""}{pnl.toLocaleString()}
              </div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 10, color: "var(--text-secondary, #888)" }}>%</div>
              <div style={{ fontSize: 14, fontWeight: 800, color }}>
                {pnlPct >= 0 ? "+" : ""}{pnlPct}%
              </div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 10, color: "var(--text-secondary, #888)" }}>SL</div>
              <div style={{ fontSize: 13, fontFamily: FONT.MONO || "monospace", fontWeight: 700 }}>
                ₹{t.sl_price}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ═══════════════════════════════════════════════════════
// MAIN COCKPIT WRAPPER
// ═══════════════════════════════════════════════════════

export default function BuyerCockpit({ live, verdicts, reasonsMap, openPositions: propPositions }) {
  const nifty = verdicts?.nifty;
  const bn = verdicts?.banknifty;
  const [fetchedPositions, setFetchedPositions] = useState([]);

  useEffect(() => {
    let cancelled = false;
    const fetchPositions = async () => {
      try {
        const res = await fetch("/api/trades/open");
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled && Array.isArray(data)) setFetchedPositions(data);
      } catch {}
    };
    fetchPositions();
    const iv = setInterval(() => { if (document.visibilityState === "visible") fetchPositions(); }, 15000);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const openPositions = propPositions && propPositions.length > 0 ? propPositions : fetchedPositions;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SPACE.LG }}>
      {/* 1. GO/NO-GO Banner */}
      <GoNoGoBanner live={live} niftyVerdict={nifty} bnVerdict={bn} />

      {/* 2. Dual Verdict Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: SPACE.MD,
        }}
      >
        <EnhancedVerdictCard index="NIFTY" verdict={nifty} reasons={reasonsMap?.nifty || nifty?.reasons} />
        <EnhancedVerdictCard index="BANKNIFTY" verdict={bn} reasons={reasonsMap?.banknifty || bn?.reasons} />
      </div>

      {/* BUYER MODE Toggle — philosophy switch (HEDGER ↔ BUYER) */}
      <BuyerModeToggle />

      {/* 3. Open Positions (swing) */}
      <OpenPositionsQuick positions={openPositions} />

      {/* OI Insight — TODAY's OI Change vs TOTAL OI with buyer interpretation */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 12 }}>
        <OIInsightPanel index="NIFTY" />
        <OIInsightPanel index="BANKNIFTY" />
      </div>

      {/* 5. Live OI Heatmap (with running spot line) */}
      <OIHeatmap live={live} />

      {/* 6. Next Day Predictor */}
      <NextDayPredictor />

      {/* 7. Smart Autopsy Mind — pattern-based predictive alerts */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 12 }}>
        <AutopsyMindWidget index="NIFTY" />
        <AutopsyMindWidget index="BANKNIFTY" />
      </div>
    </div>
  );
}
