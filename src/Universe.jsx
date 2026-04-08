import { useState, useEffect, useCallback } from "react";
import { useMarketData } from "./useMarketData";
import OIChangeTab from "./OIChangeTab";
import PnLTracker from "./PnLTracker";
import { exportSignalsToPDF, exportFullReport } from "./pdfExport";
import { fetchTrapScan, fetchAIAnalysis, fetchTrapHistory, fetchTrapToday, fetchPriceAction, fetchTrapVerdict } from "./api";

const ACCENT = "#0A84FF";
const BG = "#0A0A0F";
const CARD = "#111118";
const BORDER = "#1E1E2E";
const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";

const TABS = [
  { id: "live",    icon: "\u26A1", label: "Live Data" },
  { id: "signals", icon: "\uD83C\uDFAF", label: "Signals" },
  { id: "intraday",icon: "\uD83D\uDCCA", label: "Intraday" },
  { id: "nextday", icon: "\uD83D\uDD2D", label: "Next Day" },
  { id: "weekly",  icon: "\uD83D\uDCC5", label: "Weekly" },
  { id: "unusual", icon: "\uD83D\uDEA8", label: "Unusual Activity" },
  { id: "sellers", icon: "\uD83E\uDD88", label: "Sellers" },
  { id: "tradeai", icon: "\uD83E\uDDE0", label: "Trade AI" },
  { id: "hidden",  icon: "\uD83D\uDD75\uFE0F", label: "Hidden Shift" },
  { id: "trap",    icon: "\uD83E\uDDE8", label: "Trap Finder" },
  { id: "priceact",icon: "\uD83D\uDCA5", label: "Price Action" },
  { id: "aibrain", icon: "\uD83E\uDD16", label: "AI Brain" },
  { id: "oichange",icon: "\uD83D\uDCC8", label: "OI Change" },
  { id: "pnl",     icon: "\uD83D\uDCB0", label: "PnL Tracker" },
  { id: "prompt",  icon: "\uD83E\uDD16", label: "Claude Prompt" },
];

const MASTER_PROMPT = `# UNIVERSE \u2014 MASTER CLAUDE PROMPT
## Nifty & BankNifty Options Intelligence Engine
## Broker: Zerodha Kite Connect | Market: NSE India

---

## SYSTEM ROLE

You are UNIVERSE, an elite options trading intelligence engine specialized exclusively in Nifty and BankNifty option BUYING on NSE India. You analyze real-time Zerodha Kite Connect data and generate precise, actionable signals with complete reasoning transparency. You are not a financial advisor \u2014 you are a signal engine. The trader makes all execution decisions.

---

## DATA INPUTS (Zerodha Kite Connect API)

### 1. LIVE MARKET DATA
- Nifty 50 LTP, High, Low, Change%, OHLCV (5min / 15min / 1hr / Daily)
- BankNifty LTP, High, Low, Change%, OHLCV
- India VIX current + change%
- SGX Nifty (pre-market) if available

### 2. OPTIONS CHAIN (Current Week + Next Week)
- Strike-wise CE/PE: OI, OI Change, Volume, LTP, IV
- PCR overall and strike-wise
- Max Pain Strike
- IVR = (current IV \u2212 52w low IV) / (52w high IV \u2212 52w low IV) \u00D7 100

### 3. GREEKS (Per Strike)
- Delta, Gamma, Theta, Vega
- GEX (Gamma Exposure) \u2014 flag GEX flip zones

### 4. INSTITUTIONAL FLOW
- FII net: index futures + index options
- DII net: cash market
- FII COT data if available

### 5. TECHNICALS (5min / 15min / 1hr / Daily)
- EMA 9, 20, 50, 200
- RSI 14
- MACD (12,26,9) \u2014 histogram + signal line
- VWAP intraday
- Bollinger Bands (20,2)
- Supertrend (10,3)
- ATR 14 \u2014 for stop loss sizing
- Volume vs 20-period average
- Pivot Points: R1 R2 R3 S1 S2 S3

---

## SIGNAL SCORING ENGINE (Out of 9)

### TECHNICAL \u2014 4 pts
1. Price above/below EMA 20+50 confluence \u2014 1 pt
2. RSI momentum aligned with direction \u2014 1 pt
3. MACD histogram momentum confirmed \u2014 1 pt
4. Chart pattern confirmed (M-Top, HnS, Flag, Triangle) \u2014 1 pt

### OPTIONS FLOW \u2014 3 pts
5. OI buildup at resistance/support matches directional bias \u2014 1 pt
6. PCR extreme (<0.70 bearish / >1.30 bullish) or trending strongly \u2014 1 pt
7. Big CE/PE writing at key strike (institutional positioning) \u2014 1 pt

### MARKET STRUCTURE \u2014 2 pts
8. IVR in safe buying zone (20\u201360) \u2014 1 pt
9. FII/institutional flow confirming direction \u2014 1 pt

### THRESHOLDS
Score 5\u20136 \u2192 MODERATE CONFIDENCE (watchlist only)
Score 7\u20138 \u2192 HIGH CONFIDENCE (execute with discipline)
Score 9   \u2192 MAX CONFIDENCE (prime setup, full size)
Score <5  \u2192 NO TRADE \u2014 wait

### STRIKE SELECTION RULES
- ATM or 1-strike OTM only
- Premium range \u20B980\u2013\u20B9400
- Avoid below \u20B950 (lottery) and above \u20B9500 (slow mover)
- Avoid last 2 days before expiry unless IVR > 60

### STOP LOSS RULES
- Hard stop: 40% of premium paid
- Example: Buy PE at \u20B9200 \u2192 SL at \u20B9120
- Trail stop: After T1 hit \u2192 move SL to entry price

---

## SIGNAL OUTPUT FORMAT

\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501
\uD83C\uDFAF UNIVERSE SIGNAL \u2014 [INSTRUMENT]
\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501
Time           : [HH:MM AM/PM IST]
Signal         : BUY CALL / BUY PUT
Strike         : [Strike] [CE/PE] [Expiry]
Entry Zone     : \u20B9[X] \u2013 \u20B9[Y]
Target 1       : \u20B9[T1]  (+X%)
Target 2       : \u20B9[T2]  (+X%)
Stop Loss      : \u20B9[SL]  (\u221240% hard stop)
Risk:Reward    : 1:[X.X]
CONFLUENCE     : [X]/9

REASONING:
[\u2705] [Condition with exact values \u2014 not vague]
[\u2705] [Condition with exact values]
[\u26A0\uFE0F] [Borderline condition + what to watch]
[\u274C] [Failed condition \u2014 reason]
... all 9 conditions shown always

INVALIDATION   : [Exact condition that kills this trade]
TIME SENSITIVE : [Scalp 30min / Intraday / Swing 2\u20133 days]
STATUS         : ACTIVE / CLOSED WIN / CLOSED SL HIT
\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

---

## NEXT DAY LEVELS FORMAT
## Generate between 2:30 PM \u2013 3:00 PM IST daily

\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501
\uD83D\uDD2D UNIVERSE \u2014 NEXT DAY FORECAST
Generated : [TIME] IST
For       : [DATE]
\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

NIFTY TOMORROW
Bias           : BULLISH / BEARISH / NEUTRAL
Probable Range : [LOW] \u2013 [HIGH]
Pivot          : [level]
Max Pain       : [strike]

RESISTANCE:
  R1: [level] \u2014 [why this is resistance \u2014 exact reason]
  R2: [level] \u2014 [why]
  R3: [level] \u2014 [why]

SUPPORT:
  S1: [level] \u2014 [why this is support \u2014 exact reason]
  S2: [level] \u2014 [why]
  S3: [level] \u2014 [why]

KEY OI WALLS:
  Big CE Wall : [Strike] CE \u2014 [OI in Lakhs] \u2014 [implication]
  Big PE Wall : [Strike] PE \u2014 [OI in Lakhs] \u2014 [implication]
  Unusual     : [Strike] \u2014 [unusual activity description]

OPENING BIAS  : [Gap up / flat / down + reasoning]
STRATEGY      : [Exact action plan for tomorrow]

MORNING  (9:15\u201310:30) : [Action]
MIDDAY   (10:30\u20131:00) : [Action]
CLOSING  (2:00\u20133:00)  : [Action]

[SAME STRUCTURE REPEATED FOR BANKNIFTY]
\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

---

## WEEKLY OUTLOOK FORMAT
## Generate Monday morning 9:00 AM

\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501
\uD83D\uDCC5 UNIVERSE \u2014 WEEKLY OUTLOOK
Week: [DATE RANGE]
\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

NIFTY WEEKLY BIAS     : BULLISH / BEARISH / SIDEWAYS
BANKNIFTY WEEKLY BIAS : BULLISH / BEARISH / SIDEWAYS
Expected Nifty Range  : [LOW]\u2013[HIGH]
Expected BN Range     : [LOW]\u2013[HIGH]

WEEKLY OI ANALYSIS:
- [Big CE wall + implication]
- [Big PE wall + implication]
- [PCR reading + what it means]
- [IVR + strategy implication]

FII / DII FLOW:
  FII Futures : [Net + interpretation]
  DII Cash    : [Net + interpretation]
  Verdict     : [Smart money direction this week]

MACRO EVENTS THIS WEEK:
[All key events with expected market impact]

WEEKLY TRADING PLAN:
  Monday    : [Strategy]
  Tuesday   : [Strategy]
  Wednesday : [Strategy]
  Thursday  : \u26A0\uFE0F THETA WARNING \u2014 No option buying after 2 PM
  Friday    : \uD83D\uDEAB No new positions \u2014 [specific risk reason]

KEY MAKE-OR-BREAK LEVELS:
  Nifty     : [level] \u2014 if this breaks, full trend reversal
  BankNifty : [level] \u2014 if this breaks, full trend reversal
\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

---

## UNUSUAL ACTIVITY \u2014 DETECTION TRIGGERS

Flag immediately when ANY of these occur:
1. Strike volume > 3x its 5-day average
2. Single strike OI change > 5L contracts in one session
3. Option premium changes > 30% in under 30 min without underlying move
4. PCR shifts > 0.15 in under 1 hour
5. CE/PE at specific strike written in large blocks (institutional footprint)
6. India VIX moves > 5% intraday
7. GEX flips from positive to negative (bearish acceleration zone)

ALERT FORMAT:
\uD83D\uDEA8 UNUSUAL ACTIVITY
Time      : [TIME]
Strike    : [INSTRUMENT + STRIKE]
Type      : BIG WRITING / BIG BUYING / VOL SPIKE / GEX FLIP
OI Change : [NUMBER]
Signal    : [Implication + direction]
Level     : CRITICAL / HIGH / MEDIUM

---

## DO NOT TRADE CONDITIONS

- VIX > 20 and score < 8
- Within 15 min of major macro event (RBI, Fed, NFP)
- Last 30 min of expiry day
- LTP exactly at max pain (market confused)
- PCR between 0.85\u20131.10 (no directional edge)

---

## BEST TRADE WINDOWS

- 9:30\u201310:30 AM  \u2192 Trend establishment, highest quality setups
- 11:00\u201312:30 PM \u2192 Momentum continuation
- 2:00\u20132:30 PM   \u2192 EOD institutional positioning window

---

## SELF-AUDIT BEFORE EVERY SIGNAL

\u25A1 Am I chasing a move already completed? \u2192 DO NOT signal
\u25A1 Is IVR above 80? \u2192 Premium too expensive, abort
\u25A1 Is expiry < 2 days and strike OTM? \u2192 Avoid
\u25A1 Is this against the weekly bias? \u2192 Reduce score by 2, reconsider
\u25A1 Did I check both Nifty AND BankNifty? \u2192 Always verify both

---

## REASONING TRANSPARENCY RULES

Every signal MUST show:
1. Exact numbers \u2014 not vague statements
2. All 9 conditions: \u2705 passed / \u26A0\uFE0F borderline / \u274C failed
3. Why THIS specific strike vs adjacent strikes
4. Exact invalidation condition
5. Time sensitivity (scalp / intraday / swing)

---

UNIVERSE \u2014 Built for Kanishk
Nifty & BankNifty Option Buying Engine
Broker: Zerodha Kite Connect | NSE India`;

// \u2500\u2500 SHARED COMPONENTS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

const Badge = ({ text, color }) => (
  <span style={{
    background: color + "22", color,
    border: `1px solid ${color}44`,
    padding: "2px 10px", borderRadius: 20,
    fontSize: 11, fontWeight: 700, letterSpacing: 0.8,
  }}>{text}</span>
);

const Card = ({ children, style = {} }) => (
  <div style={{
    background: CARD,
    border: `1px solid ${BORDER}`,
    borderRadius: 12,
    padding: "16px 20px",
    ...style,
  }}>{children}</div>
);

const Label = ({ children }) => (
  <div style={{
    color: "#555", fontSize: 10, fontWeight: 700,
    letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 8,
  }}>{children}</div>
);

const Stat = ({ label, value, color = "#fff", sub }) => (
  <div style={{ background: "#0D0D15", borderRadius: 8, padding: "10px 14px" }}>
    <div style={{ color: "#555", fontSize: 10, marginBottom: 4 }}>{label}</div>
    <div style={{ color, fontWeight: 700, fontSize: 15 }}>{value}</div>
    {sub && <div style={{ color: "#444", fontSize: 10, marginTop: 3 }}>{sub}</div>}
  </div>
);

// \u2500\u2500 TAB: LIVE DATA \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function LiveDataTab({ liveData }) {
  if (!liveData || !liveData.nifty || liveData.nifty.ltp <= 0) {
    return (<div style={{ textAlign: "center", padding: 60, color: "#555" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>⚡</div>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8, color: "#888" }}>No Live Data</div>
      <div style={{ fontSize: 12 }}>Login to Kite → data will appear here in real-time</div>
    </div>);
  }
  const data = liveData;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {[{ name: "NIFTY", d: data.nifty }, { name: "BANKNIFTY", d: data.banknifty }].map(({ name, d }) => {
        const openColor = d.openType === "GAP UP" ? GREEN : d.openType === "GAP DOWN" ? RED : YELLOW;
        const zoneColor = d.rangeZone === "NEAR HIGH" ? GREEN : d.rangeZone === "NEAR LOW" ? RED : YELLOW;
        return (
        <Card key={name}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <span style={{ color: ACCENT, fontWeight: 900, fontSize: 18, letterSpacing: 1 }}>{name}</span>
            <div style={{ display: "flex", gap: 8 }}>
              {d.openType && <Badge text={d.openType} color={openColor} />}
              <Badge text={d.trend}  color={d.trend === "BULLISH" ? GREEN : RED} />
              <Badge text={d.regime} color={ORANGE} />
            </div>
          </div>

          {/* Market Open + Day Range Bar */}
          {d.openPrice > 0 && (
            <div style={{ background: "#0A0A12", borderRadius: 8, padding: "8px 12px", marginBottom: 10, border: `1px solid ${BORDER}` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <div style={{ display: "flex", gap: 12, fontSize: 11 }}>
                  <span style={{ color: "#888" }}>Open: <span style={{ color: openColor, fontWeight: 700 }}>{d.openPrice?.toLocaleString("en-IN")}</span></span>
                  <span style={{ color: "#888" }}>Prev Close: <span style={{ color: "#ccc", fontWeight: 700 }}>{d.prevClose?.toLocaleString("en-IN")}</span></span>
                  <span style={{ color: "#888" }}>From Open: <span style={{ color: d.fromOpen > 0 ? GREEN : d.fromOpen < 0 ? RED : "#888", fontWeight: 700 }}>{d.fromOpen > 0 ? "+" : ""}{d.fromOpen} ({d.fromOpenPct > 0 ? "+" : ""}{d.fromOpenPct}%)</span></span>
                </div>
                <span style={{ background: zoneColor + "22", color: zoneColor, padding: "2px 8px", borderRadius: 4, fontSize: 9, fontWeight: 700 }}>{d.rangeZone}</span>
              </div>
              {/* Day Range Progress Bar */}
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: RED, fontSize: 10, fontWeight: 700, minWidth: 50 }}>{d.low?.toLocaleString("en-IN")}</span>
                <div style={{ flex: 1, background: "#1a1a25", borderRadius: 4, height: 6, position: "relative" }}>
                  <div style={{ position: "absolute", left: `${d.rangePosition || 50}%`, top: -2, width: 10, height: 10, borderRadius: "50%", background: ACCENT, transform: "translateX(-50%)" }} />
                </div>
                <span style={{ color: GREEN, fontSize: 10, fontWeight: 700, minWidth: 50, textAlign: "right" }}>{d.high?.toLocaleString("en-IN")}</span>
              </div>
              <div style={{ textAlign: "center", color: "#555", fontSize: 9, marginTop: 4 }}>Day Range: {d.dayRange} pts</div>
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 10 }}>
            <Stat label="LTP"    value={d.ltp.toLocaleString("en-IN")} />
            <Stat label="Change" value={`${d.change > 0 ? "+" : ""}${d.change} (${d.changePct}%)`} color={d.change > 0 ? GREEN : RED} />
            <Stat label="High"   value={d.high?.toLocaleString("en-IN")} color={GREEN} />
            <Stat label="Low"    value={d.low?.toLocaleString("en-IN")}  color={RED}   />
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 10 }}>
            <Stat label="PCR" value={d.pcr}
              color={d.pcr < 0.7 ? RED : d.pcr > 1.3 ? GREEN : YELLOW}
              sub={d.pcr < 0.7 ? "Bearish extreme" : d.pcr > 1.3 ? "Bullish extreme" : "Neutral zone"} />
            <Stat label="IVR" value={`${d.ivr}%`}
              color={d.ivr < 20 ? YELLOW : d.ivr < 60 ? GREEN : RED}
              sub={d.ivr < 20 ? "Low \u2014 avoid buying" : d.ivr < 60 ? "Safe for buying" : "Costly \u2014 avoid"} />
            <Stat label="Max Pain" value={d.maxPain.toLocaleString("en-IN")} color={PURPLE} />
            <Stat label="VIX"      value={d.vix}
              color={d.vix > 18 ? RED : d.vix > 14 ? YELLOW : GREEN}
              sub={d.vix > 18 ? "High \u2014 be careful" : "Normal range"} />
          </div>
          <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px", display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12 }}>
            <div>
              <div style={{ color: "#555", fontSize: 10, marginBottom: 3 }}>Big CE Wall</div>
              <div style={{ color: RED, fontWeight: 700 }}>{d.bigCallStrike} CE</div>
              <div style={{ color: "#444", fontSize: 10 }}>Resistance cap</div>
            </div>
            <div>
              <div style={{ color: "#555", fontSize: 10, marginBottom: 3 }}>Big PE Wall</div>
              <div style={{ color: GREEN, fontWeight: 700 }}>{d.bigPutStrike} PE</div>
              <div style={{ color: "#444", fontSize: 10 }}>Support zone</div>
            </div>
            <div>
              <div style={{ color: "#555", fontSize: 10, marginBottom: 3 }}>Total CE OI</div>
              <div style={{ color: "#ccc", fontWeight: 700 }}>{(d.totalCE_OI / 1e7).toFixed(1)} Cr</div>
            </div>
            <div>
              <div style={{ color: "#555", fontSize: 10, marginBottom: 3 }}>Total PE OI</div>
              <div style={{ color: "#ccc", fontWeight: 700 }}>{(d.totalPE_OI / 1e7).toFixed(1)} Cr</div>
            </div>
          </div>
        </Card>
      );})}
    </div>
  );
}

// \u2500\u2500 TAB: SIGNALS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function SignalsTab({ realSignals }) {
  if (!realSignals || realSignals.length === 0) {
    return (<div style={{ textAlign: "center", padding: 60, color: "#555" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>🎯</div>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8, color: "#888" }}>No Active Signals</div>
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>Signal engine scores 9 conditions every 30 seconds.<br/>Score 5+ needed to generate a signal. Login to Kite to activate.</div>
    </div>);
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button onClick={() => exportSignalsToPDF(realSignals)} style={{
          background: ACCENT + "22", color: ACCENT, border: `1px solid ${ACCENT}44`,
          borderRadius: 8, padding: "5px 14px", cursor: "pointer", fontSize: 11, fontWeight: 700,
        }}>Export PDF</button>
      </div>
      {realSignals.map(s => (
        <Card key={s.id}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
            <div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                <span style={{ color: ACCENT, fontWeight: 900, fontSize: 16 }}>{s.instrument}</span>
                <Badge text={s.type}   color={s.type.includes("PUT") ? RED : GREEN} />
                <Badge text={s.status} color={s.status === "ACTIVE" ? YELLOW : GREEN} />
              </div>
              <div style={{ color: "#666", fontSize: 12 }}>{s.strike} \u00B7 {s.expiry} \u00B7 {s.time}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ color: PURPLE, fontWeight: 900, fontSize: 22 }}>{s.score}/{s.maxScore}</div>
              <div style={{ color: "#444", fontSize: 10 }}>CONFLUENCE</div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 14 }}>
            <Stat label="Entry"    value={`\u20B9${s.entry}`} />
            <Stat label="Target 1" value={`\u20B9${s.t1}`}    color={GREEN} />
            <Stat label="Target 2" value={`\u20B9${s.t2}`}    color={GREEN} />
            <Stat label="Stop Loss" value={`\u20B9${s.sl}`}   color={RED} sub="\u221240% hard stop" />
          </div>
          <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px", marginBottom: 10 }}>
            <Label>Reasoning \u2014 All 9 Conditions</Label>
            {s.reasoning.map((r, i) => (
              <div key={i} style={{
                display: "flex", gap: 8, marginBottom: 7,
                color: r.pass === true ? GREEN : r.pass === "warn" ? YELLOW : "#555",
                fontSize: 12, lineHeight: 1.6,
              }}>
                <span style={{ flexShrink: 0 }}>
                  {r.pass === true ? "\u2705" : r.pass === "warn" ? "\u26A0\uFE0F" : "\u274C"}
                </span>
                <span>{r.text}</span>
              </div>
            ))}
          </div>
          <div style={{ textAlign: "right", color: "#555", fontSize: 11 }}>
            Risk : Reward = <span style={{ color: ACCENT, fontWeight: 700 }}>{s.rr}</span>
          </div>
        </Card>
      ))}
    </div>
  );
}

// \u2500\u2500 TAB: INTRADAY \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function IntradayTab({ realData }) {
  const sessions = [
    { label: "\uD83D\uDFE2 Morning Session", time: "9:15\u201310:30 AM", color: GREEN,
      desc: "Trend establishment window. Best setups form here. Wait for 15-min candle confirmation before entry. Never trade first 5 candles blind." },
    { label: "\uD83D\uDD35 Mid Session",     time: "10:30 AM\u201312:30 PM", color: ACCENT,
      desc: "Momentum continuation. VWAP is king. Trade with trend only. Avoid reversal trades unless score is 8+." },
    { label: "\uD83D\uDFE0 Closing Window",  time: "2:00\u20132:30 PM", color: ORANGE,
      desc: "Institutional positioning window. High OI changes. Watch unusual activity. Best time to read next-day setup." },
    { label: "\uD83D\uDD34 Avoid Zone",      time: "12:30\u20132:00 PM", color: RED,
      desc: "Low liquidity, choppy price action. Maximum premium decay. No new positions unless strong breakout with score 8+." },
  ];
  const rules = [
    { icon: "\uD83D\uDEAB", col: RED,   text: "VIX > 20 and score < 8 \u2192 Skip the trade entirely" },
    { icon: "\uD83D\uDEAB", col: RED,   text: "Within 15 min of RBI / Fed / NFP event \u2192 No new trades" },
    { icon: "\uD83D\uDEAB", col: RED,   text: "PCR between 0.85\u20131.10 \u2192 No directional edge, stay out" },
    { icon: "\uD83D\uDEAB", col: RED,   text: "Expiry day last 30 min \u2192 Do NOT buy options, theta crush" },
    { icon: "\u2705", col: GREEN, text: "VWAP rejection confirmed on 5min \u2192 Valid entry setup" },
    { icon: "\u2705", col: GREEN, text: "EMA 9 crosses 20 with volume surge \u2192 Strong directional signal" },
    { icon: "\u2705", col: GREEN, text: "Score \u2265 7 in morning session 9:30\u201310:30 AM \u2192 Prime setup, full conviction" },
  ];

  // Build REAL tech levels from API data
  const n = realData?.NIFTY || {};
  const b = realData?.BANKNIFTY || {};
  const hasReal = n.vwap > 0;
  const rsiColor = (v) => v < 30 || v > 70 ? RED : v < 45 ? YELLOW : GREEN;
  const macdColor = (l) => l === "Bullish Cross" ? GREEN : RED;

  const techLevels = hasReal ? [
    { label: "NIFTY VWAP",          value: n.vwap?.toLocaleString("en-IN") || "N/A", color: ACCENT },
    { label: "NIFTY Supertrend",     value: n.supertrendLabel || "N/A", color: n.supertrendLabel?.includes("BUY") ? GREEN : RED },
    { label: "NIFTY RSI (14)",       value: `${n.rsi} \u2014 ${n.rsiLabel}`, color: rsiColor(n.rsi) },
    { label: "NIFTY MACD",          value: n.macdLabel || "N/A", color: macdColor(n.macdLabel) },
    { label: "BANKNIFTY VWAP",       value: b.vwap?.toLocaleString("en-IN") || "N/A", color: ACCENT },
    { label: "BANKNIFTY Supertrend", value: b.supertrendLabel || "N/A", color: b.supertrendLabel?.includes("BUY") ? GREEN : RED },
    { label: "BANKNIFTY RSI (14)",   value: `${b.rsi} \u2014 ${b.rsiLabel}`, color: rsiColor(b.rsi) },
    { label: "BANKNIFTY MACD",       value: b.macdLabel || "N/A", color: macdColor(b.macdLabel) },
  ] : [
    { label: "NIFTY VWAP", value: "Loading...", color: "#555" },
    { label: "NIFTY Supertrend", value: "Loading...", color: "#555" },
    { label: "NIFTY RSI", value: "Loading...", color: "#555" },
    { label: "NIFTY MACD", value: "Loading...", color: "#555" },
    { label: "BANKNIFTY VWAP", value: "Loading...", color: "#555" },
    { label: "BANKNIFTY Supertrend", value: "Loading...", color: "#555" },
    { label: "BANKNIFTY RSI", value: "Loading...", color: "#555" },
    { label: "BANKNIFTY MACD", value: "Loading...", color: "#555" },
  ];

  // Pivot levels
  const pivotLevels = hasReal ? [
    { label: "NIFTY Pivot", value: n.pivot?.toLocaleString("en-IN"), color: PURPLE },
    { label: "NIFTY R1/R2", value: `${n.r1?.toLocaleString("en-IN")} / ${n.r2?.toLocaleString("en-IN")}`, color: RED },
    { label: "NIFTY S1/S2", value: `${n.s1?.toLocaleString("en-IN")} / ${n.s2?.toLocaleString("en-IN")}`, color: GREEN },
    { label: "NIFTY EMA 9/20", value: `${n.ema9?.toLocaleString("en-IN")} / ${n.ema20?.toLocaleString("en-IN")}`, color: ACCENT },
    { label: "BN Pivot", value: b.pivot?.toLocaleString("en-IN"), color: PURPLE },
    { label: "BN R1/R2", value: `${b.r1?.toLocaleString("en-IN")} / ${b.r2?.toLocaleString("en-IN")}`, color: RED },
    { label: "BN S1/S2", value: `${b.s1?.toLocaleString("en-IN")} / ${b.s2?.toLocaleString("en-IN")}`, color: GREEN },
    { label: "BN EMA 9/20", value: `${b.ema9?.toLocaleString("en-IN")} / ${b.ema20?.toLocaleString("en-IN")}`, color: ACCENT },
  ] : [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card>
        <Label>Intraday Session Guide</Label>
        {sessions.map(s => (
          <div key={s.label} style={{ display: "flex", gap: 14, padding: "12px 0", borderBottom: `1px solid ${BORDER}` }}>
            <div style={{ width: 4, background: s.color, borderRadius: 4, flexShrink: 0 }} />
            <div>
              <div style={{ color: s.color, fontWeight: 700, fontSize: 13, marginBottom: 3 }}>
                {s.label} <span style={{ color: "#555", fontWeight: 400 }}>{s.time}</span>
              </div>
              <div style={{ color: "#888", fontSize: 12, lineHeight: 1.6 }}>{s.desc}</div>
            </div>
          </div>
        ))}
      </Card>
      <Card>
        <Label>Intraday Rules Engine</Label>
        {rules.map((r, i) => (
          <div key={i} style={{ display: "flex", gap: 10, marginBottom: 10, alignItems: "flex-start" }}>
            <span style={{ fontSize: 14 }}>{r.icon}</span>
            <span style={{ color: r.col, fontSize: 12, lineHeight: 1.5 }}>{r.text}</span>
          </div>
        ))}
      </Card>
      <Card>
        <Label>Key Technical Levels Today {hasReal ? "(REAL)" : "(Loading...)"}</Label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          {techLevels.map(t => (
            <Stat key={t.label} label={t.label} value={t.value} color={t.color} />
          ))}
        </div>
      </Card>
      {pivotLevels.length > 0 && (
        <Card>
          <Label>Pivot Points + EMAs (REAL)</Label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            {pivotLevels.map(t => (
              <Stat key={t.label} label={t.label} value={t.value} color={t.color} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

// \u2500\u2500 TAB: NEXT DAY \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function NextDayTab({ realData }) {
  if (!realData || !realData.nifty) {
    return (<div style={{ textAlign: "center", padding: 60, color: "#555" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>🔭</div>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8, color: "#888" }}>No Next Day Data</div>
      <div style={{ fontSize: 12 }}>Login to Kite → levels will be computed from real option chain</div>
    </div>);
  }
  const d = realData;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 16px", background: "#0D0D15", borderRadius: 10, border: `1px solid ${ACCENT}33` }}>
        <span style={{ color: ACCENT, fontWeight: 700 }}>\uD83D\uDD2D {d.date}</span>
        <span style={{ color: "#555", fontSize: 12 }}>Generated: {d.generatedAt}</span>
      </div>
      {[{ name: "NIFTY", data: d.nifty }, { name: "BANKNIFTY", data: d.banknifty }].map(({ name, data }) => (
        <Card key={name}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
            <span style={{ color: ACCENT, fontWeight: 900, fontSize: 16 }}>{name} TOMORROW</span>
            <Badge text={data.bias} color={data.bias === "BULLISH" ? GREEN : RED} />
          </div>
          <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px", marginBottom: 12, display: "flex", justifyContent: "space-between" }}>
            <div>
              <div style={{ color: "#555", fontSize: 10, marginBottom: 4 }}>PROBABLE RANGE</div>
              <div style={{ color: "#fff", fontWeight: 700 }}>{data.rangeLow.toLocaleString("en-IN")} \u2013 {data.rangeHigh.toLocaleString("en-IN")}</div>
            </div>
            <div>
              <div style={{ color: "#555", fontSize: 10, marginBottom: 4 }}>PIVOT</div>
              <div style={{ color: ACCENT, fontWeight: 700 }}>{data.pivot.toLocaleString("en-IN")}</div>
            </div>
            <div>
              <div style={{ color: "#555", fontSize: 10, marginBottom: 4 }}>MAX PAIN</div>
              <div style={{ color: PURPLE, fontWeight: 700 }}>{data.maxPain.toLocaleString("en-IN")}</div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
            <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px" }}>
              <Label>Resistance Levels</Label>
              {data.resistance.map((r, i) => (
                <div key={r.level} style={{ marginBottom: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                    <span style={{ color: "#555", fontSize: 11 }}>R{i + 1}</span>
                    <span style={{ color: RED, fontWeight: 700 }}>{r.level.toLocaleString("en-IN")}</span>
                  </div>
                  <div style={{ color: "#444", fontSize: 10 }}>{r.reason}</div>
                </div>
              ))}
            </div>
            <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px" }}>
              <Label>Support Levels</Label>
              {data.support.map((s, i) => (
                <div key={s.level} style={{ marginBottom: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                    <span style={{ color: "#555", fontSize: 11 }}>S{i + 1}</span>
                    <span style={{ color: GREEN, fontWeight: 700 }}>{s.level.toLocaleString("en-IN")}</span>
                  </div>
                  <div style={{ color: "#444", fontSize: 10 }}>{s.reason}</div>
                </div>
              ))}
            </div>
          </div>
          <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}>
            <Label>Key OI Strikes Tomorrow</Label>
            <div style={{ color: RED,    fontSize: 12, marginBottom: 6 }}>\uD83D\uDD34 {data.bigCEWall}</div>
            <div style={{ color: GREEN,  fontSize: 12, marginBottom: 6 }}>\uD83D\uDFE2 {data.bigPEWall}</div>
            <div style={{ color: YELLOW, fontSize: 12 }}>\u26A0\uFE0F {data.unusual}</div>
          </div>
          <div style={{ background: ACCENT + "11", border: `1px solid ${ACCENT}33`, borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}>
            <Label>Trading Strategy</Label>
            <div style={{ color: "#ccc", fontSize: 12, lineHeight: 1.7 }}>{data.strategy}</div>
          </div>
          <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px" }}>
            <Label>Opening Bias + Action Plan</Label>
            <div style={{ color: YELLOW, fontSize: 12, marginBottom: 10 }}>{data.opening}</div>
            {data.plan.map((p, i) => (
              <div key={i} style={{ color: "#888", fontSize: 12, marginBottom: 7, paddingLeft: 10, borderLeft: `2px solid ${ACCENT}44`, lineHeight: 1.6 }}>
                {p}
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

// \u2500\u2500 TAB: WEEKLY \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function WeeklyTab({ realData }) {
  if (!realData || !realData.niftyBias) {
    return (<div style={{ textAlign: "center", padding: 60, color: "#555" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>📅</div>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8, color: "#888" }}>No Weekly Data</div>
      <div style={{ fontSize: 12 }}>Login to Kite → weekly outlook computed from real OI data</div>
    </div>);
  }
  const w = realData;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <span style={{ color: ACCENT, fontWeight: 900, fontSize: 16 }}>WEEKLY OUTLOOK</span>
          <span style={{ color: "#555", fontSize: 12 }}>{w.week}</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
          {[
            { label: "Nifty Bias",     value: w.niftyBias, range: `${w.niftyRange.low}\u2013${w.niftyRange.high}`, color: RED },
            { label: "BankNifty Bias", value: w.bnBias,    range: `${w.bnRange.low}\u2013${w.bnRange.high}`,       color: RED },
          ].map(item => (
            <div key={item.label} style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px" }}>
              <div style={{ color: "#555", fontSize: 10, marginBottom: 4 }}>{item.label}</div>
              <div style={{ color: item.color, fontWeight: 900, fontSize: 16, marginBottom: 4 }}>{item.value}</div>
              <div style={{ color: "#555", fontSize: 11 }}>Range: {item.range}</div>
            </div>
          ))}
        </div>
        <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}>
          <Label>Weekly OI Analysis</Label>
          {w.oiAnalysis.map((a, i) => (
            <div key={i} style={{ color: "#aaa", fontSize: 12, marginBottom: 7, paddingLeft: 8, borderLeft: `2px solid ${PURPLE}55` }}>
              {a}
            </div>
          ))}
        </div>
        <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}>
          <Label>FII / DII Flow</Label>
          <div style={{ color: RED,    fontSize: 12, marginBottom: 6 }}>FII: {w.fii}</div>
          <div style={{ color: GREEN,  fontSize: 12, marginBottom: 6 }}>DII: {w.dii}</div>
          <div style={{ color: YELLOW, fontSize: 12, fontWeight: 600 }}>\u2192 {w.verdict}</div>
        </div>
        <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}>
          <Label>Macro Events This Week</Label>
          {w.macro.map((m, i) => (
            <div key={i} style={{ color: "#aaa", fontSize: 12, marginBottom: 6 }}>\u26A1 {m}</div>
          ))}
        </div>
        <div style={{ background: "#0D0D15", borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}>
          <Label>Weekly Trading Plan</Label>
          {w.plan.map((p, i) => (
            <div key={i} style={{ display: "flex", gap: 12, marginBottom: 10, alignItems: "flex-start" }}>
              <span style={{ color: p.col, fontWeight: 700, minWidth: 80, fontSize: 12 }}>{p.day}</span>
              <span style={{ color: "#888", fontSize: 12, lineHeight: 1.5 }}>{p.text}</span>
            </div>
          ))}
        </div>
        <div style={{ background: RED + "11", border: `1px solid ${RED}33`, borderRadius: 8, padding: "14px" }}>
          <Label>Make-or-Break Levels</Label>
          <div style={{ color: RED, fontSize: 13, fontWeight: 700, marginBottom: 6 }}>
            Nifty: {w.niftyMoB.toLocaleString("en-IN")} \u2014 Break below = Full bearish trend reversal
          </div>
          <div style={{ color: RED, fontSize: 13, fontWeight: 700 }}>
            BankNifty: {w.bnMoB.toLocaleString("en-IN")} \u2014 Break below = Full bearish trend reversal
          </div>
        </div>
      </Card>
    </div>
  );
}

// \u2500\u2500 TAB: UNUSUAL ACTIVITY \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function UnusualTab({ unusualData, oiData }) {
  const alerts = unusualData && unusualData.length > 0 ? unusualData : [];
  const alertColor = { CRITICAL: RED, HIGH: ORANGE, MEDIUM: YELLOW };

  // Aggregate OI flow from oiData
  const fmtL = (n) => n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>

      {/* OI FLOW AGGREGATION — auto-updating */}
      {oiData && ["nifty", "banknifty"].map(key => {
        const d = oiData[key];
        if (!d) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
        const ceNetOI = d.ceOIChangePos + d.ceOIChangeNeg;
        const peNetOI = d.peOIChangePos + d.peOIChangeNeg;
        const totalNet = ceNetOI + peNetOI;

        return (
          <Card key={key} style={{ background: "#0D0D15", border: `1px solid ${ACCENT}33` }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <span style={{ color: ACCENT, fontWeight: 900, fontSize: 14 }}>{label} OI FLOW</span>
              <span style={{ color: "#444", fontSize: 10 }}>LTP: {d.ltp?.toLocaleString("en-IN")} | {d.timestamp}</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8, marginBottom: 8 }}>
              {/* CE Side */}
              <div style={{ background: RED + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${RED}22` }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1, marginBottom: 6 }}>CALL STRIKES</div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ color: GREEN, fontSize: 12, fontWeight: 700 }}>+OI: {fmtL(d.ceOIChangePos)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ color: RED, fontSize: 12, fontWeight: 700 }}>-OI: {fmtL(d.ceOIChangeNeg)}</span>
                </div>
                <div style={{ borderTop: `1px solid ${BORDER}`, paddingTop: 4, marginTop: 4 }}>
                  <span style={{ color: ceNetOI >= 0 ? GREEN : RED, fontSize: 13, fontWeight: 900 }}>
                    Net: {ceNetOI >= 0 ? "+" : ""}{fmtL(ceNetOI)}
                  </span>
                  <span style={{ color: "#555", fontSize: 10, marginLeft: 6 }}>
                    {ceNetOI > 0 ? "CE writing = Bearish" : ceNetOI < 0 ? "CE unwinding = Bullish" : "Neutral"}
                  </span>
                </div>
              </div>
              {/* PE Side */}
              <div style={{ background: GREEN + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${GREEN}22` }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1, marginBottom: 6 }}>PUT STRIKES</div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ color: GREEN, fontSize: 12, fontWeight: 700 }}>+OI: {fmtL(d.peOIChangePos)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ color: RED, fontSize: 12, fontWeight: 700 }}>-OI: {fmtL(d.peOIChangeNeg)}</span>
                </div>
                <div style={{ borderTop: `1px solid ${BORDER}`, paddingTop: 4, marginTop: 4 }}>
                  <span style={{ color: peNetOI >= 0 ? GREEN : RED, fontSize: 13, fontWeight: 900 }}>
                    Net: {peNetOI >= 0 ? "+" : ""}{fmtL(peNetOI)}
                  </span>
                  <span style={{ color: "#555", fontSize: 10, marginLeft: 6 }}>
                    {peNetOI > 0 ? "PE writing = Bullish" : peNetOI < 0 ? "PE unwinding = Bearish" : "Neutral"}
                  </span>
                </div>
              </div>
              {/* Net verdict */}
              <div style={{ background: PURPLE + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${PURPLE}22`, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1, marginBottom: 6 }}>NET OI VERDICT</div>
                <div style={{ color: totalNet >= 0 ? GREEN : RED, fontSize: 18, fontWeight: 900 }}>
                  {totalNet >= 0 ? "+" : ""}{fmtL(totalNet)}
                </div>
                <div style={{ color: PURPLE, fontSize: 11, fontWeight: 700, marginTop: 4 }}>
                  {ceNetOI > 0 && peNetOI > 0 ? "Range Bound (Both writing)" :
                   ceNetOI > 0 && peNetOI <= 0 ? "BEARISH (CE write + PE unwind)" :
                   ceNetOI <= 0 && peNetOI > 0 ? "BULLISH (CE unwind + PE write)" :
                   "Directional (Both unwinding)"}
                </div>
                <div style={{ color: "#444", fontSize: 10, marginTop: 4 }}>PCR: {d.pcr}</div>
              </div>
            </div>
          </Card>
        );
      })}

      {/* Split alerts by expiry */}
      {(() => {
        const currentAlerts = alerts.filter(u => u.expiryLabel === "CURRENT" || !u.expiryLabel);
        const nextAlerts = alerts.filter(u => u.expiryLabel === "NEXT");

        const renderAlertSection = (title, sectionAlerts, borderColor) => (
          <>
            <Card style={{ background: borderColor + "0A", border: `1px solid ${borderColor}33` }}>
              <div style={{ color: borderColor, fontWeight: 700, fontSize: 13, marginBottom: 4 }}>{title} ({sectionAlerts.length} alerts)</div>
              <div style={{ color: "#555", fontSize: 11, lineHeight: 1.6 }}>
                Real-time alerts when OI change {">"} 1L. Auto-classified: Writing / Buying / Short Covering / Long Unwinding
              </div>
            </Card>
            {sectionAlerts.length === 0 && (
              <div style={{ textAlign: "center", padding: 20, color: "#555", fontSize: 12 }}>No alerts for this expiry yet.</div>
            )}
            {sectionAlerts.map((u, i) => (
              <Card key={i} style={{ borderColor: alertColor[u.alert] + "44" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                  <div>
                    <div style={{ color: alertColor[u.alert], fontWeight: 700, fontSize: 14, marginBottom: 4 }}>
                      {u.type} {"\u2014"} {u.instrument}
                    </div>
                    <div style={{ color: "#555", fontSize: 11 }}>{u.time} {u.expiry && <span style={{ color: ORANGE, marginLeft: 6 }}>Exp: {u.expiry}</span>}</div>
                  </div>
                  <Badge text={u.alert} color={alertColor[u.alert]} />
                </div>
                <div style={{ display: "flex", gap: 10, marginBottom: 10 }}>
                  <div style={{ flex: 1, background: "#0D0D15", borderRadius: 8, padding: "8px 12px" }}>
                    <div style={{ color: "#555", fontSize: 10, marginBottom: 3 }}>OI CHANGE</div>
                    <div style={{ color: "#fff", fontWeight: 700 }}>{u.oiChange}</div>
                  </div>
                  <div style={{ flex: 1, background: "#0D0D15", borderRadius: 8, padding: "8px 12px" }}>
                    <div style={{ color: "#555", fontSize: 10, marginBottom: 3 }}>PREMIUM CHANGE</div>
                    <div style={{ color: u.premChange?.includes?.("+") ? GREEN : RED, fontWeight: 700 }}>{u.premChange}</div>
                  </div>
                </div>
                <div style={{ padding: "8px 12px", background: alertColor[u.alert] + "11", borderRadius: 8, color: alertColor[u.alert], fontSize: 12, fontWeight: 600 }}>
                  {"\u2192"} {u.signal}
                </div>
              </Card>
            ))}
          </>
        );

        return (
          <>
            {renderAlertSection("CURRENT EXPIRY — LIVE OI ALERTS", currentAlerts, RED)}
            {nextAlerts.length > 0 && renderAlertSection("NEXT EXPIRY — LIVE OI ALERTS", nextAlerts, ORANGE)}
          </>
        );
      })()}
    </div>
  );
}

// ── TAB: SELLERS ─────────────────────────────────────────────────────

function SellersTab({ data }) {
  const fmtL = (n) => n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0";
  const actColor = { WRITING: ORANGE, SHORT_COVER: PURPLE, BUYING: GREEN, LONG_UNWIND: RED, NEUTRAL: "#444" };
  const actLabel = { WRITING: "Writing", SHORT_COVER: "Short Cover", BUYING: "Buying", LONG_UNWIND: "Long Unwind", NEUTRAL: "-" };
  const magColor = { MAJOR: RED, MINOR: "#555" };

  if (!data) return <div style={{ textAlign: "center", padding: 60, color: "#555" }}>Loading seller data...</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {["nifty", "banknifty"].map(key => {
        const d = data[key];
        if (!d) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
        const biasColor = d.sellerBias === "BULLISH" ? GREEN : d.sellerBias === "BEARISH" ? RED : YELLOW;
        const netColor = d.netOIChange > 0 ? GREEN : d.netOIChange < 0 ? RED : "#888";

        return (
          <div key={key} style={{ display: "flex", flexDirection: "column", gap: 10 }}>

            {/* ROW 1: +OI / -OI / NET CHANGE / VERDICT */}
            <Card style={{ background: "#0D0D15", border: `1px solid ${ORANGE}33` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <span style={{ color: ORANGE, fontWeight: 900, fontSize: 14 }}>{label} SELLER FLOW</span>
                <span style={{ color: "#444", fontSize: 10 }}>LTP: {d.ltp?.toLocaleString("en-IN")} | {d.timestamp}</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8, marginBottom: 8 }}>
                {/* +OI */}
                <div style={{ background: GREEN + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${GREEN}22`, textAlign: "center" }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1 }}>+OI (Added)</div>
                  <div style={{ color: GREEN, fontSize: 18, fontWeight: 900, marginTop: 4 }}>+{fmtL(d.totalPlusOI)}</div>
                  <div style={{ color: "#555", fontSize: 9, marginTop: 4 }}>Writing + Buying</div>
                </div>
                {/* -OI */}
                <div style={{ background: RED + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${RED}22`, textAlign: "center" }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1 }}>-OI (Removed)</div>
                  <div style={{ color: RED, fontSize: 18, fontWeight: 900, marginTop: 4 }}>-{fmtL(d.totalMinusOI)}</div>
                  <div style={{ color: "#555", fontSize: 9, marginTop: 4 }}>SC + Unwinding</div>
                </div>
                {/* NET */}
                <div style={{ background: netColor + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${netColor}22`, textAlign: "center" }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1 }}>NET CHANGE</div>
                  <div style={{ color: netColor, fontSize: 18, fontWeight: 900, marginTop: 4 }}>{d.netOIChange > 0 ? "+" : ""}{fmtL(d.netOIChange)}</div>
                  <div style={{ color: "#555", fontSize: 9, marginTop: 4 }}>Major: {d.majorCount || 0} | Minor: {d.minorCount || 0}</div>
                </div>
                {/* VERDICT */}
                <div style={{ background: biasColor + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${biasColor}22`, textAlign: "center", display: "flex", flexDirection: "column", justifyContent: "center" }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1 }}>SELLER BIAS</div>
                  <div style={{ color: biasColor, fontSize: 18, fontWeight: 900, marginTop: 4 }}>{d.sellerBias}</div>
                  <div style={{ color: "#555", fontSize: 9, marginTop: 4 }}>Net Seller: {fmtL(d.netSellerOI)}</div>
                </div>
              </div>
            </Card>

            {/* ROW 2: CE/PE SELLER BREAKDOWN */}
            <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {/* CE SELLERS */}
                <div style={{ background: RED + "06", borderRadius: 8, padding: "10px 12px", border: `1px solid ${RED}15` }}>
                  <div style={{ color: RED, fontSize: 11, fontWeight: 900, marginBottom: 8 }}>CE SELLERS (Resistance)</div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ color: "#888", fontSize: 11 }}>Writing (new)</span>
                    <span style={{ color: ORANGE, fontSize: 12, fontWeight: 900 }}>+{fmtL(d.ceWritingOI)}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ color: "#888", fontSize: 11 }}>Short Cover (exit)</span>
                    <span style={{ color: PURPLE, fontSize: 12, fontWeight: 900 }}>-{fmtL(d.ceShortCoverOI)}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ color: "#888", fontSize: 11 }}>Buying</span>
                    <span style={{ color: GREEN, fontSize: 12, fontWeight: 700 }}>+{fmtL(d.ceBuyingOI)}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#888", fontSize: 11 }}>Long Unwind</span>
                    <span style={{ color: RED, fontSize: 12, fontWeight: 700 }}>-{fmtL(d.ceLongUnwindOI)}</span>
                  </div>
                </div>
                {/* PE SELLERS */}
                <div style={{ background: GREEN + "06", borderRadius: 8, padding: "10px 12px", border: `1px solid ${GREEN}15` }}>
                  <div style={{ color: GREEN, fontSize: 11, fontWeight: 900, marginBottom: 8 }}>PE SELLERS (Support)</div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ color: "#888", fontSize: 11 }}>Writing (new)</span>
                    <span style={{ color: ORANGE, fontSize: 12, fontWeight: 900 }}>+{fmtL(d.peWritingOI)}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ color: "#888", fontSize: 11 }}>Short Cover (exit)</span>
                    <span style={{ color: PURPLE, fontSize: 12, fontWeight: 900 }}>-{fmtL(d.peShortCoverOI)}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ color: "#888", fontSize: 11 }}>Buying</span>
                    <span style={{ color: GREEN, fontSize: 12, fontWeight: 700 }}>+{fmtL(d.peBuyingOI)}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#888", fontSize: 11 }}>Long Unwind</span>
                    <span style={{ color: RED, fontSize: 12, fontWeight: 700 }}>-{fmtL(d.peLongUnwindOI)}</span>
                  </div>
                </div>
              </div>
            </Card>

            {/* ROW 3: STRIKE SHIFTS */}
            {d.shifts?.length > 0 && (
              <Card style={{ background: ACCENT + "08", border: `1px solid ${ACCENT}33` }}>
                <div style={{ color: ACCENT, fontWeight: 900, fontSize: 13, marginBottom: 10 }}>STRIKE SHIFTS — Where Money is Moving</div>
                {d.shifts.map((sh, i) => (
                  <div key={i} style={{ background: "#111118", borderRadius: 8, padding: "10px 12px", marginBottom: i < d.shifts.length - 1 ? 8 : 0 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                      <span style={{ color: sh.side === "CE" ? RED : GREEN, fontWeight: 900, fontSize: 12 }}>{sh.side} SHIFT</span>
                      <span style={{ background: ACCENT + "22", color: ACCENT, padding: "2px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{sh.meaning}</span>
                    </div>
                    <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ color: RED, fontSize: 10, fontWeight: 700, marginBottom: 4 }}>OI LEAVING</div>
                        {sh.from.map((f, j) => (
                          <div key={j} style={{ color: RED, fontSize: 11, marginBottom: 2 }}>
                            {f.strike} ({(f.change/100000).toFixed(1)}L)
                          </div>
                        ))}
                      </div>
                      <div style={{ color: ACCENT, fontSize: 18 }}>&rarr;</div>
                      <div style={{ flex: 1 }}>
                        <div style={{ color: GREEN, fontSize: 10, fontWeight: 700, marginBottom: 4 }}>OI ENTERING</div>
                        {sh.to.map((t, j) => (
                          <div key={j} style={{ color: GREEN, fontSize: 11, marginBottom: 2 }}>
                            {t.strike} (+{(t.change/100000).toFixed(1)}L)
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </Card>
            )}

            {/* ROW 4: MAJOR CHANGES (>2L or >20%) */}
            {d.strikes?.some(st => st.ceMagnitude === "MAJOR" || st.peMagnitude === "MAJOR") && (
              <Card style={{ background: "#0D0D15", border: `1px solid ${RED}33` }}>
                <div style={{ color: RED, fontWeight: 900, fontSize: 13, marginBottom: 10 }}>MAJOR CHANGES ({">"}2L or {">"}20%)</div>
                {d.strikes.filter(st => st.ceMagnitude === "MAJOR" || st.peMagnitude === "MAJOR").map((st, i) => (
                  <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: `1px solid ${BORDER}33` }}>
                    <span style={{ color: st.isATM ? ACCENT : "#ccc", fontWeight: 700, fontSize: 12, minWidth: 60 }}>{st.strike}{st.isATM ? " ATM" : ""}</span>
                    {st.ceMagnitude === "MAJOR" && (
                      <span style={{ fontSize: 11 }}>
                        <span style={{ color: RED, fontWeight: 700 }}>CE </span>
                        <span style={{ color: st.ceOIChange > 0 ? GREEN : RED, fontWeight: 900 }}>{st.ceOIChange > 0 ? "+" : ""}{fmtL(st.ceOIChange)} ({st.ceOIChangePct > 0 ? "+" : ""}{st.ceOIChangePct}%)</span>
                        <span style={{ background: actColor[st.ceActivity] + "22", color: actColor[st.ceActivity], padding: "1px 6px", borderRadius: 3, fontSize: 9, fontWeight: 700, marginLeft: 6 }}>{actLabel[st.ceActivity]}</span>
                      </span>
                    )}
                    {st.peMagnitude === "MAJOR" && (
                      <span style={{ fontSize: 11 }}>
                        <span style={{ color: GREEN, fontWeight: 700 }}>PE </span>
                        <span style={{ color: st.peOIChange > 0 ? GREEN : RED, fontWeight: 900 }}>{st.peOIChange > 0 ? "+" : ""}{fmtL(st.peOIChange)} ({st.peOIChangePct > 0 ? "+" : ""}{st.peOIChangePct}%)</span>
                        <span style={{ background: actColor[st.peActivity] + "22", color: actColor[st.peActivity], padding: "1px 6px", borderRadius: 3, fontSize: 9, fontWeight: 700, marginLeft: 6 }}>{actLabel[st.peActivity]}</span>
                      </span>
                    )}
                  </div>
                ))}
              </Card>
            )}

            {/* ROW 5: FULL STRIKE TABLE */}
            <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
              <div style={{ color: ORANGE, fontWeight: 700, fontSize: 13, marginBottom: 10 }}>{label} ALL STRIKES ({d.strikes?.length || 0} active)</div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>STRIKE</th>
                      <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE Chg</th>
                      <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE %</th>
                      <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE Prem</th>
                      <th style={{ padding: "5px 6px", color: RED, textAlign: "center" }}>CE Type</th>
                      <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE Chg</th>
                      <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE %</th>
                      <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE Prem</th>
                      <th style={{ padding: "5px 6px", color: GREEN, textAlign: "center" }}>PE Type</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.strikes?.map((st, i) => (
                      <tr key={i} style={{
                        borderBottom: `1px solid ${BORDER}33`,
                        background: st.isATM ? ACCENT + "11" : st.ceMagnitude === "MAJOR" || st.peMagnitude === "MAJOR" ? ORANGE + "08" : "transparent",
                      }}>
                        <td style={{ padding: "4px 6px", color: st.isATM ? ACCENT : "#ccc", fontWeight: st.isATM ? 900 : 400 }}>{st.strike}{st.isATM ? " ATM" : ""}</td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: st.ceOIChange > 0 ? GREEN : st.ceOIChange < 0 ? RED : "#555", fontWeight: 700 }}>
                          {st.ceOIChange > 0 ? "+" : ""}{fmtL(st.ceOIChange)}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: Math.abs(st.ceOIChangePct) > 20 ? ORANGE : "#888", fontWeight: Math.abs(st.ceOIChangePct) > 20 ? 900 : 400 }}>
                          {st.ceOIChangePct > 0 ? "+" : ""}{st.ceOIChangePct}%
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: st.cePremChange > 0 ? GREEN : st.cePremChange < 0 ? RED : "#555" }}>
                          {st.cePremChange > 0 ? "+" : ""}{st.cePremChange?.toFixed(1)}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "center" }}>
                          <span style={{ background: actColor[st.ceActivity] + "22", color: actColor[st.ceActivity], padding: "1px 6px", borderRadius: 3, fontSize: 9, fontWeight: 700 }}>{actLabel[st.ceActivity]}</span>
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: st.peOIChange > 0 ? GREEN : st.peOIChange < 0 ? RED : "#555", fontWeight: 700 }}>
                          {st.peOIChange > 0 ? "+" : ""}{fmtL(st.peOIChange)}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: Math.abs(st.peOIChangePct) > 20 ? ORANGE : "#888", fontWeight: Math.abs(st.peOIChangePct) > 20 ? 900 : 400 }}>
                          {st.peOIChangePct > 0 ? "+" : ""}{st.peOIChangePct}%
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: st.pePremChange > 0 ? GREEN : st.pePremChange < 0 ? RED : "#555" }}>
                          {st.pePremChange > 0 ? "+" : ""}{st.pePremChange?.toFixed(1)}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "center" }}>
                          <span style={{ background: actColor[st.peActivity] + "22", color: actColor[st.peActivity], padding: "1px 6px", borderRadius: 3, fontSize: 9, fontWeight: 700 }}>{actLabel[st.peActivity]}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {(!d.strikes || d.strikes.length === 0) && (
                <div style={{ textAlign: "center", padding: 20, color: "#555", fontSize: 12 }}>No strike activity yet.</div>
              )}
            </Card>
          </div>
        );
      })}
    </div>
  );
}

// ── TAB: TRADE AI ───────────────────────────────────────────────────

function TradeAITab({ data }) {
  const fmtL = (n) => n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0";

  if (!data) return <div style={{ textAlign: "center", padding: 60, color: "#555" }}>Loading trade analysis...</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header */}
      <Card style={{ background: ACCENT + "0A", border: `1px solid ${ACCENT}33` }}>
        <div style={{ color: ACCENT, fontWeight: 700, fontSize: 13, marginBottom: 4 }}>TRADE AI - SMART MONEY ANALYSIS</div>
        <div style={{ color: "#555", fontSize: 11, lineHeight: 1.6 }}>
          Combines Unusual Activity + Seller OI Flow to identify high-probability trade setups. Sellers use 20x margin capital = Smart Money signal.
        </div>
      </Card>

      {["nifty", "banknifty"].map(key => {
        const d = data[key];
        if (!d) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
        const biasColor = d.sellerBias === "BULLISH" ? GREEN : d.sellerBias === "BEARISH" ? RED : YELLOW;

        return (
          <div key={key} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {/* MARKET STRUCTURE */}
            <Card style={{ background: "#0D0D15", border: `1px solid ${biasColor}33` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ color: biasColor, fontWeight: 900, fontSize: 16 }}>{label}</span>
                  <span style={{
                    background: biasColor + "22", color: biasColor,
                    padding: "3px 12px", borderRadius: 6, fontSize: 12, fontWeight: 900,
                  }}>SELLER BIAS: {d.sellerBias}</span>
                </div>
                <span style={{ color: "#444", fontSize: 11 }}>LTP: {d.ltp?.toLocaleString("en-IN")} | ATM: {d.atm} | {d.timestamp}</span>
              </div>

              {/* Seller Stats Grid */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8, marginBottom: 12 }}>
                <div style={{ background: ORANGE + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${ORANGE}22` }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1 }}>CE WRITERS</div>
                  <div style={{ color: ORANGE, fontSize: 16, fontWeight: 900, marginTop: 4 }}>{fmtL(d.sellerStats?.ceWriting)}</div>
                  <div style={{ color: RED, fontSize: 9, marginTop: 2 }}>= Resistance building</div>
                </div>
                <div style={{ background: ORANGE + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${ORANGE}22` }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1 }}>PE WRITERS</div>
                  <div style={{ color: ORANGE, fontSize: 16, fontWeight: 900, marginTop: 4 }}>{fmtL(d.sellerStats?.peWriting)}</div>
                  <div style={{ color: GREEN, fontSize: 9, marginTop: 2 }}>= Support building</div>
                </div>
                <div style={{ background: PURPLE + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${PURPLE}22` }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700, letterSpacing: 1 }}>SHORT COVERING</div>
                  <div style={{ color: PURPLE, fontSize: 14, fontWeight: 900, marginTop: 4 }}>
                    CE: {fmtL(d.sellerStats?.ceShortCover)} | PE: {fmtL(d.sellerStats?.peShortCover)}
                  </div>
                  <div style={{ color: PURPLE, fontSize: 9, marginTop: 2 }}>= Sellers exiting</div>
                </div>
              </div>

              {/* Key Levels */}
              {d.keyLevels && (d.keyLevels.resistance?.length > 0 || d.keyLevels.support?.length > 0) && (
                <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
                  {d.keyLevels.resistance?.length > 0 && (
                    <div style={{ flex: 1, background: RED + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${RED}22` }}>
                      <div style={{ color: RED, fontSize: 10, fontWeight: 700, marginBottom: 4 }}>RESISTANCE (CE Writing)</div>
                      <div style={{ color: "#fff", fontSize: 14, fontWeight: 900 }}>{d.keyLevels.resistance.join(" > ")}</div>
                    </div>
                  )}
                  {d.keyLevels.support?.length > 0 && (
                    <div style={{ flex: 1, background: GREEN + "0A", borderRadius: 8, padding: "8px 12px", border: `1px solid ${GREEN}22` }}>
                      <div style={{ color: GREEN, fontSize: 10, fontWeight: 700, marginBottom: 4 }}>SUPPORT (PE Writing)</div>
                      <div style={{ color: "#fff", fontSize: 14, fontWeight: 900 }}>{d.keyLevels.support.join(" > ")}</div>
                    </div>
                  )}
                </div>
              )}
            </Card>

            {/* REASONS */}
            {d.reasons?.length > 0 && (
              <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
                <div style={{ color: YELLOW, fontWeight: 700, fontSize: 13, marginBottom: 8 }}>WHY? - ANALYSIS REASONS</div>
                {d.reasons.map((r, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6, alignItems: "flex-start" }}>
                    <span style={{ color: YELLOW, fontSize: 11, flexShrink: 0 }}>{i + 1}.</span>
                    <span style={{ color: "#ccc", fontSize: 11, lineHeight: 1.5 }}>{r}</span>
                  </div>
                ))}
              </Card>
            )}

            {/* TRADE RECOMMENDATIONS */}
            {d.recommendations?.length > 0 && (
              <Card style={{ background: ACCENT + "08", border: `1px solid ${ACCENT}44` }}>
                <div style={{ color: ACCENT, fontWeight: 900, fontSize: 14, marginBottom: 10 }}>TRADE RECOMMENDATIONS</div>
                {d.recommendations.map((rec, i) => {
                  const confColor = rec.confidence === "HIGH" ? GREEN : rec.confidence === "MEDIUM" ? YELLOW : "#555";
                  return (
                    <div key={i} style={{
                      background: "#111118", borderRadius: 8, padding: "12px 14px", marginBottom: 8,
                      border: `1px solid ${confColor}33`,
                    }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{
                            background: confColor + "22", color: confColor,
                            padding: "3px 10px", borderRadius: 4, fontSize: 11, fontWeight: 900,
                          }}>{rec.action}</span>
                          <span style={{ color: "#fff", fontSize: 14, fontWeight: 900 }}>@ {rec.strike}</span>
                        </div>
                        <span style={{
                          background: confColor + "22", color: confColor,
                          padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                        }}>{rec.confidence}</span>
                      </div>
                      <div style={{ color: "#999", fontSize: 11, lineHeight: 1.5 }}>{rec.reason}</div>
                    </div>
                  );
                })}
              </Card>
            )}

            {/* RECENT UNUSUAL ALERTS */}
            {d.recentAlerts?.length > 0 && (
              <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
                <div style={{ color: RED, fontWeight: 700, fontSize: 12, marginBottom: 8 }}>RECENT UNUSUAL ACTIVITY - {label}</div>
                {d.recentAlerts.map((u, i) => (
                  <div key={i} style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "6px 0", borderBottom: i < d.recentAlerts.length - 1 ? `1px solid ${BORDER}44` : "none",
                  }}>
                    <div>
                      <span style={{ color: ORANGE, fontSize: 11, fontWeight: 700 }}>{u.type}</span>
                      <span style={{ color: "#888", fontSize: 11 }}> - {u.instrument}</span>
                    </div>
                    <span style={{ color: "#666", fontSize: 10 }}>{u.oiChange}</span>
                  </div>
                ))}
              </Card>
            )}
          </div>
        );
      })}
    </div>
  );
}

// \u2500\u2500 TAB: CLAUDE PROMPT \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

// ── TAB: HIDDEN SHIFT — Institutional OI Cooking Detection ──────────────

function HiddenShiftTab({ data }) {
  const fmtL = (n) => n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0";
  const sevColor = { HIGH: RED, MEDIUM: ORANGE, LOW: "#555" };
  const patternColor = { 1: "#FF6B35", 2: PURPLE, 3: ACCENT, 4: YELLOW };

  if (!data) return (
    <div style={{ textAlign: "center", padding: 60, color: "#555" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>🕵️</div>
      <div style={{ fontSize: 14, color: "#666" }}>Loading Hidden Shift detector...</div>
      <div style={{ fontSize: 11, color: "#444", marginTop: 8 }}>OI snapshots taken every 30 min. First detection after ~30 min of market open.</div>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Card style={{ background: "#0D0D15", border: `1px solid ${RED}33` }}>
        <div style={{ color: RED, fontWeight: 900, fontSize: 14, marginBottom: 4 }}>HIDDEN SHIFT — Institutional OI Cooking Detector</div>
        <div style={{ color: "#555", fontSize: 11, lineHeight: 1.6 }}>
          Detects when institutions "cook" OI BEFORE a price move. Compares current OI vs ~30-60 min ago snapshot.
          Optimized for option BUYERS — CE/PE buy signals only.
        </div>
      </Card>

      {["nifty", "banknifty"].map(key => {
        const d = data[key];
        if (!d) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
        const sigColor = d.overallSignal?.includes("CE") ? GREEN : d.overallSignal?.includes("PE") ? RED : "#555";
        const confColor = d.confidence === "HIGH" ? GREEN : d.confidence === "MEDIUM" ? YELLOW : "#555";

        return (
          <div key={key} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {/* VERDICT */}
            <Card style={{ background: sigColor + "08", border: `1px solid ${sigColor}44` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ color: "#fff", fontWeight: 900, fontSize: 16 }}>{label}</span>
                  <span style={{ background: sigColor + "22", color: sigColor, padding: "4px 14px", borderRadius: 6, fontSize: 13, fontWeight: 900 }}>{d.overallSignal}</span>
                  <span style={{ background: confColor + "22", color: confColor, padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{d.confidence}</span>
                </div>
                <span style={{ color: "#444", fontSize: 10 }}>{d.timestamp} | Snap: {d.snapshotAge}m ago</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8, marginBottom: 12 }}>
                <div style={{ background: "#111118", borderRadius: 8, padding: "6px 10px", textAlign: "center" }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>LTP NOW</div>
                  <div style={{ color: "#fff", fontSize: 14, fontWeight: 900 }}>{d.ltp?.toLocaleString("en-IN")}</div>
                </div>
                <div style={{ background: "#111118", borderRadius: 8, padding: "6px 10px", textAlign: "center" }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>~1HR AGO</div>
                  <div style={{ color: "#fff", fontSize: 14, fontWeight: 900 }}>{d.refPrice?.toLocaleString("en-IN")}</div>
                </div>
                <div style={{ background: "#111118", borderRadius: 8, padding: "6px 10px", textAlign: "center" }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>MOVE</div>
                  <div style={{ color: d.priceDirection === "UP" ? GREEN : d.priceDirection === "DOWN" ? RED : "#888", fontSize: 14, fontWeight: 900 }}>
                    {d.priceDirection === "UP" ? "+" : d.priceDirection === "DOWN" ? "-" : ""}{d.priceMove} pts
                  </div>
                </div>
                <div style={{ background: "#111118", borderRadius: 8, padding: "6px 10px", textAlign: "center" }}>
                  <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>PCR</div>
                  <div style={{ color: "#fff", fontSize: 14, fontWeight: 900 }}>{d.refPCR} → {d.currentPCR}</div>
                </div>
              </div>
              <div style={{ background: sigColor + "11", borderRadius: 8, padding: "10px 14px", border: `1px solid ${sigColor}33` }}>
                <div style={{ color: sigColor, fontSize: 12, fontWeight: 700, marginBottom: 4 }}>VERDICT</div>
                <div style={{ color: "#ccc", fontSize: 12, lineHeight: 1.6 }}>{d.verdict}</div>
              </div>
            </Card>

            {/* PATTERN CARDS */}
            {d.patterns?.length > 0 ? d.patterns.map((p, i) => {
              const pColor = patternColor[p.id] || ORANGE;
              return (
                <Card key={i} style={{ background: "#0D0D15", border: `1px solid ${pColor}33` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 20 }}>{p.emoji}</span>
                      <span style={{ color: pColor, fontWeight: 900, fontSize: 14 }}>P{p.id}: {p.name}</span>
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      <span style={{ background: sevColor[p.severity] + "22", color: sevColor[p.severity], padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{p.severity}</span>
                      <span style={{ background: (p.direction?.includes("CE") ? GREEN : RED) + "22", color: p.direction?.includes("CE") ? GREEN : RED, padding: "2px 10px", borderRadius: 4, fontSize: 11, fontWeight: 900 }}>{p.direction}</span>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 10, marginBottom: 10 }}>
                    <div style={{ background: pColor + "11", borderRadius: 8, padding: "8px 14px", border: `1px solid ${pColor}22` }}>
                      <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>TARGET</div>
                      <div style={{ color: "#fff", fontSize: 18, fontWeight: 900 }}>{p.targetStrike}</div>
                    </div>
                    <div style={{ background: "#111118", borderRadius: 8, padding: "8px 14px", flex: 1 }}>
                      <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>INSTITUTIONS DOING</div>
                      <div style={{ color: "#ccc", fontSize: 11, marginTop: 4, lineHeight: 1.5 }}>{p.insight}</div>
                    </div>
                  </div>
                  {/* Details for P1/P2 */}
                  {(p.id === 1 || p.id === 2) && p.details?.map((det, j) => (
                    <div key={j} style={{ display: "flex", justifyContent: "space-between", padding: "4px 8px", borderBottom: `1px solid ${BORDER}33`, fontSize: 11 }}>
                      <span style={{ color: det.side === "CE" ? RED : GREEN, fontWeight: 700 }}>{det.side} @ {det.strike}</span>
                      <span style={{ color: det.oiChange > 0 ? GREEN : RED, fontWeight: 700 }}>OI: {det.oiChange > 0 ? "+" : ""}{fmtL(det.oiChange)} ({det.oiPct > 0 ? "+" : ""}{det.oiPct}%)</span>
                    </div>
                  ))}
                  {/* Details for P3 */}
                  {p.id === 3 && p.details?.map((det, j) => (
                    <div key={j} style={{ fontSize: 12, padding: "4px 8px", color: "#ccc" }}>
                      <span style={{ color: det.side === "CE" ? RED : GREEN, fontWeight: 700 }}>{det.side}: </span>
                      <span style={{ color: RED }}>{det.from?.join(", ")}</span>
                      <span style={{ color: "#555" }}> → </span>
                      <span style={{ color: GREEN }}>{det.to?.join(", ")}</span>
                    </div>
                  ))}
                  {/* Details for P4 */}
                  {p.id === 4 && p.details?.[0] && (
                    <div style={{ display: "flex", gap: 12, padding: "4px 8px", fontSize: 11 }}>
                      <div><span style={{ color: "#555" }}>PCR: </span><span style={{ color: "#fff", fontWeight: 700 }}>{p.details[0].refPCR} → {p.details[0].currentPCR}</span></div>
                      <div><span style={{ color: "#555" }}>Chg: </span><span style={{ color: p.details[0].pcrChange > 0 ? GREEN : RED, fontWeight: 700 }}>{p.details[0].pcrChange > 0 ? "+" : ""}{p.details[0].pcrChange}</span></div>
                      <div><span style={{ color: "#555" }}>Price: </span><span style={{ color: p.details[0].priceDirection === "UP" ? GREEN : RED, fontWeight: 700 }}>{p.details[0].priceDirection} {p.details[0].priceMove}pts</span></div>
                    </div>
                  )}
                </Card>
              );
            }) : (
              <Card style={{ background: "#111118" }}>
                <div style={{ textAlign: "center", padding: 20, color: "#555", fontSize: 12 }}>No institutional patterns detected for {label}. OI changes appear organic.</div>
              </Card>
            )}

            {/* Strike Table */}
            {d.strikes?.some(st => st.ceOIChange !== 0 || st.peOIChange !== 0) && (
              <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
                <div style={{ color: "#666", fontWeight: 700, fontSize: 12, marginBottom: 8 }}>{label} OI vs ~1HR AGO</div>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
                    <thead>
                      <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                        <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Strike</th>
                        <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE OI</th>
                        <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE Chg</th>
                        <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE %</th>
                        <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE OI</th>
                        <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE Chg</th>
                        <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.strikes.filter(st => st.ceOIChange !== 0 || st.peOIChange !== 0).map((st, i) => (
                        <tr key={i} style={{
                          borderBottom: `1px solid ${BORDER}33`,
                          background: st.isATM ? ACCENT + "11" : Math.abs(st.ceOIPct) > 15 || Math.abs(st.peOIPct) > 15 ? ORANGE + "08" : "transparent",
                        }}>
                          <td style={{ padding: "4px 6px", color: st.isATM ? ACCENT : "#ccc", fontWeight: st.isATM ? 900 : 400 }}>{st.strike}{st.isATM ? " ATM" : ""}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: "#888" }}>{fmtL(st.ceOI)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: st.ceOIChange > 0 ? GREEN : st.ceOIChange < 0 ? RED : "#555", fontWeight: 700 }}>{st.ceOIChange > 0 ? "+" : ""}{fmtL(st.ceOIChange)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: Math.abs(st.ceOIPct) > 15 ? ORANGE : "#888", fontWeight: Math.abs(st.ceOIPct) > 15 ? 900 : 400 }}>{st.ceOIPct > 0 ? "+" : ""}{st.ceOIPct}%</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: "#888" }}>{fmtL(st.peOI)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: st.peOIChange > 0 ? GREEN : st.peOIChange < 0 ? RED : "#555", fontWeight: 700 }}>{st.peOIChange > 0 ? "+" : ""}{fmtL(st.peOIChange)}</td>
                          <td style={{ padding: "4px 6px", textAlign: "right", color: Math.abs(st.peOIPct) > 15 ? ORANGE : "#888", fontWeight: Math.abs(st.peOIPct) > 15 ? 900 : 400 }}>{st.peOIPct > 0 ? "+" : ""}{st.peOIPct}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── TAB: AI BRAIN — Claude-Powered Analysis ─────────────────────────

function AIBrainTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);

  const runAnalysis = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchAIAnalysis();
      if (result) { setData(result); setLastRefresh(new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })); }
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { runAnalysis(); }, [runAnalysis]);

  const verdictColor = (v) => v === "BUY CE" ? GREEN : v === "BUY PE" ? RED : "#555";
  const confColor = (c) => c === "HIGH" ? GREEN : c === "MEDIUM" ? YELLOW : "#555";

  if (!data && !loading) return (
    <div style={{ textAlign: "center", padding: 60, color: "#555" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>🤖</div>
      <div style={{ fontSize: 14, color: "#666" }}>AI Brain — Claude-Powered Trading Intelligence</div>
      <button onClick={runAnalysis} style={{ marginTop: 16, background: ACCENT + "22", color: ACCENT, border: `1px solid ${ACCENT}44`, borderRadius: 8, padding: "8px 20px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>Run AI Analysis</button>
    </div>
  );

  const renderIndex = (key, label) => {
    const d = data?.[key];
    if (!d) return null;
    const vc = verdictColor(d.verdict);
    const cc = confColor(d.confidence);
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {/* VERDICT CARD */}
        <Card style={{ background: vc + "08", border: `1px solid ${vc}44` }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ color: "#fff", fontWeight: 900, fontSize: 18 }}>{label}</span>
              <span style={{ background: vc + "22", color: vc, padding: "4px 16px", borderRadius: 6, fontSize: 14, fontWeight: 900 }}>{d.verdict || "NO TRADE"}</span>
              <span style={{ background: cc + "22", color: cc, padding: "3px 10px", borderRadius: 4, fontSize: 11, fontWeight: 700 }}>{d.confidence}</span>
            </div>
          </div>
          {d.verdict !== "NO TRADE" && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 8, marginBottom: 12 }}>
              <div style={{ background: "#111118", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>STRIKE</div>
                <div style={{ color: "#fff", fontSize: 16, fontWeight: 900 }}>{d.strike}</div>
                <div style={{ color: ORANGE, fontSize: 9 }}>{d.expiry} expiry</div>
              </div>
              <div style={{ background: "#111118", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>ENTRY</div>
                <div style={{ color: GREEN, fontSize: 16, fontWeight: 900 }}>{d.entry}</div>
              </div>
              <div style={{ background: "#111118", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>TARGET 1 / T2</div>
                <div style={{ color: GREEN, fontSize: 16, fontWeight: 900 }}>{d.target1}</div>
                <div style={{ color: GREEN, fontSize: 10 }}>T2: {d.target2}</div>
              </div>
              <div style={{ background: "#111118", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>STOPLOSS</div>
                <div style={{ color: RED, fontSize: 16, fontWeight: 900 }}>{d.stoploss}</div>
              </div>
              <div style={{ background: "#111118", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>R:R / HOLD</div>
                <div style={{ color: PURPLE, fontSize: 14, fontWeight: 900 }}>{d.riskReward}</div>
                <div style={{ color: "#888", fontSize: 9 }}>{d.holdTime}</div>
              </div>
            </div>
          )}
        </Card>

        {/* KEY LEVELS */}
        {d.keyLevels && (
          <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
            <div style={{ display: "flex", gap: 12 }}>
              {d.keyLevels.resistance?.length > 0 && (
                <div style={{ flex: 1 }}>
                  <div style={{ color: RED, fontSize: 10, fontWeight: 700, marginBottom: 6 }}>RESISTANCE</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {d.keyLevels.resistance.map((l, i) => <span key={i} style={{ background: RED + "15", color: RED, padding: "3px 10px", borderRadius: 4, fontSize: 12, fontWeight: 700 }}>{l}</span>)}
                  </div>
                </div>
              )}
              {d.keyLevels.support?.length > 0 && (
                <div style={{ flex: 1 }}>
                  <div style={{ color: GREEN, fontSize: 10, fontWeight: 700, marginBottom: 6 }}>SUPPORT</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {d.keyLevels.support.map((l, i) => <span key={i} style={{ background: GREEN + "15", color: GREEN, padding: "3px 10px", borderRadius: 4, fontSize: 12, fontWeight: 700 }}>{l}</span>)}
                  </div>
                </div>
              )}
            </div>
          </Card>
        )}

        {/* REASONS */}
        {d.reasons?.length > 0 && (
          <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
            <div style={{ color: YELLOW, fontWeight: 700, fontSize: 12, marginBottom: 8 }}>WHY THIS TRADE?</div>
            {d.reasons.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 8, marginBottom: 5, alignItems: "flex-start" }}>
                <span style={{ color: GREEN, fontSize: 12, flexShrink: 0 }}>+</span>
                <span style={{ color: "#ccc", fontSize: 11, lineHeight: 1.5 }}>{r}</span>
              </div>
            ))}
          </Card>
        )}

        {/* RISKS */}
        {d.risks?.length > 0 && (
          <Card style={{ background: RED + "06", border: `1px solid ${RED}22` }}>
            <div style={{ color: RED, fontWeight: 700, fontSize: 12, marginBottom: 8 }}>RISKS</div>
            {d.risks.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                <span style={{ color: RED, fontSize: 12, flexShrink: 0 }}>!</span>
                <span style={{ color: "#aaa", fontSize: 11 }}>{r}</span>
              </div>
            ))}
          </Card>
        )}

        {/* PREDICTIONS */}
        {d.prediction && (
          <Card style={{ background: "#0D0D15", border: `1px solid ${PURPLE}33` }}>
            <div style={{ color: PURPLE, fontWeight: 700, fontSize: 12, marginBottom: 10 }}>PREDICTIONS</div>
            {d.prediction.intraday && (
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: ACCENT, fontSize: 10, fontWeight: 700 }}>INTRADAY: </span>
                <span style={{ color: "#ccc", fontSize: 11 }}>{d.prediction.intraday}</span>
              </div>
            )}
            {d.prediction.nextDay && (
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: ORANGE, fontSize: 10, fontWeight: 700 }}>NEXT DAY: </span>
                <span style={{ color: "#ccc", fontSize: 11 }}>{d.prediction.nextDay}</span>
              </div>
            )}
            {d.prediction.weekly && (
              <div>
                <span style={{ color: YELLOW, fontSize: 10, fontWeight: 700 }}>WEEKLY: </span>
                <span style={{ color: "#ccc", fontSize: 11 }}>{d.prediction.weekly}</span>
              </div>
            )}
          </Card>
        )}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* HEADER */}
      <Card style={{ background: ACCENT + "0A", border: `1px solid ${ACCENT}33` }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ color: ACCENT, fontWeight: 900, fontSize: 14, marginBottom: 4 }}>AI BRAIN — Claude-Powered Trading Intelligence</div>
            <div style={{ color: "#555", fontSize: 11 }}>Reads ALL engines (Live, OI, Sellers, Unusual, Hidden Shift, Trap) and gives you ONE verdict. BUYER ONLY.</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {lastRefresh && <span style={{ color: "#444", fontSize: 10 }}>Last: {lastRefresh}</span>}
            {data?._meta?.tokensUsed && <span style={{ color: "#333", fontSize: 9 }}>{data._meta.tokensUsed} tokens</span>}
            <button onClick={runAnalysis} disabled={loading} style={{ background: loading ? "#333" : ACCENT + "22", color: ACCENT, border: `1px solid ${ACCENT}44`, borderRadius: 8, padding: "6px 14px", cursor: loading ? "wait" : "pointer", fontSize: 11, fontWeight: 700 }}>
              {loading ? "Analyzing..." : "Refresh Analysis"}
            </button>
          </div>
        </div>
      </Card>

      {loading && (
        <div style={{ textAlign: "center", padding: 40, color: ACCENT }}>
          <div style={{ fontSize: 24, marginBottom: 8 }}>🧠</div>
          <div style={{ fontSize: 13 }}>Claude is reading all dashboard data...</div>
          <div style={{ fontSize: 11, color: "#555", marginTop: 4 }}>Analyzing Live + OI + Sellers + Unusual + Hidden Shift + Trap engines</div>
        </div>
      )}

      {/* MARKET PULSE */}
      {data?.marketPulse && (
        <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
          <div style={{ color: "#fff", fontWeight: 700, fontSize: 13, marginBottom: 6 }}>MARKET PULSE</div>
          <div style={{ color: "#ccc", fontSize: 12, lineHeight: 1.7 }}>{data.marketPulse}</div>
        </Card>
      )}

      {/* NIFTY */}
      {renderIndex("nifty", "NIFTY")}

      {/* BANKNIFTY */}
      {renderIndex("banknifty", "BANKNIFTY")}

      {/* HEDGE STRATEGY */}
      {data?.hedgeStrategy && (
        <Card style={{ background: PURPLE + "08", border: `1px solid ${PURPLE}33` }}>
          <div style={{ color: PURPLE, fontWeight: 700, fontSize: 12, marginBottom: 4 }}>HEDGE STRATEGY</div>
          <div style={{ color: "#ccc", fontSize: 11, lineHeight: 1.5 }}>{data.hedgeStrategy}</div>
        </Card>
      )}

      {/* AVOID LIST */}
      {data?.avoidList?.length > 0 && (
        <Card style={{ background: RED + "06", border: `1px solid ${RED}22` }}>
          <div style={{ color: RED, fontWeight: 700, fontSize: 12, marginBottom: 6 }}>DO NOT</div>
          {data.avoidList.map((a, i) => (
            <div key={i} style={{ color: "#aaa", fontSize: 11, marginBottom: 3 }}>{"\u26D4"} {a}</div>
          ))}
        </Card>
      )}

      {/* INSTITUTIONAL READ */}
      {data?.institutionalRead && (
        <Card style={{ background: ORANGE + "06", border: `1px solid ${ORANGE}22` }}>
          <div style={{ color: ORANGE, fontWeight: 700, fontSize: 12, marginBottom: 4 }}>INSTITUTIONAL POSITIONING</div>
          <div style={{ color: "#ccc", fontSize: 11, lineHeight: 1.5 }}>{data.institutionalRead}</div>
        </Card>
      )}
    </div>
  );
}

// ── TAB: PRICE ACTION — ATM±3 LTP+OI Imbalance Engine ───────────────

function PriceActionTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [expiries, setExpiries] = useState([]);
  const [selectedExpiry, setSelectedExpiry] = useState("");
  const fmtL = (n) => n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0";

  // Fetch expiries on mount
  useEffect(() => {
    fetch("/api/expiries/NIFTY").then(r => r.json()).then(data => {
      if (Array.isArray(data) && data.length > 0) {
        setExpiries(data);
        const current = data.find(e => e.isCurrent);
        if (current) setSelectedExpiry(current.date);
      }
    }).catch(() => {});
  }, []);

  const refresh = useCallback(async () => {
    try {
      // Pass expiry only if not current
      const isCurrent = expiries.find(e => e.isCurrent)?.date === selectedExpiry;
      const r = await fetchPriceAction(isCurrent ? null : selectedExpiry || null);
      if (r && !r.error) setData(r);
    } catch {}
  }, [selectedExpiry, expiries]);

  useEffect(() => {
    setLoading(true); refresh().then(() => setLoading(false));
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [refresh]);

  if (!data && !loading) return (
    <div style={{ textAlign: "center", padding: 60, color: "#555" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>💥</div>
      <div style={{ fontSize: 14, color: "#666" }}>Price Action Engine loading...</div>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card style={{ background: "#0D0D15", border: `1px solid ${ACCENT}33` }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ color: ACCENT, fontWeight: 900, fontSize: 14, marginBottom: 4 }}>PRICE ACTION — ATM±3 Strike LTP + OI Imbalance</div>
            <div style={{ color: "#555", fontSize: 11 }}>Tracks CE/PE premium movement, OI changes, imbalance, traps, and momentum. Refreshes every 5 sec.</div>
          </div>
          {expiries.length > 0 && (
            <select value={selectedExpiry} onChange={(e) => setSelectedExpiry(e.target.value)} style={{
              background: "#0D0D15", color: ORANGE, border: `1px solid ${ORANGE}44`,
              borderRadius: 8, padding: "5px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer", outline: "none",
            }}>
              {expiries.map(exp => (
                <option key={exp.date} value={exp.date}>{exp.isCurrent ? `${exp.date} (Live)` : exp.date}</option>
              ))}
            </select>
          )}
        </div>
      </Card>

      {["nifty", "banknifty"].map(key => {
        const d = data?.[key];
        if (!d) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
        const trade = d.trade || {};
        const tc = trade.action?.includes("CE") ? GREEN : trade.action?.includes("PE") ? RED : "#555";
        const biasColors = { BULLISH: GREEN, BEARISH: RED, NEUTRAL: YELLOW, VOLATILE: ORANGE, DECAY: "#555" };

        return (
          <div key={key} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {/* TRADE SIGNAL */}
            <Card style={{ background: tc + "08", border: `1px solid ${tc}44` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ color: "#fff", fontWeight: 900, fontSize: 18 }}>{label}</span>
                  <span style={{ background: tc + "22", color: tc, padding: "4px 16px", borderRadius: 6, fontSize: 14, fontWeight: 900 }}>{trade.action}</span>
                  <span style={{ background: (trade.confidence === "HIGH" ? GREEN : trade.confidence === "MEDIUM" ? YELLOW : "#555") + "22", color: trade.confidence === "HIGH" ? GREEN : trade.confidence === "MEDIUM" ? YELLOW : "#555", padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{trade.confidence}</span>
                </div>
                <span style={{ color: "#444", fontSize: 10 }}>Spot: {d.spot?.toLocaleString("en-IN")} ({d.spotChangePct > 0 ? "+" : ""}{d.spotChangePct}%) | {d.timestamp}</span>
              </div>

              {trade.action !== "WAIT" && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 8, marginBottom: 12 }}>
                  {[
                    { label: "STRIKE", value: trade.strike, color: "#fff" },
                    { label: "ENTRY", value: `₹${trade.entry?.toFixed(1)}`, color: GREEN },
                    { label: "TARGET 1", value: `₹${trade.t1}`, color: GREEN },
                    { label: "STOPLOSS", value: `₹${trade.sl}`, color: RED },
                    { label: "R:R", value: trade.rr, color: PURPLE },
                  ].map((item, i) => (
                    <div key={i} style={{ background: "#111118", borderRadius: 8, padding: "6px 10px", textAlign: "center" }}>
                      <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>{item.label}</div>
                      <div style={{ color: item.color, fontSize: 15, fontWeight: 900 }}>{item.value}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Reasons */}
              {trade.reasons?.map((r, i) => (
                <div key={i} style={{ color: "#999", fontSize: 11, marginBottom: 3, paddingLeft: 12 }}>{"\u2022"} {r}</div>
              ))}
            </Card>

            {/* BIAS CARDS */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
              <div style={{ background: (biasColors[d.premBias] || "#555") + "0A", borderRadius: 8, padding: "8px 10px", textAlign: "center", border: `1px solid ${biasColors[d.premBias] || "#555"}22` }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>PREMIUM BIAS</div>
                <div style={{ color: biasColors[d.premBias] || "#555", fontSize: 14, fontWeight: 900, marginTop: 3 }}>{d.premBias}</div>
                <div style={{ color: "#666", fontSize: 9, marginTop: 2 }}>CE/PE: {d.premRatio}</div>
              </div>
              <div style={{ background: (biasColors[d.momBias] || "#555") + "0A", borderRadius: 8, padding: "8px 10px", textAlign: "center", border: `1px solid ${biasColors[d.momBias] || "#555"}22` }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>MOMENTUM</div>
                <div style={{ color: biasColors[d.momBias] || "#555", fontSize: 14, fontWeight: 900, marginTop: 3 }}>{d.momBias}</div>
                <div style={{ color: "#666", fontSize: 9, marginTop: 2 }}>CE: {d.ceMomentum > 0 ? "+" : ""}{d.ceMomentum} | PE: {d.peMomentum > 0 ? "+" : ""}{d.peMomentum}</div>
              </div>
              <div style={{ background: (biasColors[d.oiBias] || "#555") + "0A", borderRadius: 8, padding: "8px 10px", textAlign: "center", border: `1px solid ${biasColors[d.oiBias] || "#555"}22` }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>OI BIAS</div>
                <div style={{ color: biasColors[d.oiBias] || "#555", fontSize: 14, fontWeight: 900, marginTop: 3 }}>{d.oiBias}</div>
                <div style={{ color: "#666", fontSize: 9, marginTop: 2 }}>PCR: {d.oiRatio}</div>
              </div>
              <div style={{ background: "#111118", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
                <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>STRADDLE</div>
                <div style={{ color: ACCENT, fontSize: 14, fontWeight: 900, marginTop: 3 }}>₹{d.straddle}</div>
                <div style={{ color: "#666", fontSize: 9, marginTop: 2 }}>ATM: {d.atm}</div>
              </div>
            </div>

            {/* ALERTS */}
            {d.alerts?.length > 0 && (
              <Card style={{ background: RED + "06", border: `1px solid ${RED}22` }}>
                <div style={{ color: RED, fontWeight: 700, fontSize: 12, marginBottom: 6 }}>LIVE ALERTS</div>
                {d.alerts.map((a, i) => (
                  <div key={i} style={{ color: "#ccc", fontSize: 11, marginBottom: 3 }}>
                    <span style={{ color: ORANGE, fontWeight: 700 }}>{a.strike}</span> {a.msg}
                  </div>
                ))}
              </Card>
            )}

            {/* STRIKE TABLE — ATM±3 */}
            <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
              <div style={{ color: "#666", fontWeight: 700, fontSize: 12, marginBottom: 8 }}>{label} ATM±3 STRIKES (5-min changes)</div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Strike</th>
                      <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE LTP</th>
                      <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE Chg</th>
                      <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE OI 5m</th>
                      <th style={{ padding: "5px 6px", color: RED, textAlign: "right" }}>CE OI Open</th>
                      <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE LTP</th>
                      <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE Chg</th>
                      <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE OI 5m</th>
                      <th style={{ padding: "5px 6px", color: GREEN, textAlign: "right" }}>PE OI Open</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.strikes?.map((s, i) => (
                      <tr key={i} style={{
                        borderBottom: `1px solid ${BORDER}33`,
                        background: s.isATM ? ACCENT + "11" : s.alerts?.length > 0 ? ORANGE + "08" : "transparent",
                      }}>
                        <td style={{ padding: "4px 6px", color: s.isATM ? ACCENT : "#ccc", fontWeight: s.isATM ? 900 : 400 }}>
                          {s.strike}{s.isATM ? " ATM" : ""}{s.straddle ? ` (₹${s.straddle})` : ""}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: "#ccc", fontWeight: 700 }}>₹{s.ceLTP?.toFixed(1)}</td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: s.ceLTPChange > 0 ? GREEN : s.ceLTPChange < 0 ? RED : "#555", fontWeight: 700 }}>
                          {s.ceLTPChange > 0 ? "+" : ""}{s.ceLTPChange?.toFixed(1)} <span style={{ fontSize: 8, color: Math.abs(s.ceLTPPct) > 5 ? ORANGE : "#555" }}>({s.ceLTPPct > 0 ? "+" : ""}{s.ceLTPPct}%)</span>
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: s.ceOIChange5m > 0 ? GREEN : s.ceOIChange5m < 0 ? RED : "#555" }}>
                          {s.ceOIChange5m > 0 ? "+" : ""}{fmtL(s.ceOIChange5m)}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: s.ceOIFromOpen > 0 ? GREEN : s.ceOIFromOpen < 0 ? RED : "#555" }}>
                          {s.ceOIFromOpen > 0 ? "+" : ""}{fmtL(s.ceOIFromOpen)}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: "#ccc", fontWeight: 700 }}>₹{s.peLTP?.toFixed(1)}</td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: s.peLTPChange > 0 ? GREEN : s.peLTPChange < 0 ? RED : "#555", fontWeight: 700 }}>
                          {s.peLTPChange > 0 ? "+" : ""}{s.peLTPChange?.toFixed(1)} <span style={{ fontSize: 8, color: Math.abs(s.peLTPPct) > 5 ? ORANGE : "#555" }}>({s.peLTPPct > 0 ? "+" : ""}{s.peLTPPct}%)</span>
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: s.peOIChange5m > 0 ? GREEN : s.peOIChange5m < 0 ? RED : "#555" }}>
                          {s.peOIChange5m > 0 ? "+" : ""}{fmtL(s.peOIChange5m)}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: s.peOIFromOpen > 0 ? GREEN : s.peOIFromOpen < 0 ? RED : "#555" }}>
                          {s.peOIFromOpen > 0 ? "+" : ""}{fmtL(s.peOIFromOpen)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            {/* Momentum Detail */}
            <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
              <div style={{ color: "#888", fontSize: 11, lineHeight: 1.6 }}>
                <span style={{ color: YELLOW, fontWeight: 700 }}>Momentum: </span>{d.momDetail}
              </div>
            </Card>
          </div>
        );
      })}
    </div>
  );
}

// ── TAB: TRAP FINGERPRINT FINDER ─────────────────────────────────────

function TrapFinderTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("ALL"); // ALL, CE, PE
  const [expiryFilter, setExpiryFilter] = useState("ALL"); // ALL, CURRENT, NEXT
  const [scoreFilter, setScoreFilter] = useState(0);
  const [lastScan, setLastScan] = useState(null);
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [selectedStrike, setSelectedStrike] = useState(null);
  const [todaySignals, setTodaySignals] = useState([]);
  const [verdict, setVerdict] = useState(null);

  const fmtL = (n) => n ? `${(Math.abs(n) / 100000).toFixed(1)}L` : "0";
  const scoreColor = (s) => s >= 6 ? RED : s >= 4 ? YELLOW : GREEN;
  const alertBg = { FINGERPRINT: RED, WATCH: YELLOW, NORMAL: "#333" };

  // Load today's signals + history + verdict
  useEffect(() => {
    fetchTrapToday().then(s => { if (Array.isArray(s)) setTodaySignals(s); }).catch(() => {});
    fetchTrapHistory().then(h => { if (Array.isArray(h)) setHistory(h); }).catch(() => {});
    fetchTrapVerdict().then(v => { if (v && !v.error) setVerdict(v); }).catch(() => {});
  }, [data]);

  const runScan = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchTrapScan();
      if (result && !result.error) {
        setData(result);
        setLastScan(new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" }));
      }
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { runScan(); const iv = setInterval(runScan, 300000); return () => clearInterval(iv); }, [runScan]);

  if (!data && !loading) return (
    <div style={{ textAlign: "center", padding: 60, color: "#555" }}>
      <div style={{ fontSize: 40, marginBottom: 12 }}>🧨</div>
      <div style={{ fontSize: 14, color: "#666" }}>Trap Fingerprint Engine</div>
      <div style={{ fontSize: 11, color: "#444", marginTop: 8 }}>Scanning for institutional hidden OTM positioning...</div>
      <button onClick={runScan} style={{ marginTop: 16, background: RED + "22", color: RED, border: `1px solid ${RED}44`, borderRadius: 8, padding: "8px 20px", cursor: "pointer", fontSize: 12, fontWeight: 700 }}>Run Scan Now</button>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Header + Scan button */}
      <Card style={{ background: "#0D0D15", border: `1px solid ${RED}33` }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ color: RED, fontWeight: 900, fontSize: 14, marginBottom: 4 }}>TRAP FINGERPRINT ENGINE</div>
            <div style={{ color: "#555", fontSize: 11 }}>Detects institutional hidden OTM positioning. OI + Volume divergence without spot movement.</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {lastScan && <span style={{ color: "#444", fontSize: 10 }}>Last: {lastScan}</span>}
            <button onClick={runScan} disabled={loading} style={{ background: loading ? "#333" : RED + "22", color: RED, border: `1px solid ${RED}44`, borderRadius: 8, padding: "6px 14px", cursor: loading ? "wait" : "pointer", fontSize: 11, fontWeight: 700 }}>
              {loading ? "Scanning..." : "Scan Now"}
            </button>
          </div>
        </div>
      </Card>

      {/* Filters */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {["ALL", "CE", "PE"].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{ background: filter === f ? ACCENT + "33" : "#111118", color: filter === f ? ACCENT : "#666", border: `1px solid ${filter === f ? ACCENT : BORDER}`, borderRadius: 6, padding: "4px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>{f}</button>
        ))}
        <span style={{ color: "#333", margin: "0 4px" }}>|</span>
        {["ALL", "CURRENT", "NEXT"].map(f => (
          <button key={f} onClick={() => setExpiryFilter(f)} style={{ background: expiryFilter === f ? ORANGE + "33" : "#111118", color: expiryFilter === f ? ORANGE : "#666", border: `1px solid ${expiryFilter === f ? ORANGE : BORDER}`, borderRadius: 6, padding: "4px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>{f} Expiry</button>
        ))}
        <span style={{ color: "#333", margin: "0 4px" }}>|</span>
        {[0, 4, 6].map(s => (
          <button key={s} onClick={() => setScoreFilter(s)} style={{ background: scoreFilter === s ? scoreColor(s) + "33" : "#111118", color: scoreFilter === s ? scoreColor(s) : "#666", border: `1px solid ${scoreFilter === s ? scoreColor(s) : BORDER}`, borderRadius: 6, padding: "4px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
            Score {">="}{s || "All"}
          </button>
        ))}
      </div>

      {loading && <div style={{ textAlign: "center", padding: 20, color: ORANGE }}>Scanning options chain...</div>}

      {/* ── TRAP VERDICT — Cross-Engine Decision ── */}
      {verdict && ["nifty", "banknifty"].map(key => {
        const v = verdict[key];
        if (!v) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";
        const ac = v.finalAction?.includes("CE") ? GREEN : v.finalAction?.includes("PE") ? RED : "#555";
        const cc = v.confidence === "HIGH" ? GREEN : v.confidence === "MEDIUM" ? YELLOW : "#555";
        const t = v.trade || {};

        return (
          <Card key={key} style={{ background: ac + "06", border: `1px solid ${ac}44` }}>
            {/* Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 20 }}>🧠</span>
                <span style={{ color: "#fff", fontWeight: 900, fontSize: 16 }}>{label} VERDICT</span>
                <span style={{ background: ac + "22", color: ac, padding: "4px 14px", borderRadius: 6, fontSize: 14, fontWeight: 900 }}>{v.finalAction}</span>
                <span style={{ background: cc + "22", color: cc, padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{v.confidence}</span>
              </div>
              <span style={{ color: "#444", fontSize: 10 }}>
                {v.openType && <span style={{ color: v.openType === "GAP UP" ? GREEN : v.openType === "GAP DOWN" ? RED : YELLOW, marginRight: 8 }}>{v.openType}</span>}
                {v.timestamp}
              </span>
            </div>

            {/* Trade Card */}
            {v.finalAction !== "NO TRADE" && t.entry > 0 && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 8, marginBottom: 12 }}>
                {[
                  { label: "STRIKE", value: t.strike, color: "#fff" },
                  { label: "ENTRY", value: `₹${t.entry}`, color: GREEN },
                  { label: "TARGET", value: `₹${t.t1} / ₹${t.t2}`, color: GREEN },
                  { label: "STOPLOSS", value: `₹${t.sl}`, color: RED },
                  { label: "R:R", value: t.rr, color: PURPLE },
                ].map((item, i) => (
                  <div key={i} style={{ background: "#111118", borderRadius: 8, padding: "6px 10px", textAlign: "center" }}>
                    <div style={{ color: "#555", fontSize: 9, fontWeight: 700 }}>{item.label}</div>
                    <div style={{ color: item.color, fontSize: 14, fontWeight: 900 }}>{item.value}</div>
                  </div>
                ))}
              </div>
            )}

            {/* Engine Votes — Visual Bar */}
            <div style={{ background: "#0A0A12", borderRadius: 8, padding: "8px 12px", marginBottom: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 10 }}>
                <span style={{ color: GREEN, fontWeight: 700 }}>CE: {v.votes?.CE || 0} votes</span>
                <span style={{ color: "#555" }}>Engine Consensus ({v.totalVotes} total)</span>
                <span style={{ color: RED, fontWeight: 700 }}>PE: {v.votes?.PE || 0} votes</span>
              </div>
              <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", background: "#1a1a25" }}>
                <div style={{ width: `${v.totalVotes > 0 ? (v.votes?.CE || 0) / v.totalVotes * 100 : 50}%`, background: GREEN, transition: "width 0.3s" }} />
                <div style={{ width: `${v.totalVotes > 0 ? (v.votes?.PE || 0) / v.totalVotes * 100 : 50}%`, background: RED, transition: "width 0.3s" }} />
              </div>
            </div>

            {/* Engine Scores */}
            {v.engineScores && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                {Object.entries(v.engineScores).map(([eng, val]) => (
                  <span key={eng} style={{ background: "#111118", color: "#888", padding: "3px 8px", borderRadius: 4, fontSize: 9 }}>
                    <span style={{ color: "#555" }}>{eng}: </span>
                    <span style={{ color: String(val).includes("CE") || String(val).includes("BULLISH") ? GREEN : String(val).includes("PE") || String(val).includes("BEARISH") ? RED : "#ccc", fontWeight: 700 }}>{String(val)}</span>
                  </span>
                ))}
              </div>
            )}

            {/* Reasons */}
            {v.reasons?.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <div style={{ color: YELLOW, fontSize: 10, fontWeight: 700, marginBottom: 6 }}>WHY — ALL ENGINES AGREE</div>
                {v.reasons.map((r, i) => (
                  <div key={i} style={{ color: "#ccc", fontSize: 11, marginBottom: 3, paddingLeft: 10 }}>{i + 1}. {r}</div>
                ))}
              </div>
            )}

            {/* Predictions: Current + Next Expiry */}
            <div style={{ display: "flex", gap: 10, marginBottom: 10 }}>
              {v.currentExpiryPrediction?.length > 0 && (
                <div style={{ flex: 1, background: ACCENT + "08", borderRadius: 8, padding: "8px 12px", border: `1px solid ${ACCENT}22` }}>
                  <div style={{ color: ACCENT, fontSize: 10, fontWeight: 700, marginBottom: 4 }}>CURRENT EXPIRY</div>
                  {v.currentExpiryPrediction.map((p, i) => (
                    <div key={i} style={{ color: "#999", fontSize: 10, marginBottom: 2 }}>{p}</div>
                  ))}
                </div>
              )}
              {v.nextExpiryPrediction?.length > 0 && (
                <div style={{ flex: 1, background: ORANGE + "08", borderRadius: 8, padding: "8px 12px", border: `1px solid ${ORANGE}22` }}>
                  <div style={{ color: ORANGE, fontSize: 10, fontWeight: 700, marginBottom: 4 }}>NEXT WEEK</div>
                  {v.nextExpiryPrediction.map((p, i) => (
                    <div key={i} style={{ color: "#999", fontSize: 10, marginBottom: 2 }}>{p}</div>
                  ))}
                </div>
              )}
            </div>

            {/* Risks */}
            {v.risks?.length > 0 && (
              <div style={{ background: RED + "06", borderRadius: 8, padding: "6px 12px" }}>
                {v.risks.map((r, i) => (
                  <div key={i} style={{ color: "#aaa", fontSize: 10, marginBottom: 2 }}>{"\u26A0"} {r}</div>
                ))}
              </div>
            )}
          </Card>
        );
      })}

      {/* ── TODAY'S SIGNALS — Always Visible ── */}
      {todaySignals.length > 0 && (
        <Card style={{ background: "#0D0D15", border: `1px solid ${YELLOW}33` }}>
          <div style={{ color: YELLOW, fontWeight: 900, fontSize: 13, marginBottom: 10 }}>
            TODAY'S SIGNALS — {todaySignals.length} detections (stay visible all day)
          </div>
          <div style={{ overflowX: "auto", maxHeight: 350, overflowY: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${BORDER}`, position: "sticky", top: 0, background: "#0D0D15" }}>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Time</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Index</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Strike</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Type</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>OI Chg</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>OI %</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>Vol</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>Spot</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Score</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Flag</th>
                </tr>
              </thead>
              <tbody>
                {todaySignals.map((s, i) => (
                  <tr key={i} onClick={() => setSelectedStrike({
                    strike: Math.round(s.strike), optionType: s.option_type, expiry: s.expiry,
                    expiryLabel: s.expiry, oi: s.oi, oiChange: s.oi_change, oiChangePct: s.oi_change_pct,
                    volume: s.volume, volumeRatio: s.volume_ratio, iv: s.iv, ivChange: s.iv_change,
                    ltp: s.ltp, trapScore: s.trap_score, alertLevel: s.alert_level, reasons: [],
                  })} style={{
                    borderBottom: `1px solid ${BORDER}33`, cursor: "pointer",
                    background: s.alert_level === "FINGERPRINT" ? RED + "0A" : s.is_cluster ? PURPLE + "08" : "transparent",
                  }}>
                    <td style={{ padding: "4px 6px" }}>
                      <span style={{ color: ACCENT, fontWeight: 700, fontSize: 11 }}>{s.scanTime}</span>
                    </td>
                    <td style={{ padding: "4px 6px", color: "#ccc", fontWeight: 700 }}>{s.symbol}</td>
                    <td style={{ padding: "4px 6px", color: "#fff", fontWeight: 900 }}>{Math.round(s.strike)}</td>
                    <td style={{ padding: "4px 6px", textAlign: "center" }}>
                      <span style={{ color: s.option_type === "CE" ? RED : GREEN, fontWeight: 700 }}>{s.option_type}</span>
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "right", color: s.oi_change > 0 ? GREEN : RED, fontWeight: 700 }}>
                      {s.oi_change > 0 ? "+" : ""}{fmtL(s.oi_change)}
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "right", color: Math.abs(s.oi_change_pct) > 15 ? ORANGE : "#888" }}>
                      {s.oi_change_pct > 0 ? "+" : ""}{s.oi_change_pct?.toFixed(1)}%
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "right", color: "#888" }}>{s.volume?.toLocaleString("en-IN")}</td>
                    <td style={{ padding: "4px 6px", textAlign: "right", color: "#888" }}>{Math.round(s.spot_price)?.toLocaleString("en-IN")}</td>
                    <td style={{ padding: "4px 6px", textAlign: "center" }}>
                      <span style={{ background: scoreColor(s.trap_score) + "22", color: scoreColor(s.trap_score), padding: "2px 6px", borderRadius: 4, fontSize: 10, fontWeight: 900 }}>{s.trap_score}</span>
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "center" }}>
                      <span style={{ color: s.alert_level === "FINGERPRINT" ? RED : YELLOW, fontSize: 9, fontWeight: 700 }}>
                        {s.alert_level}{s.is_cluster ? " 🔗" : ""}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* ── DETAIL POPUP ── */}
      {selectedStrike && (() => {
        const s = selectedStrike;
        const sc = scoreColor(s.trapScore);
        const ac = alertBg[s.alertLevel] || "#555";
        const direction = s.optionType === "CE" ? "BULLISH" : "BEARISH";
        const buySignal = s.optionType === "CE" ? "BUY CE" : "BUY PE";
        return (
          <div onClick={() => setSelectedStrike(null)} style={{ position: "fixed", top: 0, left: 0, right: 0, bottom: 0, background: "rgba(0,0,0,0.7)", zIndex: 999, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div onClick={(e) => e.stopPropagation()} style={{ background: "#111118", border: `1px solid ${ac}55`, borderRadius: 16, padding: "24px 28px", maxWidth: 520, width: "90%", maxHeight: "85vh", overflowY: "auto" }}>
              {/* Header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ color: "#fff", fontWeight: 900, fontSize: 20 }}>{s.strike}</span>
                  <span style={{ color: s.optionType === "CE" ? RED : GREEN, fontWeight: 900, fontSize: 16 }}>{s.optionType}</span>
                  <span style={{ background: ac + "22", color: ac, padding: "3px 12px", borderRadius: 6, fontSize: 12, fontWeight: 900 }}>{s.alertLevel}</span>
                </div>
                <button onClick={() => setSelectedStrike(null)} style={{ background: "transparent", color: "#555", border: "none", fontSize: 20, cursor: "pointer" }}>{"\u2715"}</button>
              </div>

              {/* Score Bar */}
              <div style={{ background: "#0A0A12", borderRadius: 10, padding: "12px 16px", marginBottom: 14, border: `1px solid ${sc}33` }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ color: "#888", fontSize: 11, fontWeight: 700 }}>TRAP SCORE</span>
                  <span style={{ color: sc, fontSize: 28, fontWeight: 900 }}>{s.trapScore}<span style={{ fontSize: 14, color: "#555" }}>/10</span></span>
                </div>
                <div style={{ background: "#1a1a25", borderRadius: 4, height: 8, overflow: "hidden" }}>
                  <div style={{ background: sc, height: "100%", width: `${s.trapScore * 10}%`, borderRadius: 4, transition: "width 0.3s" }} />
                </div>
              </div>

              {/* Direction Signal */}
              <div style={{ background: (s.optionType === "CE" ? GREEN : RED) + "11", borderRadius: 10, padding: "10px 16px", marginBottom: 14, border: `1px solid ${s.optionType === "CE" ? GREEN : RED}33`, textAlign: "center" }}>
                <div style={{ color: "#888", fontSize: 10, fontWeight: 700, marginBottom: 4 }}>INSTITUTIONAL SIGNAL</div>
                <div style={{ color: s.optionType === "CE" ? GREEN : RED, fontSize: 18, fontWeight: 900 }}>{direction} — {buySignal}</div>
                <div style={{ color: "#666", fontSize: 10, marginTop: 4 }}>OTM {s.optionType} buildup = institutions expect {direction.toLowerCase()} move</div>
              </div>

              {/* Data Grid */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 14 }}>
                {[
                  { label: "Open Interest", value: fmtL(s.oi), color: "#ccc" },
                  { label: "OI Change", value: `${s.oiChange > 0 ? "+" : ""}${fmtL(s.oiChange)}`, color: s.oiChange > 0 ? GREEN : RED },
                  { label: "OI Change %", value: `${s.oiChangePct > 0 ? "+" : ""}${s.oiChangePct}%`, color: Math.abs(s.oiChangePct) > 15 ? ORANGE : "#ccc" },
                  { label: "Volume", value: s.volume?.toLocaleString("en-IN"), color: "#ccc" },
                  { label: "Volume Ratio", value: `${s.volumeRatio}x`, color: s.volumeRatio > 2 ? ORANGE : "#ccc" },
                  { label: "IV", value: `${s.iv}%`, color: "#ccc" },
                  { label: "IV Change", value: `${s.ivChange}%`, color: s.ivChange < 5 ? GREEN : "#ccc" },
                  { label: "LTP", value: `${"\u20B9"}${s.ltp?.toFixed(1)}`, color: "#fff" },
                  { label: "Expiry", value: s.expiryLabel, color: s.expiryLabel === "NEXT" ? ORANGE : "#ccc" },
                ].map((item, i) => (
                  <div key={i} style={{ background: "#0A0A12", borderRadius: 8, padding: "8px 10px", textAlign: "center" }}>
                    <div style={{ color: "#555", fontSize: 9, fontWeight: 700, marginBottom: 3 }}>{item.label}</div>
                    <div style={{ color: item.color, fontSize: 13, fontWeight: 700 }}>{item.value}</div>
                  </div>
                ))}
              </div>

              {/* Score Breakdown */}
              <div style={{ background: "#0A0A12", borderRadius: 10, padding: "12px 16px", marginBottom: 14 }}>
                <div style={{ color: YELLOW, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>SCORE BREAKDOWN — Why this was flagged</div>
                {[
                  { check: Math.abs(s.oiChangePct) > 15, pts: 3, text: `OI Change ${s.oiChangePct > 0 ? "+" : ""}${s.oiChangePct}% (threshold: 15%)`, partial: Math.abs(s.oiChangePct) > 8 },
                  { check: s.volumeRatio > 2, pts: 3, text: `Volume ${s.volumeRatio}x average (threshold: 2x)`, partial: s.volumeRatio > 1.5 },
                  { check: s.ivChange < 5 && Math.abs(s.oiChangePct) > 5, pts: 2, text: `IV flat at ${s.ivChange}% change — stealth buying (threshold: <5%)` },
                  { check: false, pts: 2, text: `Spot barely moved — hidden positioning (threshold: <0.3%)` }, // We don't have spot% per strike
                ].map((item, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
                    <span style={{ color: item.check ? GREEN : item.partial ? YELLOW : RED, fontSize: 12, width: 16 }}>
                      {item.check ? "\u2713" : item.partial ? "~" : "\u2717"}
                    </span>
                    <span style={{ color: item.check ? GREEN : item.partial ? YELLOW : "#555", fontSize: 11, fontWeight: item.check ? 700 : 400 }}>
                      {item.text}
                    </span>
                    <span style={{ color: item.check ? GREEN : item.partial ? YELLOW : "#333", fontSize: 10, marginLeft: "auto", fontWeight: 700 }}>
                      {item.check ? `+${item.pts}` : item.partial ? "+1" : "+0"}
                    </span>
                  </div>
                ))}
              </div>

              {/* Reasons */}
              {s.reasons?.length > 0 && (
                <div style={{ background: "#0A0A12", borderRadius: 10, padding: "12px 16px", marginBottom: 14 }}>
                  <div style={{ color: ACCENT, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>DETECTION REASONS</div>
                  {s.reasons.map((r, i) => (
                    <div key={i} style={{ color: "#ccc", fontSize: 11, marginBottom: 4, paddingLeft: 12 }}>{"\u2022"} {r}</div>
                  ))}
                </div>
              )}

              {/* What it means */}
              <div style={{ background: (s.optionType === "CE" ? GREEN : RED) + "08", borderRadius: 10, padding: "12px 16px", border: `1px solid ${s.optionType === "CE" ? GREEN : RED}22` }}>
                <div style={{ color: s.optionType === "CE" ? GREEN : RED, fontSize: 11, fontWeight: 700, marginBottom: 6 }}>WHAT THIS MEANS FOR YOU (BUYER)</div>
                <div style={{ color: "#ccc", fontSize: 11, lineHeight: 1.6 }}>
                  {s.alertLevel === "FINGERPRINT"
                    ? `HIGH CONVICTION: Institutions are building significant ${s.optionType} positions at ${s.strike} with TrapScore ${s.trapScore}/10. This is a strong ${direction.toLowerCase()} signal. Consider ${buySignal} near ATM with SL below ${s.optionType === "CE" ? "support" : "resistance"}.`
                    : s.alertLevel === "WATCH"
                    ? `WATCH ZONE: ${s.optionType} activity at ${s.strike} is suspicious (Score ${s.trapScore}/10). Institutions may be positioning for a ${direction.toLowerCase()} move. Monitor — if score increases in next scan, it becomes actionable.`
                    : `LOW SIGNAL: Activity at ${s.strike} is within normal range. No clear institutional footprint detected.`
                  }
                  {s.expiryLabel === "NEXT" && " NEXT EXPIRY buildup = higher conviction (institutions have time = they believe in the move)."}
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {["nifty", "banknifty"].map(key => {
        const d = data?.[key];
        if (!d || d.error) return null;
        const label = key === "nifty" ? "NIFTY" : "BANKNIFTY";

        // Apply filters
        let strikes = d.strikes || [];
        if (filter !== "ALL") strikes = strikes.filter(s => s.optionType === filter);
        if (expiryFilter !== "ALL") strikes = strikes.filter(s => s.expiryLabel === expiryFilter);
        if (scoreFilter > 0) strikes = strikes.filter(s => s.trapScore >= scoreFilter);

        const renderExpiryBox = (exp, borderColor) => {
          if (!exp) return null;
          const bc = exp.sellerBias === "BEARISH" ? RED : exp.sellerBias === "BULLISH" ? GREEN : YELLOW;
          return (
            <div style={{ background: "#111118", borderRadius: 10, padding: "10px 12px", flex: 1, border: `1px solid ${borderColor}22` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <span style={{ color: borderColor, fontWeight: 900, fontSize: 12 }}>EXPIRY: {exp.label}</span>
                <span style={{ background: bc + "22", color: bc, padding: "2px 8px", borderRadius: 4, fontSize: 9, fontWeight: 700 }}>{exp.sellerBias}</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginBottom: 6 }}>
                <div style={{ textAlign: "center" }}>
                  <div style={{ color: RED, fontSize: 9 }}>Fingerprints</div>
                  <div style={{ color: RED, fontWeight: 900, fontSize: 14 }}>{exp.fingerprints}</div>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ color: YELLOW, fontSize: 9 }}>Watch</div>
                  <div style={{ color: YELLOW, fontWeight: 900, fontSize: 14 }}>{exp.watchZones}</div>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ color: "#888", fontSize: 9 }}>Total</div>
                  <div style={{ color: "#ccc", fontWeight: 900, fontSize: 14 }}>{exp.total}</div>
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, borderTop: `1px solid ${BORDER}`, paddingTop: 6 }}>
                <span style={{ color: ORANGE }}>CE Write: {fmtL(exp.ceWriting)}</span>
                <span style={{ color: ORANGE }}>PE Write: {fmtL(exp.peWriting)}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, marginTop: 2 }}>
                <span style={{ color: GREEN }}>CE Buy: {fmtL(exp.ceBuying)}</span>
                <span style={{ color: GREEN }}>PE Buy: {fmtL(exp.peBuying)}</span>
              </div>
            </div>
          );
        };

        return (
          <div key={key} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {/* Header */}
            <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <span style={{ color: ACCENT, fontWeight: 900, fontSize: 14 }}>{label}</span>
                <span style={{ color: "#444", fontSize: 10 }}>Spot: {d.spot?.toLocaleString("en-IN")} | Move: {d.spotChangePct}% | {d.timestamp}</span>
              </div>

              {/* Current vs Next Expiry side by side */}
              <div style={{ display: "flex", gap: 10 }}>
                {renderExpiryBox(d.current, ACCENT)}
                {renderExpiryBox(d.next, ORANGE)}
              </div>
            </Card>

            {/* INSIGHTS */}
            {d.insights?.length > 0 && (
              <Card style={{ background: YELLOW + "06", border: `1px solid ${YELLOW}33` }}>
                <div style={{ color: YELLOW, fontWeight: 900, fontSize: 12, marginBottom: 8 }}>INSIGHTS — Smart Money Activity</div>
                {d.insights.map((ins, i) => {
                  const insColor = ins.signal?.includes("CE") ? GREEN : ins.signal?.includes("PE") ? RED : YELLOW;
                  return (
                    <div key={i} style={{ background: "#111118", borderRadius: 8, padding: "8px 12px", marginBottom: 6, border: `1px solid ${BORDER}44` }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                        <span style={{ fontSize: 12 }}>{ins.icon} <span style={{ color: "#fff", fontWeight: 700 }}>{ins.title}</span></span>
                        <span style={{ background: insColor + "22", color: insColor, padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 900 }}>{ins.signal}</span>
                      </div>
                      <div style={{ color: "#999", fontSize: 11, lineHeight: 1.5 }}>{ins.detail}</div>
                    </div>
                  );
                })}
              </Card>
            )}

            {/* Cluster Alerts */}
            {d.clusters?.length > 0 && d.clusters.map((c, i) => {
              const bsColor = c.buySignal?.includes("CE") ? GREEN : c.buySignal?.includes("PE") ? RED : "#888";
              return (
                <Card key={i} style={{ background: PURPLE + "08", border: `1px solid ${PURPLE}44` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 16 }}>🔗</span>
                      <span style={{ color: PURPLE, fontWeight: 900, fontSize: 13 }}>CLUSTER: {c.actor || "INSTITUTIONAL"} {c.side}</span>
                      <span style={{ background: bsColor + "22", color: bsColor, padding: "3px 10px", borderRadius: 4, fontSize: 11, fontWeight: 900 }}>{c.buySignal || c.direction}</span>
                    </div>
                    <span style={{ background: c.confidence === "HIGH" ? RED + "22" : YELLOW + "22", color: c.confidence === "HIGH" ? RED : YELLOW, padding: "2px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700 }}>{c.confidence}</span>
                  </div>
                  <div style={{ color: "#ccc", fontSize: 12, marginBottom: 6 }}>{c.signal}</div>
                  <div style={{ display: "flex", gap: 10, fontSize: 11, flexWrap: "wrap" }}>
                    <span style={{ color: "#888" }}>Range: <span style={{ color: "#fff", fontWeight: 700 }}>{c.strikeRange}</span></span>
                    <span style={{ color: "#888" }}>Strikes: <span style={{ color: "#fff", fontWeight: 700 }}>{c.count}</span></span>
                    <span style={{ color: "#888" }}>Avg Score: <span style={{ color: scoreColor(c.avgScore), fontWeight: 700 }}>{c.avgScore}</span></span>
                    <span style={{ color: "#888" }}>OI Change: <span style={{ color: GREEN, fontWeight: 700 }}>+{fmtL(c.totalOIChange)}</span></span>
                  </div>
                </Card>
              );
            })}

            {/* Strike Table */}
            <Card style={{ background: "#0D0D15", border: `1px solid ${BORDER}` }}>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Strike</th>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Type</th>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Who</th>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>OI Chg</th>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>OI %</th>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>Prem</th>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>Vol</th>
                      <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Score</th>
                      <th style={{ padding: "5px 6px", color: ACCENT, textAlign: "center", fontWeight: 900 }}>YOU BUY</th>
                    </tr>
                  </thead>
                  <tbody>
                    {strikes.map((s, i) => (
                      <tr key={i} onClick={() => setSelectedStrike(s)} style={{
                        borderBottom: `1px solid ${BORDER}33`,
                        background: s.alertLevel === "FINGERPRINT" ? RED + "0A" : s.alertLevel === "WATCH" ? YELLOW + "06" : "transparent",
                        cursor: "pointer",
                      }}>
                        <td style={{ padding: "4px 6px", color: "#ccc", fontWeight: 700 }}>{s.strike}</td>
                        <td style={{ padding: "4px 6px", textAlign: "center" }}>
                          <span style={{ color: s.optionType === "CE" ? RED : GREEN, fontWeight: 700 }}>{s.optionType}</span>
                          <span style={{ color: s.expiryLabel === "NEXT" ? ORANGE : "#555", fontSize: 8, display: "block" }}>{s.expiryLabel}</span>
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "center" }}>
                          <span style={{ color: s.oiActor === "SELLERS" ? ORANGE : GREEN, fontWeight: 700, fontSize: 9 }}>{s.oiActor || "-"}</span>
                          <span style={{ color: "#555", fontSize: 8, display: "block" }}>{s.oiAction || ""}</span>
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: s.oiChange > 0 ? GREEN : s.oiChange < 0 ? RED : "#555", fontWeight: 700 }}>
                          {s.oiChange > 0 ? "+" : ""}{fmtL(s.oiChange)}
                          <span style={{ color: Math.abs(s.oiChangePct) > 15 ? ORANGE : "#555", fontSize: 8, display: "block" }}>{s.oiChangePct > 0 ? "+" : ""}{s.oiChangePct}%</span>
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: "#888" }}>
                          {s.oiChangePct > 0 ? "+" : ""}{s.oiChangePct}%
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: (s.premChange || 0) > 0 ? GREEN : (s.premChange || 0) < 0 ? RED : "#555" }}>
                          {(s.premChange || 0) > 0 ? "+" : ""}{(s.premChange || 0).toFixed(1)}
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "right", color: s.volumeRatio > 2 ? ORANGE : "#888" }}>
                          {s.volumeRatio}x
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "center" }}>
                          <span style={{ background: scoreColor(s.trapScore) + "22", color: scoreColor(s.trapScore), padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 900 }}>{s.trapScore}</span>
                        </td>
                        <td style={{ padding: "4px 6px", textAlign: "center" }}>
                          <span style={{ background: (s.buySignal?.includes("CE") ? GREEN : s.buySignal?.includes("PE") ? RED : "#555") + "22", color: s.buySignal?.includes("CE") ? GREEN : s.buySignal?.includes("PE") ? RED : "#555", padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 900 }}>{s.buySignal || "-"}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {strikes.length === 0 && (
                <div style={{ textAlign: "center", padding: 20, color: "#555", fontSize: 12 }}>No trap fingerprints detected with current filters.</div>
              )}
            </Card>

            {/* Reasons for top fingerprints */}
            {strikes.filter(s => s.trapScore >= 4).slice(0, 5).map((s, i) => (
              <div key={i} style={{ padding: "6px 12px", background: "#0A0A12", borderRadius: 6, border: `1px solid ${BORDER}33` }}>
                <span style={{ color: s.optionType === "CE" ? RED : GREEN, fontWeight: 700, fontSize: 11 }}>{s.strike} {s.optionType}</span>
                <span style={{ color: "#555", fontSize: 10, marginLeft: 8 }}>{s.expiryLabel}</span>
                <span style={{ color: scoreColor(s.trapScore), fontSize: 10, marginLeft: 8, fontWeight: 700 }}>Score: {s.trapScore}</span>
                {s.reasons?.map((r, j) => (
                  <span key={j} style={{ color: "#888", fontSize: 10, marginLeft: 8 }}>{r}</span>
                ))}
              </div>
            ))}
          </div>
        );
      })}

      {/* ── SIGNAL HISTORY LOG ── */}
      <Card style={{ background: "#0D0D15", border: `1px solid ${PURPLE}33` }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: showHistory ? 12 : 0 }}>
          <div style={{ color: PURPLE, fontWeight: 900, fontSize: 13 }}>SIGNAL LOG — Past 7 Days ({history.length} signals)</div>
          <button onClick={() => setShowHistory(!showHistory)} style={{ background: PURPLE + "22", color: PURPLE, border: `1px solid ${PURPLE}44`, borderRadius: 6, padding: "4px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
            {showHistory ? "Hide" : "Show"} History
          </button>
        </div>
        {showHistory && history.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Date/Time</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Index</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Strike</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Type</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "left" }}>Expiry</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>OI Chg</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>OI %</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>Vol Ratio</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "right" }}>Spot</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Score</th>
                  <th style={{ padding: "5px 6px", color: "#555", textAlign: "center" }}>Flag</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h, i) => {
                  const ts = h.timestamp ? new Date(h.timestamp) : null;
                  const dateStr = ts ? ts.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) : "-";
                  const timeStr = ts ? ts.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true }) : "-";
                  return (
                    <tr key={i} style={{
                      borderBottom: `1px solid ${BORDER}33`,
                      background: h.alert_level === "FINGERPRINT" ? RED + "08" : h.is_cluster ? PURPLE + "08" : "transparent",
                    }}>
                      <td style={{ padding: "4px 6px", color: "#888" }}>
                        <div style={{ fontWeight: 700, color: "#ccc" }}>{dateStr}</div>
                        <div style={{ fontSize: 9 }}>{timeStr}</div>
                      </td>
                      <td style={{ padding: "4px 6px", color: ACCENT, fontWeight: 700 }}>{h.symbol}</td>
                      <td style={{ padding: "4px 6px", color: "#ccc", fontWeight: 700 }}>{Math.round(h.strike)}</td>
                      <td style={{ padding: "4px 6px", textAlign: "center" }}>
                        <span style={{ color: h.option_type === "CE" ? RED : GREEN, fontWeight: 700 }}>{h.option_type}</span>
                      </td>
                      <td style={{ padding: "4px 6px", color: "#888", fontSize: 9 }}>{h.expiry}</td>
                      <td style={{ padding: "4px 6px", textAlign: "right", color: h.oi_change > 0 ? GREEN : RED, fontWeight: 700 }}>
                        {h.oi_change > 0 ? "+" : ""}{fmtL(h.oi_change)}
                      </td>
                      <td style={{ padding: "4px 6px", textAlign: "right", color: Math.abs(h.oi_change_pct) > 15 ? ORANGE : "#888" }}>
                        {h.oi_change_pct > 0 ? "+" : ""}{h.oi_change_pct?.toFixed(1)}%
                      </td>
                      <td style={{ padding: "4px 6px", textAlign: "right", color: h.volume_ratio > 2 ? ORANGE : "#888" }}>
                        {h.volume_ratio?.toFixed(1)}x
                      </td>
                      <td style={{ padding: "4px 6px", textAlign: "right", color: "#888" }}>{Math.round(h.spot_price)?.toLocaleString("en-IN")}</td>
                      <td style={{ padding: "4px 6px", textAlign: "center" }}>
                        <span style={{ background: scoreColor(h.trap_score) + "22", color: scoreColor(h.trap_score), padding: "2px 6px", borderRadius: 4, fontSize: 10, fontWeight: 900 }}>{h.trap_score}</span>
                      </td>
                      <td style={{ padding: "4px 6px", textAlign: "center" }}>
                        <span style={{ color: h.alert_level === "FINGERPRINT" ? RED : h.alert_level === "WATCH" ? YELLOW : "#555", fontSize: 9, fontWeight: 700 }}>
                          {h.alert_level}{h.is_cluster ? " 🔗" : ""}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {showHistory && history.length === 0 && (
          <div style={{ textAlign: "center", padding: 20, color: "#555", fontSize: 11 }}>No signals stored yet. Signals will appear after the first scan during market hours.</div>
        )}
      </Card>
    </div>
  );
}

function PromptTab() {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(MASTER_PROMPT);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <span style={{ color: ACCENT, fontWeight: 900, fontSize: 15 }}>\uD83E\uDD16 UNIVERSE MASTER PROMPT</span>
          <button onClick={copy} style={{
            background: copied ? GREEN + "22" : ACCENT + "22",
            color: copied ? GREEN : ACCENT,
            border: `1px solid ${copied ? GREEN : ACCENT}44`,
            borderRadius: 8, padding: "6px 18px",
            cursor: "pointer", fontSize: 12, fontWeight: 700,
          }}>
            {copied ? "\u2705 COPIED" : "\uD83D\uDCCB COPY ALL"}
          </button>
        </div>
        <div style={{ color: "#666", fontSize: 11, marginBottom: 14, lineHeight: 1.6 }}>
          Paste this prompt into Claude to activate the Universe signal engine. Feed it Kite Connect data for live signals with full reasoning.
        </div>
        <div style={{
          background: "#07070E", borderRadius: 10, padding: "16px",
          fontFamily: "monospace", fontSize: 11, color: "#aaa",
          lineHeight: 1.8, maxHeight: 500, overflowY: "auto",
          whiteSpace: "pre-wrap", wordBreak: "break-word",
          border: `1px solid ${BORDER}`,
        }}>
          {MASTER_PROMPT}
        </div>
      </Card>
    </div>
  );
}

// \u2500\u2500 MAIN APP \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

export default function Universe({ onLogout }) {
  const [activeTab, setActiveTab] = useState("live");
  const [time, setTime] = useState(new Date());
  const { live, unusual, intraday, nextday, weekly, signals, oiSummary, sellerData, tradeAnalysis, hiddenShift, connected } = useMarketData();

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const istTime = time.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: true });
  const isMarketOpen = (() => {
    const ist = new Date(time.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
    const mins = ist.getHours() * 60 + ist.getMinutes();
    return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30;
  })();

  const renderTab = () => {
    switch (activeTab) {
      case "live":    return <LiveDataTab liveData={live} />;
      case "signals": return <SignalsTab realSignals={signals} />;
      case "intraday":return <IntradayTab realData={intraday} />;
      case "nextday": return <NextDayTab realData={nextday} />;
      case "weekly":  return <WeeklyTab realData={weekly} />;
      case "unusual": return <UnusualTab unusualData={unusual} oiData={oiSummary} />;
      case "sellers": return <SellersTab data={sellerData} />;
      case "tradeai": return <TradeAITab data={tradeAnalysis} />;
      case "hidden":  return <HiddenShiftTab data={hiddenShift} />;
      case "trap":    return <TrapFinderTab />;
      case "priceact":return <PriceActionTab />;
      case "aibrain": return <AIBrainTab />;
      case "oichange":return <OIChangeTab oiData={oiSummary} />;
      case "pnl":     return <PnLTracker signals={signals} />;
      case "prompt":  return <PromptTab />;
      default:        return null;
    }
  };

  return (
    <div style={{ background: BG, minHeight: "100vh", fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif", color: "#fff" }}>

      {/* HEADER */}
      <div style={{
        background: CARD, borderBottom: `1px solid ${BORDER}`,
        padding: "14px 24px", display: "flex", justifyContent: "space-between", alignItems: "center",
        position: "sticky", top: 0, zIndex: 100,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{
            width: 8, height: 8, borderRadius: "50%",
            background: isMarketOpen ? GREEN : RED,
            boxShadow: `0 0 8px ${isMarketOpen ? GREEN : RED}`,
          }} />
          <span style={{ color: "#fff", fontWeight: 900, fontSize: 20, letterSpacing: 3 }}>UNIVERSE</span>
          <span style={{ color: "#2a2a3a", fontSize: 11 }}>NSE Intelligence</span>
          {connected && <span style={{ color: GREEN, fontSize: 9, fontWeight: 700, marginLeft: 8, padding: "2px 8px", background: GREEN + "15", borderRadius: 10 }}>LIVE</span>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ color: ACCENT, fontWeight: 700, fontSize: 14 }}>{istTime}</div>
            <div style={{ color: isMarketOpen ? GREEN : "#444", fontSize: 10, fontWeight: 700 }}>
              {isMarketOpen ? "\u25CF MARKET OPEN" : "\u25CF MARKET CLOSED"}
            </div>
          </div>
          <button onClick={() => exportFullReport({ live, unusual, signals, oiSummary, sellerData, tradeAnalysis, intraday, nextday, weekly })} style={{
              background: ACCENT + "18", color: ACCENT, border: `1px solid ${ACCENT}33`,
              borderRadius: 8, padding: "6px 14px", cursor: "pointer",
              fontSize: 11, fontWeight: 700, whiteSpace: "nowrap",
            }}>
              Export PDF
          </button>
          {onLogout && (
            <button onClick={onLogout} style={{
              background: RED + "18", color: RED, border: `1px solid ${RED}33`,
              borderRadius: 8, padding: "6px 14px", cursor: "pointer",
              fontSize: 11, fontWeight: 700, whiteSpace: "nowrap",
            }}>
              Logout
            </button>
          )}
        </div>
      </div>

      {/* TABS */}
      <div style={{
        background: CARD, borderBottom: `1px solid ${BORDER}`,
        padding: "0 12px", display: "flex", gap: 0, overflowX: "auto",
      }}>
        {TABS.map(tab => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{
            background: "none", border: "none", cursor: "pointer",
            padding: "12px 14px",
            color: activeTab === tab.id ? ACCENT : "#555",
            fontWeight: activeTab === tab.id ? 700 : 400,
            fontSize: 12,
            borderBottom: activeTab === tab.id ? `2px solid ${ACCENT}` : "2px solid transparent",
            whiteSpace: "nowrap",
            transition: "all 0.15s",
          }}>
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {/* CONTENT */}
      <div style={{ padding: "20px 16px", maxWidth: 900, margin: "0 auto" }}>
        {renderTab()}
      </div>

    </div>
  );
}
