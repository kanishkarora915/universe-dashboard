/**
 * ReversalTab — Capitulation Engine Dashboard
 * ───────────────────────────────────────────
 * Shows live bullish + bearish capitulation scores per index with
 * 7-signal breakdown. The textbook V-shape reversal detector for
 * option BUYERS.
 */

import { useEffect, useState } from "react";
import useSWRPoll from "./hooks/useSWRPoll";

const API = import.meta.env.VITE_API_URL || "";
const REFRESH_MS = 5000;  // was 10000 — tighter for "real-time" feel

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
  const [forcing, setForcing] = useState(false);

  // SWR — auto-refresh every 5s, ALSO refreshes on tab focus return.
  // Was using raw setInterval which only fired while tab visible — and
  // user complained about "not real-time" feel.
  const { data: live, mutate: mutateLive } = useSWRPoll(
    "/api/reversal/live",
    { refreshInterval: REFRESH_MS, revalidateOnFocus: true }
  );
  const { data: histResp, mutate: mutateHist } = useSWRPoll(
    "/api/reversal/history?limit=30",
    { refreshInterval: 30000, revalidateOnFocus: true }
  );
  const history = histResp?.events || [];

  const forcePulse = async () => {
    setForcing(true);
    try {
      await fetch(`${API}/api/reversal/pulse-now`, { method: "POST" });
      // Force fresh fetch — bypasses cache
      await Promise.all([mutateLive(), mutateHist()]);
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

      {/* MARKET STRUCTURE EVOLUTION (S/R polarity flips) */}
      <MarketStructureSection />

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


/* ═════════════════════════════════════════════════════════════════════
   MARKET STRUCTURE / POLARITY FLIP SECTION
   "Pehle kya tha vs ab kya hai" — S/R role tracking
   ═════════════════════════════════════════════════════════════════════ */

function MarketStructureSection() {
  const [idx, setIdx] = useState("NIFTY");
  const [levels, setLevels] = useState(null);
  const [flips, setFlips] = useState([]);
  const [timeline, setTimeline] = useState([]);

  const refresh = async () => {
    try {
      const [l, f, t] = await Promise.all([
        fetch(`${API}/api/structure/levels?idx=${idx}`).then(r => r.ok ? r.json() : null),
        fetch(`${API}/api/structure/flips?idx=${idx}&limit=20`).then(r => r.ok ? r.json() : null),
        fetch(`${API}/api/structure/timeline?idx=${idx}`).then(r => r.ok ? r.json() : null),
      ]);
      if (l) setLevels(l);
      if (f) setFlips(f.events || []);
      if (t) setTimeline(t.snapshots || []);
    } catch (e) { /* silent */ }
  };

  useEffect(() => {
    refresh();
    const tt = setInterval(refresh, 15000);
    return () => clearInterval(tt);
  }, [idx]);

  return (
    <div style={{
      background: "#111118", border: "1px solid #1E1E2E",
      borderRadius: 12, padding: "16px 20px", marginBottom: 20,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8,
      }}>
        <div>
          <div style={{
            color: "#A0DC5A", fontSize: 14, fontWeight: 800,
            textTransform: "uppercase", letterSpacing: 0.8,
          }}>
            🔁 Market Structure Evolution
          </div>
          <div style={{ color: "#888", fontSize: 11, marginTop: 4 }}>
            S/R role tracking — pehle kya tha vs ab kya hai. Detects R↔S polarity flips.
          </div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {["NIFTY", "BANKNIFTY"].map(i => (
            <button key={i} onClick={() => setIdx(i)} style={{
              background: idx === i ? "#A0DC5A22" : "transparent",
              color: idx === i ? "#A0DC5A" : "#888",
              border: `1px solid ${idx === i ? "#A0DC5A55" : "#2A2A3F"}`,
              padding: "5px 12px", fontSize: 11, fontWeight: 700,
              borderRadius: 6, cursor: "pointer", letterSpacing: 0.4,
            }}>
              {i}
            </button>
          ))}
        </div>
      </div>

      {/* CURRENT LEVELS — TWO COLUMNS (R / S) */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
        <LevelColumn title="🔴 RESISTANCE (above spot)"
                     levels={levels?.resistances || []} role="R" />
        <LevelColumn title="🟢 SUPPORT (below spot)"
                     levels={levels?.supports || []} role="S" />
      </div>

      {/* FLIPPED LEVELS HIGHLIGHT */}
      {levels?.flipped_count > 0 && (
        <div style={{
          background: "rgba(160,220,90,0.08)", border: "1px solid #A0DC5A55",
          borderRadius: 8, padding: "8px 12px", marginBottom: 12,
        }}>
          <div style={{
            color: "#A0DC5A", fontSize: 10, fontWeight: 700,
            textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 4,
          }}>
            ⚡ {levels.flipped_count} levels FLIPPED today
          </div>
          {levels.all_levels.filter(l => l.is_flipped).map((l, i) => (
            <div key={i} style={{ color: "#ddd", fontSize: 11, lineHeight: 1.5 }}>
              · <strong>{Math.round(l.level)}</strong>: {l.initial_role === "R" ? "Resistance" : "Support"}
              {" → "}
              <span style={{ color: l.current_role === "S" ? "#30D158" : "#FF453A", fontWeight: 700 }}>
                {l.current_role === "R" ? "Resistance" : "Support"}
              </span>
              <span style={{ color: "#666", fontSize: 10, marginLeft: 6 }}>
                ({l.touches} touches · {l.flip_count} flip{l.flip_count !== 1 ? "s" : ""})
              </span>
            </div>
          ))}
        </div>
      )}

      {/* FLIP EVENTS TODAY */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ color: "#888", fontSize: 10, fontWeight: 700,
                      textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 6 }}>
          ⚡ Today's Polarity Flips ({flips.length})
        </div>
        {flips.length === 0 ? (
          <div style={{ color: "#555", fontSize: 11, padding: "8px 0", textAlign: "center" }}>
            No flips detected yet today. Engine fires when spot crosses + holds for 3 min.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {flips.slice(0, 10).map((f, i) => <FlipEventRow key={i} flip={f} />)}
          </div>
        )}
      </div>

      {/* TIMELINE: pehle kya tha vs ab kya hai */}
      {timeline.length >= 2 && (
        <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px dashed #1E1E2E" }}>
          <div style={{ color: "#888", fontSize: 10, fontWeight: 700,
                        textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 8 }}>
            📜 Pehle vs Ab — S/R Snapshots
          </div>
          <TimelineComparison snapshots={timeline} />
        </div>
      )}
    </div>
  );
}

function LevelColumn({ title, levels, role }) {
  const isR = role === "R";
  const color = isR ? "#FF453A" : "#30D158";
  return (
    <div style={{ background: "#0A0A0F", border: "1px solid #1E1E2E",
                  borderRadius: 8, padding: "10px 12px" }}>
      <div style={{ color, fontSize: 10, fontWeight: 700,
                    textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>
        {title}
      </div>
      {levels.length === 0 ? (
        <div style={{ color: "#555", fontSize: 10, padding: "6px 0" }}>None tracked yet</div>
      ) : (
        levels.slice(0, 6).map((l, i) => (
          <div key={i} style={{
            display: "flex", justifyContent: "space-between",
            padding: "4px 0", fontSize: 11, borderBottom: i < levels.length - 1 ? "1px dashed #1E1E2E20" : "none",
          }}>
            <span style={{ color: "#fff", fontWeight: 700 }}>
              {Math.round(l.level)}
              <span style={{ color: "#666", fontSize: 9, marginLeft: 6, fontWeight: 500 }}>
                {l.source}
              </span>
            </span>
            <span style={{ color: "#888", fontSize: 10 }}>
              {l.touches > 0 && <span>{l.touches}× touched</span>}
              {l.is_flipped && <span style={{ color: "#A0DC5A", marginLeft: 6, fontWeight: 700 }}>⚡FLIPPED</span>}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function FlipEventRow({ flip }) {
  const t = new Date(flip.ts * 1000).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
  const isBreakout = flip.to_role === "S";
  const color = isBreakout ? "#30D158" : "#FF453A";
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "6px 10px", background: `${color}10`, borderRadius: 5,
      border: `1px solid ${color}33`, fontSize: 11, gap: 8, flexWrap: "wrap",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0 }}>
        <span style={{ color: "#666", fontFamily: "ui-monospace, monospace", fontSize: 10 }}>{t}</span>
        <span style={{
          background: color, color: "#000", padding: "1px 6px", borderRadius: 3,
          fontSize: 9, fontWeight: 700, letterSpacing: 0.3,
        }}>
          {isBreakout ? "BREAKOUT" : "BREAKDOWN"}
        </span>
        <span style={{ color: "#fff", fontWeight: 700 }}>
          {Math.round(flip.level)}
        </span>
        <span style={{ color: "#888", fontSize: 10 }}>
          {flip.from_role === "R" ? "Resistance" : "Support"} → {flip.to_role === "S" ? "Support" : "Resistance"}
        </span>
        {flip.oi_change_pct !== 0 && (
          <span style={{ color: "#888", fontSize: 9 }}>
            OI {flip.oi_change_pct > 0 ? "+" : ""}{flip.oi_change_pct}%
          </span>
        )}
      </div>
      <span style={{ color: "#aaa", fontSize: 10 }}>
        spot ₹{Math.round(flip.spot_at_flip)}
      </span>
    </div>
  );
}

function TimelineComparison({ snapshots }) {
  const first = snapshots[0];
  const last = snapshots[snapshots.length - 1];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
      <SnapshotCard title="🌅 PEHLE (Earliest)" snap={first} />
      <SnapshotCard title="📍 AB (Latest)" snap={last} />
    </div>
  );
}

function SnapshotCard({ title, snap }) {
  if (!snap) return null;
  const t = new Date(snap.ts * 1000).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
  return (
    <div style={{ background: "#0A0A0F", border: "1px solid #1E1E2E",
                  borderRadius: 8, padding: "10px 12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ color: "#fff", fontSize: 11, fontWeight: 700 }}>{title}</span>
        <span style={{ color: "#666", fontSize: 10, fontFamily: "ui-monospace, monospace" }}>
          {t} · {snap.tag}
        </span>
      </div>
      <div style={{ color: "#aaa", fontSize: 11, marginBottom: 6 }}>
        Spot ₹{snap.spot?.toFixed(0)}
      </div>
      <div style={{ fontSize: 10, color: "#888", marginBottom: 4 }}>
        🔴 R: {(snap.resistances || []).slice(0, 3).map(r => Math.round(r.level)).join(", ") || "—"}
      </div>
      <div style={{ fontSize: 10, color: "#888" }}>
        🟢 S: {(snap.supports || []).slice(0, 3).map(s => Math.round(s.level)).join(", ") || "—"}
      </div>
    </div>
  );
}
