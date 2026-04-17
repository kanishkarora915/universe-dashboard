import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

const ICONS = {
  CRITICAL: "\u26A0",
  WARNING: "\u26A0",
  INFO: "\u2139",
  AMBIENT: "\u2218",
};

const SEVERITY_COLOR_KEYS = {
  CRITICAL: "RED",
  WARNING: "AMBER",
  INFO: "ACCENT",
  AMBIENT: "TEXT_MUTED",
};

function severityColor(severity, theme) {
  const key = SEVERITY_COLOR_KEYS[severity] || "ACCENT";
  return theme[key];
}

function Toast({ alert, onDismiss, onClick, theme }) {
  const color = severityColor(alert.severity, theme);
  return (
    <div
      onClick={() => onClick && onClick(alert)}
      style={{
        minWidth: 280,
        maxWidth: 360,
        background: theme.SURFACE,
        border: `1px solid ${theme.BORDER_HI}`,
        borderLeft: `3px solid ${color}`,
        borderRadius: RADIUS.LG,
        padding: `${SPACE.MD}px ${SPACE.LG}px`,
        boxShadow: theme.SHADOW_HI,
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: SPACE.XS,
        animation: "toast-slide-in 300ms cubic-bezier(0.22, 1, 0.36, 1)",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: SPACE.SM }}>
        <span style={{ color, fontSize: 14 }}>{ICONS[alert.severity] || "\u2022"}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              color,
              fontSize: TEXT_SIZE.MICRO,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 1.5,
              textTransform: "uppercase",
            }}
          >
            {alert.alert_type.replace(/_/g, " ")}
          </div>
          <div
            style={{
              color: theme.TEXT,
              fontSize: TEXT_SIZE.BODY,
              fontWeight: TEXT_WEIGHT.BOLD,
              marginTop: 2,
              wordBreak: "break-word",
            }}
          >
            {alert.title}
          </div>
          {alert.message && (
            <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, marginTop: 2 }}>
              {alert.message}
            </div>
          )}
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDismiss(alert.toastId);
          }}
          style={{
            background: "transparent",
            border: "none",
            color: theme.TEXT_DIM,
            fontSize: 16,
            cursor: "pointer",
            padding: 0,
            lineHeight: 1,
          }}
        >
          \u00D7
        </button>
      </div>
    </div>
  );
}

export default function AlertToastStack({ toasts = [], onDismiss, onClickAlert }) {
  const { theme } = useTheme();

  return (
    <>
      <style>{`
        @keyframes toast-slide-in {
          from { transform: translateX(120%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
      <div
        style={{
          position: "fixed",
          bottom: SPACE.LG,
          right: SPACE.LG,
          display: "flex",
          flexDirection: "column-reverse",
          gap: SPACE.SM,
          zIndex: Z.TOAST,
          pointerEvents: "none",
        }}
      >
        {toasts.slice(-3).map((t) => (
          <div key={t.toastId} style={{ pointerEvents: "auto" }}>
            <Toast alert={t} onDismiss={onDismiss} onClick={onClickAlert} theme={theme} />
          </div>
        ))}
      </div>
    </>
  );
}
