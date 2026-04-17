import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION } from "../theme";

function Pill({ label, value, color, theme }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: `${SPACE.SM}px ${SPACE.MD}px`,
        background: color + "12",
        border: `1px solid ${color}33`,
        borderRadius: RADIUS.MD,
        minWidth: 90,
      }}
    >
      <span
        style={{
          color,
          fontSize: 9,
          fontWeight: TEXT_WEIGHT.BOLD,
          letterSpacing: 1.5,
          textTransform: "uppercase",
        }}
      >
        {label}
      </span>
      <span
        style={{
          color: theme.TEXT,
          fontSize: 18,
          fontWeight: TEXT_WEIGHT.BOLD,
          fontFamily: FONT.MONO,
        }}
      >
        {value}
      </span>
    </div>
  );
}

function ConfidenceBar({ pct, color, theme }) {
  const clamped = Math.max(0, Math.min(100, pct || 0));
  return (
    <div style={{ display: "flex", alignItems: "center", gap: SPACE.MD, minWidth: 180 }}>
      <div style={{ flex: 1 }}>
        <div
          style={{
            height: 8,
            background: theme.SURFACE_HI,
            borderRadius: RADIUS.XS,
            overflow: "hidden",
            position: "relative",
          }}
        >
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              height: "100%",
              width: `${clamped}%`,
              background: color,
              transition: TRANSITION.SLOW,
              boxShadow: `0 0 8px ${color}`,
            }}
          />
        </div>
      </div>
      <span
        style={{
          color,
          fontSize: 16,
          fontWeight: TEXT_WEIGHT.BLACK,
          fontFamily: FONT.MONO,
          minWidth: 46,
          textAlign: "right",
        }}
      >
        {clamped}%
      </span>
    </div>
  );
}

export function VerdictHero({ index = "NIFTY", verdict, reasons = [] }) {
  const { theme } = useTheme();
  const v = verdict || {};
  const action = v.action || v.verdict || "NO TRADE";
  const confidence = v.confidence || v.winProbability || 0;

  const isBuy = action.startsWith("BUY");
  const isCE = action.includes("CE");
  const isPE = action.includes("PE");
  const accentColor = isCE ? theme.GREEN : isPE ? theme.RED : theme.TEXT_MUTED;

  return (
    <div
      style={{
        background: theme.SURFACE,
        border: `1px solid ${theme.BORDER_HI}`,
        borderLeft: `3px solid ${accentColor}`,
        borderRadius: RADIUS.LG,
        padding: `${SPACE.LG}px ${SPACE.XL}px`,
        boxShadow: isBuy ? `0 0 24px ${accentColor}18` : undefined,
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* Subtle accent gradient on the right */}
      {isBuy && (
        <div
          style={{
            position: "absolute",
            top: 0,
            right: 0,
            bottom: 0,
            width: "30%",
            background: `linear-gradient(to left, ${accentColor}10, transparent)`,
            pointerEvents: "none",
          }}
        />
      )}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: SPACE.MD, position: "relative" }}>
        <div>
          <div
            style={{
              color: theme.TEXT_DIM,
              fontSize: TEXT_SIZE.MICRO,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 2,
              textTransform: "uppercase",
            }}
          >
            {index} · AI Verdict
          </div>
          <div
            style={{
              color: accentColor,
              fontSize: 28,
              fontWeight: TEXT_WEIGHT.BLACK,
              fontFamily: FONT.MONO,
              letterSpacing: 1.5,
              marginTop: SPACE.XS,
              lineHeight: 1,
            }}
          >
            {action}
            {v.strike && <span style={{ color: theme.TEXT, marginLeft: SPACE.SM }}>{v.strike}</span>}
          </div>
        </div>
        <ConfidenceBar pct={confidence} color={accentColor} theme={theme} />
      </div>

      {isBuy && (
        <div style={{ display: "flex", gap: SPACE.SM, flexWrap: "wrap", marginBottom: SPACE.MD, position: "relative" }}>
          {v.entry && <Pill label="Entry" value={v.entry} color={theme.ACCENT} theme={theme} />}
          {v.sl && <Pill label="SL" value={v.sl} color={theme.RED} theme={theme} />}
          {v.target1 && <Pill label="T1" value={v.target1} color={theme.GREEN} theme={theme} />}
          {v.target2 && <Pill label="T2" value={v.target2} color={theme.GREEN} theme={theme} />}
          {v.riskReward && <Pill label="R:R" value={v.riskReward} color={theme.PURPLE} theme={theme} />}
          {v.holdTime && <Pill label="Hold" value={v.holdTime} color={theme.CYAN} theme={theme} />}
        </div>
      )}

      {reasons.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, position: "relative" }}>
          {reasons.slice(0, 3).map((r, i) => (
            <div
              key={i}
              style={{
                color: theme.TEXT_MUTED,
                fontSize: TEXT_SIZE.BODY,
                paddingLeft: SPACE.MD,
                position: "relative",
              }}
            >
              <span style={{ position: "absolute", left: 0, color: accentColor }}>›</span>
              {r}
            </div>
          ))}
        </div>
      )}

      {!isBuy && !reasons.length && (
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.BODY, fontStyle: "italic" }}>
          No high-confidence setup right now. Wait for signal score to align with flow + seller patterns.
        </div>
      )}
    </div>
  );
}

export function DashboardHeroGrid({ niftyVerdict, bnVerdict, niftyReasons, bnReasons }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
      <VerdictHero index="NIFTY" verdict={niftyVerdict} reasons={niftyReasons} />
      <VerdictHero index="BANKNIFTY" verdict={bnVerdict} reasons={bnReasons} />
    </div>
  );
}

export default VerdictHero;
