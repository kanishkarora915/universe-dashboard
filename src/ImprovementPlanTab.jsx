import React, { useState } from "react";
import { useTheme } from "./ThemeContext";

const fmtINR = (n) => {
  if (n === null || n === undefined) return "₹0";
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
};

const fmtLakh = (n) => {
  const lakh = n / 100000;
  return `₹${lakh.toFixed(1)}L`;
};

const STATUS_COLORS = {
  done: { bg: "#0a3a1a", border: "#1f9d4e", text: "#27ae60", label: "✅ DONE" },
  active: { bg: "#0a2a3a", border: "#1f7d9d", text: "#3498db", label: "🟢 ACTIVE" },
  pending: { bg: "#3a2a0a", border: "#9d7d1f", text: "#f39c12", label: "⏳ PENDING" },
  disabled: { bg: "#3a0a0a", border: "#9d1f1f", text: "#e74c3c", label: "🔴 DISABLED" },
  optional: { bg: "#2a1a3a", border: "#7d1f9d", text: "#9b59b6", label: "💡 OPTIONAL" },
};

const PHASES = [
  {
    id: "phase1",
    title: "PHASE 1 — Monday Onwards (DEPLOYED)",
    subtitle: "Active baseline improvements",
    timeWindow: "Week 1",
    expectedGain: 9, // lakhs
    color: "#27ae60",
    improvements: [
      {
        name: "Entry Threshold: 55 → 65%",
        why: "60d data: 55-59% bucket WR 35.1%, lost ₹54k. 65-69% bucket WR 64%, made ₹105k.",
        before: {
          state: "Threshold 55% — many noise trades",
          metric: "WR 47% scalper, lots of bleeding 55-59% bucket",
          example: "Today: 14 trades, only 2 wins (14% WR)",
        },
        after: {
          state: "Threshold 65% — quality only",
          metric: "WR projected 55-65%, sweet spot 65-69%",
          example: "5-10 quality trades/day at higher WR",
        },
        impact: 100000, // ₹1L over 60d from quality
        status: "done",
        proof: "calibration_audit shows 65-69% bucket = +₹98,790 profit",
      },
      {
        name: "Sync PROB_INSTANT_FIRE = INSTANT_NEW = 65",
        why: "Today June 12: pending created at 55% but momentum fired at 60% only. Trades stuck in limbo for 4+ hours. 0 Main trades all day!",
        before: {
          state: "Mismatch: pending @ 55%, fire @ 60%",
          metric: "Pending expires every 90s → no trade",
          example: "Today: BANKNIFTY verdict 84% but no trade fired",
        },
        after: {
          state: "Both synced at 65%",
          metric: "Pending creates AND fires same cycle",
          example: "Verdict 65%+ = trade fires immediately",
        },
        impact: 300000, // Main mode unlocked
        status: "done",
        proof: "Today's 0 Main trades despite 78% NIFTY verdict",
      },
      {
        name: "Flat Sizing (Tiered Disabled)",
        why: "Tiered sizing punished 65-69% sweet spot. Killed Main 80%+ bucket where it made +₹79k.",
        before: {
          state: "65-69% = 1.5x boost, 80%+ = 0.5x cut",
          metric: "Mixed: helped scalper, hurt Main",
          example: "Today: high-prob trades got 0.5x size",
        },
        after: {
          state: "Flat 1.0x sizing on all trades",
          metric: "Full power on every qualified entry",
          example: "Quality entry × full size = max profit",
        },
        impact: 200000,
        status: "done",
        proof: "Main 80%+ historical: +₹79,523",
      },
      {
        name: "Tuesday 9-10 AM Expiry Block",
        why: "Tuesday/expiry = ENTIRE 60d system loss (-₹146k). 9-10 AM alone = -₹154k.",
        before: {
          state: "Tuesday open — system trades freely",
          metric: "-₹146k over 8 Tuesdays in 60d",
          example: "Apr 28: -₹146,838 from 10 BNF PE chained",
        },
        after: {
          state: "Tuesday 9-10 AM BLOCKED, rest allowed",
          metric: "+₹153k recovered every quarter",
          example: "10-11 AM Tuesday still profitable (+₹49k)",
        },
        impact: 150000,
        status: "done",
        proof: "60d data: Tuesday 9-10 = -₹153,854",
      },
      {
        name: "VELOCITY_EXIT On",
        why: "Disabled June 3 = SL_HIT avg hold 30min → 203min (3.5 HOURS!). ₹6.69L scalper SL_HIT damage.",
        before: {
          state: "OFF — losers ride to -8% over 200min",
          metric: "₹6.69L SL_HIT loss over 60d",
          example: "Today: 11:55 BNF caught theta trap (₹27k saved)",
        },
        after: {
          state: "ON — cuts theta traps in 5min",
          metric: "Avg hold drops back to 30min",
          example: "Today fired once — caught ₹27k loss correctly",
        },
        impact: 400000,
        status: "active",
        proof: "Today's VELOCITY_EXIT caught BNF theta trap",
      },
      {
        name: "profit_floor Per-Index",
        why: "Catches peak givebacks at +1.5% NIFTY / +1.8% BANKNIFTY. Locks SL on peak.",
        before: {
          state: "No floor — trades give back peak gains",
          metric: "Trades peak +5% → exit at -5%",
          example: "Pre-fix #269: peak +2.3% → -₹28,536",
        },
        after: {
          state: "Active — locks at entry on peak",
          metric: "Peak +5% → locked at +2.5%",
          example: "Today saved 3 trades (₹+5,074 net SL_HIT)",
        },
        impact: 150000,
        status: "active",
        proof: "Today: 3 SL_HIT trades positive due to lock",
      },
    ],
  },
  {
    id: "phase2",
    title: "PHASE 2 — Smart Leak Handling (Week 2 if needed)",
    subtitle: "Data-driven leak catches",
    timeWindow: "Week 2",
    expectedGain: 9,
    color: "#3498db",
    improvements: [
      {
        name: "Velocity-Aware Stop-Hunt Detection",
        why: "60d: 45 STOP_HUNTED trades = -₹4.52L. Premium recovered AFTER SL hit (institutional hunts).",
        before: {
          state: "SL touched = immediate exit",
          metric: "-₹4.52L from 45 hunt trades",
          example: "#154 BNF: SL hit 843, recovered to 871 (-₹38,894 lost)",
        },
        after: {
          state: "Check velocity at SL touch",
          metric: "If recovering: wait 20s, else exit",
          example: "Catches institutional hunts, lets real SLs fire",
        },
        impact: 250000,
        status: "pending",
        proof: "Exit reasons literally say 'Institutional flush detected'",
      },
      {
        name: "Verdict Trajectory Check",
        why: "WATCHER_EXIT trades had high prob (65% avg) BUT verdict was declining at entry. -₹3.30L scalper.",
        before: {
          state: "Fire on any verdict above threshold",
          metric: "-₹3.30L from 14 instant-crash trades",
          example: "Trade entered at 'top' of confidence, immediately crashed",
        },
        after: {
          state: "Check verdict direction over 30s",
          metric: "Rising = take, Declining = skip",
          example: "Avoid momentum-fading entries",
        },
        impact: 200000,
        status: "pending",
        proof: "WATCHER trades avg peak 0.85% = never positive",
      },
      {
        name: "Hold Time Cap for Losers",
        why: "SL_HIT trades hold 202 min avg before dying. By 60 min you know it's dead.",
        before: {
          state: "Trades ride to -5% even over 3 hours",
          metric: "200+ min avg hold on losers",
          example: "#106 BNF: 22 min, never positive, ₹-88,650",
        },
        after: {
          state: "If hold > 60min AND in loss: exit small",
          metric: "Cut at -2% instead of -5%",
          example: "Free capital faster",
        },
        impact: 100000,
        status: "pending",
        proof: "SL_HIT avg hold = 202 min",
      },
    ],
  },
  {
    id: "phase3",
    title: "PHASE 3 — Big Profit Locking (Week 2-3)",
    subtitle: "Capture trades that hit big peaks",
    timeWindow: "Week 2-3",
    expectedGain: 8,
    color: "#9b59b6",
    improvements: [
      {
        name: "Aggressive Profit Floor Above +5% Peak",
        why: "Current locks too conservative. Peak +10% only locks +4%. Should lock +7%.",
        before: {
          state: "Conservative ladder above +5% peak",
          metric: "Peak +10% → SL = +4% (gives back 60%)",
          example: "#12:25 BNF peak +7.2% → exit +4% = ₹37,500 (could be ₹65k+)",
        },
        after: {
          state: "Aggressive ladder above +5% peak",
          metric: "Peak +10% → SL = +7% (locks 70%)",
          example: "Same trade: exit +7% = ₹52,500 → +₹15k more",
        },
        impact: 300000,
        status: "pending",
        proof: "Today's #12:25 trade left ₹27k on table",
      },
      {
        name: "Partial Booking at Milestones",
        why: "All-or-nothing exits give back peak gains. Partial booking guarantees portions.",
        before: {
          state: "Single exit — all qty at one price",
          metric: "Trade peaks +10% → all 600 qty exits at +3%",
          example: "₹14,670 instead of guaranteed ₹65k+",
        },
        after: {
          state: "Book 25% at +5%, +10%, +15% peaks",
          metric: "75% guaranteed at higher rungs, 25% rides T2",
          example: "BNF +15% peak = ₹40k+ guaranteed",
        },
        impact: 250000,
        status: "pending",
        proof: "Multiple +5%+ peak trades exited at +3% today",
      },
      {
        name: "Suspend REVERSAL_EXIT on Big Winners",
        why: "REVERSAL_EXIT kills runners. If peak >+5%, trust profit_floor and ride.",
        before: {
          state: "REVERSAL_EXIT can fire even at +5%+ peak",
          metric: "Engine flip kills winners early",
          example: "Trade peaks +10% but reverses → exit at +1.5%",
        },
        after: {
          state: "Peak >+5% = NEVER fire REVERSAL_EXIT",
          metric: "profit_floor handles, no engine override",
          example: "Big winners protected from noise",
        },
        impact: 150000,
        status: "pending",
        proof: "Peak +10.3% trade today exited at +1% (REVERSAL_EXIT killed it)",
      },
    ],
  },
  {
    id: "phase4",
    title: "PHASE 4 — Engine Rebalance (Month 1+)",
    subtitle: "Kill bad engines, boost proven ones",
    timeWindow: "Month 1+",
    expectedGain: 3,
    color: "#e74c3c",
    improvements: [
      {
        name: "Reduce trap_fingerprints 41% → 25%",
        why: "Trap's 54.5% accuracy was 185 samples = noise. Real accuracy on 364 samples = 50% (coin flip).",
        before: {
          state: "Trap weight 41% (highest)",
          metric: "Single engine drives 41% of verdict",
          example: "Concentration risk on noisy signal",
        },
        after: {
          state: "Trap weight 25%",
          metric: "Diversified — no single engine dominates",
          example: "Reduces variance from trap's bad calls",
        },
        impact: 50000,
        status: "optional",
        proof: "Statistical 95% CI [44.9%, 55.1%] straddles 50%",
      },
      {
        name: "INVERT seller_positioning Signal",
        why: "Active fires WORSE outcomes (47.8%). Silent = better (53.2%). p=0.0003.",
        before: {
          state: "Seller fires high = BULLISH signal",
          metric: "47.8% WR when active",
          example: "Engine wrong 52% of time",
        },
        after: {
          state: "Seller fires high = BEARISH inverse",
          metric: "53.2% WR if inverted",
          example: "Use as contrarian indicator",
        },
        impact: 150000,
        status: "optional",
        proof: "Statistically significant at 99.9%",
      },
      {
        name: "Boost price_action 11% → 35%",
        why: "Only engine with PROVEN statistical edge (+5.8pp active vs silent, p=0.026).",
        before: {
          state: "Weight 11% (under-used)",
          metric: "Only proven engine = small role",
          example: "Best signal contributes least",
        },
        after: {
          state: "Weight 35%",
          metric: "Dominant signal in verdict",
          example: "Quality over quantity in engine vote",
        },
        impact: 100000,
        status: "optional",
        proof: "Only engine with statistical confidence",
      },
    ],
  },
];

const PROJECTIONS = [
  {
    phase: "Current (60d net)",
    daily: 11000,
    monthly: 230000,
    sixtyDay: 684000,
    color: "#7f8c8d",
  },
  {
    phase: "Phase 1 (Monday)",
    daily: 35000,
    monthly: 700000,
    sixtyDay: 1500000,
    color: "#27ae60",
  },
  {
    phase: "Phase 2 (Week 2)",
    daily: 55000,
    monthly: 1100000,
    sixtyDay: 2400000,
    color: "#3498db",
  },
  {
    phase: "Phase 3 (Week 3)",
    daily: 75000,
    monthly: 1500000,
    sixtyDay: 3200000,
    color: "#9b59b6",
  },
  {
    phase: "Phase 4 (Month 1+)",
    daily: 85000,
    monthly: 1700000,
    sixtyDay: 3500000,
    color: "#e74c3c",
  },
];

function StatusBadge({ status }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.pending;
  return (
    <span
      style={{
        padding: "4px 10px",
        borderRadius: 4,
        background: c.bg,
        color: c.text,
        fontSize: 11,
        fontWeight: 700,
        border: `1px solid ${c.border}`,
      }}
    >
      {c.label}
    </span>
  );
}

function ImprovementCard({ imp }) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const c = STATUS_COLORS[imp.status] || STATUS_COLORS.pending;

  return (
    <div
      onClick={() => setExpanded(!expanded)}
      style={{
        background: theme.SURFACE,
        border: `1px solid ${c.border}`,
        borderRadius: 8,
        padding: 16,
        marginBottom: 12,
        cursor: "pointer",
        transition: "all 0.2s",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: theme.TEXT }}>
          {imp.name}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 13, color: "#27ae60", fontWeight: 700 }}>
            +{fmtLakh(imp.impact)} / 60d
          </span>
          <StatusBadge status={imp.status} />
        </div>
      </div>

      <div style={{ fontSize: 12, color: theme.MUTED, marginBottom: 12 }}>
        💡 {imp.why}
      </div>

      {expanded && (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div
              style={{
                background: "#3a0a0a",
                borderRadius: 4,
                padding: 12,
                border: "1px solid #9d1f1f",
              }}
            >
              <div style={{ fontSize: 11, color: "#e74c3c", fontWeight: 700, marginBottom: 6 }}>
                ❌ BEFORE
              </div>
              <div style={{ fontSize: 12, color: theme.TEXT, marginBottom: 6 }}>
                {imp.before.state}
              </div>
              <div style={{ fontSize: 11, color: theme.MUTED, marginBottom: 4 }}>
                📊 {imp.before.metric}
              </div>
              <div style={{ fontSize: 11, color: "#e74c3c", fontStyle: "italic" }}>
                🔻 {imp.before.example}
              </div>
            </div>

            <div
              style={{
                background: "#0a3a1a",
                borderRadius: 4,
                padding: 12,
                border: "1px solid #1f9d4e",
              }}
            >
              <div style={{ fontSize: 11, color: "#27ae60", fontWeight: 700, marginBottom: 6 }}>
                ✅ AFTER
              </div>
              <div style={{ fontSize: 12, color: theme.TEXT, marginBottom: 6 }}>
                {imp.after.state}
              </div>
              <div style={{ fontSize: 11, color: theme.MUTED, marginBottom: 4 }}>
                📊 {imp.after.metric}
              </div>
              <div style={{ fontSize: 11, color: "#27ae60", fontStyle: "italic" }}>
                🟢 {imp.after.example}
              </div>
            </div>
          </div>

          <div
            style={{
              marginTop: 10,
              padding: "6px 10px",
              background: theme.SURFACE_2 || theme.SURFACE,
              borderRadius: 4,
              fontSize: 11,
              color: theme.MUTED,
              border: `1px dashed ${theme.BORDER}`,
            }}
          >
            🔍 <strong>DATA PROOF:</strong> {imp.proof}
          </div>
        </div>
      )}

      <div
        style={{
          fontSize: 10,
          color: theme.MUTED,
          marginTop: 8,
          textAlign: "right",
        }}
      >
        {expanded ? "▲ Click to collapse" : "▼ Click to expand details"}
      </div>
    </div>
  );
}

function PhaseSection({ phase }) {
  const theme = useTheme();
  return (
    <div
      style={{
        background: theme.SURFACE,
        borderLeft: `4px solid ${phase.color}`,
        borderRadius: 6,
        padding: 16,
        marginBottom: 20,
      }}
    >
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h3
              style={{
                margin: 0,
                fontSize: 18,
                color: theme.TEXT,
              }}
            >
              {phase.title}
            </h3>
            <div style={{ fontSize: 12, color: theme.MUTED, marginTop: 4 }}>
              {phase.subtitle} · {phase.timeWindow}
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 11, color: theme.MUTED }}>Expected Gain</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: phase.color }}>
              +₹{phase.expectedGain}L
            </div>
            <div style={{ fontSize: 10, color: theme.MUTED }}>per 60 days</div>
          </div>
        </div>
      </div>

      {phase.improvements.map((imp, i) => (
        <ImprovementCard key={i} imp={imp} />
      ))}
    </div>
  );
}

function ProjectionChart() {
  const theme = useTheme();
  const maxDaily = Math.max(...PROJECTIONS.map((p) => p.daily));

  return (
    <div
      style={{
        background: theme.SURFACE,
        borderRadius: 8,
        padding: 20,
        marginBottom: 24,
        border: `1px solid ${theme.BORDER}`,
      }}
    >
      <h2 style={{ margin: 0, marginBottom: 16, fontSize: 18, color: theme.TEXT }}>
        📈 Daily P&L Projection by Phase
      </h2>

      {PROJECTIONS.map((p, i) => {
        const width = (p.daily / maxDaily) * 100;
        return (
          <div key={i} style={{ marginBottom: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: theme.TEXT }}>
                {p.phase}
              </span>
              <span style={{ fontSize: 13, fontWeight: 700, color: p.color }}>
                {fmtINR(p.daily)}/day
              </span>
            </div>
            <div
              style={{
                height: 26,
                background: theme.SURFACE_2 || theme.BORDER,
                borderRadius: 4,
                position: "relative",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  left: 0,
                  top: 0,
                  bottom: 0,
                  width: `${width}%`,
                  background: `linear-gradient(90deg, ${p.color}80, ${p.color})`,
                  borderRadius: 4,
                  display: "flex",
                  alignItems: "center",
                  paddingLeft: 8,
                }}
              >
                <span style={{ fontSize: 11, color: "#fff", fontWeight: 600 }}>
                  ₹{(p.monthly / 100000).toFixed(1)}L/mo
                </span>
              </div>
            </div>
            <div style={{ fontSize: 10, color: theme.MUTED, marginTop: 2 }}>
              60d total: {fmtLakh(p.sixtyDay)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Header({ totalGain }) {
  const theme = useTheme();
  return (
    <div
      style={{
        background: `linear-gradient(135deg, #1a3a1a 0%, ${theme.SURFACE} 100%)`,
        border: `1px solid #1f9d4e`,
        borderRadius: 8,
        padding: 24,
        marginBottom: 24,
      }}
    >
      <h1 style={{ margin: 0, fontSize: 24, color: theme.TEXT }}>
        🚀 Improvement Plan — Path to ₹{totalGain}L
      </h1>
      <div style={{ fontSize: 14, color: theme.MUTED, marginTop: 8 }}>
        Data-driven improvements with before/after proof
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 12,
          marginTop: 20,
        }}
      >
        <div>
          <div style={{ fontSize: 11, color: theme.MUTED }}>Current 60d Net</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#7f8c8d" }}>₹6.84L</div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: theme.MUTED }}>Target 60d Net</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#27ae60" }}>₹33L</div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: theme.MUTED }}>Improvement</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#27ae60" }}>+₹{totalGain}L</div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: theme.MUTED }}>Multiplier</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#27ae60" }}>5x</div>
        </div>
      </div>
    </div>
  );
}

export default function ImprovementPlanTab() {
  const theme = useTheme();
  const totalGain = PHASES.reduce((sum, p) => sum + p.expectedGain, 0);

  return (
    <div style={{ padding: 16, maxWidth: 1200, margin: "0 auto", color: theme.TEXT }}>
      <Header totalGain={totalGain} />
      <ProjectionChart />
      {PHASES.map((phase) => (
        <PhaseSection key={phase.id} phase={phase} />
      ))}

      <div
        style={{
          background: theme.SURFACE,
          borderRadius: 8,
          padding: 20,
          marginTop: 20,
          border: `1px dashed ${theme.BORDER}`,
        }}
      >
        <h3 style={{ margin: 0, marginBottom: 12, fontSize: 16, color: theme.TEXT }}>
          🎯 Implementation Strategy
        </h3>
        <ul style={{ margin: 0, paddingLeft: 20, color: theme.MUTED, fontSize: 13, lineHeight: 1.8 }}>
          <li>
            <strong style={{ color: theme.TEXT }}>Phase 1 (DEPLOYED):</strong> Active from Monday morning. Daily auto-restart applies the changes.
          </li>
          <li>
            <strong style={{ color: theme.TEXT }}>Phase 2 (Week 2):</strong> Only if Week 1 data shows leaks continuing. Each catch validated before next.
          </li>
          <li>
            <strong style={{ color: theme.TEXT }}>Phase 3 (Week 3+):</strong> Capture big peak profits with aggressive ladder + partial booking.
          </li>
          <li>
            <strong style={{ color: theme.TEXT }}>Phase 4 (Month 1+):</strong> Engine rebalance based on accumulated production data.
          </li>
          <li>
            <strong style={{ color: theme.TEXT }}>NO LIVE-MARKET DEPLOYS:</strong> All changes pre-market or weekend.
          </li>
          <li>
            <strong style={{ color: theme.TEXT }}>5-day observation gates:</strong> Between each phase, observe data before adding more.
          </li>
        </ul>
      </div>
    </div>
  );
}
