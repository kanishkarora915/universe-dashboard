import { useState, useMemo } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "unread", label: "Unread" },
  { id: "CRITICAL", label: "Critical" },
  { id: "WARNING", label: "Warnings" },
  { id: "INFO", label: "Info" },
  { id: "pinned", label: "Pinned" },
];

const SEVERITY_COLOR = {
  CRITICAL: "RED",
  WARNING: "AMBER",
  INFO: "ACCENT",
  AMBIENT: "TEXT_MUTED",
};

function AlertRow({ alert, onPin, onDismiss, onClick, theme }) {
  const color = theme[SEVERITY_COLOR[alert.severity] || "ACCENT"];
  const time = new Date(alert.created_at).toLocaleTimeString("en-IN", { hour12: false }).slice(0, 5);

  return (
    <div
      onClick={() => onClick && onClick(alert)}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: SPACE.MD,
        padding: SPACE.MD,
        borderBottom: `1px solid ${theme.BORDER}`,
        background: alert.read ? "transparent" : theme.SURFACE_HI,
        opacity: alert.read ? 0.7 : 1,
        cursor: "pointer",
        transition: TRANSITION.FAST,
        borderLeft: `2px solid ${alert.read ? "transparent" : color}`,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = theme.SURFACE_ACTIVE)}
      onMouseLeave={(e) =>
        (e.currentTarget.style.background = alert.read ? "transparent" : theme.SURFACE_HI)
      }
    >
      <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, fontFamily: FONT.MONO, minWidth: 40 }}>
        {time}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            color,
            fontSize: 9,
            fontWeight: TEXT_WEIGHT.BOLD,
            letterSpacing: 1.2,
            textTransform: "uppercase",
          }}
        >
          {alert.alert_type.replace(/_/g, " ")}
        </div>
        <div
          style={{
            color: theme.TEXT,
            fontSize: TEXT_SIZE.BODY,
            fontWeight: alert.read ? TEXT_WEIGHT.MED : TEXT_WEIGHT.BOLD,
            marginTop: 2,
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
      <div style={{ display: "flex", gap: 2 }}>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onPin(alert.id, !alert.pinned);
          }}
          style={{
            background: "transparent",
            border: "none",
            color: alert.pinned ? theme.AMBER : theme.TEXT_DIM,
            fontSize: 14,
            cursor: "pointer",
            padding: 2,
          }}
          title={alert.pinned ? "Unpin" : "Pin"}
        >
          {alert.pinned ? "\u2605" : "\u2606"}
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDismiss(alert.id);
          }}
          style={{
            background: "transparent",
            border: "none",
            color: theme.TEXT_DIM,
            fontSize: 14,
            cursor: "pointer",
            padding: 2,
          }}
          title="Dismiss"
        >
          \u00D7
        </button>
      </div>
    </div>
  );
}

export default function AlertDrawer({
  isOpen,
  onClose,
  alerts = [],
  onPin,
  onDismiss,
  onMarkAllRead,
  onAlertClick,
}) {
  const { theme } = useTheme();
  const [filter, setFilter] = useState("all");

  const filtered = useMemo(() => {
    if (filter === "all") return alerts;
    if (filter === "unread") return alerts.filter((a) => !a.read);
    if (filter === "pinned") return alerts.filter((a) => a.pinned);
    return alerts.filter((a) => a.severity === filter);
  }, [alerts, filter]);

  const pinned = useMemo(() => alerts.filter((a) => a.pinned), [alerts]);

  if (!isOpen) return null;

  return (
    <>
      <style>{`
        @keyframes drawer-slide-in {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: theme.OVERLAY,
          zIndex: Z.MODAL,
        }}
      />
      <aside
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          width: "min(440px, 90vw)",
          height: "100vh",
          background: theme.SURFACE,
          borderLeft: `1px solid ${theme.BORDER_HI}`,
          zIndex: Z.MODAL + 1,
          display: "flex",
          flexDirection: "column",
          animation: "drawer-slide-in 300ms cubic-bezier(0.22, 1, 0.36, 1)",
          boxShadow: theme.SHADOW_HI,
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: SPACE.LG,
            borderBottom: `1px solid ${theme.BORDER}`,
          }}
        >
          <div
            style={{
              color: theme.TEXT,
              fontSize: TEXT_SIZE.H2,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 1,
              textTransform: "uppercase",
            }}
          >
            Alerts <span style={{ color: theme.TEXT_DIM, fontWeight: TEXT_WEIGHT.MED }}>({filtered.length})</span>
          </div>
          <div style={{ display: "flex", gap: SPACE.XS }}>
            <button
              onClick={onMarkAllRead}
              style={{
                background: "transparent",
                color: theme.TEXT_MUTED,
                border: `1px solid ${theme.BORDER}`,
                borderRadius: RADIUS.SM,
                padding: "4px 10px",
                cursor: "pointer",
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
              }}
            >
              Mark all read
            </button>
            <button
              onClick={onClose}
              style={{
                background: "transparent",
                color: theme.TEXT_MUTED,
                border: `1px solid ${theme.BORDER}`,
                borderRadius: RADIUS.SM,
                padding: "4px 10px",
                cursor: "pointer",
                fontSize: 14,
              }}
            >
              \u00D7
            </button>
          </div>
        </div>

        {/* Filters */}
        <div
          style={{
            display: "flex",
            gap: SPACE.XS,
            padding: `${SPACE.SM}px ${SPACE.LG}px`,
            borderBottom: `1px solid ${theme.BORDER}`,
            overflowX: "auto",
          }}
        >
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              style={{
                background: filter === f.id ? theme.ACCENT : "transparent",
                color: filter === f.id ? "#fff" : theme.TEXT_MUTED,
                border: `1px solid ${filter === f.id ? theme.ACCENT : theme.BORDER}`,
                borderRadius: RADIUS.SM,
                padding: "4px 10px",
                cursor: "pointer",
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                textTransform: "uppercase",
                letterSpacing: 0.5,
                whiteSpace: "nowrap",
              }}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Pinned section */}
        {pinned.length > 0 && filter === "all" && (
          <div>
            <div
              style={{
                color: theme.AMBER,
                fontSize: 9,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 1.5,
                textTransform: "uppercase",
                padding: `${SPACE.SM}px ${SPACE.LG}px`,
                background: theme.AMBER + "10",
              }}
            >
              \u2605 Pinned ({pinned.length})
            </div>
            {pinned.map((a) => (
              <AlertRow
                key={`p-${a.id}`}
                alert={a}
                onPin={onPin}
                onDismiss={onDismiss}
                onClick={onAlertClick}
                theme={theme}
              />
            ))}
          </div>
        )}

        {/* Alert list */}
        <div style={{ flex: 1, overflowY: "auto" }}>
          {filtered.length === 0 ? (
            <div
              style={{
                padding: SPACE.XXXL,
                textAlign: "center",
                color: theme.TEXT_DIM,
                fontSize: TEXT_SIZE.BODY,
              }}
            >
              No alerts in this view
            </div>
          ) : (
            filtered
              .filter((a) => !(filter === "all" && a.pinned))
              .map((a) => (
                <AlertRow
                  key={a.id}
                  alert={a}
                  onPin={onPin}
                  onDismiss={onDismiss}
                  onClick={onAlertClick}
                  theme={theme}
                />
              ))
          )}
        </div>
      </aside>
    </>
  );
}
