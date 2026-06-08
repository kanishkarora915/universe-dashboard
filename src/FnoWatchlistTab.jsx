import { useEffect, useState } from "react";

/**
 * FnoWatchlistTab — 1-3 day swing trade ideas for NSE F&O universe.
 *
 * Shows ranked top bullish + bearish setups for next 1-3 sessions.
 * Built from daily 08:00 IST scan of ~190 F&O stocks.
 *
 * Each row = stock with predicted direction, target, R/R, confidence.
 */

const GREEN = "#22c55e";
const RED = "#ef4444";
const YELLOW = "#facc15";
const MUTED = "#94a3b8";
const BG_CARD = "#1e293b";
const BG_ROW = "#0f172a";

export default function FnoWatchlistTab() {
  const [data, setData] = useState({ bullish: [], bearish: [], meta: null });
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);
  const [selectedSymbol, setSelectedSymbol] = useState(null);
  const [detail, setDetail] = useState(null);

  async function fetchWatchlist() {
    try {
      const r = await fetch("/api/fno/watchlist?top=15").then(r => r.json());
      if (r.ok === false) {
        setError(r.error || "fetch failed");
      } else {
        setData(r);
        setError(null);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function triggerScan() {
    setScanning(true);
    try {
      const r = await fetch("/api/fno/scan", { method: "POST" }).then(r => r.json());
      if (!r.ok) {
        setError(r.error || "scan trigger failed");
      } else {
        // Poll until results arrive (up to 3 min)
        for (let i = 0; i < 36; i++) {
          await new Promise(res => setTimeout(res, 5000));
          await fetchWatchlist();
          if (data.meta?.scan_ts) break;
        }
      }
    } finally {
      setScanning(false);
    }
  }

  async function fetchDetail(symbol) {
    setSelectedSymbol(symbol);
    setDetail(null);
    try {
      const r = await fetch(`/api/fno/stock/${symbol}`).then(r => r.json());
      if (r.ok) setDetail(r.stock);
    } catch {}
  }

  useEffect(() => {
    fetchWatchlist();
    const iv = setInterval(fetchWatchlist, 60000);
    return () => clearInterval(iv);
  }, []);

  if (loading) {
    return (
      <div style={{ padding: 20, color: MUTED }}>Loading F&O scan...</div>
    );
  }

  const noData =
    (!data.bullish || data.bullish.length === 0) &&
    (!data.bearish || data.bearish.length === 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Header */}
      <div
        style={{
          background: BG_CARD,
          padding: "14px 18px",
          borderRadius: 8,
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 16, color: "#e2e8f0" }}>
            F&O Watchlist — Next 1-3 Sessions
          </div>
          <div style={{ fontSize: 11, color: MUTED, marginTop: 2 }}>
            Daily scan of ~190 F&O stocks · 200d structure + ATR-based targets ·
            {data.meta?.scan_ts
              ? ` last scan ${new Date(data.meta.scan_ts).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}`
              : " no scan yet"}
            {data.meta?.scanned_count
              ? ` (${data.meta.scanned_count} stocks)`
              : ""}
          </div>
        </div>
        <button
          onClick={triggerScan}
          disabled={scanning}
          style={{
            background: scanning ? MUTED : "#3b82f6",
            color: "#fff",
            border: "none",
            padding: "8px 16px",
            borderRadius: 6,
            cursor: scanning ? "wait" : "pointer",
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          {scanning ? "Scanning... (60-90s)" : "Refresh Scan"}
        </button>
      </div>

      {error && (
        <div
          style={{
            background: "rgba(239,68,68,0.10)",
            border: `1px solid ${RED}`,
            color: "#fecaca",
            padding: 12,
            borderRadius: 6,
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      {noData && !error && (
        <div
          style={{
            background: BG_CARD,
            padding: 30,
            borderRadius: 8,
            textAlign: "center",
            color: MUTED,
          }}
        >
          <div style={{ fontSize: 14, marginBottom: 8 }}>
            No scan results yet
          </div>
          <div style={{ fontSize: 11 }}>
            Click <b>Refresh Scan</b> above to trigger first scan. Takes ~60-90 sec for full universe.
            <br />
            Daily auto-scan fires at 08:00 IST.
          </div>
        </div>
      )}

      {/* Bullish + Bearish columns */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
        }}
      >
        <RankedColumn
          title="🟢 Top Bullish"
          color={GREEN}
          items={data.bullish || []}
          onSelect={fetchDetail}
        />
        <RankedColumn
          title="🔴 Top Bearish"
          color={RED}
          items={data.bearish || []}
          onSelect={fetchDetail}
        />
      </div>

      {/* Detail modal */}
      {detail && (
        <StockDetail
          stock={detail}
          onClose={() => {
            setSelectedSymbol(null);
            setDetail(null);
          }}
        />
      )}
    </div>
  );
}

function RankedColumn({ title, color, items, onSelect }) {
  return (
    <div style={{ background: BG_CARD, padding: 12, borderRadius: 8 }}>
      <div
        style={{
          color,
          fontWeight: 700,
          fontSize: 13,
          marginBottom: 8,
          paddingBottom: 6,
          borderBottom: `1px solid ${color}33`,
        }}
      >
        {title} ({items.length})
      </div>
      {items.length === 0 ? (
        <div style={{ color: MUTED, fontSize: 11, padding: 12 }}>
          No qualifying setups
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {items.map(s => (
            <Row key={s.symbol} stock={s} onClick={() => onSelect(s.symbol)} />
          ))}
        </div>
      )}
    </div>
  );
}

function Row({ stock, onClick }) {
  const isBull = stock.predicted_direction === "BULL";
  const c = isBull ? GREEN : RED;
  return (
    <div
      onClick={onClick}
      style={{
        background: BG_ROW,
        padding: "8px 10px",
        borderRadius: 6,
        cursor: "pointer",
        borderLeft: `3px solid ${c}`,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontWeight: 700, fontSize: 13, color: "#e2e8f0" }}>
          {stock.symbol}
        </div>
        <div style={{ fontSize: 11, color: MUTED }}>
          ₹{stock.current_price}
          <span
            style={{
              color: stock.moved_today_pct >= 0 ? GREEN : RED,
              marginLeft: 6,
            }}
          >
            ({stock.moved_today_pct >= 0 ? "+" : ""}
            {stock.moved_today_pct}%)
          </span>
        </div>
      </div>
      <div
        style={{
          display: "flex",
          gap: 10,
          fontSize: 10,
          marginTop: 4,
          color: MUTED,
        }}
      >
        <span>
          → ₹{stock.predicted_target}{" "}
          <span style={{ color: c }}>
            ({stock.predicted_move_pct > 0 ? "+" : ""}
            {stock.predicted_move_pct}%)
          </span>
        </span>
        <span>RR {stock.risk_reward}</span>
        <span style={{ color: YELLOW }}>
          ⭐ {stock.confidence_score}
        </span>
      </div>
      <div style={{ fontSize: 10, color: MUTED, marginTop: 3, opacity: 0.7 }}>
        {stock.reason}
      </div>
    </div>
  );
}

function StockDetail({ stock, onClose }) {
  const isBull = stock.predicted_direction === "BULL";
  const c = isBull ? GREEN : RED;
  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: "rgba(0,0,0,0.7)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: BG_CARD,
          padding: 20,
          borderRadius: 8,
          maxWidth: 500,
          width: "100%",
          maxHeight: "90vh",
          overflowY: "auto",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
          <div style={{ fontWeight: 700, fontSize: 18, color: c }}>
            {stock.symbol}
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: MUTED,
              fontSize: 18,
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>
        <Field label="Direction" value={`${isBull ? "🟢 BULL" : "🔴 BEAR"} (${stock.confidence_score}% confidence)`} />
        <Field label="Current price" value={`₹${stock.current_price}`} />
        <Field label="Prev close" value={`₹${stock.prev_close} (${stock.moved_today_pct >= 0 ? "+" : ""}${stock.moved_today_pct}%)`} />
        <Field label="200d trend" value={`${stock.trend_200d} · strength ${stock.trend_strength}`} />
        <Field label="ATR-14d" value={`₹${stock.atr_14d} (${stock.atr_pct}%)`} />
        <Field label="Day structure" value={stock.day_structure} />
        <hr style={{ border: "none", borderTop: `1px solid #334155`, margin: "12px 0" }} />
        <Field label="Predicted target" value={`₹${stock.predicted_target} (${stock.predicted_move_pct > 0 ? "+" : ""}${stock.predicted_move_pct}%)`} valueColor={c} />
        <Field label="Suggested SL" value={`₹${stock.predicted_sl}`} valueColor={RED} />
        <Field label="Risk:Reward" value={`1 : ${stock.risk_reward}`} />
        <Field label="Moved today (ATR units)" value={`${stock.moved_in_atr_units} ATR`} />
        <Field label="Room to next S/R (ATR units)" value={`${stock.remaining_atr_room} ATR`} />
        <Field label="Nearest support" value={`₹${stock.nearest_support}`} />
        <Field label="Nearest resistance" value={`₹${stock.nearest_resistance}`} />
        <hr style={{ border: "none", borderTop: `1px solid #334155`, margin: "12px 0" }} />
        <div style={{ fontSize: 12, color: MUTED, marginBottom: 4 }}>Reasoning:</div>
        <div style={{ fontSize: 12, color: "#cbd5e1", lineHeight: 1.5 }}>
          {stock.reason}
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, valueColor }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: 12 }}>
      <span style={{ color: MUTED }}>{label}</span>
      <span style={{ color: valueColor || "#e2e8f0", fontWeight: 500 }}>{value}</span>
    </div>
  );
}
