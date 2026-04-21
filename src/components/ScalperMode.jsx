/**
 * ScalperMode — Aggressive scalper toggle + stats widget.
 *
 * Shows:
 *  - ON/OFF toggle for scalper mode
 *  - Today's scalper trades count + remaining
 *  - Live P&L for scalper mode (separate from swing)
 *  - Open scalper positions
 *  - Win rate / avg win / avg loss
 */

import React, { useState, useEffect } from "react";
import { SPACE, RADIUS, FONT } from "../theme";

const GREEN = "#10b981";
const RED = "#ef4444";
const YELLOW = "#f59e0b";
const ORANGE = "#fb923c";
const BLUE = "#3b82f6";
const GRAY = "#6b7280";

export default function ScalperMode() {
  const [enabled, setEnabled] = useState(false);
  const [stats, setStats] = useState(null);
  const [openTrades, setOpenTrades] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = async () => {
    try {
      const [statusRes, statsRes, openRes] = await Promise.all([
        fetch("/api/scalper/status").then(r => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/scalper/stats").then(r => r.ok ? r.json() : null).catch(() => null),
        fetch("/api/scalper/trades/open").then(r => r.ok ? r.json() : []).catch(() => []),
      ]);
      if (statusRes && typeof statusRes.enabled === "boolean") setEnabled(statusRes.enabled);
      if (statsRes && !statsRes.error) setStats(statsRes);
      if (Array.isArray(openRes)) setOpenTrades(openRes);
    } catch {}
    setLoading(false);
  };

  useEffect(() => {
    fetchAll();
    const iv = setInterval(fetchAll, 10000);
    return () => clearInterval(iv);
  }, []);

  const toggleScalper = async () => {
    try {
      const url = enabled ? "/api/scalper/disable" : "/api/scalper/enable";
      await fetch(url, { method: "POST" });
      setEnabled(!enabled);
      setTimeout(fetchAll, 500);
    } catch {}
  };

  const totalPnl = stats?.totalPnl || 0;
  const todayCount = stats?.todayCount || 0;
  const remaining = stats?.remaining || 0;
  const winRate = stats?.winRate || 0;

  return (
    <div style={{
      padding: SPACE.MD,
      background: enabled ? `linear-gradient(135deg, ${ORANGE}11, ${RED}11)` : "rgba(107,114,128,0.04)",
      border: `2px solid ${enabled ? ORANGE : GRAY}33`,
      borderRadius: RADIUS.LG,
      display: "flex",
      flexDirection: "column",
      gap: SPACE.SM,
    }}>
      {/* Header with toggle */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: SPACE.SM }}>
        <div>
          <div style={{
            fontSize: 16,
            fontWeight: 800,
            color: enabled ? ORANGE : GRAY,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}>
            ⚡ SCALPER MODE
            {enabled && <span style={{
              padding: "2px 8px",
              background: GREEN + "33",
              color: GREEN,
              borderRadius: 4,
              fontSize: 10,
              letterSpacing: 1,
              animation: "pulse 2s infinite",
            }}>ACTIVE</span>}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary, #888)", marginTop: 2 }}>
            Aggressive: 15 trades/day · 8% SL · 12% T1 · 30 min max hold
          </div>
        </div>
        <button
          onClick={toggleScalper}
          style={{
            padding: "8px 20px",
            background: enabled ? RED : GREEN,
            color: "#fff",
            border: "none",
            borderRadius: RADIUS.MD,
            fontWeight: 700,
            fontSize: 13,
            cursor: "pointer",
            letterSpacing: 0.5,
          }}
        >
          {enabled ? "🛑 TURN OFF" : "▶ TURN ON"}
        </button>
      </div>

      {/* Live stats */}
      {enabled && stats && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: SPACE.XS, marginTop: SPACE.XS }}>
          <StatBox
            label="P&L TODAY"
            value={`₹${totalPnl >= 0 ? "+" : ""}${totalPnl.toLocaleString()}`}
            color={totalPnl >= 0 ? GREEN : RED}
          />
          <StatBox
            label="TRADES"
            value={`${todayCount}/${stats.dailyCap}`}
            color={remaining > 5 ? GREEN : remaining > 0 ? YELLOW : RED}
          />
          <StatBox
            label="WIN RATE"
            value={`${winRate}%`}
            color={winRate >= 55 ? GREEN : winRate >= 45 ? YELLOW : RED}
          />
          <StatBox
            label="AVG WIN"
            value={`₹${(stats.avgWin || 0).toLocaleString()}`}
            color={GREEN}
          />
          <StatBox
            label="AVG LOSS"
            value={`₹${Math.abs(stats.avgLoss || 0).toLocaleString()}`}
            color={RED}
          />
        </div>
      )}

      {/* Open scalper positions */}
      {enabled && openTrades.length > 0 && (
        <div style={{ marginTop: SPACE.XS }}>
          <div style={{ fontSize: 10, color: "var(--text-secondary, #888)", letterSpacing: 1, marginBottom: 4 }}>
            OPEN SCALP POSITIONS ({openTrades.length})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {openTrades.map((t) => {
              const pnl = t.pnl_rupees || 0;
              const color = pnl >= 0 ? GREEN : RED;
              const holdMin = Math.floor((t.hold_seconds || 0) / 60);
              return (
                <div key={t.id} style={{
                  padding: `${SPACE.XS}px ${SPACE.SM}px`,
                  background: color + "0D",
                  border: `1px solid ${color}33`,
                  borderRadius: RADIUS.SM,
                  display: "grid",
                  gridTemplateColumns: "2fr 1fr 1fr 60px",
                  gap: SPACE.XS,
                  fontSize: 11,
                  alignItems: "center",
                }}>
                  <span><b>{t.idx}</b> {t.action} {t.strike}</span>
                  <span style={{ fontFamily: FONT.MONO || "monospace" }}>
                    ₹{t.entry_price} → ₹{t.current_ltp || t.entry_price}
                  </span>
                  <span style={{ color, fontWeight: 700, fontFamily: FONT.MONO || "monospace" }}>
                    ₹{pnl >= 0 ? "+" : ""}{pnl.toLocaleString()}
                  </span>
                  <span style={{ color: holdMin > 25 ? RED : holdMin > 15 ? YELLOW : GRAY, fontSize: 10 }}>
                    {holdMin}/30min
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Disabled state info */}
      {!enabled && (
        <div style={{
          padding: SPACE.SM,
          background: "rgba(255,255,255,0.03)",
          borderRadius: RADIUS.SM,
          fontSize: 11,
          color: "var(--text-secondary, #999)",
          textAlign: "center",
        }}>
          Scalper mode OFF. Click "TURN ON" to activate aggressive paper trading
          (15 trades/day cap, separate from swing trades).
        </div>
      )}
    </div>
  );
}

function StatBox({ label, value, color }) {
  return (
    <div style={{
      padding: SPACE.XS,
      background: "rgba(255,255,255,0.04)",
      border: `1px solid ${color}33`,
      borderRadius: RADIUS.SM,
      textAlign: "center",
    }}>
      <div style={{ fontSize: 9, color: "var(--text-secondary, #888)", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 800, color, fontFamily: FONT.MONO || "monospace", marginTop: 2 }}>
        {value}
      </div>
    </div>
  );
}
