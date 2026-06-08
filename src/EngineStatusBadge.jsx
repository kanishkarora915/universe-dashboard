import { useEffect, useState, useRef } from "react";

/**
 * EngineStatusBadge — live indicator for both Scalper and PnL tabs.
 *
 * Polls /api/ws/contract every 5s. Shows:
 *   🟢 GREEN  — everything healthy (WS connected, ticks flowing, auto-trade ON)
 *   🟡 YELLOW — WS healthy but auto-trade OFF or paused
 *   🔴 RED    — WS dead OR engine error
 *
 * Props:
 *   tab: "scalper" or "main" — chooses which auto-trade toggle to inspect
 *
 * Click expand → shows full diagnostic list.
 */
export default function EngineStatusBadge({ tab = "scalper" }) {
  const [contract, setContract] = useState(null);
  const [scalperState, setScalperState] = useState(null);
  const [tradesToday, setTradesToday] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const lastFetchRef = useRef(0);

  async function fetchAll() {
    try {
      const now = Date.now();
      lastFetchRef.current = now;

      // Parallel fetches — all needed for badge state
      const [contractRes, scalperRes, statsRes] = await Promise.all([
        fetch("/api/ws/contract").then(r => r.json()).catch(() => null),
        fetch("/api/scalper/status").then(r => r.json()).catch(() => null),
        fetch(
          tab === "main"
            ? "/api/trades/stats"
            : "/api/scalper/stats"
        ).then(r => r.json()).catch(() => null),
      ]);

      setContract(contractRes);
      setScalperState(scalperRes);
      setTradesToday(statsRes);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    fetchAll();
    const iv = setInterval(fetchAll, 5000);
    return () => clearInterval(iv);
  }, [tab]);

  // ── DERIVE STATUS ──
  const wsOk = contract?.ok === true;
  const wsLastTickAge = contract?.invariants?.find(
    i => i.name === "recent_tick"
  )?.observed;
  const wsConnected = contract?.invariants?.find(
    i => i.name === "ticker_connected"
  )?.ok;

  const autoTradeOn = scalperState?.auto_trade_enabled === true;
  const autoTradePauseReason = scalperState?.auto_trade_pause_reason;

  const todayCount =
    tab === "main"
      ? tradesToday?.open ?? 0
      : tradesToday?.todayCount ?? 0;
  const todayPnl = tradesToday?.totalPnl;

  let status = "unknown";
  let label = "Checking...";
  let detail = "";

  if (error) {
    status = "error";
    label = "API ERROR";
    detail = error;
  } else if (!contract) {
    status = "loading";
    label = "Loading...";
  } else if (!wsOk) {
    status = "red";
    const failed = contract.failed_critical || [];
    label = "ENGINE DOWN";
    detail =
      failed.length > 0
        ? `Failed: ${failed.join(", ")}`
        : "WS contract violation";
  } else if (!autoTradeOn) {
    status = "yellow";
    label = "WS OK · Auto-trade OFF";
    detail = autoTradePauseReason || "Auto-trade is disabled";
  } else if (autoTradePauseReason) {
    status = "yellow";
    label = "WS OK · Paused";
    detail = autoTradePauseReason;
  } else {
    status = "green";
    label = "LIVE · Auto-trading";
    detail = `Ticks: ${wsLastTickAge ?? "?"}s ago`;
  }

  const colors = {
    green: {
      dot: "#22c55e",
      bg: "rgba(34,197,94,0.10)",
      border: "#22c55e",
      text: "#bbf7d0",
    },
    yellow: {
      dot: "#facc15",
      bg: "rgba(250,204,21,0.10)",
      border: "#facc15",
      text: "#fde68a",
    },
    red: {
      dot: "#ef4444",
      bg: "rgba(239,68,68,0.10)",
      border: "#ef4444",
      text: "#fecaca",
    },
    loading: {
      dot: "#64748b",
      bg: "rgba(100,116,139,0.10)",
      border: "#64748b",
      text: "#cbd5e1",
    },
    error: {
      dot: "#a855f7",
      bg: "rgba(168,85,247,0.10)",
      border: "#a855f7",
      text: "#e9d5ff",
    },
    unknown: {
      dot: "#64748b",
      bg: "rgba(100,116,139,0.10)",
      border: "#64748b",
      text: "#cbd5e1",
    },
  };
  const c = colors[status];

  return (
    <div
      onClick={() => setExpanded(e => !e)}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "6px",
        padding: "10px 14px",
        background: c.bg,
        border: `1px solid ${c.border}`,
        borderRadius: "8px",
        cursor: "pointer",
        userSelect: "none",
        marginBottom: "12px",
      }}
      title="Click to expand diagnostics"
    >
      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        <span
          style={{
            display: "inline-block",
            width: "10px",
            height: "10px",
            borderRadius: "50%",
            background: c.dot,
            animation:
              status === "green" ? "pulse 2s infinite" : "none",
            boxShadow:
              status === "green" ? `0 0 8px ${c.dot}` : "none",
          }}
        />
        <span style={{ fontWeight: 600, color: c.text, fontSize: "13px" }}>
          {label}
        </span>
        <span
          style={{
            color: c.text,
            opacity: 0.7,
            fontSize: "11px",
            marginLeft: "auto",
          }}
        >
          {tab === "main" ? "MAIN MODE" : "SCALPER"} ·{" "}
          {todayCount > 0
            ? `${todayCount} trade${todayCount > 1 ? "s" : ""} today`
            : "0 trades today"}
        </span>
      </div>

      {detail && (
        <div
          style={{
            color: c.text,
            opacity: 0.75,
            fontSize: "11px",
            paddingLeft: "20px",
          }}
        >
          {detail}
        </div>
      )}

      {expanded && contract && (
        <div
          style={{
            marginTop: "8px",
            paddingTop: "8px",
            borderTop: `1px solid ${c.border}`,
            fontSize: "11px",
            color: c.text,
            opacity: 0.85,
          }}
        >
          <div style={{ marginBottom: "4px", fontWeight: 600 }}>
            Engine invariants:
          </div>
          {(contract.invariants || []).map((inv, idx) => (
            <div
              key={idx}
              style={{
                display: "flex",
                gap: "8px",
                padding: "2px 0",
              }}
            >
              <span style={{ color: inv.ok ? "#22c55e" : "#ef4444" }}>
                {inv.ok ? "✓" : "✗"}
              </span>
              <span style={{ minWidth: "140px" }}>{inv.name}</span>
              <span style={{ opacity: 0.7 }}>{inv.message}</span>
            </div>
          ))}
          {scalperState && (
            <div style={{ marginTop: "8px" }}>
              <div style={{ fontWeight: 600 }}>Scalper config:</div>
              <div>
                · Auto-trade:{" "}
                {autoTradeOn ? "✓ ON" : "✗ OFF"}
                {autoTradePauseReason ? ` (${autoTradePauseReason})` : ""}
              </div>
              <div>
                · Threshold: {scalperState.config?.threshold ?? "?"}% ·
                Daily cap: {scalperState.config?.dailyCap ?? "?"}
              </div>
            </div>
          )}
          {todayPnl !== undefined && (
            <div style={{ marginTop: "6px" }}>
              · Today P&L:{" "}
              <span
                style={{
                  color: todayPnl >= 0 ? "#22c55e" : "#ef4444",
                  fontWeight: 600,
                }}
              >
                ₹{todayPnl?.toLocaleString("en-IN") ?? "0"}
              </span>
            </div>
          )}
        </div>
      )}

      <style>
        {`
          @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
          }
        `}
      </style>
    </div>
  );
}
