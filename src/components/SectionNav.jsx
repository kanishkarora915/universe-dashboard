import { useState, useEffect } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

/**
 * Compact section-based navigation to replace the cluttered 19-tab horizontal bar.
 * Top row: 6 group pills (HOME / MARKET / ANALYSIS / INTEL / TRADE / SYS)
 * Bottom row: Sub-tabs for the currently active group only
 *
 * Visual: accent beam under active section, gradient underline on sub-tabs,
 * subtle glow. Auto-derives which group is active from activeTab.
 */

const SECTION_ICONS = {
  Home: "◈",
  Market: "⚡",
  Analysis: "◎",
  Intelligence: "◉",
  Trading: "◐",
  System: "☰",
};

export default function SectionNav({ groups, activeTab, onTabChange, rightAction }) {
  const { theme } = useTheme();
  const [hoveredGroup, setHoveredGroup] = useState(null);

  // Detect which group currently holds activeTab
  const activeGroup = groups.find((g) => g.tabs.some((t) => t.id === activeTab));
  const [selectedGroup, setSelectedGroup] = useState(activeGroup?.group || groups[0]?.group);

  // Sync selectedGroup with activeTab when tab changes from elsewhere (hotkey etc)
  useEffect(() => {
    if (activeGroup && activeGroup.group !== selectedGroup) {
      setSelectedGroup(activeGroup.group);
    }
  }, [activeTab, activeGroup, selectedGroup]);

  const currentGroup = groups.find((g) => g.group === selectedGroup) || groups[0];

  return (
    <>
      <style>{`
        @keyframes beam-slide {
          from { opacity: 0; transform: scaleX(0.4); }
          to   { opacity: 1; transform: scaleX(1); }
        }
        @keyframes breathe {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.75; }
        }
      `}</style>

      <div
        style={{
          background: theme.SURFACE,
          borderBottom: `1px solid ${theme.BORDER}`,
          position: "relative",
          flexShrink: 0,
        }}
      >
        {/* Top row: Section pills */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            padding: `4px ${SPACE.MD}px`,
            gap: 2,
            borderBottom: `1px solid ${theme.BORDER}44`,
            overflowX: "auto",
          }}
        >
          {groups.map((g) => {
            const isActive = g.group === selectedGroup;
            const isHovered = hoveredGroup === g.group;
            const hasActiveTab = g.tabs.some((t) => t.id === activeTab);

            return (
              <button
                key={g.group}
                onClick={() => {
                  setSelectedGroup(g.group);
                  // If current activeTab not in this group, switch to group's first tab
                  if (!g.tabs.some((t) => t.id === activeTab)) {
                    onTabChange(g.tabs[0].id);
                  }
                }}
                onMouseEnter={() => setHoveredGroup(g.group)}
                onMouseLeave={() => setHoveredGroup(null)}
                style={{
                  position: "relative",
                  background: isActive ? theme.ACCENT_DIM : "transparent",
                  color: isActive ? theme.ACCENT : hasActiveTab ? theme.TEXT : theme.TEXT_MUTED,
                  border: "none",
                  borderRadius: RADIUS.MD,
                  padding: "5px 12px",
                  fontSize: TEXT_SIZE.MICRO,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  letterSpacing: 1.2,
                  textTransform: "uppercase",
                  fontFamily: FONT.UI,
                  cursor: "pointer",
                  transition: TRANSITION.FAST,
                  whiteSpace: "nowrap",
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                  boxShadow: isActive ? `0 0 12px ${theme.ACCENT}33` : "none",
                }}
              >
                <span style={{ fontSize: 11, opacity: isActive ? 1 : 0.7 }}>
                  {SECTION_ICONS[g.group] || "•"}
                </span>
                {g.group}
                {hasActiveTab && !isActive && (
                  <span
                    style={{
                      width: 4,
                      height: 4,
                      borderRadius: "50%",
                      background: theme.ACCENT,
                      marginLeft: 2,
                      animation: "breathe 2s ease-in-out infinite",
                    }}
                  />
                )}
              </button>
            );
          })}

          <div style={{ flex: 1 }} />

          {rightAction}
        </div>

        {/* Bottom row: Sub-tabs of selected section */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 0,
            padding: `0 ${SPACE.MD}px`,
            overflowX: "auto",
            minHeight: 34,
          }}
        >
          {currentGroup?.tabs.map((t) => {
            const isActive = t.id === activeTab;
            return (
              <button
                key={t.id}
                onClick={() => onTabChange(t.id)}
                style={{
                  position: "relative",
                  background: "transparent",
                  color: isActive ? theme.TEXT : theme.TEXT_MUTED,
                  border: "none",
                  padding: "8px 12px",
                  fontSize: TEXT_SIZE.BODY,
                  fontWeight: isActive ? TEXT_WEIGHT.BOLD : TEXT_WEIGHT.MED,
                  fontFamily: FONT.UI,
                  cursor: "pointer",
                  transition: TRANSITION.FAST,
                  whiteSpace: "nowrap",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
                onMouseOver={(e) => {
                  if (!isActive) e.currentTarget.style.color = theme.TEXT;
                }}
                onMouseOut={(e) => {
                  if (!isActive) e.currentTarget.style.color = theme.TEXT_MUTED;
                }}
              >
                <span style={{ fontSize: 11, opacity: 0.8 }}>{t.icon}</span>
                <span>{t.label}</span>
                {isActive && (
                  <span
                    style={{
                      position: "absolute",
                      left: "10%",
                      right: "10%",
                      bottom: 0,
                      height: 2,
                      background: `linear-gradient(90deg, transparent, ${theme.ACCENT}, transparent)`,
                      boxShadow: `0 0 8px ${theme.ACCENT}`,
                      animation: "beam-slide 200ms ease-out",
                      transformOrigin: "center",
                    }}
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}
