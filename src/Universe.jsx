import { useState, useEffect } from "react";
import { useMarketData } from "./useMarketData";

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
  { id: "prompt",  icon: "\uD83E\uDD16", label: "Claude Prompt" },
];

const mockLive = {
  nifty: {
    ltp: 23180.45, change: -142.3, changePct: -0.61,
    high: 23380.1, low: 23140.2, pcr: 0.78, ivr: 42,
    totalCE_OI: 87432000, totalPE_OI: 68210000,
    maxPain: 23200, bigCallStrike: 23300, bigPutStrike: 23000,
    vix: 14.82, trend: "BEARISH", regime: "TRENDING DOWN",
  },
  banknifty: {
    ltp: 48920.6, change: -380.15, changePct: -0.77,
    high: 49420.0, low: 48780.5, pcr: 0.72, ivr: 38,
    totalCE_OI: 124800000, totalPE_OI: 98600000,
    maxPain: 49000, bigCallStrike: 49500, bigPutStrike: 48500,
    vix: 14.82, trend: "BEARISH", regime: "TRENDING DOWN",
  },
};

const mockSignals = [
  {
    id: 1, time: "10:32 AM", instrument: "NIFTY", type: "BUY PUT",
    strike: "23100 PE", expiry: "10 Apr", entry: "185\u2013195",
    t1: "240", t2: "310", sl: "130", score: 7, maxScore: 9,
    rr: "1:3.2", status: "ACTIVE",
    reasoning: [
      { pass: true,   text: "Bearish M-Pattern confirmed on 15min \u2014 double top at 23380" },
      { pass: true,   text: "LTP below 20 EMA (23280) and 50 EMA (23420) \u2014 both negative" },
      { pass: true,   text: "PCR dropped to 0.78 \u2014 CE writers dominating flow" },
      { pass: true,   text: "Max Pain 23200 \u2014 LTP below, bearish gravitational pull" },
      { pass: true,   text: "IVR 42% \u2014 safe zone, premium fairly priced for buying" },
      { pass: true,   text: "Big CE OI wall 23300 \u2014 9.2L contracts, strong resistance cap" },
      { pass: true,   text: "FII net short index futures \u2014 confirmed morning flow" },
      { pass: "warn", text: "VIX stable at 14.82 \u2014 no panic spike, moderate confidence" },
      { pass: false,  text: "VIX not spiking \u2014 premium expansion may be slow" },
    ],
  },
  {
    id: 2, time: "09:18 AM", instrument: "BANKNIFTY", type: "BUY PUT",
    strike: "48500 PE", expiry: "10 Apr", entry: "310\u2013330",
    t1: "420", t2: "560", sl: "210", score: 6, maxScore: 9,
    rr: "1:2.8", status: "CLOSED \u2705",
    reasoning: [
      { pass: true,   text: "BankNifty rejected from 49400 resistance \u2014 4th consecutive rejection" },
      { pass: true,   text: "GEX Flip Detected \u2014 gamma exposure flipped negative at 49000" },
      { pass: true,   text: "PCR at 0.72 \u2014 extreme bearish tilt in options chain" },
      { pass: true,   text: "IVR 38% \u2014 safe zone for option buying" },
      { pass: true,   text: "CE OI writing surge at 49500 \u2014 12.4L contracts, institutional cap" },
      { pass: true,   text: "VWAP rejection confirmed on 5min chart at 49150" },
      { pass: "warn", text: "MACD not yet crossed bearish \u2014 confirmation still pending" },
      { pass: "warn", text: "Broader Nifty needs to confirm breakdown for full conviction" },
      { pass: false,  text: "HDFC Bank showing relative strength \u2014 divergence risk present" },
    ],
  },
];

const mockNextDay = {
  date: "Tomorrow \u2014 2 Apr 2026", generatedAt: "2:45 PM IST",
  nifty: {
    bias: "BEARISH", pivot: 23210, maxPain: 23200,
    rangeHigh: 23320, rangeLow: 23020,
    resistance: [
      { level: 23280, reason: "20 EMA \u2014 dynamic resistance" },
      { level: 23350, reason: "Previous day high \u2014 supply zone" },
      { level: 23450, reason: "Fibonacci 0.618 retracement" },
    ],
    support: [
      { level: 23090, reason: "Fibonacci 0.382 \u2014 key demand zone" },
      { level: 22820, reason: "Fibonacci 0.5 \u2014 strong weekly support" },
      { level: 22600, reason: "200 EMA on daily \u2014 macro support" },
    ],
    bigCEWall: "23300 CE \u2014 9.2L OI \u2014 DO NOT buy calls above this",
    bigPEWall: "23000 PE \u2014 7.8L OI \u2014 Strong support zone",
    unusual:   "23200 CE \u2014 Huge writing in last 30 min of session",
    opening:   "Gap down likely if Nifty closes below 23150 today",
    strategy:  "Buy PE on any pullback to 23200\u201323250 range. Avoid CE buying until 23450 reclaimed.",
    plan: [
      "9:15\u20139:30 AM \u2192 Watch gap fill attempt, don't trade first 5 candles",
      "9:30\u201310:30 AM \u2192 If 23090 breaks with volume, add PE aggressively",
      "10:30 AM\u20132:00 PM \u2192 Trail stop to entry after T1 hit",
      "2:00\u20132:30 PM \u2192 VIX above 15 at this time = last PE entry window",
    ],
  },
  banknifty: {
    bias: "BEARISH", pivot: 48950, maxPain: 49000,
    rangeHigh: 49250, rangeLow: 48400,
    resistance: [
      { level: 49200, reason: "VWAP resistance from yesterday" },
      { level: 49500, reason: "Institutional CE wall \u2014 12.4L OI" },
      { level: 49800, reason: "Weekly supply zone" },
    ],
    support: [
      { level: 48500, reason: "Big PE wall \u2014 9.1L OI battleground" },
      { level: 48200, reason: "Previous swing low support" },
      { level: 47800, reason: "Weekly demand zone" },
    ],
    bigCEWall: "49500 CE \u2014 12.4L OI \u2014 Institutional cap confirmed",
    bigPEWall: "48500 PE \u2014 9.1L OI \u2014 Key battleground",
    unusual:   "49000 CE \u2014 3x avg volume spike detected EOD",
    opening:   "Flat to negative open expected",
    strategy:  "Sell on rise to 49100\u201349200. Buy PE on breakdown below 48700.",
    plan: [
      "9:15 AM \u2192 Check SGX Nifty overnight cue",
      "If HDFC Bank above 1780 \u2192 bullish divergence risk, reduce PE size",
      "Below 48500 \u2192 panic selling possible, large PE move expected",
      "No new positions after 2:00 PM on Friday \u2014 weekend risk",
    ],
  },
};

const mockWeekly = {
  week: "31 Mar \u2013 4 Apr 2026",
  niftyBias: "BEARISH", bnBias: "BEARISH",
  niftyRange: { high: 23500, low: 22600 },
  bnRange: { high: 49800, low: 47800 },
  oiAnalysis: [
    "23000 PE highest OI \u2014 key support for bulls this week",
    "23500 CE max writing \u2014 strong ceiling, resistance confirmed",
    "PCR at 0.78 \u2014 moderate bearish tilt, not extreme yet",
    "IVR normal zone \u2014 weekly options fairly priced for buying",
  ],
  fii: "\u2212\u20B92,840 Cr (index futures net short)",
  dii: "+\u20B91,920 Cr (cash buying, absorbing FII selling)",
  verdict: "FII dominant bearish vs DII absorbing \u2014 tug of war at 23000",
  macro: [
    "Friday: US Non-Farm Payroll \u2014 high global volatility expected",
    "RBI MPC outcome week \u2014 hawkish surprise = sharp bearish",
    "Q4 FY26 earnings season begins \u2014 stock-specific volatility",
  ],
  plan: [
    { day: "Monday",    col: ACCENT,  text: "Wait and watch \u2014 observe open + first 30 min before any entry" },
    { day: "Tuesday",   col: ACCENT,  text: "Core trade window \u2014 look for clean signal with score 7+" },
    { day: "Wednesday", col: GREEN,   text: "Best momentum day \u2014 add to winning positions if trend is clear" },
    { day: "Thursday",  col: RED,     text: "\u26A0\uFE0F Theta decay aggressive \u2014 NO option buying after 2 PM" },
    { day: "Friday",    col: ORANGE,  text: "\uD83D\uDEAB No new positions \u2014 NFP + weekend risk, exit all by 1 PM" },
  ],
  niftyMoB: 23000, bnMoB: 48000,
};

const mockUnusual = [
  {
    time: "02:38 PM", instrument: "NIFTY 23200 CE", type: "BIG WRITING",
    oiChange: "+4.2L contracts", premChange: "\u221218 pts", alert: "HIGH",
    signal: "Institutional CE writing \u2014 bearish bias confirmed",
  },
  {
    time: "01:54 PM", instrument: "NIFTY 23000 PE", type: "BIG BUYING",
    oiChange: "+6.8L contracts", premChange: "+32 pts", alert: "CRITICAL",
    signal: "Smart money loading PE \u2014 strong directional bearish bet",
  },
  {
    time: "11:22 AM", instrument: "BANKNIFTY 49500 CE", type: "BIG WRITING",
    oiChange: "+8.1L contracts", premChange: "\u221224 pts", alert: "HIGH",
    signal: "Institutional cap confirmed at 49500 \u2014 do not buy CE above this",
  },
  {
    time: "10:05 AM", instrument: "BANKNIFTY 48500 PE", type: "UNUSUAL VOLUME",
    oiChange: "+3.1L contracts", premChange: "+45 pts", alert: "MEDIUM",
    signal: "3x avg volume \u2014 possible institutional hedge or directional bet",
  },
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
  const data = liveData || mockLive;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {[{ name: "NIFTY", d: data.nifty }, { name: "BANKNIFTY", d: data.banknifty }].map(({ name, d }) => (
        <Card key={name}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <span style={{ color: ACCENT, fontWeight: 900, fontSize: 18, letterSpacing: 1 }}>{name}</span>
            <div style={{ display: "flex", gap: 8 }}>
              <Badge text={d.trend}  color={d.trend === "BULLISH" ? GREEN : RED} />
              <Badge text={d.regime} color={ORANGE} />
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 10 }}>
            <Stat label="LTP"    value={d.ltp.toLocaleString("en-IN")} />
            <Stat label="Change" value={`${d.change > 0 ? "+" : ""}${d.change} (${d.changePct}%)`} color={d.change > 0 ? GREEN : RED} />
            <Stat label="High"   value={d.high.toLocaleString("en-IN")} color={GREEN} />
            <Stat label="Low"    value={d.low.toLocaleString("en-IN")}  color={RED}   />
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
      ))}
    </div>
  );
}

// \u2500\u2500 TAB: SIGNALS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function SignalsTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {mockSignals.map(s => (
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

function IntradayTab() {
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
  const techLevels = [
    { label: "NIFTY VWAP",          value: "23,195",       color: ACCENT },
    { label: "NIFTY Supertrend",     value: "23,280 \u2193 SELL", color: RED   },
    { label: "NIFTY RSI (14)",       value: "38.4 \u2014 Weak",  color: YELLOW },
    { label: "NIFTY MACD",          value: "Bearish Cross", color: RED   },
    { label: "BANKNIFTY VWAP",       value: "48,960",       color: ACCENT },
    { label: "BANKNIFTY Supertrend", value: "49,200 \u2193 SELL", color: RED   },
    { label: "BANKNIFTY RSI (14)",   value: "35.8 \u2014 Weak",  color: YELLOW },
    { label: "BANKNIFTY MACD",       value: "Bearish Cross", color: RED   },
  ];
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
        <Label>Key Technical Levels Today</Label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          {techLevels.map(t => (
            <Stat key={t.label} label={t.label} value={t.value} color={t.color} />
          ))}
        </div>
      </Card>
    </div>
  );
}

// \u2500\u2500 TAB: NEXT DAY \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function NextDayTab() {
  const d = mockNextDay;
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

function WeeklyTab() {
  const w = mockWeekly;
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

function UnusualTab({ unusualData }) {
  const alerts = unusualData && unusualData.length > 0 ? unusualData : mockUnusual;
  const alertColor = { CRITICAL: RED, HIGH: ORANGE, MEDIUM: YELLOW };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Card style={{ background: RED + "0A", border: `1px solid ${RED}33` }}>
        <div style={{ color: RED, fontWeight: 700, fontSize: 13, marginBottom: 4 }}>\uD83D\uDEA8 UNUSUAL ACTIVITY MONITOR</div>
        <div style={{ color: "#555", fontSize: 11, lineHeight: 1.6 }}>
          Triggers: Volume {">"} 3x avg \u00B7 OI Change {">"} 5L \u00B7 Premium {">"} 30% swing \u00B7 PCR shift {">"} 0.15 \u00B7 VIX spike {">"} 5% \u00B7 GEX Flip
        </div>
      </Card>
      {alerts.map((u, i) => (
        <Card key={i} style={{ borderColor: alertColor[u.alert] + "44" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
            <div>
              <div style={{ color: alertColor[u.alert], fontWeight: 700, fontSize: 14, marginBottom: 4 }}>
                {u.type} \u2014 {u.instrument}
              </div>
              <div style={{ color: "#555", fontSize: 11 }}>{u.time}</div>
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
              <div style={{ color: u.premChange.includes("+") ? GREEN : RED, fontWeight: 700 }}>{u.premChange}</div>
            </div>
          </div>
          <div style={{ padding: "8px 12px", background: alertColor[u.alert] + "11", borderRadius: 8, color: alertColor[u.alert], fontSize: 12, fontWeight: 600 }}>
            \u2192 {u.signal}
          </div>
        </Card>
      ))}
    </div>
  );
}

// \u2500\u2500 TAB: CLAUDE PROMPT \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

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

export default function Universe() {
  const [activeTab, setActiveTab] = useState("live");
  const [time, setTime] = useState(new Date());
  const { live, unusual, connected } = useMarketData();

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
      case "signals": return <SignalsTab />;
      case "intraday":return <IntradayTab />;
      case "nextday": return <NextDayTab />;
      case "weekly":  return <WeeklyTab />;
      case "unusual": return <UnusualTab unusualData={unusual} />;
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
        <div style={{ textAlign: "right" }}>
          <div style={{ color: ACCENT, fontWeight: 700, fontSize: 14 }}>{istTime}</div>
          <div style={{ color: isMarketOpen ? GREEN : "#444", fontSize: 10, fontWeight: 700 }}>
            {isMarketOpen ? "\u25CF MARKET OPEN" : "\u25CF MARKET CLOSED"}
          </div>
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
