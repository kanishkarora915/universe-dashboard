import { useState } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

const ICONS = {
  dashboard: "\u25C8", // ◈
  oi: "\u27D0",       // ⟐
  pnl: "\u25D0",      // ◐
  reports: "\u2630",  // ☰
  autopsy: "\u25CE",  // ◎
  times: "\u23F1",    // ⏱
  settings: "\u2699", // ⚙
};

const TABS = [
  { id: "dashboard", label: "Dashboard", icon: ICONS.dashboard, hotkey: "1" },
  { id: "oi", label: "OI Chain", icon: ICONS.oi, hotkey: "2" },
  { id: "pnl", label: "P&L", icon: ICONS.pnl, hotkey: "3" },
  { id: "reports", label: "Reports", icon: ICONS.reports, hotkey: "4" },
  { id: "autopsy", label: "Autopsy", icon: ICONS.autopsy, hotkey: "5" },
  { id: "times", label: "Times", icon: ICONS.times, hotkey: "6" },
];

function SidebarButton({ tab, active, onClick, badge, flashing }) {
  const { theme } = useTheme();
  const [hover, setHover] = useState(false);

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ position: "relative" }}
    >
      <button
        onClick={onClick}
        title={`${tab.label} (${tab.hotkey})`}
        style={{
          width: 40,
          height: 40,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: active ? theme.SURFACE_ACTIVE : "transparent",
          color: active ? theme.ACCENT : hover ? theme.TEXT : theme.TEXT_MUTED,
          border: "none",
          borderRadius: RADIUS.MD,
          cursor: "pointer",
          fontSize: 18,
          transition: TRANSITION.FAST,
          position: "relative",
          margin: "0 auto",
          animation: flashing ? "sidebar-pulse 1s ease-in-out 3" : undefined,
        }}
      >
        {tab.icon}
        {active && (
          <div
            style={{
              position: "absolute",
              left: -8,
              top: 8,
              bottom: 8,
              width: 2,
              background: theme.ACCENT,
              borderRadius: 1,
            }}
          />
        )}
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

      {/* Tooltip on hover */}
      {hover && (
        <div
          style={{
            position: "absolute",
            left: 52,
            top: "50%",
            transform: "translateY(-50%)",
            background: theme.SURFACE_HI,
            border: `1px solid ${theme.BORDER}`,
            color: theme.TEXT,
            padding: "4px 8px",
            borderRadius: RADIUS.SM,
            fontSize: TEXT_SIZE.MICRO,
            fontWeight: TEXT_WEIGHT.BOLD,
            fontFamily: FONT.UI,
            whiteSpace: "nowrap",
            zIndex: Z.TOOLTIP,
            pointerEvents: "none",
            boxShadow: theme.SHADOW,
          }}
        >
          {tab.label}
          <span style={{ color: theme.TEXT_DIM, marginLeft: 6 }}>{tab.hotkey}</span>
        </div>
      )}
    </div>
  );
}

function WatchlistItem({ strike, onClick, onRemove }) {
  const { theme } = useTheme();
  const [hover, setHover] = useState(false);

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ position: "relative" }}
    >
      <button
        onClick={onClick}
        title={`${strike.index} ${strike.strike} ${strike.type || ""}`}
        style={{
          width: 40,
          height: 28,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "transparent",
          color: theme.TEXT_MUTED,
          border: "none",
          borderRadius: RADIUS.SM,
          cursor: "pointer",
          fontSize: 10,
          fontFamily: FONT.MONO,
          fontWeight: TEXT_WEIGHT.BOLD,
          transition: TRANSITION.FAST,
          margin: "0 auto",
        }}
        onMouseOver={(e) => (e.currentTarget.style.color = theme.TEXT)}
        onMouseOut={(e) => (e.currentTarget.style.color = theme.TEXT_MUTED)}
      >
        {String(strike.strike).slice(-3)}
      </button>
      {hover && (
        <div
          style={{
            position: "absolute",
            left: 52,
            top: "50%",
            transform: "translateY(-50%)",
            background: theme.SURFACE_HI,
            border: `1px solid ${theme.BORDER}`,
            color: theme.TEXT,
            padding: "4px 8px",
            borderRadius: RADIUS.SM,
            fontSize: TEXT_SIZE.MICRO,
            fontFamily: FONT.MONO,
            whiteSpace: "nowrap",
            zIndex: Z.TOOLTIP,
            pointerEvents: "none",
            boxShadow: theme.SHADOW,
          }}
        >
          {strike.index} {strike.strike} {strike.type || ""}
        </div>
      )}
    </div>
  );
}

export default function Sidebar({ activeTab, onTabChange, tabBadges = {}, flashingTab, watchlist = [], onWatchlistClick }) {
  const { theme } = useTheme();

  return (
    <>
      <style>{`
        @keyframes sidebar-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.15); opacity: 0.7; }
        }
      `}</style>
      <aside
        style={{
          width: 48,
          background: theme.SURFACE,
          borderRight: `1px solid ${theme.BORDER}`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: `${SPACE.MD}px 0`,
          gap: SPACE.XS,
          flexShrink: 0,
          zIndex: Z.STICKY,
        }}
      >
        {TABS.map((tab) => (
          <SidebarButton
            key={tab.id}
            tab={tab}
            active={activeTab === tab.id}
            onClick={() => onTabChange(tab.id)}
            badge={tabBadges[tab.id] || 0}
            flashing={flashingTab === tab.id}
          />
        ))}

        {watchlist.length > 0 && (
          <>
            <div
              style={{
                width: 24,
                height: 1,
                background: theme.BORDER,
                margin: `${SPACE.SM}px 0`,
              }}
            />
            <div
              style={{
                color: theme.TEXT_DIM,
                fontSize: 8,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 1,
                writingMode: "vertical-rl",
                transform: "rotate(180deg)",
                padding: `${SPACE.SM}px 0`,
              }}
            >
              WATCH
            </div>
            {watchlist.slice(0, 6).map((strike, i) => (
              <WatchlistItem
                key={i}
                strike={strike}
                onClick={() => onWatchlistClick && onWatchlistClick(strike)}
              />
            ))}
          </>
        )}

        <div style={{ flex: 1 }} />

        <SidebarButton
          tab={{ id: "settings", label: "Settings", icon: ICONS.settings, hotkey: "," }}
          active={activeTab === "settings"}
          onClick={() => onTabChange("settings")}
        />
      </aside>
    </>
  );
}
