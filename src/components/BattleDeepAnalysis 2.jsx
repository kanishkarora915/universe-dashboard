import { useEffect, useState, useMemo } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS } from "../theme";

// Helper: simple fetch
async function fetchJ(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

/**
 * Deep analysis sections for Battle Station.
 * Adds: BUY/DON'T BUY verdict, Price Journey, Breakeven probability,
 * Theta burn timer, IV context, Max Pain proximity, Event risks, Liquidity.
 *
 * Fetches /api/live and /api/trap/verdict to augment strike data.
 */

function Section({ title, accent, children, theme }) {
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

// ═════════════════ SIMPLE BUY / DON'T BUY VERDICT ═════════════════

export function SimpleVerdict({ strike, spot, verdict, theme }) {
  if (!strike) return null;

  // Heuristics for BUY/DON'T BUY based on strike data + verdict
  const ce = strike.ceLTP || 0;
  const pe = strike.peLTP || 0;
  const isCE = !strike.type || strike.type === "CE";

  // Use the backend verdict if available
  const backendAction = verdict?.[strike.index?.toLowerCase()]?.action || verdict?.action;
  const backendConf = verdict?.[strike.index?.toLowerCase()]?.winProbability || verdict?.winProbability || 0;

  // Compute our own signal
  const moneyness = strike.moneyness || 0;
  const signals = [];
  let score = 0;

  if (backendAction?.includes("CE") && isCE) {
    score += 3;
    signals.push({ tone: "good", text: "Engine confirms BUY CE signal" });
  }
  if (backendAction?.includes("PE") && !isCE) {
    score += 3;
    signals.push({ tone: "good", text: "Engine confirms BUY PE signal" });
  }
  if (backendConf >= 70) {
    score += 2;
    signals.push({ tone: "good", text: `High confidence: ${backendConf}%` });
  } else if (backendConf >= 50) {
    score += 1;
  }

  if (Math.abs(moneyness) < 0.5) {
    score += 1;
    signals.push({ tone: "good", text: "ATM strike — balanced risk/reward" });
  } else if (Math.abs(moneyness) > 2) {
    score -= 1;
    signals.push({ tone: "bad", text: `Far OTM (${Math.abs(moneyness).toFixed(1)}%) — low probability` });
  }

  const theta = strike.greeks?.thetaCE || strike.greeks?.thetaPE || 0;
  if (Math.abs(theta) > 8) {
    score -= 1;
    signals.push({ tone: "bad", text: `High theta decay: ₹${Math.abs(theta).toFixed(1)}/day` });
  }

  const iv = strike.iv || 0;
  if (iv > 25) {
    score -= 1;
    signals.push({ tone: "bad", text: `IV ${iv.toFixed(0)}% — premium expensive` });
  } else if (iv > 0 && iv < 15) {
    score += 1;
    signals.push({ tone: "good", text: `IV ${iv.toFixed(0)}% — premium cheap` });
  }

  // Decide
  let decision, color, emoji;
  if (score >= 3) {
    decision = "BUY";
    color = theme.GREEN;
    emoji = "✅";
  } else if (score >= 1) {
    decision = "MAYBE";
    color = theme.AMBER;
    emoji = "⚠";
  } else {
    decision = "DON'T BUY";
    color = theme.RED;
    emoji = "❌";
  }

  return (
    <Section title="🎯 Quick Verdict" accent={color} theme={theme}>
      <div style={{ display: "flex", alignItems: "center", gap: SPACE.LG, marginBottom: SPACE.MD }}>
        <div
          style={{
            fontSize: 56,
            fontWeight: TEXT_WEIGHT.BLACK,
            color,
            fontFamily: FONT.MONO,
            letterSpacing: 2,
            lineHeight: 1,
          }}
        >
          {emoji} {decision}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ color: theme.TEXT_DIM, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase", marginBottom: 4 }}>
            Signal score
          </div>
          <div
            style={{
              color: theme.TEXT,
              fontSize: 28,
              fontWeight: TEXT_WEIGHT.BOLD,
              fontFamily: FONT.MONO,
            }}
          >
            {score > 0 ? "+" : ""}{score}
          </div>
          {backendConf > 0 && (
            <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, marginTop: 4 }}>
              Engine confidence: {backendConf}%
            </div>
          )}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {signals.length > 0 ? signals.map((s, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: SPACE.SM,
              color: s.tone === "good" ? theme.GREEN : theme.RED,
              fontSize: TEXT_SIZE.BODY,
              padding: "2px 0",
            }}
          >
            <span style={{ fontSize: 12 }}>{s.tone === "good" ? "✓" : "✗"}</span>
            <span>{s.text}</span>
          </div>
        )) : (
          <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, fontStyle: "italic" }}>
            Insufficient signals — click Ask AI for deeper analysis.
          </div>
        )}
      </div>
    </Section>
  );
}

// ═════════════════ PRICE JOURNEY ═════════════════

export function PriceJourney({ index, liveData, theme }) {
  const d = liveData?.[index?.toLowerCase()] || {};
  const ltp = d.ltp || d.price || 0;
  const open = d.open || 0;
  const high = d.high || 0;
  const low = d.low || 0;
  const prevClose = d.prev_close || d.prevClose || 0;

  if (!ltp) {
    return (
      <Section title="📈 Price Journey" theme={theme}>
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
          Live spot data not available.
        </div>
      </Section>
    );
  }

  const dayChange = prevClose ? ltp - prevClose : 0;
  const dayChangePct = prevClose ? (dayChange / prevClose) * 100 : 0;
  const openToNow = open ? ltp - open : 0;
  const openToNowPct = open ? (openToNow / open) * 100 : 0;
  const dayRange = high - low;
  const posInRange = dayRange ? ((ltp - low) / dayRange) * 100 : 50;

  const fmt = (n) => n ? n.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";

  return (
    <Section title="📈 Price Journey" theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: SPACE.MD, marginBottom: SPACE.MD }}>
        <Stat label="Prev Close" value={fmt(prevClose)} theme={theme} />
        <Stat label="Today's Open" value={fmt(open)} theme={theme} />
        <Stat label="Current" value={fmt(ltp)} color={theme.ACCENT} theme={theme} />
        <Stat label="Day's High" value={fmt(high)} color={theme.GREEN} theme={theme} />
        <Stat label="Day's Low" value={fmt(low)} color={theme.RED} theme={theme} />
      </div>

      {/* Day range bar */}
      {dayRange > 0 && (
        <div style={{ marginBottom: SPACE.MD }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: TEXT_SIZE.MICRO, color: theme.TEXT_DIM, marginBottom: 4 }}>
            <span>Low {fmt(low)}</span>
            <span style={{ color: theme.ACCENT, fontWeight: TEXT_WEIGHT.BOLD }}>
              {posInRange.toFixed(0)}% of day range
            </span>
            <span>High {fmt(high)}</span>
          </div>
          <div style={{ position: "relative", height: 8, background: theme.SURFACE_HI, borderRadius: 4 }}>
            <div
              style={{
                position: "absolute",
                left: `${posInRange}%`,
                top: -3,
                transform: "translateX(-50%)",
                width: 4,
                height: 14,
                background: theme.ACCENT,
                borderRadius: 2,
                boxShadow: `0 0 8px ${theme.ACCENT}`,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                bottom: 0,
                width: `${posInRange}%`,
                background: `linear-gradient(90deg, ${theme.RED}22, ${theme.GREEN}22)`,
                borderRadius: 4,
              }}
            />
          </div>
        </div>
      )}

      {/* Change summaries */}
      <div style={{ display: "flex", gap: SPACE.MD, flexWrap: "wrap" }}>
        <div style={{ fontSize: TEXT_SIZE.MICRO }}>
          <span style={{ color: theme.TEXT_DIM, marginRight: 6 }}>Since prev close:</span>
          <span style={{ color: dayChange >= 0 ? theme.GREEN : theme.RED, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>
            {dayChange >= 0 ? "+" : ""}{fmt(dayChange)} ({dayChangePct >= 0 ? "+" : ""}{dayChangePct.toFixed(2)}%)
          </span>
        </div>
        <div style={{ fontSize: TEXT_SIZE.MICRO }}>
          <span style={{ color: theme.TEXT_DIM, marginRight: 6 }}>Since open:</span>
          <span style={{ color: openToNow >= 0 ? theme.GREEN : theme.RED, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>
            {openToNow >= 0 ? "+" : ""}{fmt(openToNow)} ({openToNowPct >= 0 ? "+" : ""}{openToNowPct.toFixed(2)}%)
          </span>
        </div>
      </div>
    </Section>
  );
}

// ═════════════════ BREAKEVEN PROBABILITY ═════════════════

export function BreakevenAnalysis({ strike, spot, theme }) {
  if (!strike || !spot) return null;

  const ce = strike.ceLTP || 0;
  const pe = strike.peLTP || 0;

  if (!ce && !pe) return null;

  // Breakeven: spot needs to move by premium amount
  const ceBreakeven = strike.strike + ce;
  const peBreakeven = strike.strike - pe;
  const ceMovePct = ((ceBreakeven - spot) / spot) * 100;
  const peMovePct = ((spot - peBreakeven) / spot) * 100;

  // Rough probability heuristic: intraday moves > 1% happen ~20-30% of days
  const getProb = (movePct) => {
    const abs = Math.abs(movePct);
    if (abs < 0.3) return 85;
    if (abs < 0.5) return 70;
    if (abs < 1.0) return 45;
    if (abs < 1.5) return 25;
    if (abs < 2.0) return 12;
    return 5;
  };

  return (
    <Section title="🎯 Breakeven Analysis" accent={theme.CYAN} theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
        {ce > 0 && (
          <div style={{ padding: SPACE.MD, background: theme.GREEN_DIM, borderRadius: RADIUS.MD, borderLeft: `2px solid ${theme.GREEN}` }}>
            <div style={{ color: theme.GREEN, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase", marginBottom: 4 }}>
              CE Breakeven
            </div>
            <div style={{ color: theme.TEXT, fontSize: 20, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>
              {ceBreakeven.toFixed(0)}
            </div>
            <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, marginTop: 4 }}>
              Spot needs to rise <strong style={{ color: theme.GREEN }}>{ceMovePct.toFixed(2)}%</strong>
            </div>
            <div style={{ color: theme.TEXT_DIM, fontSize: 10, marginTop: 4 }}>
              Historical probability: <strong style={{ color: theme.ACCENT }}>~{getProb(ceMovePct)}%</strong>
            </div>
          </div>
        )}
        {pe > 0 && (
          <div style={{ padding: SPACE.MD, background: theme.RED_DIM, borderRadius: RADIUS.MD, borderLeft: `2px solid ${theme.RED}` }}>
            <div style={{ color: theme.RED, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase", marginBottom: 4 }}>
              PE Breakeven
            </div>
            <div style={{ color: theme.TEXT, fontSize: 20, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>
              {peBreakeven.toFixed(0)}
            </div>
            <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, marginTop: 4 }}>
              Spot needs to fall <strong style={{ color: theme.RED }}>{peMovePct.toFixed(2)}%</strong>
            </div>
            <div style={{ color: theme.TEXT_DIM, fontSize: 10, marginTop: 4 }}>
              Historical probability: <strong style={{ color: theme.ACCENT }}>~{getProb(peMovePct)}%</strong>
            </div>
          </div>
        )}
      </div>
      <div style={{ marginTop: SPACE.SM, color: theme.TEXT_DIM, fontSize: 10, fontStyle: "italic" }}>
        Probabilities are estimates based on typical intraday NIFTY/BANKNIFTY volatility. Not exact.
      </div>
    </Section>
  );
}

// ═════════════════ THETA BURN TIMER ═════════════════

export function ThetaBurn({ strike, lotSize = 75, theme }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const iv = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(iv);
  }, []);

  if (!strike) return null;

  const theta = strike.greeks?.thetaCE || strike.greeks?.thetaPE || 0;
  if (theta === 0) return null;

  const absTheta = Math.abs(theta);
  const burnPerHour = (absTheta / 6.25) * lotSize; // 6.25 hours market open
  const burnPerMinute = burnPerHour / 60;
  const burnPer15Min = burnPerMinute * 15;
  const burnedThisView = (elapsed / 60) * burnPerMinute;

  return (
    <Section title="⏳ Theta Burn (time decay)" accent={theme.AMBER} theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: SPACE.MD, marginBottom: SPACE.MD }}>
        <Stat label="Per day" value={`₹${Math.round(absTheta * lotSize).toLocaleString("en-IN")}`} color={theme.AMBER} theme={theme} />
        <Stat label="Per hour" value={`₹${Math.round(burnPerHour).toLocaleString("en-IN")}`} color={theme.AMBER} theme={theme} />
        <Stat label="Per 15 min" value={`₹${Math.round(burnPer15Min).toLocaleString("en-IN")}`} color={theme.AMBER} theme={theme} />
        <Stat label="Per minute" value={`₹${burnPerMinute.toFixed(1)}`} color={theme.AMBER} theme={theme} />
      </div>
      <div style={{ padding: SPACE.SM, background: theme.AMBER_DIM, borderRadius: RADIUS.SM, borderLeft: `2px solid ${theme.AMBER}` }}>
        <div style={{ color: theme.AMBER, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase", marginBottom: 2 }}>
          Burned since this view opened
        </div>
        <div style={{ color: theme.TEXT, fontSize: 18, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>
          ₹{burnedThisView.toFixed(2)} <span style={{ color: theme.TEXT_DIM, fontSize: 11, fontWeight: TEXT_WEIGHT.MED }}>({elapsed}s)</span>
        </div>
      </div>
    </Section>
  );
}

// ═════════════════ IV CONTEXT ═════════════════

export function IVContext({ strike, theme }) {
  if (!strike) return null;
  const iv = strike.iv || 0;
  if (!iv) return null;

  let status, color, advice;
  if (iv < 12) {
    status = "CHEAP";
    color = theme.GREEN;
    advice = "Premium is cheap — favorable for buying options. Low volatility priced in.";
  } else if (iv < 18) {
    status = "MODERATE";
    color = theme.ACCENT;
    advice = "Typical IV range. Balanced buying/selling conditions.";
  } else if (iv < 25) {
    status = "ELEVATED";
    color = theme.AMBER;
    advice = "Premium slightly expensive. Consider smaller size or wait for IV to cool.";
  } else {
    status = "EXPENSIVE";
    color = theme.RED;
    advice = "IV very high — options overpriced. Favor selling strategies or avoid.";
  }

  return (
    <Section title="💹 Volatility Context (IV)" accent={color} theme={theme}>
      <div style={{ display: "flex", alignItems: "center", gap: SPACE.LG, marginBottom: SPACE.MD }}>
        <div>
          <div style={{ color: theme.TEXT_DIM, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase" }}>
            Current IV
          </div>
          <div style={{ color: theme.TEXT, fontSize: 32, fontWeight: TEXT_WEIGHT.BLACK, fontFamily: FONT.MONO }}>
            {iv.toFixed(1)}%
          </div>
        </div>
        <div
          style={{
            padding: "4px 12px",
            background: color + "22",
            border: `1px solid ${color}44`,
            borderRadius: RADIUS.SM,
            color,
            fontSize: TEXT_SIZE.MICRO,
            fontWeight: TEXT_WEIGHT.BOLD,
            letterSpacing: 1.5,
          }}
        >
          {status}
        </div>
      </div>
      <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.BODY, lineHeight: 1.5 }}>
        {advice}
      </div>
    </Section>
  );
}

// ═════════════════ EVENT RISK ═════════════════

export function EventRisk({ strike, spot, theme }) {
  if (!strike) return null;
  const now = new Date();
  const hours = now.getHours();
  const minutes = now.getMinutes();
  const day = now.getDay(); // 0=Sun, 4=Thu

  const warnings = [];

  // Expiry day detection per index
  // NIFTY weekly: Tuesday (day 2)
  // BANKNIFTY: monthly only (last Thursday of month) — checked below
  const idx = (strike.index || "").toUpperCase();
  if (idx === "NIFTY" && day === 2) {
    warnings.push({ level: "critical", text: "Today is Tuesday — NIFTY weekly expiry. Theta decay accelerates sharply." });
  }

  // BANKNIFTY monthly expiry: last Thursday of the month
  if (idx === "BANKNIFTY" && day === 4) {
    const date = now.getDate();
    const month = now.getMonth();
    const year = now.getFullYear();
    // Find last Thursday of this month
    const lastDay = new Date(year, month + 1, 0).getDate();
    let lastThursday = lastDay;
    const lastDow = new Date(year, month, lastDay).getDay();
    if (lastDow !== 4) {
      lastThursday = lastDay - ((lastDow - 4 + 7) % 7);
    }
    if (date === lastThursday) {
      warnings.push({ level: "critical", text: "Today is monthly BANKNIFTY expiry. Theta decay accelerates sharply." });
    }
  }

  // Market hours context
  if (hours === 15 && minutes >= 0) {
    warnings.push({ level: "critical", text: "Last hour of trading — expect volatility spike + IV crush at close." });
  }
  if (hours === 9 && minutes < 30) {
    warnings.push({ level: "warn", text: "Opening 15 min — extreme volatility, avoid fresh entries." });
  }
  if (hours === 14 && minutes >= 30) {
    warnings.push({ level: "warn", text: "2:30 PM — institutional positioning window, expect direction reveal." });
  }

  // Far OTM warning
  const moneyness = strike.moneyness || 0;
  if (Math.abs(moneyness) > 2) {
    warnings.push({ level: "warn", text: `Far OTM (${moneyness.toFixed(1)}%) — needs big move to profit.` });
  }

  // Low liquidity (very low OI)
  const ceOI = strike.ceOI || 0;
  const peOI = strike.peOI || 0;
  if (ceOI < 50000 && peOI < 50000 && (strike.ceLTP || strike.peLTP)) {
    warnings.push({ level: "warn", text: `Low OI (<0.5L on both sides) — thin liquidity, wider spreads.` });
  }

  // Pre-weekend risk (Friday afternoon)
  if (day === 5 && hours >= 13) {
    warnings.push({ level: "info", text: "Friday afternoon — weekend gap risk on Monday open." });
  }

  if (warnings.length === 0) {
    return (
      <Section title="✅ Event Risks" accent={theme.GREEN} theme={theme}>
        <div style={{ color: theme.GREEN, fontSize: TEXT_SIZE.BODY }}>
          No major timing risks detected for this window.
        </div>
      </Section>
    );
  }

  return (
    <Section title="⚠ Event Risks" accent={theme.AMBER} theme={theme}>
      <div style={{ display: "flex", flexDirection: "column", gap: SPACE.SM }}>
        {warnings.map((w, i) => {
          const color = w.level === "critical" ? theme.RED : w.level === "warn" ? theme.AMBER : theme.CYAN;
          return (
            <div
              key={i}
              style={{
                display: "flex",
                gap: SPACE.SM,
                padding: SPACE.SM,
                background: color + "15",
                borderLeft: `2px solid ${color}`,
                borderRadius: RADIUS.SM,
                fontSize: TEXT_SIZE.BODY,
                color: theme.TEXT,
              }}
            >
              <span style={{ color }}>{w.level === "critical" ? "🔴" : w.level === "warn" ? "⚠" : "ℹ"}</span>
              <span>{w.text}</span>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

// ═════════════════ SUPPORT / RESISTANCE ═════════════════

export function SupportResistance({ strikes, spot, theme }) {
  if (!strikes || !strikes.length) return null;
  // Use first strike's chain context (approximation since we only have pinned strikes)
  const s = strikes[0];
  if (!s.pcr) return null;

  const pcr = s.pcr;
  let marketBias, color;
  if (pcr > 1.3) {
    marketBias = "BULLISH (strong PE writing = support building)";
    color = theme.GREEN;
  } else if (pcr > 1.1) {
    marketBias = "Mildly bullish";
    color = theme.GREEN;
  } else if (pcr > 0.9) {
    marketBias = "Neutral";
    color = theme.TEXT_MUTED;
  } else if (pcr > 0.7) {
    marketBias = "Mildly bearish";
    color = theme.RED;
  } else {
    marketBias = "BEARISH (strong CE writing = resistance)";
    color = theme.RED;
  }

  return (
    <Section title="🧭 Market Structure" accent={color} theme={theme}>
      <div style={{ display: "flex", gap: SPACE.LG, flexWrap: "wrap" }}>
        <Stat label="PCR" value={pcr.toFixed(2)} color={color} theme={theme} />
        {s.maxPain && <Stat label="Max Pain" value={s.maxPain} theme={theme} />}
        {s.maxPain && spot && (
          <Stat
            label="Distance to Max Pain"
            value={`${((s.maxPain - spot) / spot * 100).toFixed(2)}%`}
            color={theme.AMBER}
            theme={theme}
          />
        )}
      </div>
      <div style={{ marginTop: SPACE.MD, color: theme.TEXT, fontSize: TEXT_SIZE.BODY }}>
        <strong style={{ color }}>{marketBias}</strong>
      </div>
    </Section>
  );
}

// ═════════════════ Stat helper ═════════════════

function Stat({ label, value, color, theme, sub }) {
  return (
    <div style={{ minWidth: 100 }}>
      <div style={{ color: theme.TEXT_DIM, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase", marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ color: color || theme.TEXT, fontSize: 16, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>
        {value}
      </div>
      {sub && (
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, marginTop: 2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ═════════════════ BONUS #1: PRICE SPARKLINE ═════════════════

export function PriceSparkline({ strike, theme }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!strike) return;
    setLoading(true);
    fetchJ(`/api/strike-history?index=${strike.index}&strike=${strike.strike}&minutes=30`)
      .then((d) => {
        setHistory(d?.points || []);
        setLoading(false);
      });
  }, [strike?.index, strike?.strike]);

  if (loading) {
    return <Section title="📉 Price Sparkline (last 30 min)" theme={theme}><div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>Loading...</div></Section>;
  }

  if (!history.length) {
    return <Section title="📉 Price Sparkline (last 30 min)" theme={theme}><div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>No history available (backend needs trading_times data)</div></Section>;
  }

  // Build SVG for spot + CE LTP + PE LTP
  const w = 600, h = 80, pad = 20;
  const spotSeries = history.map((p) => p.spot || 0);
  const minS = Math.min(...spotSeries);
  const maxS = Math.max(...spotSeries);
  const rangeS = maxS - minS || 1;
  const spotPath = history.map((p, i) => {
    const x = pad + (i / Math.max(history.length - 1, 1)) * (w - 2 * pad);
    const y = pad + ((maxS - p.spot) / rangeS) * (h - 2 * pad);
    return `${i === 0 ? "M" : "L"}${x},${y}`;
  }).join(" ");

  const first = spotSeries[0];
  const last = spotSeries[spotSeries.length - 1];
  const diff = last - first;
  const diffPct = first ? (diff / first) * 100 : 0;

  return (
    <Section title="📉 Price Sparkline (last 30 min)" theme={theme}>
      <div style={{ display: "flex", alignItems: "center", gap: SPACE.LG, marginBottom: SPACE.SM }}>
        <Stat label="Start" value={first?.toFixed(0) || "—"} theme={theme} />
        <Stat label="Now" value={last?.toFixed(0) || "—"} color={theme.ACCENT} theme={theme} />
        <Stat
          label="Change"
          value={`${diff > 0 ? "+" : ""}${diff.toFixed(1)} (${diffPct > 0 ? "+" : ""}${diffPct.toFixed(2)}%)`}
          color={diff >= 0 ? theme.GREEN : theme.RED}
          theme={theme}
        />
        <Stat label="Samples" value={history.length} theme={theme} />
      </div>
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ background: theme.SURFACE_HI, borderRadius: RADIUS.SM }}>
        <path d={spotPath} fill="none" stroke={diff >= 0 ? theme.GREEN : theme.RED} strokeWidth={2} />
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke={theme.BORDER} strokeWidth={0.5} />
      </svg>
      <div style={{ color: theme.TEXT_DIM, fontSize: 10, marginTop: 4, fontStyle: "italic" }}>
        Shows spot price trend for {strike.index} over last 30 min
      </div>
    </Section>
  );
}

// ═════════════════ BONUS #2: OPENING P&L ═════════════════

export function OpeningPnL({ strike, liveData, lotSize = 75, theme }) {
  if (!strike) return null;
  const ce = strike.ceLTP || 0;
  const pe = strike.peLTP || 0;
  if (!ce && !pe) return null;

  // Best-effort: we don't have exact opening LTP per strike stored.
  // Use moneyness / premium ratio to approximate.
  // Assumption: opening ATM options typically 70-75% of current if no big move.
  const d = liveData?.[strike.index?.toLowerCase()] || {};
  const open = d.open || d.openPrice;
  const currentSpot = d.ltp || strike.spot || 0;

  if (!open || !currentSpot) {
    return (
      <Section title="💰 If you entered at 9:15 open" theme={theme}>
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
          Opening spot data not available yet.
        </div>
      </Section>
    );
  }

  const spotChange = currentSpot - open;
  // Rough CE opening price estimate: current - delta * spot_change
  const deltaCE = strike.greeks?.deltaCE || 0.5;
  const deltaPE = strike.greeks?.deltaPE || -0.5;
  const estOpenCE = ce - deltaCE * spotChange;
  const estOpenPE = pe - deltaPE * spotChange;

  const pnlCE = (ce - estOpenCE) * lotSize;
  const pnlPE = (pe - estOpenPE) * lotSize;

  return (
    <Section title="💰 If you entered at 9:15 open" accent={theme.CYAN} theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
        {ce > 0 && (
          <div style={{ padding: SPACE.MD, background: pnlCE >= 0 ? theme.GREEN_DIM : theme.RED_DIM, borderRadius: RADIUS.MD, borderLeft: `2px solid ${pnlCE >= 0 ? theme.GREEN : theme.RED}` }}>
            <div style={{ color: theme.TEXT_DIM, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase" }}>BUY CE AT OPEN</div>
            <div style={{ color: pnlCE >= 0 ? theme.GREEN : theme.RED, fontSize: 22, fontWeight: TEXT_WEIGHT.BLACK, fontFamily: FONT.MONO }}>
              ₹{pnlCE >= 0 ? "+" : ""}{Math.round(pnlCE).toLocaleString("en-IN")}
            </div>
            <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO }}>
              Est. open ₹{estOpenCE.toFixed(0)} → now ₹{ce}
            </div>
          </div>
        )}
        {pe > 0 && (
          <div style={{ padding: SPACE.MD, background: pnlPE >= 0 ? theme.GREEN_DIM : theme.RED_DIM, borderRadius: RADIUS.MD, borderLeft: `2px solid ${pnlPE >= 0 ? theme.GREEN : theme.RED}` }}>
            <div style={{ color: theme.TEXT_DIM, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase" }}>BUY PE AT OPEN</div>
            <div style={{ color: pnlPE >= 0 ? theme.GREEN : theme.RED, fontSize: 22, fontWeight: TEXT_WEIGHT.BLACK, fontFamily: FONT.MONO }}>
              ₹{pnlPE >= 0 ? "+" : ""}{Math.round(pnlPE).toLocaleString("en-IN")}
            </div>
            <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO }}>
              Est. open ₹{estOpenPE.toFixed(0)} → now ₹{pe}
            </div>
          </div>
        )}
      </div>
      <div style={{ color: theme.TEXT_DIM, fontSize: 10, marginTop: SPACE.SM, fontStyle: "italic" }}>
        Estimated using current delta × spot change from open. Actual opening premium may differ.
      </div>
    </Section>
  );
}

// ═════════════════ BONUS #3: PATTERN DETECTOR ═════════════════

export function PatternDetector({ strike, liveData, theme }) {
  if (!strike) return null;
  const d = liveData?.[strike.index?.toLowerCase()] || {};
  const ltp = d.ltp || 0;
  const open = d.open || 0;
  const high = d.high || 0;
  const low = d.low || 0;

  if (!ltp || !open || !high || !low) {
    return (
      <Section title="🔍 Chart Pattern Detector" theme={theme}>
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
          Waiting for OHLC data to detect patterns.
        </div>
      </Section>
    );
  }

  const patterns = [];
  const range = high - low;
  const bodyPct = Math.abs(ltp - open) / Math.max(range, 1);
  const posInRange = (ltp - low) / Math.max(range, 1);

  // Simplified detection
  if (bodyPct < 0.1) {
    patterns.push({ name: "DOJI", color: theme.AMBER, meaning: "Indecision — watch next candle for direction" });
  }
  if (ltp > open && posInRange > 0.9) {
    patterns.push({ name: "STRONG BULLISH CLOSE", color: theme.GREEN, meaning: "Closing near day high — momentum upward" });
  }
  if (ltp < open && posInRange < 0.1) {
    patterns.push({ name: "STRONG BEARISH CLOSE", color: theme.RED, meaning: "Closing near day low — momentum downward" });
  }
  if (posInRange > 0.7 && (high - ltp) > (ltp - open) * 1.5) {
    patterns.push({ name: "UPPER WICK REJECTION", color: theme.RED, meaning: "Sellers pushed back from highs — potential reversal" });
  }
  if (posInRange < 0.3 && (ltp - low) > (open - ltp) * 1.5) {
    patterns.push({ name: "LOWER WICK SUPPORT", color: theme.GREEN, meaning: "Buyers defended lows — potential bounce" });
  }
  if (bodyPct > 0.7 && ltp > open) {
    patterns.push({ name: "MARUBOZU BULLISH", color: theme.GREEN, meaning: "Strong buying all day — trend continuation" });
  }
  if (bodyPct > 0.7 && ltp < open) {
    patterns.push({ name: "MARUBOZU BEARISH", color: theme.RED, meaning: "Strong selling all day — trend continuation" });
  }

  return (
    <Section title="🔍 Pattern Detector (today's candle)" accent={theme.PURPLE} theme={theme}>
      {patterns.length === 0 ? (
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.BODY }}>
          No clear candle pattern detected. Price moving within normal range.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: SPACE.SM }}>
          {patterns.map((p, i) => (
            <div
              key={i}
              style={{
                padding: SPACE.SM,
                background: p.color + "15",
                borderLeft: `2px solid ${p.color}`,
                borderRadius: RADIUS.SM,
              }}
            >
              <div style={{ color: p.color, fontSize: TEXT_SIZE.BODY, fontWeight: TEXT_WEIGHT.BLACK, letterSpacing: 1 }}>
                {p.name}
              </div>
              <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, marginTop: 2 }}>
                {p.meaning}
              </div>
            </div>
          ))}
        </div>
      )}
      <div style={{ color: theme.TEXT_DIM, fontSize: 10, marginTop: SPACE.SM, fontStyle: "italic" }}>
        Simplified single-candle analysis. For complex patterns (H&S, flags), view in chart.
      </div>
    </Section>
  );
}

// ═════════════════ BONUS #4: OI BUILDUP SPEED ═════════════════

export function OIVelocity({ strike, theme }) {
  if (!strike) return null;
  const ceOI = strike.ceOI || 0;
  const peOI = strike.peOI || 0;
  const ceVol = strike.ceVol || 0;
  const peVol = strike.peVol || 0;

  if (!ceOI && !peOI) return null;

  // Velocity proxy: volume / OI ratio indicates fresh activity relative to existing positions
  // Higher ratio = more turnover / fresh building
  const ceVelocity = ceOI > 0 ? (ceVol / ceOI) * 100 : 0;
  const peVelocity = peOI > 0 ? (peVol / peOI) * 100 : 0;

  const getStatus = (v) => {
    if (v > 50) return { label: "EXTREME", color: theme.RED };
    if (v > 25) return { label: "HIGH", color: theme.AMBER };
    if (v > 10) return { label: "ACTIVE", color: theme.ACCENT };
    if (v > 3) return { label: "MODERATE", color: theme.GREEN };
    return { label: "QUIET", color: theme.TEXT_MUTED };
  };

  const ceStatus = getStatus(ceVelocity);
  const peStatus = getStatus(peVelocity);

  return (
    <Section title="⚡ OI Buildup Speed" accent={theme.AMBER} theme={theme}>
      <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, marginBottom: SPACE.MD }}>
        Volume/OI ratio indicates how actively this strike is being traded relative to existing positions.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
        <div style={{ padding: SPACE.MD, background: theme.GREEN_DIM, borderRadius: RADIUS.MD, borderLeft: `2px solid ${theme.GREEN}` }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 4 }}>
            <span style={{ color: theme.GREEN, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase" }}>CE</span>
            <span style={{ color: ceStatus.color, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1 }}>{ceStatus.label}</span>
          </div>
          <div style={{ color: theme.TEXT, fontSize: 20, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>
            {ceVelocity.toFixed(1)}%
          </div>
          <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO }}>
            Vol {(ceVol / 100000).toFixed(1)}L / OI {(ceOI / 100000).toFixed(1)}L
          </div>
        </div>
        <div style={{ padding: SPACE.MD, background: theme.RED_DIM, borderRadius: RADIUS.MD, borderLeft: `2px solid ${theme.RED}` }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 4 }}>
            <span style={{ color: theme.RED, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase" }}>PE</span>
            <span style={{ color: peStatus.color, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1 }}>{peStatus.label}</span>
          </div>
          <div style={{ color: theme.TEXT, fontSize: 20, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>
            {peVelocity.toFixed(1)}%
          </div>
          <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO }}>
            Vol {(peVol / 100000).toFixed(1)}L / OI {(peOI / 100000).toFixed(1)}L
          </div>
        </div>
      </div>
    </Section>
  );
}

// ═════════════════ BONUS #5: BID-ASK SPREAD ═════════════════

export function BidAskSpread({ strike, theme }) {
  const [spread, setSpread] = useState(null);

  useEffect(() => {
    if (!strike) return;
    const type = strike.type || "CE";
    fetchJ(`/api/spread?index=${strike.index}&strike=${strike.strike}&type=${type}`).then(setSpread);
  }, [strike?.index, strike?.strike, strike?.type]);

  if (!spread || spread.error) {
    return (
      <Section title="💧 Bid-Ask Spread (liquidity)" theme={theme}>
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
          {spread?.error || "Fetching depth..."}
        </div>
      </Section>
    );
  }

  const color = spread.status === "tight" ? theme.GREEN : spread.status === "moderate" ? theme.AMBER : theme.RED;
  const advice = spread.status === "tight"
    ? "Tight spread — safe for market orders."
    : spread.status === "moderate"
    ? "Moderate spread — consider limit orders."
    : "Wide spread — USE LIMIT ORDERS, avoid market orders (slippage risk).";

  return (
    <Section title="💧 Bid-Ask Spread (liquidity)" accent={color} theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: SPACE.MD, marginBottom: SPACE.MD }}>
        <Stat label="Bid" value={`₹${spread.bid}`} color={theme.RED} sub={`${spread.bidQty} qty`} theme={theme} />
        <Stat label="Ask" value={`₹${spread.ask}`} color={theme.GREEN} sub={`${spread.askQty} qty`} theme={theme} />
        <Stat label="LTP" value={`₹${spread.ltp}`} theme={theme} />
        <Stat label="Spread" value={`₹${spread.spread}`} color={color} sub={`${spread.spreadPct}%`} theme={theme} />
      </div>
      <div style={{ padding: SPACE.SM, background: color + "15", borderLeft: `2px solid ${color}`, borderRadius: RADIUS.SM, color: theme.TEXT, fontSize: TEXT_SIZE.BODY }}>
        <strong style={{ color, letterSpacing: 1, textTransform: "uppercase", marginRight: 8 }}>{spread.status}</strong>
        {advice}
      </div>
    </Section>
  );
}

// ═════════════════ BONUS #7: NSE NEWS / MARKET CONTEXT ═════════════════

export function MarketNews({ strikes, theme }) {
  const [summary, setSummary] = useState("");
  const [loading, setLoading] = useState(false);
  const [asked, setAsked] = useState(false);

  const fetchSummary = () => {
    if (!strikes?.length) return;
    setLoading(true);
    setAsked(true);
    fetch("/api/news/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strikes: strikes.slice(0, 4) }),
    })
      .then((r) => r.json())
      .then((d) => {
        setSummary(d.summary || d.error || "No summary available");
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  return (
    <Section title="📰 Market Context (AI synthesis)" accent={theme.CYAN} theme={theme}>
      {!asked && (
        <button
          onClick={fetchSummary}
          style={{
            background: theme.CYAN,
            color: "#000",
            border: "none",
            borderRadius: RADIUS.SM,
            padding: "6px 14px",
            cursor: "pointer",
            fontSize: TEXT_SIZE.MICRO,
            fontWeight: TEXT_WEIGHT.BOLD,
            letterSpacing: 1,
            textTransform: "uppercase",
          }}
        >
          🧠 Synthesize market context
        </button>
      )}
      {loading && <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.BODY }}>Claude analyzing...</div>}
      {summary && !loading && (
        <div style={{ color: theme.TEXT, fontSize: TEXT_SIZE.BODY, lineHeight: 1.6, padding: SPACE.MD, background: theme.SURFACE_HI, borderRadius: RADIUS.MD }}>
          {summary}
        </div>
      )}
      <div style={{ color: theme.TEXT_DIM, fontSize: 10, marginTop: SPACE.SM, fontStyle: "italic" }}>
        AI synthesizes live signals + unusual activity + OI shifts relevant to pinned strikes.
      </div>
    </Section>
  );
}

// ═════════════════ BONUS #8: NIFTY-BN CORRELATION ═════════════════

export function IndexCorrelation({ theme }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    const fetchCorr = () => fetchJ("/api/correlation?minutes=30").then(setData);
    fetchCorr();
    const iv = setInterval(fetchCorr, 30000);
    return () => clearInterval(iv);
  }, []);

  if (!data || data.error) {
    return (
      <Section title="🔗 NIFTY vs BANKNIFTY" theme={theme}>
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
          {data?.error || "Loading correlation..."}
        </div>
      </Section>
    );
  }

  const corr = data.correlation || 0;
  const leader = data.leader;
  const corrColor = corr > 0.7 ? theme.GREEN : corr > 0.3 ? theme.AMBER : corr > -0.3 ? theme.TEXT_MUTED : theme.RED;
  const corrLabel = corr > 0.7 ? "STRONG" : corr > 0.3 ? "MODERATE" : corr > -0.3 ? "WEAK" : "INVERSE";

  let leaderText = "";
  if (leader === "BANKNIFTY") leaderText = "BANKNIFTY is leading — watch BN signals first, NIFTY usually follows within 2-5 min.";
  else if (leader === "NIFTY") leaderText = "NIFTY is leading — mainstream flow driving both indices.";
  else if (leader === "MOVING_TOGETHER") leaderText = "Both indices moving in sync — no clear leader.";
  else leaderText = "Insufficient data for leader detection.";

  return (
    <Section title="🔗 NIFTY vs BANKNIFTY" accent={corrColor} theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: SPACE.MD, marginBottom: SPACE.MD }}>
        <Stat label="Correlation" value={corr.toFixed(2)} color={corrColor} sub={corrLabel} theme={theme} />
        <Stat label="NIFTY Vol" value={`${data.niftyVol?.toFixed(2) || "—"}%`} theme={theme} />
        <Stat label="BN Vol" value={`${data.bnVol?.toFixed(2) || "—"}%`} theme={theme} />
        <Stat label="Leader" value={leader || "—"} color={theme.PURPLE} theme={theme} />
      </div>
      <div style={{ color: theme.TEXT, fontSize: TEXT_SIZE.BODY, lineHeight: 1.5 }}>
        {leaderText}
      </div>
      <div style={{ color: theme.TEXT_DIM, fontSize: 10, marginTop: SPACE.SM, fontStyle: "italic" }}>
        Rolling 30-min window, based on {data.samples || 0} 1-min samples.
      </div>
    </Section>
  );
}
