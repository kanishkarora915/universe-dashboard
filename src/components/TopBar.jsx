import { useState, useEffect, useRef } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

function MiniTicker({ label, price, change, pct, theme }) {
  const isUp = change >= 0;
  const color = change === 0 ? theme.TEXT_MUTED : isUp ? theme.GREEN : theme.RED;
  const arrow = isUp ? "\u2191" : "\u2193";
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: SPACE.XS }}>
      <span
        style={{
          color: theme.TEXT_MUTED,
          fontSize: 10,
          fontWeight: TEXT_WEIGHT.BOLD,
          letterSpacing: 1,
          fontFamily: FONT.UI,
        }}
      >
        {label}
      </span>
      <span
        style={{
          color: theme.TEXT,
          fontSize: 14,
          fontWeight: TEXT_WEIGHT.BOLD,
          fontFamily: FONT.MONO,
        }}
      >
        {price ? price.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—"}
      </span>
      {change !== null && change !== undefined && (
        <span
          style={{
            color,
            fontSize: 11,
            fontWeight: TEXT_WEIGHT.BOLD,
            fontFamily: FONT.MONO,
          }}
        >
          {arrow}
          {Math.abs(pct || 0).toFixed(2)}%
        </span>
      )}
    </div>
  );
}

function LiveIndicator({ theme, status = "live" }) {
  // status: live | lag | stale | disconnected
  const color =
    status === "live"
      ? theme.GREEN
      : status === "lag"
      ? theme.AMBER
      : status === "stale"
      ? theme.AMBER
      : theme.RED;
  const label =
    status === "live" ? "LIVE" : status === "lag" ? "LAG" : status === "stale" ? "STALE" : "OFFLINE";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        background: theme.SURFACE_HI,
        border: `1px solid ${theme.BORDER}`,
        borderRadius: RADIUS.SM,
      }}
      title={`Data status: ${label}`}
    >
      <div
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          animation: status === "live" ? "live-pulse 2s ease-in-out infinite" : undefined,
          boxShadow: status === "live" ? `0 0 8px ${color}` : undefined,
        }}
      />
      <span
        style={{
          color,
          fontSize: 10,
          fontWeight: TEXT_WEIGHT.BOLD,
          letterSpacing: 1,
          fontFamily: FONT.UI,
        }}
      >
        {label}
      </span>
    </div>
  );
}

function IconButton({ icon, onClick, title, badge, color, theme }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={title}
      style={{
        position: "relative",
        width: 32,
        height: 32,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: hover ? theme.SURFACE_HI : "transparent",
        color: color || (hover ? theme.TEXT : theme.TEXT_MUTED),
        border: "none",
        borderRadius: RADIUS.SM,
        cursor: "pointer",
        fontSize: 16,
        transition: TRANSITION.FAST,
      }}
    >
      {icon}
      {badge > 0 && (
        <div
          style={{
            position: "absolute",
            top: 2,
            right: 2,
            minWidth: 14,
            height: 14,
            padding: "0 3px",
            background: theme.RED,
            color: "#fff",
            borderRadius: 7,
            fontSize: 9,
            fontWeight: TEXT_WEIGHT.BOLD,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: FONT.MONO,
          }}
        >
          {badge > 99 ? "99+" : badge}
        </div>
      )}
    </button>
  );
}

export default function TopBar({
  nifty,
  banknifty,
  vix,
  pcr,
  liveStatus = "live",
  onSearchClick,
  onAlertsClick,
  alertCount = 0,
  onThemeToggle,
  onSettingsClick,
  onHelpClick,
}) {
  const { theme, isDark } = useTheme();
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const timeStr = now.toLocaleTimeString("en-IN", { hour12: false });

  return (
    <>
      <style>{`
        @keyframes live-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(1.2); }
        }
      `}</style>
      <header
        style={{
          height: 56,
          background: theme.SCRIM,
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderBottom: `1px solid ${theme.BORDER}`,
          display: "flex",
          alignItems: "center",
          padding: `0 ${SPACE.LG}px`,
          gap: SPACE.LG,
          flexShrink: 0,
          position: "sticky",
          top: 0,
          zIndex: Z.STICKY,
        }}
      >
        {/* Logo */}
        <div style={{ display: "flex", alignItems: "baseline", gap: SPACE.SM, minWidth: 180 }}>
          <span
            style={{
              color: theme.ACCENT,
              fontSize: 18,
              fontWeight: TEXT_WEIGHT.BLACK,
              fontFamily: FONT.MONO,
            }}
          >
            \u25B8
          </span>
          <div>
            <div
              style={{
                color: theme.TEXT,
                fontSize: 14,
                fontWeight: TEXT_WEIGHT.BLACK,
                fontFamily: FONT.MONO,
                letterSpacing: 2,
                lineHeight: 1,
              }}
            >
              UNIVERSE
            </div>
            <div
              style={{
                color: theme.TEXT_DIM,
                fontSize: 8,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 1.5,
                fontFamily: FONT.UI,
                marginTop: 2,
              }}
            >
              by Kanishk Arora
            </div>
          </div>
        </div>

        {/* Ticker row */}
        <div style={{ display: "flex", alignItems: "center", gap: SPACE.LG, flex: 1 }}>
          <MiniTicker
            label="NIFTY"
            price={nifty?.ltp}
            change={nifty?.change}
            pct={nifty?.changePct}
            theme={theme}
          />
          <MiniTicker
            label="BN"
            price={banknifty?.ltp}
            change={banknifty?.change}
            pct={banknifty?.changePct}
            theme={theme}
          />
          {vix != null && (
            <div style={{ display: "flex", alignItems: "baseline", gap: SPACE.XS }}>
              <span
                style={{
                  color: theme.TEXT_MUTED,
                  fontSize: 10,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  letterSpacing: 1,
                  fontFamily: FONT.UI,
                }}
              >
                VIX
              </span>
              <span
                style={{
                  color: vix > 20 ? theme.AMBER : theme.TEXT,
                  fontSize: 13,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  fontFamily: FONT.MONO,
                }}
              >
                {vix.toFixed(2)}
              </span>
            </div>
          )}
          {pcr != null && (
            <div style={{ display: "flex", alignItems: "baseline", gap: SPACE.XS }}>
              <span
                style={{
                  color: theme.TEXT_MUTED,
                  fontSize: 10,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  letterSpacing: 1,
                  fontFamily: FONT.UI,
                }}
              >
                PCR
              </span>
              <span
                style={{
                  color: pcr > 1.3 ? theme.GREEN : pcr < 0.7 ? theme.RED : theme.TEXT,
                  fontSize: 13,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  fontFamily: FONT.MONO,
                }}
              >
                {pcr.toFixed(2)}
              </span>
            </div>
          )}
        </div>

        {/* Search trigger */}
        <button
          onClick={onSearchClick}
          style={{
            display: "flex",
            alignItems: "center",
            gap: SPACE.SM,
            padding: `6px ${SPACE.MD}px`,
            background: theme.SURFACE_HI,
            color: theme.TEXT_MUTED,
            border: `1px solid ${theme.BORDER}`,
            borderRadius: RADIUS.MD,
            cursor: "pointer",
            fontSize: TEXT_SIZE.BODY,
            fontFamily: FONT.UI,
            transition: TRANSITION.FAST,
            minWidth: 240,
            textAlign: "left",
          }}
          onMouseOver={(e) => {
            e.currentTarget.style.borderColor = theme.BORDER_HI;
            e.currentTarget.style.color = theme.TEXT;
          }}
          onMouseOut={(e) => {
            e.currentTarget.style.borderColor = theme.BORDER;
            e.currentTarget.style.color = theme.TEXT_MUTED;
          }}
        >
          <span style={{ fontSize: 14 }}>\u2315</span>
          <span style={{ flex: 1 }}>Search strike, action...</span>
          <span
            style={{
              fontSize: 10,
              padding: "2px 6px",
              background: theme.BG,
              borderRadius: RADIUS.XS,
              fontFamily: FONT.MONO,
              color: theme.TEXT_DIM,
            }}
          >
            \u2318K
          </span>
        </button>

        {/* Right controls */}
        <div style={{ display: "flex", alignItems: "center", gap: SPACE.XS }}>
          <span
            style={{
              color: theme.TEXT_DIM,
              fontSize: 11,
              fontFamily: FONT.MONO,
              marginRight: SPACE.SM,
            }}
          >
            {timeStr}
          </span>

          <IconButton
            icon="?"
            onClick={onHelpClick}
            title="Keyboard shortcuts (?)"
            theme={theme}
          />

          <IconButton
            icon={isDark ? "\u2600" : "\u263D"}
            onClick={onThemeToggle}
            title={isDark ? "Switch to light" : "Switch to dark"}
            theme={theme}
          />

          <IconButton
            icon="\u25CB"
            onClick={onAlertsClick}
            title="Alerts"
            badge={alertCount}
            theme={theme}
          />

          <IconButton
            icon="\u2699"
            onClick={onSettingsClick}
            title="Settings"
            theme={theme}
          />

          <LiveIndicator theme={theme} status={liveStatus} />
        </div>
      </header>
    </>
  );
}
