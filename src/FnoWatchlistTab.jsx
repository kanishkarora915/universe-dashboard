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
      // Try scan cache first (has deep payload)
      const r = await fetch(`/api/fno/stock/${symbol}`).then(r => r.json());
      if (r.ok && r.deep) {
        setDetail({ summary: r.stock, deep: r.deep });
      } else if (r.ok) {
        // Force live deep analysis
        const live = await fetch(`/api/fno/analyze/${symbol}`).then(r => r.json());
        setDetail({ summary: r.stock, deep: live.deep });
      }
    } catch (e) {
      console.error("detail fetch", e);
    }
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
  // stock = { summary, deep }
  const summary = stock?.summary || stock;
  const deep = stock?.deep;
  const isBull = (deep?.prediction?.direction || summary.predicted_direction) === "BULL";
  const c = isBull ? GREEN : RED;

  if (!deep) {
    // Fallback: show simple view
    return (
      <SimpleStockDetail stock={summary} onClose={onClose} color={c} isBull={isBull} />
    );
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.85)",
        zIndex: 1000,
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        padding: 20,
        overflowY: "auto",
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: BG_CARD,
          padding: 20,
          borderRadius: 8,
          maxWidth: 760,
          width: "100%",
          maxHeight: "92vh",
          overflowY: "auto",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 22, color: c }}>{deep.symbol}</div>
            <div style={{ fontSize: 11, color: MUTED }}>
              {isBull ? "🟢 BULL setup" : "🔴 BEAR setup"} · confidence{" "}
              <b style={{ color: YELLOW }}>{deep.prediction?.confidence_score}/100</b>
              {" "}· scan @ {new Date(deep.scan_ts).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })}
            </div>
          </div>
          <button onClick={onClose}
            style={{ background: "transparent", border: "none", color: MUTED, fontSize: 22, cursor: "pointer" }}>
            ✕
          </button>
        </div>

        {/* PRICE */}
        <Section title="💹 Price">
          <Row2 label="Current" value={`₹${deep.price?.price}`} label2="Prev close" value2={`₹${deep.price?.prev_close}`} />
          <Row2 label="Open" value={`₹${deep.price?.open}`} label2="Today move" value2={`${deep.price?.moved_pct >= 0 ? "+" : ""}${deep.price?.moved_pct}%`} valueColor2={deep.price?.moved_pct >= 0 ? GREEN : RED} />
          <Row2 label="Day high" value={`₹${deep.price?.day_high}`} label2="Day low" value2={`₹${deep.price?.day_low}`} />
          <Row2 label="Day range %" value={`${deep.price?.day_range_pct}%`} label2="Gap %" value2={`${deep.price?.gap_pct >= 0 ? "+" : ""}${deep.price?.gap_pct}%`} />
        </Section>

        {/* RETURNS */}
        <Section title="📈 Returns">
          <Row2 label="1 day" value={`${(deep.returns?.["1d_pct"] || 0) >= 0 ? "+" : ""}${deep.returns?.["1d_pct"]}%`}
                label2="5 days" value2={`${(deep.returns?.["5d_pct"] || 0) >= 0 ? "+" : ""}${deep.returns?.["5d_pct"]}%`}
                valueColor={deep.returns?.["1d_pct"] >= 0 ? GREEN : RED}
                valueColor2={deep.returns?.["5d_pct"] >= 0 ? GREEN : RED} />
          <Row2 label="1 month" value={`${(deep.returns?.["1m_pct"] || 0) >= 0 ? "+" : ""}${deep.returns?.["1m_pct"]}%`}
                label2="3 months" value2={`${(deep.returns?.["3m_pct"] || 0) >= 0 ? "+" : ""}${deep.returns?.["3m_pct"]}%`}
                valueColor={deep.returns?.["1m_pct"] >= 0 ? GREEN : RED}
                valueColor2={deep.returns?.["3m_pct"] >= 0 ? GREEN : RED} />
          <Row2 label="6 months" value={`${(deep.returns?.["6m_pct"] || 0) >= 0 ? "+" : ""}${deep.returns?.["6m_pct"]}%`}
                label2="1 year" value2={`${(deep.returns?.["1y_pct"] || 0) >= 0 ? "+" : ""}${deep.returns?.["1y_pct"]}%`}
                valueColor={deep.returns?.["6m_pct"] >= 0 ? GREEN : RED}
                valueColor2={deep.returns?.["1y_pct"] >= 0 ? GREEN : RED} />
        </Section>

        {/* 52W */}
        <Section title="📅 52-Week Range">
          <Row2 label="High" value={`₹${deep["52w"]?.high}`} label2="Low" value2={`₹${deep["52w"]?.low}`} />
          <Row2 label="From high" value={`${deep["52w"]?.dist_from_high_pct}%`} label2="From low" value2={`+${deep["52w"]?.dist_from_low_pct}%`}
                valueColor={RED} valueColor2={GREEN} />
          <ProgressBar label="Position in 52w range" value={deep["52w"]?.position_pct} max={100} />
        </Section>

        {/* TREND */}
        <Section title="📊 Trend (Multi-Timeframe)">
          <Row2 label="200d trend" value={deep.trend?.["200d_trend"]} label2="Strength" value2={`${deep.trend?.["200d_strength"]}/100`}
                valueColor={deep.trend?.["200d_trend"] === "UPTREND" ? GREEN : deep.trend?.["200d_trend"] === "DOWNTREND" ? RED : MUTED} />
          <Row2 label="vs SMA 20" value={deep.trend?.vs_sma20} label2="SMA 20" value2={`₹${deep.trend?.sma20}`}
                valueColor={deep.trend?.vs_sma20 === "ABOVE" ? GREEN : RED} />
          <Row2 label="vs SMA 50" value={deep.trend?.vs_sma50} label2="SMA 50" value2={`₹${deep.trend?.sma50}`}
                valueColor={deep.trend?.vs_sma50 === "ABOVE" ? GREEN : RED} />
          <Row2 label="vs SMA 200" value={deep.trend?.vs_sma200} label2="SMA 200" value2={`₹${deep.trend?.sma200}`}
                valueColor={deep.trend?.vs_sma200 === "ABOVE" ? GREEN : RED} />
          {deep.trend?.golden_cross && <div style={{ color: GREEN, fontSize: 11, padding: "4px 0" }}>✓ Golden Cross (SMA50 &gt; SMA200)</div>}
          {deep.trend?.death_cross && <div style={{ color: RED, fontSize: 11, padding: "4px 0" }}>✗ Death Cross (SMA50 &lt; SMA200)</div>}
          <div style={{ marginTop: 8, padding: 8, background: BG_ROW, borderRadius: 4 }}>
            <div style={{ fontSize: 10, color: MUTED, marginBottom: 4 }}>Multi-TF Structure:</div>
            {["1w", "1d", "1h", "15m", "5m"].map(tf => (
              <Row2Inline key={tf} label={tf} value={deep.trend?.[`struct_${tf}`]}
                valueColor={deep.trend?.[`struct_${tf}`] === "UPTREND" ? GREEN : deep.trend?.[`struct_${tf}`] === "DOWNTREND" ? RED : MUTED} />
            ))}
            <Row2Inline label="Alignment" value={`${deep.trend?.alignment_direction} (${deep.trend?.alignment_score}%)`}
              valueColor={deep.trend?.alignment_direction === "BULLISH" ? GREEN : deep.trend?.alignment_direction === "BEARISH" ? RED : MUTED} />
          </div>
        </Section>

        {/* MOMENTUM */}
        <Section title="⚡ Momentum">
          <Row2 label="RSI-14" value={deep.momentum?.rsi_14} label2="Status" value2={deep.momentum?.rsi_status}
                valueColor={deep.momentum?.rsi_status === "OVERBOUGHT" ? RED : deep.momentum?.rsi_status === "OVERSOLD" ? GREEN : MUTED} />
          {deep.momentum?.macd && (
            <>
              <Row2 label="MACD line" value={deep.momentum.macd.line} label2="Signal" value2={deep.momentum.macd.signal} />
              <Row2 label="Histogram" value={deep.momentum.macd.histogram} label2="Verdict" value2={deep.momentum.macd.verdict}
                    valueColor2={deep.momentum.macd.verdict === "BULLISH" ? GREEN : RED} />
            </>
          )}
          {deep.momentum?.stochastic && (
            <Row2 label="Stoch K/D" value={`${deep.momentum.stochastic.k}/${deep.momentum.stochastic.d}`}
                  label2="Status" value2={deep.momentum.stochastic.status} />
          )}
        </Section>

        {/* VOLATILITY */}
        <Section title="🎯 Volatility">
          <Row2 label="ATR-14d" value={`₹${deep.volatility?.atr_14d} (${deep.volatility?.atr_pct}%)`}
                label2="HV-20d" value2={`${deep.volatility?.hv_20d_pct}%`} />
          <Row2 label="HV-60d" value={`${deep.volatility?.hv_60d_pct}%`} label2=" " value2=" " />
          {deep.volatility?.bollinger && (
            <>
              <Row2 label="BB Upper" value={`₹${deep.volatility.bollinger.upper}`}
                    label2="BB Lower" value2={`₹${deep.volatility.bollinger.lower}`} />
              <Row2 label="BB Width %" value={deep.volatility.bollinger.width_pct}
                    label2="BB Position" value2={`${(deep.volatility.bollinger.position * 100).toFixed(0)}%`} />
              {deep.volatility.bollinger.squeeze && <div style={{ color: YELLOW, fontSize: 11, padding: "4px 0" }}>⚠ Bollinger Squeeze — volatility expansion expected</div>}
            </>
          )}
        </Section>

        {/* LEVELS */}
        <Section title="🎚 Support / Resistance">
          <Row2 label="Nearest support" value={`₹${deep.levels?.nearest_support || "?"}`}
                label2="Distance" value2={`${deep.levels?.dist_to_support_pct ?? "?"}%`} valueColor={GREEN} />
          <Row2 label="Nearest resistance" value={`₹${deep.levels?.nearest_resistance || "?"}`}
                label2="Distance" value2={`+${deep.levels?.dist_to_resistance_pct ?? "?"}%`} valueColor={RED} />
          <div style={{ marginTop: 8, fontSize: 10, color: MUTED }}>
            Resistance zones: {(deep.levels?.resistance_zones || []).join(" · ") || "(none)"}
          </div>
          <div style={{ fontSize: 10, color: MUTED }}>
            Support zones: {(deep.levels?.support_zones || []).join(" · ") || "(none)"}
          </div>
        </Section>

        {/* VOLUME */}
        <Section title="📦 Volume">
          <Row2 label="Today" value={(deep.volume?.today / 1000).toFixed(0) + "K"}
                label2="20d avg" value2={(deep.volume?.avg_20d / 1000).toFixed(0) + "K"} />
          <Row2 label="Ratio" value={`${deep.volume?.ratio}x`}
                label2="Trend" value2={deep.volume?.trend}
                valueColor={deep.volume?.ratio > 1.3 ? GREEN : deep.volume?.ratio < 0.7 ? RED : MUTED} />
        </Section>

        {/* FUTURES */}
        {deep.futures && (
          <Section title="📜 Futures">
            <Row2 label="Expiry" value={deep.futures.expiry} label2="Symbol" value2={deep.futures.tradingsymbol} />
            <Row2 label="Future price" value={`₹${deep.futures.price ?? "?"}`}
                  label2="Basis %" value2={`${deep.futures.basis_pct ?? "?"}%`}
                  valueColor2={deep.futures.basis_signal === "PREMIUM" ? GREEN : RED} />
            <Row2 label="Basis signal" value={deep.futures.basis_signal}
                  label2="OI buildup" value2={deep.futures.oi_buildup}
                  valueColor={deep.futures.basis_signal === "PREMIUM" ? GREEN : RED}
                  valueColor2={deep.futures.oi_buildup?.includes("LONG") ? GREEN : RED} />
            <Row2 label="OI" value={(deep.futures.oi / 1000).toFixed(0) + "K"}
                  label2="Lot size" value2={deep.futures.lot_size} />
          </Section>
        )}

        {/* PATTERNS */}
        {deep.patterns && deep.patterns.length > 0 && (
          <Section title="🔍 Patterns Detected">
            {deep.patterns.map((p, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, padding: "3px 0", color: "#cbd5e1" }}>
                <span>{p.name} <span style={{ color: MUTED, fontSize: 10 }}>[{p.tf}]</span></span>
                <span style={{ color: YELLOW }}>⭐ {p.confidence}</span>
              </div>
            ))}
          </Section>
        )}

        {/* PREDICTION */}
        <Section title={`🎯 Prediction (${isBull ? "BULL" : "BEAR"} setup)`} highlight={c}>
          <Row2 label="Target 1-day" value={`₹${deep.prediction?.target_1d || "?"}`}
                label2="Target 3-day" value2={`₹${deep.prediction?.target_3d || "?"}`} valueColor2={c} />
          <Row2 label="Target 1-week" value={`₹${deep.prediction?.target_1w || "?"}`}
                label2="Stop loss" value2={`₹${deep.prediction?.stop_loss || "?"}`} valueColor2={RED} />
          <Row2 label="Risk:Reward" value={`1 : ${deep.prediction?.risk_reward}`}
                label2="Bull/Bear signals" value2={`${deep.prediction?.bull_signals} / ${deep.prediction?.bear_signals}`} />
          <Row2 label="Moved (ATR units)" value={`${deep.prediction?.moved_in_atr_units} ATR`}
                label2="Room (ATR units)" value2={`${isBull ? deep.prediction?.room_atr_up : deep.prediction?.room_atr_down} ATR`} />
        </Section>

        {/* SCORE BREAKDOWN */}
        {deep.prediction?.score_breakdown && (
          <Section title="🧮 Score Breakdown">
            {Object.entries(deep.prediction.score_breakdown).map(([key, val]) => (
              <Row2Inline key={key} label={key} value={`${val}`} />
            ))}
            <hr style={{ borderTop: `1px solid #334155`, margin: "6px 0" }} />
            <Row2Inline label="TOTAL" value={`${deep.prediction?.confidence_score}/100`} valueColor={YELLOW} bold />
          </Section>
        )}

        {/* REASONING */}
        <Section title="💭 Reasoning">
          <div style={{ fontSize: 11, color: "#cbd5e1", lineHeight: 1.6 }}>
            {deep.prediction?.reason}
          </div>
        </Section>
      </div>
    </div>
  );
}

function SimpleStockDetail({ stock, onClose, color, isBull }) {
  return (
    <div onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 1000,
               display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div onClick={e => e.stopPropagation()}
           style={{ background: BG_CARD, padding: 20, borderRadius: 8, maxWidth: 500, width: "100%" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
          <div style={{ fontWeight: 700, fontSize: 18, color }}>{stock.symbol}</div>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: MUTED, fontSize: 18, cursor: "pointer" }}>✕</button>
        </div>
        <Field label="Direction" value={`${isBull ? "🟢 BULL" : "🔴 BEAR"} (${stock.confidence_score}%)`} />
        <Field label="Current" value={`₹${stock.current_price}`} />
        <Field label="Target" value={`₹${stock.predicted_target} (${stock.predicted_move_pct}%)`} valueColor={color} />
        <Field label="SL" value={`₹${stock.predicted_sl}`} valueColor={RED} />
        <Field label="RR" value={`1 : ${stock.risk_reward}`} />
        <div style={{ fontSize: 11, color: MUTED, marginTop: 12 }}>Loading deep analysis...</div>
      </div>
    </div>
  );
}

function Section({ title, children, highlight }) {
  return (
    <div style={{ marginBottom: 12, padding: 10,
                  background: BG_ROW, borderRadius: 6,
                  borderLeft: highlight ? `3px solid ${highlight}` : "none" }}>
      <div style={{ fontWeight: 700, fontSize: 12, color: "#e2e8f0", marginBottom: 6 }}>{title}</div>
      {children}
    </div>
  );
}

function Row2({ label, value, valueColor, label2, value2, valueColor2 }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", fontSize: 11 }}>
      <div style={{ flex: 1 }}>
        <span style={{ color: MUTED }}>{label}: </span>
        <span style={{ color: valueColor || "#cbd5e1", fontWeight: 500 }}>{value}</span>
      </div>
      <div style={{ flex: 1, textAlign: "right" }}>
        <span style={{ color: MUTED }}>{label2}: </span>
        <span style={{ color: valueColor2 || "#cbd5e1", fontWeight: 500 }}>{value2}</span>
      </div>
    </div>
  );
}

function Row2Inline({ label, value, valueColor, bold }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "2px 0", fontSize: 11 }}>
      <span style={{ color: MUTED, textTransform: "capitalize" }}>{label.replace(/_/g, " ")}</span>
      <span style={{ color: valueColor || "#cbd5e1", fontWeight: bold ? 700 : 500 }}>{value}</span>
    </div>
  );
}

function ProgressBar({ label, value, max }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div style={{ padding: "4px 0", fontSize: 11 }}>
      <div style={{ display: "flex", justifyContent: "space-between", color: MUTED, marginBottom: 4 }}>
        <span>{label}</span>
        <span style={{ color: "#cbd5e1" }}>{value?.toFixed(1)}%</span>
      </div>
      <div style={{ height: 6, background: "#1e293b", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: pct > 70 ? RED : pct < 30 ? GREEN : YELLOW, transition: "width 0.3s" }} />
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
