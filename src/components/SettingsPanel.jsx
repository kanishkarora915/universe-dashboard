import { useState, useEffect } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

const SOUND_ENABLED_KEY = "universe_sound_enabled";
const SOUND_VOL_KEY = "universe_sound_volume";
const REMEMBER_KEY = "universe_kite_remember";

function Row({ label, children, desc, theme }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: `${SPACE.MD}px 0`,
        borderBottom: `1px solid ${theme.BORDER}`,
        gap: SPACE.MD,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            color: theme.TEXT,
            fontSize: TEXT_SIZE.BODY,
            fontWeight: TEXT_WEIGHT.MED,
          }}
        >
          {label}
        </div>
        {desc && (
          <div
            style={{
              color: theme.TEXT_DIM,
              fontSize: TEXT_SIZE.MICRO,
              marginTop: 2,
            }}
          >
            {desc}
          </div>
        )}
      </div>
      <div>{children}</div>
    </div>
  );
}

function ThemeButton({ mode, currentMode, label, onClick, theme }) {
  const active = currentMode === mode;
  return (
    <button
      onClick={() => onClick(mode)}
      style={{
        background: active ? theme.ACCENT : "transparent",
        color: active ? "#fff" : theme.TEXT_MUTED,
        border: `1px solid ${active ? theme.ACCENT : theme.BORDER}`,
        borderRadius: RADIUS.SM,
        padding: "5px 12px",
        fontSize: TEXT_SIZE.MICRO,
        fontWeight: TEXT_WEIGHT.BOLD,
        cursor: "pointer",
        transition: TRANSITION.FAST,
      }}
    >
      {label}
    </button>
  );
}

export default function SettingsPanel({ isOpen, onClose }) {
  const { theme, mode, setMode } = useTheme();
  const [soundEnabled, setSoundEnabled] = useState(() => {
    const v = localStorage.getItem(SOUND_ENABLED_KEY);
    return v === null ? true : v === "true";
  });
  const [soundVol, setSoundVol] = useState(() => {
    const v = parseFloat(localStorage.getItem(SOUND_VOL_KEY));
    return isNaN(v) ? 0.6 : v;
  });
  const [rememberCreds, setRememberCreds] = useState(() => {
    const v = localStorage.getItem(REMEMBER_KEY);
    return v === null ? true : v === "true";
  });
  const [notifPermission, setNotifPermission] = useState(
    typeof Notification !== "undefined" ? Notification.permission : "unsupported"
  );

  useEffect(() => {
    localStorage.setItem(SOUND_ENABLED_KEY, String(soundEnabled));
  }, [soundEnabled]);

  useEffect(() => {
    localStorage.setItem(SOUND_VOL_KEY, String(soundVol));
  }, [soundVol]);

  const requestNotif = async () => {
    if (typeof Notification === "undefined") return;
    try {
      const p = await Notification.requestPermission();
      setNotifPermission(p);
    } catch {
      // ignore
    }
  };

  const clearAllData = () => {
    if (!window.confirm("Clear all local preferences (theme, sounds, saved credentials, recent strikes, pinned)? This cannot be undone.")) return;
    // Keep only origin-critical keys
    const keys = [
      "universe_theme_mode", "universe_sound_enabled", "universe_sound_volume",
      "universe_sound_per_type", "universe_kite_api_key", "universe_kite_api_secret",
      "universe_kite_saved_at", "universe_kite_remember", "universe_recent_strikes",
      "universe_pinned_strikes",
    ];
    keys.forEach((k) => localStorage.removeItem(k));
    // Also clear notes (prefix match)
    Object.keys(localStorage).filter((k) => k.startsWith("notes_")).forEach((k) => localStorage.removeItem(k));
    window.location.reload();
  };

  if (!isOpen) return null;

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: theme.OVERLAY,
          zIndex: Z.MODAL,
          backdropFilter: "blur(4px)",
        }}
      />
      <div
        style={{
          position: "fixed",
          top: "10vh",
          left: "50%",
          transform: "translateX(-50%)",
          width: "min(560px, 90vw)",
          maxHeight: "80vh",
          overflowY: "auto",
          background: theme.SURFACE,
          border: `1px solid ${theme.BORDER_HI}`,
          borderRadius: RADIUS.LG,
          boxShadow: theme.SHADOW_HI,
          zIndex: Z.MODAL + 1,
          padding: SPACE.XL,
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: SPACE.LG,
          }}
        >
          <div>
            <div
              style={{
                color: theme.TEXT,
                fontSize: TEXT_SIZE.H1,
                fontWeight: TEXT_WEIGHT.BLACK,
                letterSpacing: 1,
              }}
            >
              Settings
            </div>
            <div
              style={{
                color: theme.TEXT_DIM,
                fontSize: TEXT_SIZE.MICRO,
                marginTop: 2,
              }}
            >
              Universe Pro preferences — saved locally on this device
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
            {"×"}
          </button>
        </div>

        {/* Theme Section */}
        <div style={{ marginBottom: SPACE.LG }}>
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
            Appearance
          </div>

          <Row label="Theme mode" desc="Choose how colors adapt" theme={theme}>
            <div style={{ display: "flex", gap: SPACE.XS }}>
              <ThemeButton mode="dark" currentMode={mode} label="Dark" onClick={setMode} theme={theme} />
              <ThemeButton mode="light" currentMode={mode} label="Light" onClick={setMode} theme={theme} />
              <ThemeButton mode="auto-time" currentMode={mode} label="Auto (time)" onClick={setMode} theme={theme} />
              <ThemeButton mode="auto-system" currentMode={mode} label="Auto (OS)" onClick={setMode} theme={theme} />
            </div>
          </Row>
        </div>

        {/* Alerts Section */}
        <div style={{ marginBottom: SPACE.LG }}>
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
            Alerts & Sound
          </div>

          <Row label="Enable alert sounds" desc="Play tones on trade events and warnings" theme={theme}>
            <input
              type="checkbox"
              checked={soundEnabled}
              onChange={(e) => setSoundEnabled(e.target.checked)}
              style={{ width: 18, height: 18, accentColor: theme.ACCENT, cursor: "pointer" }}
            />
          </Row>

          {soundEnabled && (
            <Row label="Master volume" desc={`${Math.round(soundVol * 100)}%`} theme={theme}>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={soundVol}
                onChange={(e) => setSoundVol(parseFloat(e.target.value))}
                style={{ width: 140, accentColor: theme.ACCENT, cursor: "pointer" }}
              />
            </Row>
          )}

          <Row
            label="Browser notifications"
            desc={
              notifPermission === "granted"
                ? "Enabled — push alerts work even when tab is inactive"
                : notifPermission === "denied"
                ? "Blocked — enable in browser site settings"
                : notifPermission === "unsupported"
                ? "Not supported in this browser"
                : "Not asked yet"
            }
            theme={theme}
          >
            {notifPermission === "granted" ? (
              <span
                style={{
                  color: theme.GREEN,
                  fontSize: TEXT_SIZE.MICRO,
                  fontWeight: TEXT_WEIGHT.BOLD,
                }}
              >
                ✓ GRANTED
              </span>
            ) : notifPermission === "denied" ? (
              <span
                style={{
                  color: theme.RED,
                  fontSize: TEXT_SIZE.MICRO,
                  fontWeight: TEXT_WEIGHT.BOLD,
                }}
              >
                × BLOCKED
              </span>
            ) : (
              <button
                onClick={requestNotif}
                disabled={notifPermission === "unsupported"}
                style={{
                  background: theme.ACCENT,
                  color: "#fff",
                  border: "none",
                  borderRadius: RADIUS.SM,
                  padding: "5px 12px",
                  fontSize: TEXT_SIZE.MICRO,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  cursor: "pointer",
                }}
              >
                Enable
              </button>
            )}
          </Row>
        </div>

        {/* Credentials Section */}
        <div style={{ marginBottom: SPACE.LG }}>
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
            Credentials
          </div>

          <Row label="Remember Kite API credentials" desc="Stored locally, never transmitted" theme={theme}>
            <input
              type="checkbox"
              checked={rememberCreds}
              onChange={(e) => {
                setRememberCreds(e.target.checked);
                localStorage.setItem(REMEMBER_KEY, String(e.target.checked));
                if (!e.target.checked) {
                  localStorage.removeItem("universe_kite_api_key");
                  localStorage.removeItem("universe_kite_api_secret");
                  localStorage.removeItem("universe_kite_saved_at");
                }
              }}
              style={{ width: 18, height: 18, accentColor: theme.ACCENT, cursor: "pointer" }}
            />
          </Row>
        </div>

        {/* Data Section */}
        <div style={{ marginBottom: SPACE.LG }}>
          <div
            style={{
              color: theme.RED,
              fontSize: TEXT_SIZE.MICRO,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 1.5,
              textTransform: "uppercase",
              marginBottom: SPACE.SM,
            }}
          >
            Danger Zone
          </div>

          <Row label="Clear all local data" desc="Wipes theme, sounds, credentials, watchlist, notes — cannot be undone" theme={theme}>
            <button
              onClick={clearAllData}
              style={{
                background: theme.RED_DIM,
                color: theme.RED,
                border: `1px solid ${theme.RED}44`,
                borderRadius: RADIUS.SM,
                padding: "5px 12px",
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                cursor: "pointer",
              }}
            >
              Clear all
            </button>
          </Row>
        </div>

        {/* Footer */}
        <div
          style={{
            textAlign: "center",
            color: theme.TEXT_DIM,
            fontSize: 9,
            paddingTop: SPACE.MD,
            borderTop: `1px solid ${theme.BORDER}`,
            fontFamily: FONT.UI,
          }}
        >
          Universe Pro · by Kanishk Arora · v1.0.0
        </div>
      </div>
    </>
  );
}
