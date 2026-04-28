/**
 * TrinityTab — Real vs Trap vs Fake Move Detection
 *
 * Triangulates: Spot (truth) + Future (intent) + Synthetic (stress via PCP)
 * 7-regime classifier · Trap zones · Strike recommender
 *
 * Layout (per spec §8.1):
 *   Section A: Header strip
 *   Section B: 3-panel chart (spot+future, premium oscillator, trinity deviation)
 *   Section C: Strike heatmap (9 cells)
 *   Section D: Signal sidebar
 */

import { useState, useEffect, useRef, useCallback } from "react";
import HeaderStrip from "./trinity/HeaderStrip";
import ThreePanelChart from "./trinity/ThreePanelChart";
import StrikeHeatmap from "./trinity/StrikeHeatmap";
import SignalSidebar from "./trinity/SignalSidebar";

const BG = "#0a0e1a";

async function safeFetch(url, fb) {
  try { const r = await fetch(url); if (!r.ok) return fb; return await r.json(); } catch { return fb; }
}

async function postJSON(url, body) {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await r.json();
  } catch { return null; }
}

export default function TrinityTab() {
  const [snapshot, setSnapshot] = useState(null);
  const [status, setStatus] = useState(null);
  const [series, setSeries] = useState([]);
  const [heatmap, setHeatmap] = useState(null);
  const [trapZones, setTrapZones] = useState(null);
  const [activeSignals, setActiveSignals] = useState([]);
  const [recentSignals, setRecentSignals] = useState([]);
  const [config, setConfig] = useState(null);
  const [windowMins, setWindowMins] = useState(30);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef(null);

  // Edit risk capital
  const [riskInput, setRiskInput] = useState("");

  const loadAll = useCallback(async () => {
    const [snap, st, sigA, sigH, hm, tz, cfg, ts] = await Promise.all([
      safeFetch("/api/trinity/snapshot", null),
      safeFetch("/api/trinity/status", null),
      safeFetch("/api/trinity/signals/active", { signals: [] }),
      safeFetch("/api/trinity/signals/history?limit=10", { signals: [] }),
      safeFetch("/api/trinity/strikes/heatmap", null),
      safeFetch("/api/trinity/trap-zones", null),
      safeFetch("/api/trinity/config", null),
      safeFetch(`/api/trinity/timeseries?mins=${windowMins}`, { data: [] }),
    ]);
    if (snap && !snap.error) setSnapshot(snap);
    if (st && !st.error) setStatus(st);
    if (sigA?.signals) setActiveSignals(sigA.signals);
    if (sigH?.signals) setRecentSignals(sigH.signals);
    if (hm && !hm.error) setHeatmap(hm);
    if (tz && !tz.error) setTrapZones(tz);
    if (cfg) {
      setConfig(cfg);
      if (!riskInput) setRiskInput(String(cfg.risk_capital || 1000000));
    }
    if (ts?.data) setSeries(ts.data);
  }, [windowMins, riskInput]);

  // Initial load + 5s polling fallback
  useEffect(() => {
    loadAll();
    // 30s for full state (WebSocket /ws/trinity/live handles realtime)
    const iv = setInterval(() => { if (document.visibilityState === "visible") loadAll(); }, 30000);
    return () => clearInterval(iv);
  }, [loadAll]);

  // Live WS subscription for snapshots (1Hz from backend)
  useEffect(() => {
    let cancelled = false;
    function connect() {
      try {
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${proto}//${window.location.host}/ws/trinity/live`;
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => { if (!cancelled) setWsConnected(true); };
        ws.onclose = () => {
          if (!cancelled) {
            setWsConnected(false);
            setTimeout(connect, 3000);  // exponential backoff (simple 3s)
          }
        };
        ws.onerror = () => { if (!cancelled) setWsConnected(false); };
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "snapshot" && msg.data) {
              setSnapshot(prev => ({ ...prev, ...msg.data }));
            }
          } catch {}
        };
      } catch (e) {
        console.error("[Trinity WS] connect error", e);
        if (!cancelled) setTimeout(connect, 5000);
      }
    }
    connect();
    return () => {
      cancelled = true;
      if (wsRef.current) try { wsRef.current.close(); } catch {}
    };
  }, []);

  const saveRiskCapital = async () => {
    const v = parseFloat(riskInput) || 1000000;
    await postJSON("/api/trinity/config", { risk_capital: v });
    await loadAll();
  };

  // Pick best active signal
  const topSignal = activeSignals && activeSignals.length > 0
    ? activeSignals.find(s => s.signal_type && s.signal_type.startsWith("BUY_")) || activeSignals[0]
    : null;

  const regime = snapshot?.regime || status?.current_regime || "UNKNOWN";
  const confidence = snapshot?.confidence || (topSignal && topSignal.confidence) || 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, background: BG, padding: 4 }}>
      {/* SECTION A: HEADER STRIP */}
      <HeaderStrip
        regime={regime}
        confidence={confidence}
        activeSignal={topSignal}
        connected={wsConnected || (status && !status.degraded)}
        status={status}
        snapshot={snapshot}
      />

      {/* INFO BAR — degraded/lunch/news warnings */}
      {(status?.degraded || status?.first_5min || status?.lunch_hour) && (
        <div style={{
          background: "#ffaa0011", border: "1px solid #ffaa0044",
          color: "#ffaa00", padding: "8px 12px", borderRadius: 8,
          fontSize: 11, display: "flex", gap: 12, flexWrap: "wrap",
        }}>
          {status?.degraded && <span>⚠️ Stale tick — synthetic DEGRADED</span>}
          {status?.first_5min && <span>⏳ First 5 min — no signals (synthetic stabilizing)</span>}
          {status?.lunch_hour && <span>🍱 Lunch hour — confidence capped 75%</span>}
          {status?.expiry_day && <span>📅 NIFTY EXPIRY DAY — 3-min EMA active</span>}
        </div>
      )}

      {/* MAIN GRID: chart (left) + signal sidebar (right) */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "1fr 320px",
        gap: 12,
      }}>
        {/* LEFT: Chart + Heatmap */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Window controls */}
          <div style={{
            display: "flex", justifyContent: "space-between",
            alignItems: "center", padding: "0 4px",
          }}>
            <div style={{ display: "flex", gap: 6 }}>
              {[15, 30, 60, 180, 360].map(m => (
                <button key={m} onClick={() => setWindowMins(m)} style={{
                  background: windowMins === m ? "#00d4ff" : "transparent",
                  color: windowMins === m ? "#000" : "#666",
                  border: `1px solid ${windowMins === m ? "#00d4ff" : "#1a2030"}`,
                  borderRadius: 4, padding: "3px 10px", fontSize: 10, fontWeight: 700,
                  cursor: "pointer",
                }}>
                  {m}m
                </button>
              ))}
            </div>
            <div style={{ fontSize: 10, color: "#666" }}>
              {series.length} bars · ATM {status?.atm} · FUT {status?.fut_meta?.tradingsymbol || "—"}
            </div>
          </div>

          {/* SECTION B: 3-panel chart */}
          <ThreePanelChart data={series} premiumBaseline={0} />

          {/* SECTION C: Strike heatmap */}
          <StrikeHeatmap heatmap={heatmap} />
        </div>

        {/* RIGHT: SECTION D — Signal sidebar */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <SignalSidebar
            activeSignal={topSignal}
            recentSignals={recentSignals}
            trapZones={trapZones}
            regime={regime}
            snapshot={snapshot}
          />

          {/* CONFIG PANEL */}
          <div style={{
            background: "#0a0e1a", border: "1px solid #1a2030",
            borderRadius: 12, padding: 14,
          }}>
            <div style={{
              fontSize: 10, color: "#888", fontWeight: 700,
              textTransform: "uppercase", letterSpacing: 1, marginBottom: 8,
            }}>
              Risk Capital (₹)
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                type="text"
                value={riskInput}
                onChange={(e) => setRiskInput(e.target.value)}
                style={{
                  flex: 1,
                  background: "#000", border: "1px solid #1a2030",
                  color: "#fff", padding: "6px 10px", borderRadius: 4,
                  fontSize: 12, fontWeight: 600, outline: "none",
                }}
                placeholder="1000000"
              />
              <button onClick={saveRiskCapital} style={{
                background: "#00d4ff", color: "#000", border: "none",
                padding: "6px 14px", borderRadius: 4,
                fontSize: 11, fontWeight: 800, cursor: "pointer",
              }}>
                Save
              </button>
            </div>
            <div style={{ fontSize: 9, color: "#666", marginTop: 4 }}>
              Used for position sizing. Suggested lots = floor(capital × conf/100 / (premium × lot_size))
            </div>
          </div>

          {/* SUBSCRIPTION STATUS */}
          {status?.subscription && (
            <div style={{
              background: "#0a0e1a", border: "1px solid #1a2030",
              borderRadius: 12, padding: 14,
            }}>
              <div style={{
                fontSize: 10, color: "#888", fontWeight: 700,
                textTransform: "uppercase", letterSpacing: 1, marginBottom: 8,
              }}>
                Trinity Subscription
              </div>
              <div style={{ fontSize: 10, color: "#aaa", lineHeight: 1.6 }}>
                <div>NIFTY Spot: <span style={{ color: status.subscription.spot_subscribed ? "#00ff88" : "#ff3366" }}>
                  {status.subscription.spot_subscribed ? "✓" : "✗"}
                </span></div>
                <div>NIFTY FUT: <span style={{ color: status.subscription.fut_subscribed ? "#00ff88" : "#ff3366" }}>
                  {status.subscription.fut_subscribed ? `✓ ${status.fut_meta?.tradingsymbol || ""}` : "✗"}
                </span></div>
                <div>Strikes (9): <span style={{ color: "#00ff88" }}>
                  {Object.values(status.subscription.strikes || {}).filter(s => s.ce_subscribed && s.pe_subscribed).length}/9
                </span></div>
                <div>Total subscribed: {status.subscription.total_subscribed}</div>
                <div>Buffer: {status.buffer_size} bars</div>
                {status.regime_duration_secs > 0 && (
                  <div>Regime duration: {Math.round(status.regime_duration_secs)}s</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
