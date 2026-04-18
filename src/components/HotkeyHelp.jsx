import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, Z } from "../theme";

const SHORTCUTS = [
  { section: "Navigation", items: [
    { keys: ["⌘K", "Ctrl+K"], desc: "Search strikes / command palette" },
    { keys: ["1"], desc: "Dashboard tab" },
    { keys: ["2"], desc: "OI Change tab" },
    { keys: ["3"], desc: "P&L tab" },
    { keys: ["4"], desc: "Reports tab" },
    { keys: ["5"], desc: "Autopsy tab" },
    { keys: ["6"], desc: "Trading Times tab" },
    { keys: ["Esc"], desc: "Close modal / clear search" },
  ]},
  { section: "View", items: [
    { keys: ["⌘Shift+L", "Ctrl+Shift+L"], desc: "Toggle dark / light theme" },
    { keys: ["R"], desc: "Refresh data (reloads page)" },
  ]},
  { section: "Strikes", items: [
    { keys: ["⌘W", "Ctrl+W"], desc: "Close active strike tab" },
    { keys: ["☆"], desc: "Pin strike to watchlist (from search / detail view)" },
    { keys: ["B"], desc: "⚔ Battle Station — compare pinned strikes" },
    { keys: ["⌘Shift+B", "Ctrl+Shift+B"], desc: "Force open Battle Station" },
    { keys: ["⌘Shift+R", "Ctrl+Shift+R"], desc: "🔮 Replay Mode" },
  ]},
  { section: "Alerts", items: [
    { keys: ["⌘Shift+A", "Ctrl+Shift+A"], desc: "Toggle alerts drawer" },
    { keys: ["⌘Shift+M", "Ctrl+Shift+M"], desc: "Mute / unmute all sounds" },
  ]},
  { section: "Engine Control", items: [
    { keys: ["⌘Shift+E", "Ctrl+Shift+E"], desc: "⚙ Engine Control — toggle which engines decide trades" },
  ]},
  { section: "Help", items: [
    { keys: ["?"], desc: "Show this shortcut guide" },
  ]},
];

function Key({ label, theme }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 7px",
        background: theme.SURFACE_HI,
        border: `1px solid ${theme.BORDER}`,
        borderRadius: RADIUS.SM,
        color: theme.TEXT,
        fontSize: TEXT_SIZE.MICRO,
        fontFamily: FONT.MONO,
        fontWeight: TEXT_WEIGHT.BOLD,
        minWidth: 20,
        textAlign: "center",
      }}
    >
      {label}
    </span>
  );
}

export default function HotkeyHelp({ isOpen, onClose }) {
  const { theme } = useTheme();
  if (!isOpen) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: theme.OVERLAY,
        zIndex: Z.MODAL,
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        backdropFilter: "blur(4px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(640px, 90vw)",
          maxHeight: "80vh",
          overflowY: "auto",
          background: theme.SURFACE,
          border: `1px solid ${theme.BORDER_HI}`,
          borderRadius: RADIUS.LG,
          padding: SPACE.XL,
          boxShadow: theme.SHADOW_HI,
        }}
      >
        {/* Header with feature label */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: SPACE.LG,
          }}
        >
          <div>
            <div
              style={{
                color: theme.ACCENT,
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 2,
                textTransform: "uppercase",
                fontFamily: FONT.UI,
                marginBottom: 2,
              }}
            >
              ⌨ Shortcuts
            </div>
            <div
              style={{
                color: theme.TEXT,
                fontSize: TEXT_SIZE.H1,
                fontWeight: TEXT_WEIGHT.BLACK,
                letterSpacing: 1,
                fontFamily: FONT.UI,
              }}
            >
              Keyboard Shortcuts
            </div>
            <div
              style={{
                color: theme.TEXT_DIM,
                fontSize: TEXT_SIZE.MICRO,
                marginTop: 4,
              }}
            >
              All shortcuts below are wired and active across the dashboard
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: `1px solid ${theme.BORDER}`,
              color: theme.TEXT_MUTED,
              borderRadius: RADIUS.SM,
              padding: "4px 10px",
              cursor: "pointer",
              fontSize: 14,
            }}
          >
            ×
          </button>
        </div>

        {SHORTCUTS.map((section) => (
          <div key={section.section} style={{ marginBottom: SPACE.LG }}>
            <div
              style={{
                color: theme.ACCENT,
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 1.5,
                textTransform: "uppercase",
                marginBottom: SPACE.SM,
              }}
            >
              {section.section}
            </div>
            {section.items.map((item, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  padding: `${SPACE.SM}px 0`,
                  borderBottom: `1px solid ${theme.BORDER}44`,
                }}
              >
                <div style={{ display: "flex", gap: SPACE.XS, minWidth: 160, flexWrap: "wrap" }}>
                  {item.keys.map((k, j) => (
                    <span key={j} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <Key label={k} theme={theme} />
                      {j < item.keys.length - 1 && (
                        <span style={{ color: theme.TEXT_DIM, fontSize: 10 }}>or</span>
                      )}
                    </span>
                  ))}
                </div>
                <div style={{ color: theme.TEXT, fontSize: TEXT_SIZE.BODY, flex: 1 }}>{item.desc}</div>
              </div>
            ))}
          </div>
        ))}

        <div
          style={{
            color: theme.TEXT_DIM,
            fontSize: TEXT_SIZE.MICRO,
            textAlign: "center",
            marginTop: SPACE.LG,
            paddingTop: SPACE.LG,
            borderTop: `1px solid ${theme.BORDER}`,
          }}
        >
          Press <Key label="?" theme={theme} /> anytime to reopen this guide
        </div>
      </div>
    </div>
  );
}
