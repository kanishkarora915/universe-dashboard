import { useState, useEffect, useRef, useCallback } from "react";
import { useTheme } from "./ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "./theme";
import { useKiteCredentials } from "./hooks/useKiteCredentials";
import { useHotkeys } from "./hooks/useHotkeys";

// ═════════════════ Help Modal ═════════════════

function HelpModal({ isOpen, onClose }) {
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
        alignItems: "center",
        justifyContent: "center",
        backdropFilter: "blur(4px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(520px, 90vw)",
          maxHeight: "80vh",
          overflowY: "auto",
          background: theme.SURFACE,
          border: `1px solid ${theme.BORDER_HI}`,
          borderRadius: RADIUS.LG,
          padding: SPACE.XL,
          boxShadow: theme.SHADOW_HI,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: SPACE.MD }}>
          <div style={{ color: theme.TEXT, fontSize: TEXT_SIZE.H1, fontWeight: TEXT_WEIGHT.BLACK }}>
            What is Kite API?
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

        <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.BODY, lineHeight: 1.7 }}>
          <p>
            Universe Pro connects to Zerodha's Kite Connect API to get live NSE options data, place/track trades, and monitor your portfolio.
          </p>
          <div style={{ color: theme.ACCENT, fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1.5, textTransform: "uppercase", marginTop: SPACE.LG, marginBottom: SPACE.SM }}>
            How to get your keys
          </div>
          <ol style={{ paddingLeft: SPACE.LG, margin: 0 }}>
            <li>Sign in at <strong>developers.kite.trade</strong></li>
            <li>Create a new app (type: <em>Personal</em> or <em>Connect</em>)</li>
            <li>Set redirect URL: <code style={{ background: theme.BG, padding: "2px 6px", borderRadius: 3, fontFamily: FONT.MONO, fontSize: 11, color: theme.ACCENT }}>{typeof window !== "undefined" ? `${window.location.origin}/api/callback` : "/api/callback"}</code></li>
            <li>Copy API Key + API Secret from the app page</li>
            <li>Paste them here and connect</li>
          </ol>

          <div style={{ color: theme.ACCENT, fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1.5, textTransform: "uppercase", marginTop: SPACE.LG, marginBottom: SPACE.SM }}>
            Security
          </div>
          <p style={{ margin: 0 }}>
            Your API Key and Secret are stored only on this device (browser localStorage). They're used exclusively to initiate OAuth with Kite. Kite's session tokens expire daily — you'll re-authenticate each morning.
          </p>

          <div style={{ color: theme.AMBER, fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1.5, textTransform: "uppercase", marginTop: SPACE.LG, marginBottom: SPACE.SM }}>
            Kite subscription
          </div>
          <p style={{ margin: 0 }}>
            Kite Connect requires a ₹2,000/month subscription for live data + trading APIs. See <strong>kite.trade/connect/</strong> for current pricing.
          </p>
        </div>
      </div>
    </div>
  );
}

// ═════════════════ Status Bar ═════════════════

function StatusBar({ onHelp }) {
  const { theme, toggle: toggleTheme, isDark } = useTheme();
  const [now, setNow] = useState(new Date());
  const [backendStatus, setBackendStatus] = useState("checking");

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch("/api/status");
        setBackendStatus(r.ok ? "ready" : "down");
      } catch {
        setBackendStatus("down");
      }
    };
    check();
    const t = setInterval(check, 15000);
    return () => clearInterval(t);
  }, []);

  const statusColor =
    backendStatus === "ready" ? theme.GREEN : backendStatus === "checking" ? theme.AMBER : theme.RED;
  const statusLabel =
    backendStatus === "ready" ? "BACKEND READY" : backendStatus === "checking" ? "CHECKING" : "BACKEND DOWN";

  return (
    <div
      style={{
        position: "fixed",
        bottom: SPACE.LG,
        left: SPACE.LG,
        right: SPACE.LG,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: SPACE.SM,
          padding: "4px 10px",
          background: theme.SURFACE,
          border: `1px solid ${theme.BORDER}`,
          borderRadius: RADIUS.SM,
          pointerEvents: "auto",
        }}
      >
        <div
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: statusColor,
            boxShadow: `0 0 6px ${statusColor}`,
            animation: backendStatus === "ready" ? "pulse 2s ease-in-out infinite" : undefined,
          }}
        />
        <span
          style={{
            color: statusColor,
            fontSize: 10,
            fontWeight: TEXT_WEIGHT.BOLD,
            letterSpacing: 1,
            fontFamily: FONT.UI,
          }}
        >
          {statusLabel}
        </span>
        <span
          style={{
            color: theme.TEXT_DIM,
            fontSize: 10,
            fontFamily: FONT.MONO,
            marginLeft: SPACE.SM,
          }}
        >
          {now.toLocaleTimeString("en-IN", { hour12: false })} IST
        </span>
      </div>

      <div style={{ display: "flex", gap: SPACE.XS, pointerEvents: "auto" }}>
        <button
          onClick={toggleTheme}
          title={isDark ? "Switch to light" : "Switch to dark"}
          style={{
            width: 30,
            height: 30,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: theme.SURFACE,
            color: theme.TEXT_MUTED,
            border: `1px solid ${theme.BORDER}`,
            borderRadius: RADIUS.SM,
            cursor: "pointer",
            fontSize: 14,
            transition: TRANSITION.FAST,
          }}
        >
          {isDark ? "☀" : "☾"}
        </button>
        <button
          onClick={onHelp}
          title="What is Kite API?"
          style={{
            width: 30,
            height: 30,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: theme.SURFACE,
            color: theme.TEXT_MUTED,
            border: `1px solid ${theme.BORDER}`,
            borderRadius: RADIUS.SM,
            cursor: "pointer",
            fontSize: 14,
            fontWeight: TEXT_WEIGHT.BOLD,
            transition: TRANSITION.FAST,
          }}
        >
          ?
        </button>
      </div>
    </div>
  );
}

// ═════════════════ Field (with saved-preview + clear) ═════════════════

function Field({
  label,
  type = "text",
  value,
  onChange,
  placeholder,
  saved,
  savedAgo,
  onClear,
  maskedValue,
  autoFocus,
  onEnter,
  theme,
  inputRef,
}) {
  const [focused, setFocused] = useState(false);
  const [editing, setEditing] = useState(!saved || !value);

  const showMasked = saved && !editing && !focused && value;

  return (
    <div style={{ marginBottom: SPACE.MD }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 6,
        }}
      >
        <span
          style={{
            color: theme.TEXT_MUTED,
            fontSize: 10,
            fontWeight: TEXT_WEIGHT.BOLD,
            letterSpacing: 1.5,
            textTransform: "uppercase",
            fontFamily: FONT.UI,
          }}
        >
          {label}
        </span>
        {saved && savedAgo && (
          <span
            style={{
              color: theme.GREEN,
              fontSize: 9,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 0.5,
              fontFamily: FONT.UI,
              textTransform: "uppercase",
            }}
          >
            {"✓"} saved {savedAgo}
          </span>
        )}
      </div>

      <div style={{ position: "relative" }}>
        {showMasked ? (
          <div
            onClick={() => {
              setEditing(true);
              setTimeout(() => inputRef?.current?.focus(), 10);
            }}
            style={{
              display: "flex",
              alignItems: "center",
              width: "100%",
              padding: "11px 14px",
              background: theme.SURFACE_HI,
              border: `1px solid ${theme.BORDER}`,
              borderRadius: RADIUS.MD,
              color: theme.TEXT,
              fontSize: 14,
              fontFamily: FONT.MONO,
              cursor: "pointer",
              minHeight: 44,
              boxSizing: "border-box",
              transition: TRANSITION.FAST,
            }}
          >
            <span style={{ flex: 1, letterSpacing: 2 }}>{maskedValue}</span>
            <span style={{ color: theme.TEXT_DIM, fontSize: 10, fontFamily: FONT.UI }}>
              tap to change
            </span>
          </div>
        ) : (
          <input
            ref={inputRef}
            type={type}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && onEnter) onEnter();
            }}
            placeholder={placeholder}
            autoFocus={autoFocus}
            autoComplete={type === "password" ? "current-password" : "username"}
            spellCheck={false}
            style={{
              width: "100%",
              padding: "11px 14px",
              background: theme.SURFACE_HI,
              border: `1px solid ${focused ? theme.ACCENT : theme.BORDER}`,
              borderRadius: RADIUS.MD,
              color: theme.TEXT,
              fontSize: 14,
              fontFamily: FONT.MONO,
              outline: "none",
              boxSizing: "border-box",
              transition: TRANSITION.FAST,
              boxShadow: focused ? `0 0 0 3px ${theme.ACCENT}22` : "none",
            }}
          />
        )}

        {saved && value && (
          <button
            type="button"
            onClick={() => {
              onClear();
              setEditing(true);
              setTimeout(() => inputRef?.current?.focus(), 10);
            }}
            title="Clear saved value"
            style={{
              position: "absolute",
              right: 8,
              top: "50%",
              transform: "translateY(-50%)",
              width: 22,
              height: 22,
              background: theme.SURFACE,
              color: theme.TEXT_DIM,
              border: `1px solid ${theme.BORDER}`,
              borderRadius: RADIUS.XS,
              cursor: "pointer",
              fontSize: 12,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              lineHeight: 1,
              transition: TRANSITION.FAST,
            }}
          >
            {"×"}
          </button>
        )}
      </div>
    </div>
  );
}

// ═════════════════ Main Login ═════════════════

export default function Login() {
  const { theme, isDark } = useTheme();
  const creds = useKiteCredentials();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [shake, setShake] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const keyRef = useRef(null);
  const secretRef = useRef(null);

  useHotkeys({
    "?": () => setHelpOpen(true),
    "cmd+shift+l": () => {
      // theme toggle handled in StatusBar — fallthrough
    },
  });

  const handleError = (msg) => {
    setError(msg);
    setShake(true);
    setTimeout(() => setShake(false), 500);
  };

  const handleConnect = useCallback(async () => {
    const k = creds.apiKey.trim();
    const s = creds.apiSecret.trim();
    if (!k || !s) {
      handleError("Both API Key and API Secret are required");
      return;
    }

    setLoading(true);
    setError("");

    // Persist before OAuth redirect
    if (creds.remember) creds.save(k, s);

    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: k, api_secret: s }),
      });
      const data = await res.json();
      if (data.login_url) {
        window.location.href = data.login_url;
      } else {
        handleError(data.error || "Failed to get login URL");
        setLoading(false);
      }
    } catch (err) {
      handleError("Backend not running. Start the FastAPI server on port 8000.");
      setLoading(false);
    }
  }, [creds]);

  const hasSavedBoth = creds.hasSaved && creds.apiKey && creds.apiSecret;

  return (
    <>
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(1.2); }
        }
        @keyframes shake {
          0%, 100% { transform: translateX(0); }
          25% { transform: translateX(-6px); }
          75% { transform: translateX(6px); }
        }
      `}</style>

      <div
        style={{
          background: theme.BG,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: SPACE.LG,
          fontFamily: FONT.UI,
          color: theme.TEXT,
        }}
      >
        <div
          style={{
            width: "min(440px, 100%)",
            background: theme.SURFACE,
            border: `1px solid ${theme.BORDER}`,
            borderRadius: RADIUS.LG,
            padding: `${SPACE.XXXL}px ${SPACE.XXL}px`,
            boxShadow: theme.GLOW_ACCENT,
            animation: shake ? "shake 400ms ease-in-out" : undefined,
          }}
        >
          {/* Logo mark */}
          <div style={{ textAlign: "center", marginBottom: SPACE.LG }}>
            <div
              style={{
                color: theme.ACCENT,
                fontSize: 32,
                fontWeight: TEXT_WEIGHT.BLACK,
                fontFamily: FONT.MONO,
                lineHeight: 1,
                marginBottom: SPACE.MD,
              }}
            >
              {"▸"}
            </div>
            <div
              style={{
                color: theme.TEXT,
                fontSize: 26,
                fontWeight: TEXT_WEIGHT.BLACK,
                fontFamily: FONT.MONO,
                letterSpacing: 3,
                lineHeight: 1,
              }}
            >
              UNIVERSE PRO
            </div>
            <div
              style={{
                color: theme.TEXT_DIM,
                fontSize: 11,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 2,
                marginTop: SPACE.SM,
                fontFamily: FONT.UI,
                textTransform: "uppercase",
              }}
            >
              by Kanishk Arora
            </div>
            <div
              style={{
                color: theme.TEXT_MUTED,
                fontSize: TEXT_SIZE.BODY,
                marginTop: SPACE.MD,
                fontFamily: FONT.UI,
              }}
            >
              Institutional Options Intelligence
            </div>
          </div>

          {/* Divider */}
          <div
            style={{
              height: 1,
              background: theme.BORDER,
              margin: `${SPACE.LG}px 0`,
            }}
          />

          {/* Fields */}
          <Field
            label="API Key"
            value={creds.apiKey}
            onChange={creds.setApiKey}
            placeholder="Enter Kite API Key"
            saved={creds.hasSaved && !!creds.apiKey}
            savedAgo={creds.savedAgo}
            maskedValue={creds.maskKey(creds.apiKey)}
            onClear={() => creds.clear("key")}
            autoFocus={!hasSavedBoth}
            onEnter={() => secretRef.current?.focus()}
            theme={theme}
            inputRef={keyRef}
          />

          <Field
            label="API Secret"
            type="password"
            value={creds.apiSecret}
            onChange={creds.setApiSecret}
            placeholder="Enter Kite API Secret"
            saved={creds.hasSaved && !!creds.apiSecret}
            savedAgo={creds.savedAgo}
            maskedValue={creds.maskSecret(creds.apiSecret)}
            onClear={() => creds.clear("secret")}
            onEnter={handleConnect}
            theme={theme}
            inputRef={secretRef}
          />

          {/* Remember toggle */}
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: SPACE.SM,
              marginBottom: SPACE.LG,
              cursor: "pointer",
              userSelect: "none",
            }}
          >
            <input
              type="checkbox"
              checked={creds.remember}
              onChange={(e) => creds.toggleRemember(e.target.checked)}
              style={{
                width: 16,
                height: 16,
                accentColor: theme.ACCENT,
                cursor: "pointer",
              }}
            />
            <span style={{ color: theme.TEXT_MUTED, fontSize: 11, fontFamily: FONT.UI }}>
              Remember on this device
            </span>
            {hasSavedBoth && (
              <button
                type="button"
                onClick={() => creds.clear("all")}
                style={{
                  background: "transparent",
                  border: "none",
                  color: theme.RED,
                  fontSize: 10,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  cursor: "pointer",
                  letterSpacing: 0.5,
                  textTransform: "uppercase",
                  marginLeft: "auto",
                  padding: 0,
                }}
              >
                Clear saved
              </button>
            )}
          </label>

          {/* Error */}
          {error && (
            <div
              style={{
                background: theme.RED_DIM,
                border: `1px solid ${theme.RED}44`,
                borderRadius: RADIUS.MD,
                padding: `10px ${SPACE.MD}px`,
                marginBottom: SPACE.MD,
                color: theme.RED,
                fontSize: TEXT_SIZE.BODY,
                lineHeight: 1.5,
              }}
            >
              {error}
            </div>
          )}

          {/* Submit */}
          <button
            onClick={handleConnect}
            disabled={loading}
            style={{
              width: "100%",
              padding: "14px 20px",
              background: loading ? theme.SURFACE_HI : theme.ACCENT,
              color: loading ? theme.TEXT_MUTED : "#FFF",
              border: "none",
              borderRadius: RADIUS.MD,
              fontSize: TEXT_SIZE.H2,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 1.5,
              textTransform: "uppercase",
              cursor: loading ? "not-allowed" : "pointer",
              transition: TRANSITION.BASE,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: SPACE.SM,
              fontFamily: FONT.UI,
            }}
            onMouseOver={(e) => {
              if (!loading) {
                e.currentTarget.style.transform = "translateY(-1px)";
                e.currentTarget.style.boxShadow = `0 4px 16px ${theme.ACCENT}44`;
              }
            }}
            onMouseOut={(e) => {
              e.currentTarget.style.transform = "translateY(0)";
              e.currentTarget.style.boxShadow = "none";
            }}
          >
            {loading
              ? "Redirecting to Kite..."
              : hasSavedBoth
              ? "Connect with saved →"
              : "Connect →"}
          </button>

          {/* Footer */}
          <div
            style={{
              marginTop: SPACE.LG,
              paddingTop: SPACE.MD,
              borderTop: `1px solid ${theme.BORDER}`,
              textAlign: "center",
            }}
          >
            <div
              style={{
                color: theme.TEXT_DIM,
                fontSize: 10,
                fontFamily: FONT.UI,
                marginBottom: SPACE.XS,
              }}
            >
              {"🔒"} Secure OAuth via Kite Connect
            </div>
            <div
              style={{
                color: theme.TEXT_DIM,
                fontSize: 9,
                fontFamily: FONT.UI,
                lineHeight: 1.5,
              }}
            >
              Credentials stored locally in your browser only.
              <br />
              Callback URL:{" "}
              <span style={{ color: theme.TEXT_MUTED, fontFamily: FONT.MONO }}>
                {typeof window !== "undefined" ? `${window.location.origin}/api/callback` : "/api/callback"}
              </span>
            </div>
          </div>
        </div>

        <StatusBar onHelp={() => setHelpOpen(true)} />
        <HelpModal isOpen={helpOpen} onClose={() => setHelpOpen(false)} />
      </div>
    </>
  );
}
