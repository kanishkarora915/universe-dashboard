/**
 * MarketCloseBanner
 * ─────────────────
 * Global flashing banner that surfaces at 3:20 PM IST and warns the user
 * to exit positions before 3:25 PM auto-close.
 *
 * States (driven by /api/market/close-status):
 *   NORMAL       → hidden
 *   CLOSING_SOON → 3:20-3:24 → AMBER flashing, countdown to auto-close
 *   AUTO_CLOSING → 3:25-3:30 → RED, "Engine is now closing all open trades"
 *   CLOSED       → hidden (engine already finished)
 *   PRE_OPEN     → hidden
 *
 * Mounted once globally in Universe.jsx so it shows on every tab.
 */

import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

export default function MarketCloseBanner() {
  const [status, setStatus] = useState(null);
  const [openCount, setOpenCount] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const [s, w] = await Promise.all([
          fetch(`${API}/api/market/close-status`).then(r => r.ok ? r.json() : null),
          fetch(`${API}/api/positions/watcher-debug`).then(r => r.ok ? r.json() : null),
        ]);
        if (!alive) return;
        setStatus(s);
        if (w) setOpenCount((w.open_main_in_db || 0) + (w.open_scalper_in_db || 0));
      } catch (e) { /* silent */ }
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!status || !status.warning_active || dismissed) return null;

  const isAutoClosing = status.state === "AUTO_CLOSING";
  const secsLeft = status.seconds_to_close ?? 0;
  const mins = Math.floor(secsLeft / 60);
  const secs = secsLeft % 60;

  const color = isAutoClosing ? "#FF453A" : "#FF9F0A";
  const bg = isAutoClosing
    ? "linear-gradient(180deg, rgba(255,69,58,0.25), rgba(255,69,58,0.10))"
    : "linear-gradient(180deg, rgba(255,159,10,0.25), rgba(255,159,10,0.10))";

  return (
    <div style={{
      position: "fixed",
      top: 0, left: 0, right: 0,
      zIndex: 1100,
      background: bg,
      borderBottom: `2px solid ${color}`,
      padding: "10px 20px",
      animation: "marketCloseFlash 1.2s ease-in-out infinite",
      display: "flex", alignItems: "center", justifyContent: "space-between",
      gap: 16, flexWrap: "wrap",
      backdropFilter: "blur(8px)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span style={{
          width: 12, height: 12, borderRadius: "50%",
          background: color, boxShadow: `0 0 12px ${color}`,
          animation: "marketDotPulse 0.8s ease-in-out infinite",
        }} />
        <span style={{
          fontSize: 14, fontWeight: 800, color,
          textTransform: "uppercase", letterSpacing: 0.8,
        }}>
          {isAutoClosing
            ? "🔒 MARKET CLOSING NOW — engine is auto-closing all open positions"
            : "⏰ MARKET CLOSING SOON — exit positions or auto-close at 3:25 PM IST"}
        </span>
        {!isAutoClosing && secsLeft > 0 && (
          <span style={{
            fontFamily: "ui-monospace, monospace",
            fontSize: 14, fontWeight: 700,
            color: "#fff",
            background: color,
            padding: "3px 10px", borderRadius: 6,
            letterSpacing: 1,
          }}>
            T-{String(mins).padStart(2, "0")}:{String(secs).padStart(2, "0")}
          </span>
        )}
        {openCount !== null && openCount > 0 && (
          <span style={{
            fontSize: 12, color: "#fff", fontWeight: 600,
            background: "rgba(0,0,0,0.4)", padding: "3px 10px", borderRadius: 6,
          }}>
            {openCount} open trade{openCount > 1 ? "s" : ""} will be auto-closed
          </span>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 11, color: "#aaa", fontWeight: 500 }}>
          IST {status.now_ist}
        </span>
        <button
          onClick={() => setDismissed(true)}
          style={{
            background: "transparent",
            border: `1px solid ${color}77`,
            color, fontSize: 11, fontWeight: 700,
            padding: "4px 10px", borderRadius: 6, cursor: "pointer",
            letterSpacing: 0.4,
          }}>
          Dismiss
        </button>
      </div>

      <style>{`
        @keyframes marketCloseFlash {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.8; }
        }
        @keyframes marketDotPulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.6); }
        }
      `}</style>
    </div>
  );
}
