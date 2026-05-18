/**
 * WatcherControls — compact panel for Active Position Watcher
 * ───────────────────────────────────────────────────────────
 * Shows toggles for auto-exit + tight-SL per mode, plus a recent
 * exit log. Rendered inline at top of PnL + Scalper tabs.
 *
 * Props:
 *   mode: "MAIN" | "SCALPER"
 */

import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

export default function WatcherControls({ mode = "MAIN" }) {
  const [cfg, setCfg] = useState(null);
  const [exits, setExits] = useState([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const autoKey = mode === "MAIN" ? "auto_exit_main" : "auto_exit_scalper";
  const slKey   = mode === "MAIN" ? "tight_sl_main"  : "tight_sl_scalper";

  const refresh = async () => {
    try {
      const [r1, r2] = await Promise.all([
        fetch(`${API}/api/positions/config`),
        fetch(`${API}/api/positions/exits?limit=20`),
      ]);
      if (r1.ok) setCfg(await r1.json());
      if (r2.ok) {
        const j = await r2.json();
        setExits((j.exits || []).filter(x => x.source === mode));
      }
    } catch (e) { /* silent */ }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, [mode]);

  const toggle = async (key) => {
    if (!cfg) return;
    setLoading(true);
    try {
      const next = { [key]: !cfg[key] };
      const r = await fetch(`${API}/api/positions/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
      if (r.ok) setCfg(await r.json());
    } catch (e) { /* silent */ }
    finally { setLoading(false); }
  };

  if (!cfg) return null;

  const autoOn = cfg[autoKey];
  const slOn = cfg[slKey];

  return (
    <div style={{
      background: "#111118", border: "1px solid #1E1E2E",
      borderRadius: 12, padding: "12px 16px", marginBottom: 12,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", flexWrap: "wrap", gap: 12,
      }}>
        <div>
          <div style={{
            color: "#0A84FF", fontSize: 11, fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 4,
          }}>
            🛡️ Active Position Watcher · {mode}
          </div>
          <div style={{ color: "#888", fontSize: 11 }}>
            30-sec health monitor on every open trade. Triggers: reversal, VIX crush, theta, day-high trap, post-lunch stall, pattern loser.
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <ToggleChip
            label="Auto-Exit"
            on={autoOn}
            disabled={loading}
            onClick={() => toggle(autoKey)}
            color="#FF453A"
          />
          <ToggleChip
            label="Tight SL"
            on={slOn}
            disabled={loading}
            onClick={() => toggle(slKey)}
            color="#FF9F0A"
          />
          <button
            onClick={() => setOpen(v => !v)}
            style={{
              background: "transparent", border: "1px solid #2A2A3F",
              color: "#aaa", fontSize: 11, padding: "4px 10px",
              borderRadius: 6, cursor: "pointer",
            }}>
            Exits ({exits.length}) {open ? "▲" : "▼"}
          </button>
        </div>
      </div>

      {open && (
        <div style={{
          marginTop: 12, paddingTop: 10, borderTop: "1px dashed #1E1E2E",
        }}>
          <div style={{
            color: "#888", fontSize: 10, fontWeight: 700, textTransform: "uppercase",
            letterSpacing: 0.8, marginBottom: 6,
          }}>
            Recent Watcher Exits
          </div>
          {exits.length === 0 ? (
            <div style={{ color: "#555", fontSize: 11, padding: "6px 0" }}>
              No watcher-triggered exits yet.
            </div>
          ) : (
            exits.slice(0, 6).map((e, i) => (
              <div key={i} style={{
                display: "flex", justifyContent: "space-between",
                fontSize: 11, padding: "4px 0",
                borderBottom: "1px dashed #1E1E2E20",
              }}>
                <div style={{ color: "#ccc" }}>
                  <strong>{e.idx} {e.action} {e.strike}</strong>
                  <span style={{ color: "#888", marginLeft: 8 }}>
                    {e.trigger}
                  </span>
                </div>
                <div style={{
                  color: (e.pnl_rupees ?? 0) >= 0 ? "#30D158" : "#FF453A",
                  fontWeight: 700,
                }}>
                  ₹{Math.round(e.pnl_rupees ?? 0).toLocaleString("en-IN")}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function ToggleChip({ label, on, onClick, disabled, color }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: on ? color : "transparent",
        color: on ? "#000" : "#888",
        border: `1px solid ${on ? color : "#2A2A3F"}`,
        padding: "5px 12px",
        fontSize: 11,
        fontWeight: 700,
        borderRadius: 6,
        cursor: disabled ? "wait" : "pointer",
        transition: "all 0.15s",
        letterSpacing: 0.4,
      }}>
      {on ? "● " : "○ "}{label} {on ? "ON" : "OFF"}
    </button>
  );
}
