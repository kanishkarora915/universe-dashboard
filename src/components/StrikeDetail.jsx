import { useState, useEffect, useMemo } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION } from "../theme";

async function fetchJSON(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) {
      return { _fetchError: `HTTP ${res.status}: ${res.statusText}` };
    }
    return await res.json();
  } catch (e) {
    return { _fetchError: e?.message || "Network error — is backend running?" };
  }
}

function Card({ title, children, theme, style = {} }) {
  return (
    <div
      style={{
        background: theme.SURFACE,
        border: `1px solid ${theme.BORDER}`,
        borderRadius: RADIUS.LG,
        padding: SPACE.LG,
        ...style,
      }}
    >
      {title && (
        <div
          style={{
            color: theme.TEXT_DIM,
            fontSize: TEXT_SIZE.MICRO,
            fontWeight: TEXT_WEIGHT.BOLD,
            letterSpacing: 1.5,
            textTransform: "uppercase",
            marginBottom: SPACE.MD,
          }}
        >
          {title}
        </div>
      )}
      {children}
    </div>
  );
}

function Stat({ label, value, sub, color, mono = true, theme }) {
  return (
    <div>
      <div
        style={{
          color: theme.TEXT_DIM,
          fontSize: 9,
          fontWeight: TEXT_WEIGHT.BOLD,
          letterSpacing: 1,
          textTransform: "uppercase",
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      <div
        style={{
          color: color || theme.TEXT,
          fontSize: 15,
          fontWeight: TEXT_WEIGHT.BOLD,
          fontFamily: mono ? FONT.MONO : FONT.UI,
        }}
      >
        {value}
      </div>
      {sub && (
        <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, marginTop: 2 }}>{sub}</div>
      )}
    </div>
  );
}

function SubTab({ label, active, onClick, theme }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: `6px ${SPACE.MD}px`,
        background: active ? theme.SURFACE_ACTIVE : "transparent",
        color: active ? theme.TEXT : theme.TEXT_MUTED,
        border: "none",
        borderBottom: active ? `2px solid ${theme.ACCENT}` : `2px solid transparent`,
        cursor: "pointer",
        fontSize: TEXT_SIZE.BODY,
        fontWeight: TEXT_WEIGHT.BOLD,
        fontFamily: FONT.UI,
        textTransform: "uppercase",
        letterSpacing: 1,
        transition: TRANSITION.FAST,
      }}
    >
      {label}
    </button>
  );
}

// ──────────────── Sub-views ────────────────

function OverviewView({ data, theme }) {
  const { ceLTP, peLTP, ceOI, peOI, ceVol, peVol, pcr, maxPain, iv, ivRank, deltaCE, deltaPE, theta, gamma, verdict, signalScore, atmDistance } = data || {};

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
      <Card title="AI Verdict" theme={theme}>
        <div
          style={{
            fontSize: 20,
            fontWeight: TEXT_WEIGHT.BLACK,
            color: verdict?.action?.startsWith("BUY CE")
              ? theme.GREEN
              : verdict?.action?.startsWith("BUY PE")
              ? theme.RED
              : theme.TEXT_MUTED,
            marginBottom: SPACE.SM,
            fontFamily: FONT.MONO,
          }}
        >
          {verdict?.action || "NO TRADE"}
        </div>
        <div style={{ display: "flex", gap: SPACE.MD, marginBottom: SPACE.MD }}>
          <Stat
            label="Confidence"
            value={`${verdict?.confidence || 0}%`}
            color={theme.ACCENT}
            theme={theme}
          />
          <Stat label="Signal" value={`${signalScore || 0}/9`} theme={theme} />
        </div>
        {verdict?.entry && (
          <div style={{ display: "flex", gap: SPACE.SM, flexWrap: "wrap" }}>
            <Pill label="ENTRY" value={verdict.entry} color={theme.ACCENT} theme={theme} />
            <Pill label="SL" value={verdict.sl} color={theme.RED} theme={theme} />
            <Pill label="T1" value={verdict.t1} color={theme.GREEN} theme={theme} />
            <Pill label="T2" value={verdict.t2} color={theme.GREEN} theme={theme} />
          </div>
        )}
      </Card>

      <Card title="Key Metrics" theme={theme}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
          <Stat label="PCR" value={pcr?.toFixed(2) || "—"} theme={theme} />
          <Stat label="Max Pain" value={maxPain || "—"} theme={theme} />
          <Stat label="IV" value={iv ? `${iv.toFixed(1)}%` : "—"} theme={theme} />
          <Stat label="IV Rank" value={ivRank != null ? ivRank : "—"} theme={theme} />
          <Stat label="Delta CE" value={deltaCE?.toFixed(2) || "—"} color={theme.GREEN} theme={theme} />
          <Stat label="Delta PE" value={deltaPE?.toFixed(2) || "—"} color={theme.RED} theme={theme} />
          <Stat label="Theta" value={theta?.toFixed(2) || "—"} color={theme.AMBER} theme={theme} />
          <Stat label="Gamma" value={gamma?.toFixed(3) || "—"} theme={theme} />
        </div>
      </Card>

      <Card title="CE Option" theme={theme} style={{ gridColumn: "span 1" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
          <Stat label="LTP" value={ceLTP != null ? `₹${ceLTP}` : "—"} color={theme.GREEN} theme={theme} />
          <Stat label="OI" value={ceOI != null ? `${(ceOI / 100000).toFixed(1)}L` : "—"} theme={theme} />
          <Stat label="Volume" value={ceVol != null ? `${(ceVol / 100000).toFixed(1)}L` : "—"} theme={theme} />
          <Stat label="Distance" value={atmDistance != null ? `${atmDistance > 0 ? "+" : ""}${atmDistance}` : "—"} theme={theme} />
        </div>
      </Card>

      <Card title="PE Option" theme={theme} style={{ gridColumn: "span 1" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
          <Stat label="LTP" value={peLTP != null ? `₹${peLTP}` : "—"} color={theme.RED} theme={theme} />
          <Stat label="OI" value={peOI != null ? `${(peOI / 100000).toFixed(1)}L` : "—"} theme={theme} />
          <Stat label="Volume" value={peVol != null ? `${(peVol / 100000).toFixed(1)}L` : "—"} theme={theme} />
          <Stat label="Distance" value={atmDistance != null ? `${atmDistance > 0 ? "+" : ""}${-atmDistance}` : "—"} theme={theme} />
        </div>
      </Card>
    </div>
  );
}

function Pill({ label, value, color, theme }) {
  return (
    <div
      style={{
        padding: `4px ${SPACE.SM}px`,
        background: color + "15",
        border: `1px solid ${color}33`,
        borderRadius: RADIUS.SM,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <span style={{ color, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1 }}>{label}</span>
      <span
        style={{
          color: theme.TEXT,
          fontSize: TEXT_SIZE.BODY,
          fontWeight: TEXT_WEIGHT.BOLD,
          fontFamily: FONT.MONO,
        }}
      >
        {value}
      </span>
    </div>
  );
}

function GreeksView({ data, theme }) {
  const g = data?.greeks || {};
  const rows = [
    { label: "Delta", ce: g.deltaCE, pe: g.deltaPE, exp: "Price sensitivity to spot (per ₹1 move)" },
    { label: "Gamma", ce: g.gammaCE, pe: g.gammaPE, exp: "Rate of delta change" },
    { label: "Theta", ce: g.thetaCE, pe: g.thetaPE, exp: "Time decay per day (₹)" },
    { label: "Vega", ce: g.vegaCE, pe: g.vegaPE, exp: "IV sensitivity (per 1% IV change)" },
    { label: "Rho", ce: g.rhoCE, pe: g.rhoPE, exp: "Interest rate sensitivity" },
  ];
  return (
    <Card title="Greeks" theme={theme}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: TEXT_SIZE.BODY }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${theme.BORDER}` }}>
            <th style={{ padding: SPACE.SM, color: theme.TEXT_DIM, textAlign: "left", fontSize: TEXT_SIZE.MICRO, letterSpacing: 1 }}>
              GREEK
            </th>
            <th style={{ padding: SPACE.SM, color: theme.GREEN, textAlign: "center", fontSize: TEXT_SIZE.MICRO, letterSpacing: 1 }}>
              CE
            </th>
            <th style={{ padding: SPACE.SM, color: theme.RED, textAlign: "center", fontSize: TEXT_SIZE.MICRO, letterSpacing: 1 }}>
              PE
            </th>
            <th style={{ padding: SPACE.SM, color: theme.TEXT_DIM, textAlign: "left", fontSize: TEXT_SIZE.MICRO, letterSpacing: 1 }}>
              EXPLANATION
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} style={{ borderBottom: `1px solid ${theme.BORDER}44` }}>
              <td style={{ padding: SPACE.SM, color: theme.TEXT, fontWeight: TEXT_WEIGHT.BOLD }}>
                {r.label}
              </td>
              <td style={{ padding: SPACE.SM, textAlign: "center", color: theme.TEXT, fontFamily: FONT.MONO }}>
                {r.ce != null ? r.ce.toFixed(3) : "—"}
              </td>
              <td style={{ padding: SPACE.SM, textAlign: "center", color: theme.TEXT, fontFamily: FONT.MONO }}>
                {r.pe != null ? r.pe.toFixed(3) : "—"}
              </td>
              <td style={{ padding: SPACE.SM, color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO }}>{r.exp}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function FlowView({ data, theme }) {
  const events = data?.flow || [];
  if (events.length === 0) {
    return (
      <Card title="Live Flow (last 60 min)" theme={theme}>
        <div style={{ color: theme.TEXT_DIM, padding: SPACE.LG, textAlign: "center" }}>
          No flow events yet
        </div>
      </Card>
    );
  }
  return (
    <Card title={`Live Flow (${events.length} events)`} theme={theme}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {events.map((e, i) => {
          const isCall = e.side === "CE";
          const color = e.magnitude === "MAJOR" ? theme.ACCENT : isCall ? theme.GREEN : theme.RED;
          return (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: SPACE.MD,
                padding: `6px ${SPACE.SM}px`,
                borderLeft: `2px solid ${color}`,
                background: theme.SURFACE_HI,
                borderRadius: RADIUS.SM,
                fontSize: TEXT_SIZE.BODY,
                fontFamily: FONT.MONO,
              }}
            >
              <span style={{ color: theme.TEXT_DIM, minWidth: 48 }}>{e.time}</span>
              <span style={{ color: isCall ? theme.GREEN : theme.RED, minWidth: 24, fontWeight: TEXT_WEIGHT.BOLD }}>
                {e.side}
              </span>
              <span style={{ color: theme.TEXT, minWidth: 70 }}>
                OI {e.oiChange > 0 ? "+" : ""}
                {(e.oiChange / 1000).toFixed(0)}K
              </span>
              <span style={{ color: theme.TEXT_MUTED, minWidth: 50 }}>₹{e.ltp}</span>
              <span style={{ color, fontWeight: TEXT_WEIGHT.BOLD, fontSize: TEXT_SIZE.MICRO }}>
                {e.classification}
              </span>
              <span style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, marginLeft: "auto", fontFamily: FONT.UI }}>
                {e.note}
              </span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function OIHistoryView({ data, theme }) {
  const history = data?.oiHistory || [];
  if (history.length === 0) {
    return (
      <Card title="OI History" theme={theme}>
        <div style={{ color: theme.TEXT_DIM, padding: SPACE.LG, textAlign: "center" }}>
          OI history will appear during market hours
        </div>
      </Card>
    );
  }
  const maxCE = Math.max(...history.map((h) => h.ceOI || 0), 1);
  const maxPE = Math.max(...history.map((h) => h.peOI || 0), 1);
  const max = Math.max(maxCE, maxPE);

  const w = 600;
  const h = 200;
  const pad = 32;
  const dx = (w - 2 * pad) / Math.max(history.length - 1, 1);

  const cePath = history.map((d, i) => `${i === 0 ? "M" : "L"}${pad + i * dx},${h - pad - ((d.ceOI || 0) / max) * (h - 2 * pad)}`).join(" ");
  const pePath = history.map((d, i) => `${i === 0 ? "M" : "L"}${pad + i * dx},${h - pad - ((d.peOI || 0) / max) * (h - 2 * pad)}`).join(" ");

  return (
    <Card title="OI History (today)" theme={theme}>
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
        <path d={cePath} fill="none" stroke={theme.GREEN} strokeWidth={2} />
        <path d={pePath} fill="none" stroke={theme.RED} strokeWidth={2} />
        {/* Axis */}
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke={theme.BORDER} />
        <line x1={pad} y1={pad} x2={pad} y2={h - pad} stroke={theme.BORDER} />
        {/* Labels */}
        <text x={pad} y={pad - 4} fill={theme.TEXT_DIM} fontSize={10} fontFamily={FONT.MONO}>
          {(max / 100000).toFixed(1)}L
        </text>
        <text x={w - pad - 30} y={h - pad + 14} fill={theme.TEXT_DIM} fontSize={10} fontFamily={FONT.MONO}>
          {history[history.length - 1]?.time || ""}
        </text>
      </svg>
      <div style={{ display: "flex", gap: SPACE.LG, justifyContent: "center", marginTop: SPACE.SM }}>
        <span style={{ color: theme.GREEN, fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD }}>─ CE OI</span>
        <span style={{ color: theme.RED, fontSize: TEXT_SIZE.MICRO, fontWeight: TEXT_WEIGHT.BOLD }}>─ PE OI</span>
      </div>
    </Card>
  );
}

function IVView({ data, theme }) {
  return (
    <Card title="IV & Premium History" theme={theme}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: SPACE.MD, marginBottom: SPACE.MD }}>
        <Stat label="Current IV" value={data?.iv ? `${data.iv.toFixed(1)}%` : "—"} color={theme.ACCENT} theme={theme} />
        <Stat label="IV Rank" value={data?.ivRank != null ? data.ivRank : "—"} theme={theme} />
        <Stat label="7d Avg IV" value={data?.ivAvg7d ? `${data.ivAvg7d.toFixed(1)}%` : "—"} theme={theme} />
        <Stat label="IV Percentile" value={data?.ivPct != null ? `${data.ivPct}%` : "—"} theme={theme} />
      </div>
      <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, padding: SPACE.SM }}>
        IV {data?.iv > 20 ? "elevated" : "moderate"} — {data?.ivRank > 70 ? "premium expensive, favor selling setups" : data?.ivRank < 30 ? "premium cheap, favor buying setups" : "neutral"}
      </div>
    </Card>
  );
}

function TradesView({ data, theme }) {
  const trades = data?.trades || [];
  if (trades.length === 0) {
    return (
      <Card title="Your trades on this strike" theme={theme}>
        <div style={{ color: theme.TEXT_DIM, padding: SPACE.LG, textAlign: "center" }}>
          You haven't traded this strike yet
        </div>
      </Card>
    );
  }
  const wins = trades.filter((t) => (t.pnl || 0) > 0).length;
  const losses = trades.filter((t) => (t.pnl || 0) <= 0).length;
  const netPnl = trades.reduce((a, t) => a + (t.pnl || 0), 0);
  return (
    <Card title={`Your trades on this strike (${trades.length})`} theme={theme}>
      <div style={{ display: "flex", gap: SPACE.MD, marginBottom: SPACE.MD }}>
        <Stat label="Wins" value={wins} color={theme.GREEN} theme={theme} />
        <Stat label="Losses" value={losses} color={theme.RED} theme={theme} />
        <Stat
          label="Net P&L"
          value={`₹${netPnl > 0 ? "+" : ""}${netPnl.toLocaleString("en-IN")}`}
          color={netPnl >= 0 ? theme.GREEN : theme.RED}
          theme={theme}
        />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {trades.slice(0, 20).map((t, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: SPACE.MD,
              padding: `6px ${SPACE.SM}px`,
              background: theme.SURFACE_HI,
              borderRadius: RADIUS.SM,
              fontSize: TEXT_SIZE.BODY,
              fontFamily: FONT.MONO,
              borderLeft: `2px solid ${(t.pnl || 0) >= 0 ? theme.GREEN : theme.RED}`,
            }}
          >
            <span style={{ color: theme.TEXT_DIM, minWidth: 80 }}>{t.date}</span>
            <span style={{ color: theme.TEXT, minWidth: 40, fontWeight: TEXT_WEIGHT.BOLD }}>
              {t.action}
            </span>
            <span style={{ color: theme.TEXT_MUTED, minWidth: 60 }}>@₹{t.entry}</span>
            <span style={{ color: theme.TEXT_DIM, minWidth: 80 }}>→ ₹{t.exit || "open"}</span>
            <span
              style={{
                color: (t.pnl || 0) >= 0 ? theme.GREEN : theme.RED,
                fontWeight: TEXT_WEIGHT.BOLD,
                marginLeft: "auto",
              }}
            >
              {t.pnl != null ? `₹${t.pnl > 0 ? "+" : ""}${t.pnl.toLocaleString("en-IN")}` : "—"}
            </span>
            <span style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, fontFamily: FONT.UI }}>
              {t.reason}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function NotesView({ strike, theme }) {
  const key = `notes_${strike.index}_${strike.strike}`;
  const [notes, setNotes] = useState(() => localStorage.getItem(key) || "");
  useEffect(() => {
    const t = setTimeout(() => localStorage.setItem(key, notes), 300);
    return () => clearTimeout(t);
  }, [notes, key]);
  return (
    <Card title="Notes (private, saved locally)" theme={theme}>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Your thoughts on this strike — setup, entry plan, lessons..."
        style={{
          width: "100%",
          minHeight: 160,
          background: theme.BG,
          color: theme.TEXT,
          border: `1px solid ${theme.BORDER}`,
          borderRadius: RADIUS.MD,
          padding: SPACE.MD,
          fontFamily: FONT.UI,
          fontSize: TEXT_SIZE.BODY,
          resize: "vertical",
          outline: "none",
        }}
      />
      <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, marginTop: SPACE.XS }}>
        {notes.length} chars — autosaved
      </div>
    </Card>
  );
}

const SUBTABS = [
  { id: "overview", label: "Overview" },
  { id: "greeks", label: "Greeks" },
  { id: "flow", label: "Flow" },
  { id: "oi", label: "OI History" },
  { id: "iv", label: "IV/Vol" },
  { id: "trades", label: "My Trades" },
  { id: "notes", label: "Notes" },
];

// ──────────────── Main component ────────────────

export default function StrikeDetail({ strike, onClose, onPin, pinned, liveData }) {
  const { theme } = useTheme();
  const [sub, setSub] = useState("overview");
  const [data, setData] = useState(null);

  useEffect(() => {
    if (!strike) return;
    const exp = strike.expiry ? `&expiry=${encodeURIComponent(strike.expiry)}` : "";
    const url = `/api/strike-detail?index=${strike.index}&strike=${strike.strike}${exp}`;
    fetchJSON(url).then(setData);
    const interval = setInterval(() => fetchJSON(url).then(setData), 5000);
    return () => clearInterval(interval);
  }, [strike?.index, strike?.strike, strike?.expiry]);

  const merged = useMemo(() => ({ ...(data || {}), ...(liveData || {}) }), [data, liveData]);

  if (!strike) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SPACE.MD }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: `${SPACE.MD}px ${SPACE.LG}px`,
          background: theme.SURFACE,
          border: `1px solid ${theme.BORDER}`,
          borderRadius: RADIUS.LG,
        }}
      >
        <div>
          <div
            style={{
              color: theme.TEXT_DIM,
              fontSize: TEXT_SIZE.MICRO,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 1.5,
              textTransform: "uppercase",
            }}
          >
            {strike.index} › Strike Detail
          </div>
          <div
            style={{
              color: theme.TEXT,
              fontSize: 22,
              fontWeight: TEXT_WEIGHT.BLACK,
              fontFamily: FONT.MONO,
              letterSpacing: 1,
            }}
          >
            {strike.strike} {strike.type ? ` ${strike.type}` : ""}
          </div>
        </div>
        <div style={{ display: "flex", gap: SPACE.SM, alignItems: "center" }}>
          {merged.expiry && (
            <span
              style={{
                color: theme.TEXT_MUTED,
                fontSize: TEXT_SIZE.MICRO,
                padding: "3px 8px",
                background: theme.SURFACE_HI,
                borderRadius: RADIUS.SM,
                fontFamily: FONT.MONO,
              }}
            >
              Exp {merged.expiry}
            </span>
          )}
          <button
            onClick={() => onPin && onPin(strike)}
            style={{
              background: "transparent",
              color: pinned ? theme.AMBER : theme.TEXT_MUTED,
              border: `1px solid ${pinned ? theme.AMBER : theme.BORDER}`,
              borderRadius: RADIUS.SM,
              padding: "4px 10px",
              cursor: "pointer",
              fontSize: TEXT_SIZE.BODY,
              fontWeight: TEXT_WEIGHT.BOLD,
            }}
          >
            {pinned ? "★ Pinned" : "☆ Pin"}
          </button>
          {onClose && (
            <button
              onClick={onClose}
              style={{
                background: "transparent",
                color: theme.TEXT_MUTED,
                border: `1px solid ${theme.BORDER}`,
                borderRadius: RADIUS.SM,
                padding: "4px 10px",
                cursor: "pointer",
                fontSize: 14,
              }}
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* Fetch error banner */}
      {data?._fetchError && (
        <div
          role="alert"
          style={{
            background: theme.RED_DIM,
            border: `1px solid ${theme.RED}44`,
            borderLeft: `3px solid ${theme.RED}`,
            borderRadius: RADIUS.SM,
            padding: `${SPACE.SM}px ${SPACE.MD}px`,
            color: theme.RED,
            fontSize: TEXT_SIZE.MICRO,
            fontFamily: FONT.UI,
          }}
        >
          <strong>Failed to load strike data:</strong> {data._fetchError}
        </div>
      )}

      {/* Sub-tabs */}
      <div
        style={{
          display: "flex",
          gap: 0,
          borderBottom: `1px solid ${theme.BORDER}`,
          overflowX: "auto",
        }}
      >
        {SUBTABS.map((t) => (
          <SubTab key={t.id} label={t.label} active={sub === t.id} onClick={() => setSub(t.id)} theme={theme} />
        ))}
      </div>

      {/* Content */}
      {sub === "overview" && <OverviewView data={merged} theme={theme} />}
      {sub === "greeks" && <GreeksView data={merged} theme={theme} />}
      {sub === "flow" && <FlowView data={merged} theme={theme} />}
      {sub === "oi" && <OIHistoryView data={merged} theme={theme} />}
      {sub === "iv" && <IVView data={merged} theme={theme} />}
      {sub === "trades" && <TradesView data={merged} theme={theme} />}
      {sub === "notes" && <NotesView strike={strike} theme={theme} />}
    </div>
  );
}
