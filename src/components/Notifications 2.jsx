import { useState, useEffect, useRef } from "react";

const GREEN = "#30D158";
const RED = "#FF453A";
const PURPLE = "#BF5AF2";
const ORANGE = "#FF9F0A";

function playSound(type) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    gain.gain.value = 0.15;

    if (type === "entry") {
      osc.frequency.value = 880; // High A
      osc.type = "sine";
    } else if (type === "win") {
      osc.frequency.value = 1046; // High C
      osc.type = "sine";
    } else if (type === "loss") {
      osc.frequency.value = 330; // Low E
      osc.type = "triangle";
    } else {
      osc.frequency.value = 660;
      osc.type = "sine";
    }

    osc.start();
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
    osc.stop(ctx.currentTime + 0.5);
  } catch {}
}

export default function Notifications() {
  const [toasts, setToasts] = useState([]);
  const lastSeenRef = useRef(0);

  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch("/api/trades/alerts-feed");
        if (!res.ok) return;
        const alerts = await res.json();
        if (!Array.isArray(alerts) || alerts.length === 0) return;

        // Find new alerts
        const newAlerts = alerts.filter((_, i) => i >= lastSeenRef.current);
        if (newAlerts.length === 0) return;
        lastSeenRef.current = alerts.length;

        newAlerts.forEach((alert) => {
          const id = Date.now() + Math.random();
          let color = ORANGE;
          let soundType = "entry";

          if (alert.type === "TRADE_ENTRY") {
            color = GREEN;
            soundType = "entry";
          } else if (alert.type === "TRADE_EXIT") {
            color = alert.details?.includes("+") ? GREEN : RED;
            soundType = alert.details?.includes("+") ? "win" : "loss";
          } else if (alert.type === "PARTIAL_BOOK") {
            color = PURPLE;
            soundType = "win";
          }

          playSound(soundType);
          setToasts((prev) => [...prev, { id, alert, color }]);

          // Auto-dismiss after 8 seconds
          setTimeout(() => {
            setToasts((prev) => prev.filter((t) => t.id !== id));
          }, 8000);
        });
      } catch {}
    };

    poll();
    const interval = setInterval(poll, 10000);
    return () => clearInterval(interval);
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div style={{
      position: "fixed", bottom: 20, right: 20, zIndex: 9999,
      display: "flex", flexDirection: "column", gap: 8, maxWidth: 360,
    }}>
      {toasts.map((toast) => (
        <div
          key={toast.id}
          style={{
            background: "#111118", border: `1px solid ${toast.color}44`,
            borderLeft: `4px solid ${toast.color}`,
            borderRadius: 8, padding: "10px 14px",
            boxShadow: `0 4px 20px ${toast.color}22`,
            animation: "slideIn 0.3s ease-out",
          }}
          onClick={() => setToasts((prev) => prev.filter((t) => t.id !== toast.id))}
        >
          <div style={{ color: toast.color, fontWeight: 700, fontSize: 12, marginBottom: 2 }}>
            {toast.alert.message}
          </div>
          <div style={{ color: "#888", fontSize: 10 }}>{toast.alert.details}</div>
          <div style={{ color: "#444", fontSize: 9, marginTop: 4 }}>{toast.alert.time}</div>
        </div>
      ))}
      <style>{`
        @keyframes slideIn {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
