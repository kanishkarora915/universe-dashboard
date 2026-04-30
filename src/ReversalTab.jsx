/**
 * ReversalTab — Capitulation Engine Dashboard
 * ───────────────────────────────────────────
 * Shows live bullish + bearish capitulation scores per index with
 * 7-signal breakdown. The textbook V-shape reversal detector for
 * option BUYERS.
 */

import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";
const REFRESH_MS = 10000;

const SIGNAL_LABELS = {
  ce_writer_covering:  "CE Writer Covering",
  ce_writer_adding:    "CE Writer Adding",
  pe_writer_covering:  "PE Writer Covering",
  pe_writer_adding:    "PE Writer Adding",
  pcr_bullish_flip:    "PCR Bullish Flip",
  pcr_bearish_flip:    "PCR Bearish Flip",
  ce_premium_cheap:    "ATM CE Cheap",
  pe_premium_cheap:    "ATM PE Cheap",
  vix_cooling:         "VIX Cooling",
  vix_spiking:         "VIX Spiking",
  higher_lows:         "Higher Lows (5min)",
  lower_highs:         "Lower Highs (5min)",
};

export default function ReversalTab() {
  const [live, setLive] = useState(null);
  const [history, setHistory] = useState([]);
  const [forcing, setForcing] = useState(false);

  const refresh = async () => {
    try {
      const [l, h] = await Promise.all([
        fetch(`${API}/api/reversal/live`).then(r => r.ok ? r.json() : null),
        fetch(`${API}/api/reversal/history?limit=30`).then(r => r.ok ? r.json() : null),
      ]);
      if (l) setLive(l);
      if (h) setHistory(h.events || []);
    } catch (e) { /* silent */ }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  const forcePulse = async () => {
    setForcing(true);
    try {
      await fetch(`${API}/api/reversal/pulse-now`, { method: "POST" });
      await refresh();
    } catch (e) { /* silent */ }
    finally { setForcing(false); }
  };

  return (
    <div style={{ padding: "20px 24px", fontFamily: "ui-sans-serif" }}>
      {/* HEADER */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ color: "#fff", fontSize: 20, fontWeight: 800, letterSpacing: -0.3 }}>
            🎯 Reversal Capitulation Engine
          </div>
          <div style={{ color: "#888", fontSize: 12, marginTop: 4 }}>
            7-signal aggregator detecting V-shape bottoms (CE buy) and inverted-V tops (PE buy).
            Live since market open · refreshes every 10s.
          </div>
        </div>
        <button onClick={forcePulse} disabled={forcing} style={{
          background: "#0A84FF22", border: "1px solid #0A84FF55",
          color: "#0A84FF", fontSize: 12, fontWeight: 700,
          padding: "8px 16px", borderRadius: 6, cursor: forcing ? "wait" : "pointer",
        }}>
          {forcing ? "Pulsing…" : "⚡ Force Pulse Now"}
        </button>
      </div>

      {/* PER-INDEX CARDS */}
      {live && live.results ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(440px, 1fr))", gap: 16, marginBottom: 20 }}>
          {Object.entries(live.results).map(([idx, data]) => (
            <IndexCard key={idx} idx={idx} data={data} />
          ))}
        </div>
      ) : (
        <div style={{ background: "#111118", border: "1px solid #1E1E2E", borderRadius: 12, padding: "32px 20px", textAlign: "center", color: "#666" }}>
          ⏳ Waiting for first capitulation pulse… (60s cycle)
        </div>
      )}

      {/* TODAY'S CAPITULATION EVENTS */}
      <div style={{ background: "#111118", border: "1px solid #1E1E2E", borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ color: "#aaa", fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>
          📋 Today's Capitulation Events ({history.length})
        </div>
        {history.length === 0 ? (
          <div style={{ color: "#555", fontSize: 12, padding: "12px 0", textAlign: "center" }}>
            No capitulation events logged yet today. Engine fires when score ≥ 5 (ALERT) or ≥ 7 (STRONG).
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {history.map((e, i) => (
              <EventRow key={i} event={e} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}


function IndexCard({ idx, data }) {
  if (!data || data.error) return (
    <div style={{ background: "#111118", border: "1px solid #1E1E2E", borderRadius: 12, padding: 16, color: "#FF453A" }}>
      {idx}: {data?.error || "no data"}
    </div>
  );

  const bull = data.bullish || {};
  const bear = data.bearish || {};

  return (
    <div style={{ background: "#111118", border: "1px solid #1E1E2E", borderRadius: 12, padding: "16px 20px" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div>
          <div style={{ color: "#fff", fontSize: 16, fontWeight: 800 }}>{idx}</div>
          <div style={{ color: "#888", fontSize: 11, marginTop: 2 }}>
            Spot ₹{data.spot?.toFixed(2)} · ATM {data.atm_strike} · VIX {data.vix?.toFixed(2)}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 9, color: "#888" }}>ATM CE / PE</div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#ccc" }}>
            ₹{data.atm_ce?.toFixed(0)} / ₹{data.atm_pe?.toFixed(0)}
          </div>
        </div>
      </div>

      {/* TWIN SCORES */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
        <ScoreBox direction="BULLISH" data={bull} color="#30D158" />
        <ScoreBox direction="BEARISH" data={bear} color="#FF453A" />
      </div>

      {/* OI snapshot */}
      {data.oi_data && (
        <div style={{ background: "#0A0A0F", border: "1px solid #1E1E2E", borderRadius: 6, padding: "8px 10px", fontSize: 10, marginBottom: 12 }}>
          <div style={{ color: "#888", fontSize: 9, fontWeight: 700, textTransform: "uppercase", marginBottom: 4 }}>
            NTM OI Movement (15m)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, color: "#aaa" }}>
            <div>CE OI Δ: <span style={{ color: (data.oi_data.ce_oi_delta_15m_pct ?? 0) < 0 ? "#30D158" : "#FF9F0A", fontWeight: 700 }}>
              {data.oi_data.ce_oi_delta_15m_pct != null ? `${data.oi_data.ce_oi_delta_15m_pct > 0 ? "+" : ""}${data.oi_data.ce_oi_delta_15m_pct}%` : "—"}
            </span></div>
            <div>PE OI Δ: <span style={{ color: (data.oi_data.pe_oi_delta_15m_pct ?? 0) > 0 ? "#30D158" : "#FF9F0A", fontWeight: 700 }}>
              {data.oi_data.pe_oi_delta_15m_pct != null ? `${data.oi_data.pe_oi_delta_15m_pct > 0 ? "+" : ""}${data.oi_data.pe_oi_delta_15m_pct}%` : "—"}
            </span></div>
            <div>PCR now: <b style={{ color: "#ccc" }}>{data.oi_data.pcr_now?.toFixed(2)}</b></div>
            <div>PCR Δ15m: <span style={{ color: (data.oi_data.pcr_delta_15m ?? 0) >= 0 ? "#30D158" : "#FF453A", fontWeight: 700 }}>
              {data.oi_data.pcr_delta_15m != null ? (data.oi_data.pcr_delta_15m > 0 ? "+" : "") + data.oi_data.pcr_delta_15m.toFixed(2) : "—"}
            </span></div>
            <div>Max Pain: <b style={{ color: "#ccc" }}>{data.oi_data.max_pain_now?.toFixed(0)}</b></div>
            <div>MP shift: <b style={{ color: (data.oi_data.max_pain_shift ?? 0) >= 0 ? "#30D158" : "#FF453A" }}>
              {data.oi_data.max_pain_shift != null ? (data.oi_data.max_pain_shift > 0 ? "+" : "") + data.oi_data.max_pain_shift : "—"}
            </b></div>
          </div>
        </div>
      )}
    </div>
  );
}


function ScoreBox({ direction, data, color }) {
  const score = data.score ?? 0;
  const verdict = data.verdict || "QUIET";
  const fired = data.fired_count || 0;
  const total = data.total_signals || 6;
  const reasons = data.reasons || [];
  const action = data.recommended_action;

  const verdictColors = {
    QUIET: "#666",
    WATCH: "#FFD60A",
    ALERT: "#FF9F0A",
    STRONG_CAPITULATION: color,
  };
  const vc = verdictColors[verdict] || "#888";

  return (
    <div style={{
      background: `${color}10`, border: `1px solid ${vc}55`, borderRadius: 8, padding: "10px 12px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontSize: 10, fontWeight: 700, color, letterSpacing: 0.6, textTransform: "uppercase" }}>
          {direction === "BULLISH" ? "📈 " : "📉 "}{direction}
        </span>
        <span style={{ fontSize: 9, fontWeight: 700, color: vc, padding: "2px 6px",
                        background: `${vc}22`, borderRadius: 3, letterSpacing: 0.4 }}>
          {verdict}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 6 }}>
        <span style={{ fontSize: 24, fontWeight: 800, color: vc }}>{score.toFixed(1)}</span>
        <span style={{ fontSize: 10, color: "#888" }}>/ 10 · {fired}/{total} signals</span>
      </div>

      {/* Signal grid */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 6 }}>
        {Object.entries(data.signals || {}).map(([key, sig]) => (
          <span key={key} style={{
            fontSize: 8, padding: "2px 5px", borderRadius: 3,
            background: sig.fired ? `${color}33` : "rgba(255,255,255,0.04)",
            color: sig.fired ? color : "#555",
            border: sig.fired ? `1px solid ${color}55` : "1px solid transparent",
            fontWeight: 600,
          }}>
            {sig.fired ? "✓ " : "· "}{SIGNAL_LABELS[key] || key}
          </span>
        ))}
      </div>

      {/* Reasons */}
      {reasons.length > 0 && (
        <div style={{ fontSize: 9, color: "#aaa", lineHeight: 1.4, marginTop: 4 }}>
          {reasons.slice(0, 3).map((r, i) => (
            <div key={i}>· {r}</div>
          ))}
        </div>
      )}

      {/* Action recommendation */}
      {action && (
        <div style={{
          marginTop: 6, padding: "4px 8px", background: color, color: "#000",
          borderRadius: 4, fontSize: 11, fontWeight: 700, textAlign: "center",
          letterSpacing: 0.4,
        }}>
          → {action}
        </div>
      )}
    </div>
  );
}


function EventRow({ event }) {
  const t = new Date(event.ts * 1000).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
  const isStrong = event.verdict === "STRONG_CAPITULATION";
  const isBull = event.direction === "BULLISH";
  const color = isStrong ? (isBull ? "#30D158" : "#FF453A") : "#FF9F0A";
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "8px 10px", background: "#0A0A0F", borderRadius: 6,
      border: `1px solid ${color}33`, fontSize: 11, gap: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flex: 1 }}>
        <span style={{ color: "#666", fontFamily: "ui-monospace, monospace", fontSize: 10 }}>{t}</span>
        <span style={{
          background: color, color: "#000", padding: "1px 6px", borderRadius: 3,
          fontSize: 9, fontWeight: 700, letterSpacing: 0.3,
        }}>
          {event.direction}
        </span>
        <span style={{ color: "#fff", fontWeight: 700 }}>{event.idx}</span>
        <span style={{ color: "#aaa" }}>spot ₹{event.spot?.toFixed(0)}</span>
        <span style={{ color, fontWeight: 700 }}>{event.score?.toFixed(1)}/10</span>
        <span style={{ color: "#666", fontSize: 10 }}>{event.signal_count} signals</span>
      </div>
      {event.recommended_action && (
        <span style={{ color, fontSize: 10, fontWeight: 700 }}>
          {event.recommended_action}
        </span>
      )}
    </div>
  );
}
