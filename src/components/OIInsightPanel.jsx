/**
 * OIInsightPanel — Side-by-side TODAY's OI Change vs TOTAL OI.
 * Shows what each indicates with calculations + buyer signal.
 *
 * Endpoint: GET /api/oi-insight/{index}
 */

import React, { useEffect, useState } from "react";

const GREEN = "#10b981";
const RED = "#ef4444";
const YELLOW = "#f59e0b";
const BLUE = "#3b82f6";
const GRAY = "#6b7280";
const BG = "#0b0f14";
const CARD = "#111823";
const BORDER = "#1f2937";

const fmtL = (n) => `${n >= 0 ? "+" : ""}${(n / 100000).toFixed(2)}L`;
const fmtLAbs = (n) => `${(Math.abs(n) / 100000).toFixed(1)}L`;

function biasColor(bias) {
  if (bias === "BULLISH" || bias === "BULL BIAS") return GREEN;
  if (bias === "BEARISH" || bias === "BEAR BIAS") return RED;
  if (bias === "UNCERTAIN") return YELLOW;
  return GRAY;
}

function signalColor(sig) {
  if (sig === "BUY CE") return GREEN;
  if (sig === "BUY PE") return RED;
  if (sig === "WAIT" || sig === "RANGE") return YELLOW;
  return GRAY;
}

export default function OIInsightPanel({ index = "NIFTY", apiBase = "" }) {
  const [data, setData] = useState(null);
  const [view, setView] = useState("today"); // today | total | both
  const [err, setErr] = useState(null);

  const load = async () => {
    try {
      const r = await fetch(`${apiBase}/api/oi-insight/${index}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      setData(j);
      setErr(null);
    } catch (e) { setErr(e.message); }
  };

  useEffect(() => {
    load();
    const iv = setInterval(load, 15_000);
    return () => clearInterval(iv);
  }, [index]);

  if (err) return <div style={wrap}><div style={{ color: RED, fontSize: 12 }}>OI Insight error: {err}</div></div>;
  if (!data) return <div style={wrap}><div style={{ color: GRAY, fontSize: 12 }}>Loading OI insight…</div></div>;

  const today = data.today || {};
  const total = data.total || {};

  return (
    <div style={wrap}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ color: "#e5e7eb", fontWeight: 800, fontSize: 14 }}>📊 OI INSIGHT — {data.index}</div>
          <div style={{ color: GRAY, fontSize: 10, marginTop: 2 }}>
            LTP {data.ltp} · ATM {data.atm} · Buyer perspective
          </div>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {[
            { id: "today", label: "TODAY (Primary)" },
            { id: "total", label: "TOTAL" },
            { id: "both", label: "BOTH" },
          ].map(v => (
            <button key={v.id} onClick={() => setView(v.id)} style={{
              background: view === v.id ? BLUE : "transparent",
              color: view === v.id ? "#fff" : GRAY,
              border: `1px solid ${view === v.id ? BLUE : BORDER}`,
              borderRadius: 4, padding: "4px 10px", fontSize: 10, fontWeight: 700, cursor: "pointer",
            }}>{v.label}</button>
          ))}
        </div>
      </div>

      {/* PRIMARY SIGNAL BANNER (always visible) */}
      <div style={{
        background: signalColor(data.primary_signal) + "15",
        border: `1px solid ${signalColor(data.primary_signal)}55`,
        borderRadius: 8, padding: "10px 14px", marginBottom: 14,
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <div style={{ fontSize: 11, color: GRAY, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>
            🎯 Primary Signal (from today's OI)
          </div>
          <div style={{
            background: signalColor(data.primary_signal),
            color: "#fff", padding: "3px 12px", borderRadius: 4,
            fontSize: 12, fontWeight: 800,
          }}>{data.primary_signal}</div>
        </div>
        <div style={{ fontSize: 12, color: "#d1d5db", lineHeight: 1.5 }}>{data.primary_reason}</div>
      </div>

      {/* TODAY's OI Change */}
      {(view === "today" || view === "both") && (
        <Section
          title="TODAY's OI CHANGE"
          subtitle="Aaj 9:15 AM se ab tak — fresh activity (most predictive)"
          tag="PRIMARY"
          tagColor={GREEN}
        >
          {/* CE vs PE columns */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <OIColumn
              title="CE OI Change"
              netValue={today.ce_oi_net}
              added={today.ce_oi_added}
              removed={today.ce_oi_removed}
              tops={today.top_ce_writing || []}
              tops2={today.top_ce_covering || []}
              labelTops="Top CE Writing (resistance)"
              labelTops2="Top CE Covering (resistance breaking)"
              keyTops="added"
              keyTops2="removed"
              meaning={
                today.ce_oi_net >= 0
                  ? "CE writers active = sellers expect price NOT to go above these strikes (bearish/range)"
                  : "CE covering = sellers exiting = resistance weakening (bullish for upside)"
              }
              color={today.ce_oi_net >= 0 ? RED : GREEN}
            />
            <OIColumn
              title="PE OI Change"
              netValue={today.pe_oi_net}
              added={today.pe_oi_added}
              removed={today.pe_oi_removed}
              tops={today.top_pe_writing || []}
              tops2={today.top_pe_covering || []}
              labelTops="Top PE Writing (support)"
              labelTops2="Top PE Covering (support breaking)"
              keyTops="added"
              keyTops2="removed"
              meaning={
                today.pe_oi_net >= 0
                  ? "PE writers active = sellers expect price NOT to fall below = support (bullish for upside)"
                  : "PE covering = sellers exiting = support weakening (bearish for downside)"
              }
              color={today.pe_oi_net >= 0 ? GREEN : RED}
            />
          </div>

          {/* Summary stats + interpretation */}
          <div style={{ marginTop: 12, padding: 10, background: BG, borderRadius: 6, border: `1px solid ${BORDER}` }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 8 }}>
              <Metric label="Net CE Build-up" value={fmtL(today.ce_oi_net || 0)} color={(today.ce_oi_net || 0) >= 0 ? RED : GREEN} />
              <Metric label="Net PE Build-up" value={fmtL(today.pe_oi_net || 0)} color={(today.pe_oi_net || 0) >= 0 ? GREEN : RED} />
              <Metric label="PCR Change" value={(today.pcr_change || 0).toFixed(2)} color={(today.pcr_change || 0) > 1 ? GREEN : RED} />
            </div>
            <div style={{ fontSize: 11, color: "#9ca3af", lineHeight: 1.5, fontStyle: "italic" }}>
              💡 <b style={{ color: biasColor(today.bias) }}>{today.bias}</b> — {today.reason}
            </div>
            <div style={{ marginTop: 6, fontSize: 10, color: GRAY }}>
              Logic: {today.explain?.logic}
            </div>
          </div>
        </Section>
      )}

      {/* TOTAL OI */}
      {(view === "total" || view === "both") && (
        <Section
          title="TOTAL OI (Cumulative)"
          subtitle="Contract start se ab tak — historical context"
          tag="SECONDARY"
          tagColor={GRAY}
          marginTop={view === "both" ? 16 : 0}
        >
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <TotalCard
              title="Total CE OI"
              value={total.ce_oi}
              wallStrike={total.ce_wall_strike}
              wallOI={total.ce_wall_oi}
              wallLabel="CE Wall (Resistance)"
              color={RED}
              meaning="Resistance ceiling — price me upar ye level rokega"
            />
            <TotalCard
              title="Total PE OI"
              value={total.pe_oi}
              wallStrike={total.pe_wall_strike}
              wallOI={total.pe_wall_oi}
              wallLabel="PE Wall (Support)"
              color={GREEN}
              meaning="Support floor — price me niche ye level rokega"
            />
          </div>

          <div style={{ marginTop: 12, padding: 10, background: BG, borderRadius: 6, border: `1px solid ${BORDER}` }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
              <Metric label="Total PCR" value={(total.pcr || 0).toFixed(2)} color={(total.pcr || 0) > 1 ? GREEN : RED} />
              <Metric label="Bias" value={total.signal} color={biasColor(total.signal)} />
            </div>
            <div style={{ fontSize: 11, color: "#9ca3af", lineHeight: 1.5 }}>
              {total.reason}
            </div>
            <div style={{ marginTop: 6, fontSize: 10, color: GRAY }}>
              {total.explain?.pcr}
            </div>
          </div>
        </Section>
      )}

      {/* Calculation Reference */}
      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: "pointer", color: GRAY, fontSize: 11, fontWeight: 600 }}>
          📐 Calculation Formula (click to expand)
        </summary>
        <div style={{ marginTop: 8, padding: 10, background: BG, borderRadius: 6, fontSize: 10, color: "#9ca3af", lineHeight: 1.7 }}>
          <div><b style={{ color: GREEN }}>TODAY's OI Change:</b></div>
          <div>• CE OI Net = Σ(current_ce_oi − ce_oi_at_9:15AM) across all strikes</div>
          <div>• PE OI Net = Σ(current_pe_oi − pe_oi_at_9:15AM) across all strikes</div>
          <div>• PCR Change = PE_net / CE_net</div>
          <div style={{ marginTop: 6 }}><b style={{ color: BLUE }}>TOTAL OI (Cumulative):</b></div>
          <div>• Total CE = Σ(ce_oi) across all strikes</div>
          <div>• Total PE = Σ(pe_oi) across all strikes</div>
          <div>• PCR = Total PE / Total CE</div>
          <div>• CE Wall = strike with max CE OI (resistance)</div>
          <div>• PE Wall = strike with max PE OI (support)</div>
          <div style={{ marginTop: 6, color: YELLOW }}>
            <b>Buyer Rule:</b> Today's OI change &gt; Total OI for direction. Total OI shows zone limits.
          </div>
        </div>
      </details>
    </div>
  );
}

// ─── Sub-components ───

function Section({ title, subtitle, tag, tagColor, marginTop = 0, children }) {
  return (
    <div style={{ marginTop }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div>
          <div style={{ color: "#d1d5db", fontWeight: 700, fontSize: 12 }}>{title}</div>
          <div style={{ color: GRAY, fontSize: 10 }}>{subtitle}</div>
        </div>
        <span style={{
          background: `${tagColor}22`, color: tagColor,
          fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 4, letterSpacing: 0.5,
        }}>{tag}</span>
      </div>
      {children}
    </div>
  );
}

function OIColumn({ title, netValue, added, removed, tops, tops2, labelTops, labelTops2, keyTops, keyTops2, meaning, color }) {
  return (
    <div style={{ background: BG, border: `1px solid ${color}33`, borderRadius: 6, padding: 10 }}>
      <div style={{ color, fontSize: 11, fontWeight: 700, marginBottom: 4 }}>{title}</div>
      <div style={{ color: "#fff", fontSize: 18, fontWeight: 800 }}>{fmtL(netValue || 0)}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 4, fontSize: 10, color: GRAY }}>
        <span>Added: <b style={{ color: GREEN }}>+{fmtLAbs(added || 0)}</b></span>
        <span>Removed: <b style={{ color: RED }}>−{fmtLAbs(removed || 0)}</b></span>
      </div>

      {/* Top strikes */}
      {tops.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ color: GRAY, fontSize: 9, fontWeight: 600 }}>{labelTops}:</div>
          {tops.slice(0, 3).map((s, i) => (
            <div key={i} style={{ fontSize: 10, color: "#d1d5db", display: "flex", justifyContent: "space-between" }}>
              <span>{s.strike}</span>
              <span style={{ color: RED, fontWeight: 600 }}>+{fmtLAbs(s[keyTops])}</span>
            </div>
          ))}
        </div>
      )}
      {tops2.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ color: GRAY, fontSize: 9, fontWeight: 600 }}>{labelTops2}:</div>
          {tops2.slice(0, 3).map((s, i) => (
            <div key={i} style={{ fontSize: 10, color: "#d1d5db", display: "flex", justifyContent: "space-between" }}>
              <span>{s.strike}</span>
              <span style={{ color: GREEN, fontWeight: 600 }}>−{fmtLAbs(s[keyTops2])}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: 8, padding: 6, background: CARD, borderRadius: 4, fontSize: 10, color: "#9ca3af", lineHeight: 1.4 }}>
        {meaning}
      </div>
    </div>
  );
}

function TotalCard({ title, value, wallStrike, wallOI, wallLabel, color, meaning }) {
  return (
    <div style={{ background: BG, border: `1px solid ${color}33`, borderRadius: 6, padding: 10 }}>
      <div style={{ color, fontSize: 11, fontWeight: 700, marginBottom: 4 }}>{title}</div>
      <div style={{ color: "#fff", fontSize: 18, fontWeight: 800 }}>{(value / 100000).toFixed(1)}L</div>
      <div style={{ marginTop: 8, padding: 6, background: CARD, borderRadius: 4 }}>
        <div style={{ fontSize: 9, color: GRAY, fontWeight: 600 }}>{wallLabel}</div>
        <div style={{ fontSize: 13, color: "#fff", fontWeight: 700 }}>{wallStrike}</div>
        <div style={{ fontSize: 9, color }}>OI: {(wallOI / 100000).toFixed(1)}L</div>
      </div>
      <div style={{ marginTop: 8, fontSize: 10, color: "#9ca3af", lineHeight: 1.4 }}>
        {meaning}
      </div>
    </div>
  );
}

function Metric({ label, value, color }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: GRAY, fontWeight: 600, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 14, color, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

const wrap = {
  background: CARD,
  border: `1px solid ${BORDER}`,
  borderRadius: 10,
  padding: 14,
  marginTop: 12,
};
