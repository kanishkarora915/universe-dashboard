import { useState } from "react";

const BG = "#0A0A0F";
const CARD = "#111118";
const BORDER = "#1E1E2E";
const ACCENT = "#0A84FF";
const RED = "#FF453A";

export default function Login() {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleConnect = async () => {
    if (!apiKey.trim() || !apiSecret.trim()) {
      setError("Both API Key and API Secret are required");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey.trim(), api_secret: apiSecret.trim() }),
      });

      const data = await res.json();

      if (data.login_url) {
        // Redirect to Kite login page
        window.location.href = data.login_url;
      } else {
        setError(data.error || "Failed to get login URL");
        setLoading(false);
      }
    } catch (err) {
      setError("Backend not running. Start the FastAPI server on port 8000.");
      setLoading(false);
    }
  };

  return (
    <div style={{
      background: BG, minHeight: "100vh",
      display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif",
    }}>
      <div style={{
        background: CARD, border: `1px solid ${BORDER}`, borderRadius: 16,
        padding: "40px 36px", width: 420, maxWidth: "90vw",
      }}>
        {/* Header */}
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div style={{
            color: "#fff", fontWeight: 900, fontSize: 28, letterSpacing: 4, marginBottom: 8,
          }}>UNIVERSE</div>
          <div style={{ color: "#444", fontSize: 12, letterSpacing: 1 }}>
            NSE Options Intelligence Engine
          </div>
        </div>

        {/* Kite Connect Badge */}
        <div style={{
          background: ACCENT + "11", border: `1px solid ${ACCENT}33`, borderRadius: 10,
          padding: "12px 16px", marginBottom: 24, textAlign: "center",
        }}>
          <div style={{ color: ACCENT, fontSize: 12, fontWeight: 600 }}>
            Powered by Zerodha Kite Connect
          </div>
          <div style={{ color: "#555", fontSize: 10, marginTop: 4 }}>
            Enter your Kite API credentials to connect
          </div>
        </div>

        {/* API Key Input */}
        <div style={{ marginBottom: 16 }}>
          <label style={{
            color: "#555", fontSize: 10, fontWeight: 700,
            letterSpacing: 1.5, textTransform: "uppercase", display: "block", marginBottom: 6,
          }}>API Key</label>
          <input
            type="text"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="Enter your Kite API Key"
            style={{
              width: "100%", padding: "12px 14px",
              background: "#0D0D15", border: `1px solid ${BORDER}`,
              borderRadius: 8, color: "#fff", fontSize: 14,
              outline: "none", boxSizing: "border-box",
              fontFamily: "monospace",
            }}
            onFocus={(e) => e.target.style.borderColor = ACCENT}
            onBlur={(e) => e.target.style.borderColor = BORDER}
          />
        </div>

        {/* API Secret Input */}
        <div style={{ marginBottom: 24 }}>
          <label style={{
            color: "#555", fontSize: 10, fontWeight: 700,
            letterSpacing: 1.5, textTransform: "uppercase", display: "block", marginBottom: 6,
          }}>API Secret</label>
          <input
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            placeholder="Enter your Kite API Secret"
            style={{
              width: "100%", padding: "12px 14px",
              background: "#0D0D15", border: `1px solid ${BORDER}`,
              borderRadius: 8, color: "#fff", fontSize: 14,
              outline: "none", boxSizing: "border-box",
              fontFamily: "monospace",
            }}
            onFocus={(e) => e.target.style.borderColor = ACCENT}
            onBlur={(e) => e.target.style.borderColor = BORDER}
          />
        </div>

        {/* Error */}
        {error && (
          <div style={{
            background: RED + "15", border: `1px solid ${RED}33`, borderRadius: 8,
            padding: "10px 14px", marginBottom: 16,
            color: RED, fontSize: 12, lineHeight: 1.5,
          }}>{error}</div>
        )}

        {/* Connect Button */}
        <button
          onClick={handleConnect}
          disabled={loading}
          style={{
            width: "100%", padding: "14px",
            background: loading ? "#333" : ACCENT,
            color: "#fff", border: "none", borderRadius: 10,
            fontSize: 15, fontWeight: 700, cursor: loading ? "not-allowed" : "pointer",
            letterSpacing: 1, transition: "all 0.2s",
          }}
        >
          {loading ? "Redirecting to Kite..." : "Connect to Kite"}
        </button>

        {/* Footer note */}
        <div style={{
          color: "#333", fontSize: 10, textAlign: "center", marginTop: 20, lineHeight: 1.6,
        }}>
          Your credentials are stored in memory only.
          <br />
          Set callback URL in Kite app: <span style={{ color: "#555" }}>{window.location.origin}/api/callback</span>
        </div>
      </div>
    </div>
  );
}
