/**
 * WatcherStatusBadge
 * ──────────────────
 * Visible heartbeat with diagnostic depth. Shows whether the watcher is
 * pulsing, whether DB trade-counts match cache counts, and a manual
 * "Force Pulse Now" trigger if things look off.
 */

import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

export default function WatcherStatusBadge() {
  const [status, setStatus] = useState(null);
  const [debug, setDebug] = useState(null);
  const [forcing, setForcing] = useState(false);
  const [showDebug, setShowDebug] = useState(false);

  const refresh = async () => {
    try {
      const [s, d] = await Promise.all([
        fetch(`${API}/api/positions/watcher-status`).then(r => r.ok ? r.json() : null),
        fetch(`${API}/api/positions/watcher-debug`).then(r => r.ok ? r.json() : null),
      ]);
      setStatus(s);
      setDebug(d);
    } catch (e) { /* silent */ }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  }, []);

  const forcePulse = async () => {
    setForcing(true);
    try {
      const r = await fetch(`${API}/api/positions/watcher-pulse-now`, { method: "POST" });
      if (r.ok) await refresh();
    } catch (e) { /* silent */ }
    finally { setForcing(false); }
  };

  if (!status || !debug) {
    return <Pill color="#888" label="WATCHER" detail="checking…" />;
  }

  const dbMain = debug.open_main_in_db || 0;
  const dbScalper = debug.open_scalper_in_db || 0;
  const totalDb = dbMain + dbScalper;
  const cachedMain = status.main_count || 0;
  const cachedScalper = status.scalper_count || 0;
  const totalCached = cachedMain + cachedScalper;
  const mismatch = totalDb !== totalCached;
  const stubs = status.stub_count || 0;
  const live = status.live;
  const age = status.last_pulse_age_sec;
  const engineOK = debug.engine_alive && debug.engine_has_chains && debug.engine_has_spot_tokens;

  let color, label, detail;
  if (totalDb === 0) {
    color = "#888";
    label = "WATCHER · NO OPEN TRADES";
    detail = `DB scan clean · last pulse ${age != null ? Math.round(age) + "s ago" : "—"}`;
  } else if (mismatch) {
    color = "#FF9F0A";
    label = "WATCHER · MISMATCH";
    detail = `DB=${totalDb} but cache=${totalCached} — pulse may be erroring`;
  } else if (live && stubs === 0) {
    color = "#30D158";
    label = "WATCHER · LIVE";
    detail = `Last pulse ${Math.round(age)}s ago · ${totalCached} tracked`;
  } else if (live && stubs > 0) {
    color = "#A0DC5A";
    label = "WATCHER · WARMING";
    detail = `${totalCached} tracked · ${stubs} initialising · last ${Math.round(age)}s`;
  } else if (age == null) {
    color = "#FFD60A";
    label = "WATCHER · INITIALISING";
    detail = `${totalDb} open trade${totalDb !== 1 ? "s" : ""} found · waiting for first pulse`;
  } else {
    color = "#FF453A";
    label = "WATCHER · STALLED";
    detail = `No pulse for ${Math.round(age)}s — engine may be down`;
  }

  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 8,
      flexWrap: "wrap",
    }}>
      <Pill color={color} label={label} detail={detail} pulse={live} />

      {(mismatch || !engineOK || age == null || age > 60) && (
        <button
          onClick={forcePulse}
          disabled={forcing}
          style={{
            background: "#0A84FF22", border: "1px solid #0A84FF55",
            color: "#0A84FF", fontSize: 10, fontWeight: 700,
            padding: "5px 10px", borderRadius: 6, cursor: forcing ? "wait" : "pointer",
            letterSpacing: 0.4,
          }}>
          {forcing ? "Pulsing…" : "⚡ Force Pulse Now"}
        </button>
      )}

      <button
        onClick={() => setShowDebug(v => !v)}
        style={{
          background: "transparent", border: "1px solid #2A2A3F",
          color: "#666", fontSize: 10, padding: "5px 10px",
          borderRadius: 6, cursor: "pointer",
        }}>
        {showDebug ? "Hide debug" : "Debug"}
      </button>

      {showDebug && (
        <div style={{
          width: "100%", marginTop: 6,
          background: "#0A0A0F", border: "1px solid #1E1E2E",
          borderRadius: 8, padding: "10px 12px",
          fontFamily: "ui-monospace, monospace", fontSize: 10, color: "#aaa",
          lineHeight: 1.6,
        }}>
          <Row k="trades.db" v={`${debug.trades_db_path} (${debug.trades_db_exists ? `${(debug.trades_db_size/1024).toFixed(1)} KB` : "MISSING"})`} />
          <Row k="scalper.db" v={`${debug.scalper_db_path} (${debug.scalper_db_exists ? `${(debug.scalper_db_size/1024).toFixed(1)} KB` : "MISSING"})`} />
          <Row k="watcher.db" v={debug.watcher_db_path} />
          <Row k="DATA_DIR" v={`${debug.data_dir} ${debug.data_dir_is_data ? "✓ (Render persistent)" : "(local fallback)"}`} />
          <Row k="open in DB" v={`MAIN=${dbMain} ids=${JSON.stringify(debug.main_trade_ids || [])} · SCALPER=${dbScalper} ids=${JSON.stringify(debug.scalper_trade_ids || [])}`} />
          <Row k="cached" v={`${totalCached} keys=${JSON.stringify(debug.cached_keys || [])}`} />
          <Row k="engine" v={`alive=${debug.engine_alive} chains=${debug.engine_has_chains} spot_tokens=${debug.engine_has_spot_tokens}`} />
          <Row k="spot tokens" v={JSON.stringify(debug.spot_tokens_keys || [])} />
          <Row k="chains" v={JSON.stringify(debug.chains_keys || [])} />
          {debug.main_trade_summary?.length > 0 && (
            <Row k="main trades" v={debug.main_trade_summary.map(t => `#${t.id} ${t.idx} ${t.action} ${t.strike} entry=${t.entry} cur=${t.current_ltp}`).join(" | ")} />
          )}
          {debug.scalper_trade_summary?.length > 0 && (
            <Row k="scalper trades" v={debug.scalper_trade_summary.map(t => `#${t.id} ${t.idx} ${t.action} ${t.strike} entry=${t.entry} cur=${t.current_ltp}`).join(" | ")} />
          )}
        </div>
      )}
    </div>
  );
}


function Row({ k, v }) {
  return (
    <div style={{ display: "flex", gap: 8, marginBottom: 2 }}>
      <span style={{ color: "#666", minWidth: 100 }}>{k}:</span>
      <span style={{ color: "#ccc", wordBreak: "break-all", flex: 1 }}>{String(v)}</span>
    </div>
  );
}


function Pill({ color, label, detail, pulse }) {
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 8,
      background: `${color}15`,
      border: `1px solid ${color}55`,
      borderRadius: 8, padding: "5px 12px",
      fontFamily: "ui-monospace, monospace",
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%",
        background: color,
        boxShadow: `0 0 8px ${color}`,
        animation: pulse ? "watcherPulse 1.4s ease-in-out infinite" : "none",
      }} />
      <span style={{
        color, fontSize: 11, fontWeight: 700,
        letterSpacing: 0.6, textTransform: "uppercase",
      }}>
        {label}
      </span>
      <span style={{
        color: "#aaa", fontSize: 10, fontWeight: 500,
        borderLeft: `1px solid ${color}33`, paddingLeft: 8,
      }}>
        {detail}
      </span>
      <style>{`
        @keyframes watcherPulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(1.4); }
        }
      `}</style>
    </div>
  );
}
