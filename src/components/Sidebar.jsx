import { useState } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";
import { prefetchMany } from "../utils/prefetch";

// N5: Hover-intent prefetch — tab id → endpoints to warm
// Only known-valid endpoints to avoid 404 storms during prefetch.
const TAB_PREFETCH = {
  pnl:      ["/api/positions/health"],
  scalper:  ["/api/scalper/trades/open", "/api/scalper/stats",
             "/api/reversal/live"],
  reversal: ["/api/reversal/live"],
  ttimes:   ["/api/times/events?idx=NIFTY"],
};

// Tab IDs aligned with Universe.jsx (oichange, ttimes — not oi, times)
const TABS = [
  { id: "dashboard", label: "Dashboard", icon: "◈", hotkey: "1" },
  { id: "oichange",  label: "OI Change", icon: "⟐", hotkey: "2" },
  { id: "pnl",       label: "P&L",       icon: "◐", hotkey: "3" },
  { id: "reports",   label: "Reports",   icon: "☰", hotkey: "4" },
  { id: "autopsy",   label: "Autopsy",   icon: "◎", hotkey: "5" },
  { id: "ttimes",    label: "Times",     icon: "⏱", hotkey: "6" },
];

function SidebarButton({ tab, active, onClick, badge, flashing, theme }) {
  const [hover, setHover] = useState(false);

  // N5: warm SWR cache for this tab's endpoints when user hovers.
  // By the time they click, data is loaded.
  const handleEnter = () => {
    setHover(true);
    const paths = TAB_PREFETCH[tab.id];
    if (paths && !active) prefetchMany(paths);
  };

  return (
    <div
      onMouseEnter={handleEnter}
      onMouseLeave={() => setHover(false)}
      style={{ position: "relative" }}
    >
      <button
        onClick={onClick}
        title={`${tab.label} (${tab.hotkey})`}
        aria-label={`${tab.label}${tab.hotkey ? ` — shortcut ${tab.hotkey}` : ""}`}
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

      {hover && (
        <div
          style={{
            position: "absolute",
            left: 52,
            top: "50%",
            transform: "translateY(-50%)",
            background: theme.SURFACE_HI,
            border: `1px solid ${theme.ACCENT}44`,
            color: theme.TEXT,
            padding: "6px 12px",
            borderRadius: RADIUS.MD,
            fontSize: TEXT_SIZE.BODY,
            fontWeight: TEXT_WEIGHT.BOLD,
            fontFamily: FONT.UI,
            whiteSpace: "nowrap",
            zIndex: Z.TOOLTIP,
            pointerEvents: "none",
            boxShadow: theme.SHADOW_HI,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {tab.label}
          {tab.hotkey && (
            <span
              style={{
                color: theme.ACCENT,
                padding: "1px 6px",
                background: theme.ACCENT_DIM,
                borderRadius: 3,
                fontFamily: FONT.MONO,
                fontSize: 10,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 0.5,
              }}
            >
              {tab.hotkey}
            </span>
          )}
          {/* Arrow pointing left to the icon */}
          <div
            style={{
              position: "absolute",
              left: -5,
              top: "50%",
              transform: "translateY(-50%)",
              width: 0,
              height: 0,
              borderTop: "5px solid transparent",
              borderBottom: "5px solid transparent",
              borderRight: `5px solid ${theme.SURFACE_HI}`,
            }}
          />
        </div>
      )}
    </div>
  );
}

function WatchlistItem({ strike, onClick, onUnpin, theme }) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ position: "relative" }}
    >
      <button
        onClick={onClick}
        aria-label={`Open ${strike.index} ${strike.strike} ${strike.type || ""} details`}
        style={{
          width: 36,
          height: 24,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: hover ? theme.SURFACE_HI : "transparent",
          color: strike.type === "CE" ? theme.GREEN : strike.type === "PE" ? theme.RED : theme.TEXT_MUTED,
          border: `1px solid ${theme.BORDER}`,
          borderRadius: RADIUS.XS,
          cursor: "pointer",
          fontSize: 9,
          fontFamily: FONT.MONO,
          fontWeight: TEXT_WEIGHT.BOLD,
          transition: TRANSITION.FAST,
          margin: "0 auto",
        }}
      >
        {String(strike.strike).slice(-3)}
      </button>
      {/* Unpin × button — visible on hover */}
      {hover && onUnpin && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onUnpin(strike);
          }}
          aria-label={`Unpin ${strike.index} ${strike.strike} ${strike.type || ""}`}
          title={`Unpin ${strike.index} ${strike.strike} ${strike.type || ""}`}
          style={{
            position: "absolute",
            top: -6,
            right: -2,
            width: 14,
            height: 14,
            background: theme.RED,
            color: "#fff",
            border: "none",
            borderRadius: "50%",
            cursor: "pointer",
            fontSize: 10,
            fontWeight: TEXT_WEIGHT.BOLD,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 0,
            lineHeight: 1,
            boxShadow: theme.SHADOW,
            zIndex: Z.TOOLTIP,
          }}
        >
          ×
        </button>
      )}
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
          {strike.index} {strike.strike} {strike.type || ""} — click to open
        </div>
      )}
    </div>
  );
}

export default function Sidebar({
  activeTab,
  onTabChange,
  tabBadges = {},
  flashingTab,
  watchlist = [],
  onWatchlistClick,
  onWatchlistUnpin,
  onWatchlistAdd,
  onReplayClick,
  onBattleClick,
  battleEnabled,
}) {
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
          overflowY: "auto",
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
            theme={theme}
          />
        ))}

        {/* Quick-action buttons — replay + battle */}
        {(onReplayClick || onBattleClick) && (
          <div
            style={{
              width: 28,
              height: 1,
              background: theme.BORDER,
              margin: `${SPACE.SM}px 0`,
            }}
          />
        )}
        {onReplayClick && (
          <SidebarButton
            tab={{ id: "replay", label: "Replay Mode (⌘Shift+R)", icon: "🔮", hotkey: "" }}
            active={false}
            onClick={onReplayClick}
            theme={theme}
          />
        )}
        {onBattleClick && (
          <SidebarButton
            tab={{ id: "battle", label: battleEnabled ? "Battle Station (B)" : "Pin 2+ strikes to compare", icon: "⚔", hotkey: "B" }}
            active={false}
            onClick={battleEnabled ? onBattleClick : undefined}
            theme={theme}
          />
        )}

        {/* Watchlist: always show header + Add button when callback provided */}
        {(watchlist.length > 0 || onWatchlistAdd) && (
          <>
            <div
              style={{
                width: 28,
                height: 1,
                background: theme.BORDER,
                margin: `${SPACE.SM}px 0 ${SPACE.XS}px`,
              }}
            />
            <div
              style={{
                color: theme.AMBER,
                fontSize: 12,
                fontWeight: TEXT_WEIGHT.BOLD,
                padding: `0 0 ${SPACE.XS}px`,
                fontFamily: FONT.UI,
              }}
              title={`Watchlist — ${watchlist.length} pinned strike(s)`}
              aria-label={`Watchlist with ${watchlist.length} pinned strikes`}
            >
              ★
            </div>

            {watchlist.slice(0, 8).map((strike, i) => (
              <WatchlistItem
                key={`${strike.index}-${strike.strike}-${strike.type || ""}-${i}`}
                strike={strike}
                onClick={() => onWatchlistClick && onWatchlistClick(strike)}
                onUnpin={onWatchlistUnpin}
                theme={theme}
              />
            ))}

            {/* Quick-add + button — opens search to pin more */}
            {onWatchlistAdd && watchlist.length < 8 && (
              <button
                onClick={onWatchlistAdd}
                aria-label="Add strike to watchlist — opens search"
                title="Pin more strikes (⌘K)"
                style={{
                  width: 36,
                  height: 24,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: "transparent",
                  color: theme.ACCENT,
                  border: `1px dashed ${theme.ACCENT}66`,
                  borderRadius: RADIUS.XS,
                  cursor: "pointer",
                  fontSize: 14,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  transition: TRANSITION.FAST,
                  margin: "0 auto",
                }}
                onMouseOver={(e) => {
                  e.currentTarget.style.background = theme.ACCENT_DIM;
                  e.currentTarget.style.borderStyle = "solid";
                }}
                onMouseOut={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.borderStyle = "dashed";
                }}
              >
                +
              </button>
            )}
          </>
        )}

        <div style={{ flex: 1 }} />
      </aside>
    </>
  );
}
