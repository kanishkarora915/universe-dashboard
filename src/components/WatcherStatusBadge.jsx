/**
 * WatcherStatusBadge
 * ──────────────────
 * Visible heartbeat indicator showing whether the position watcher
 * is alive and computing health scores. Use at top of PnL + Scalper tabs.
 *
 * GREEN dot + "LIVE Xs ago" → watcher pulsing within last 90s
 * RED dot   + "STALLED"     → no pulse in 90s+
 * AMBER     + "INITIALISING"→ never pulsed (cold start)
 */

import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

export default function WatcherStatusBadge() {
  const [status, setStatus] = useState(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const fetchOnce = async () => {
      try {
        const r = await fetch(`${API}/api/positions/watcher-status`);
        if (!r.ok) return;
        const j = await r.json();
        if (alive) setStatus(j);
      } catch (e) { /* silent */ }
    };
    fetchOnce();
    const t = setInterval(fetchOnce, 10000);
    const tt = setInterval(() => setTick(x => x + 1), 1000);
    return () => { alive = false; clearInterval(t); clearInterval(tt); };
  }, []);

  if (!status) {
    return (
      <Pill color="#888" label="WATCHER" detail="checking…" pulse={false} />
    );
  }

  const live = status.live;
  const age = status.last_pulse_age_sec;
  const total = (status.main_count || 0) + (status.scalper_count || 0);
  const stubs = status.stub_count || 0;

  let color, label, detail;
  if (age == null) {
    color = "#FFD60A";
    label = "WATCHER · INITIALISING";
    detail = total === 0 ? "No open trades" : "First pulse incoming…";
  } else if (live) {
    color = "#30D158";
    label = "WATCHER · LIVE";
    const ageSec = Math.max(1, Math.round(age));
    detail = `Last pulse ${ageSec}s ago · ${total} tracked${stubs ? ` · ${stubs} initialising` : ""}`;
  } else {
    color = "#FF453A";
    label = "WATCHER · STALLED";
    detail = `No pulse for ${Math.round(age)}s — engine may be down`;
  }

  return <Pill color={color} label={label} detail={detail} pulse={live} />;
}


function Pill({ color, label, detail, pulse }) {
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 8,
      background: `${color}15`,
      border: `1px solid ${color}55`,
      borderRadius: 8, padding: "5px 12px",
      fontFamily: "ui-monospace, monospace",
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%",
        background: color,
        boxShadow: `0 0 8px ${color}`,
        animation: pulse ? "watcherPulse 1.4s ease-in-out infinite" : "none",
      }} />
      <span style={{
        color, fontSize: 11, fontWeight: 700,
        letterSpacing: 0.6, textTransform: "uppercase",
      }}>
        {label}
      </span>
      <span style={{
        color: "#aaa", fontSize: 10, fontWeight: 500,
        borderLeft: `1px solid ${color}33`, paddingLeft: 8,
      }}>
        {detail}
      </span>
      <style>{`
        @keyframes watcherPulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(1.4); }
        }
      `}</style>
    </div>
  );
}
