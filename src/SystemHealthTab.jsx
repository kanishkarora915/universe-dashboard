/**
 * SystemHealthTab
 * ───────────────
 * One-click full-dashboard diagnostic. Calls /api/system/health-check
 * and renders every component's PASS/WARN/FAIL with detail.
 */

import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "";

const STATUS_COLOR = {
  PASS: "#30D158",
  WARN: "#FF9F0A",
  FAIL: "#FF453A",
};

const OVERALL_COLOR = {
  HEALTHY: "#30D158",
  OK_WITH_WARNINGS: "#FFD60A",
  DEGRADED: "#FF9F0A",
  BROKEN: "#FF453A",
};

export default function SystemHealthTab() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const runCheck = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/system/health-check`);
      if (!r.ok) {
        setError(`API returned ${r.status}`);
        return;
      }
      const j = await r.json();
      if (j.error) {
        setError(j.error);
        return;
      }
      setReport(j);
    } catch (e) {
      setError(e.message || "network error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    runCheck();
  }, []);

  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(runCheck, 30000);
    return () => clearInterval(t);
  }, [autoRefresh]);

  return (
    <div style={{ padding: "20px 24px", fontFamily: "ui-sans-serif" }}>
      {/* HEADER */}
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "flex-start", flexWrap: "wrap", gap: 12, marginBottom: 16,
      }}>
        <div>
          <div style={{ color: "#fff", fontSize: 20, fontWeight: 800, letterSpacing: -0.3 }}>
            🔬 System Health Check
          </div>
          <div style={{ color: "#888", fontSize: 12, marginTop: 4 }}>
            Full end-to-end diagnostic of every dashboard component.
            {report && ` · Last run took ${report.duration_ms}ms`}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "#aaa", cursor: "pointer" }}>
            <input type="checkbox" checked={autoRefresh}
                   onChange={e => setAutoRefresh(e.target.checked)}/>
            Auto-refresh 30s
          </label>
          <button onClick={runCheck} disabled={loading} style={{
            background: "#0A84FF22", border: "1px solid #0A84FF55",
            color: "#0A84FF", fontSize: 12, fontWeight: 700,
            padding: "8px 16px", borderRadius: 6, cursor: loading ? "wait" : "pointer",
          }}>
            {loading ? "Running…" : "🔄 Run Check Now"}
          </button>
        </div>
      </div>

      {error && (
        <div style={{
          background: "#FF453A20", border: "1px solid #FF453A",
          color: "#FF453A", padding: "12px 16px", borderRadius: 8,
          fontSize: 12, marginBottom: 16,
        }}>
          ❌ {error}
        </div>
      )}

      {report && (
        <>
          {/* OVERALL VERDICT */}
          <OverallVerdict report={report}/>

          {/* CATEGORIES */}
          {Object.entries(report.categories).map(([catName, checks]) => (
            <CategoryBlock key={catName} name={catName} checks={checks}/>
          ))}
        </>
      )}
    </div>
  );
}


function OverallVerdict({ report }) {
  const overall = report.overall || "UNKNOWN";
  const summary = report.summary || {};
  const color = OVERALL_COLOR[overall] || "#888";
  const ts = new Date(report.ts * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });

  return (
    <div style={{
      background: `${color}10`, border: `2px solid ${color}55`,
      borderRadius: 12, padding: "16px 20px", marginBottom: 16,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ color, fontSize: 24, fontWeight: 800, letterSpacing: -0.3 }}>
            {overall.replace(/_/g, " ")}
          </div>
          <div style={{ color: "#888", fontSize: 11, marginTop: 4 }}>Last check: {ts}</div>
        </div>
        <div style={{ display: "flex", gap: 16 }}>
          <Stat label="TOTAL" value={summary.total} color="#aaa"/>
          <Stat label="PASS" value={summary.pass} color={STATUS_COLOR.PASS}/>
          <Stat label="WARN" value={summary.warn} color={STATUS_COLOR.WARN}/>
          <Stat label="FAIL" value={summary.fail} color={STATUS_COLOR.FAIL}/>
        </div>
      </div>
    </div>
  );
}


function CategoryBlock({ name, checks }) {
  const fails = checks.filter(c => c.status === "FAIL").length;
  const warns = checks.filter(c => c.status === "WARN").length;
  const passes = checks.filter(c => c.status === "PASS").length;

  return (
    <div style={{
      background: "#111118", border: "1px solid #1E1E2E",
      borderRadius: 10, padding: "12px 16px", marginBottom: 12,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8,
      }}>
        <div style={{
          color: "#fff", fontSize: 13, fontWeight: 700,
          textTransform: "uppercase", letterSpacing: 0.6,
        }}>
          {name}
        </div>
        <div style={{ display: "flex", gap: 8, fontSize: 10 }}>
          {passes > 0 && (
            <span style={{ color: STATUS_COLOR.PASS, fontWeight: 700 }}>✓ {passes}</span>
          )}
          {warns > 0 && (
            <span style={{ color: STATUS_COLOR.WARN, fontWeight: 700 }}>⚠ {warns}</span>
          )}
          {fails > 0 && (
            <span style={{ color: STATUS_COLOR.FAIL, fontWeight: 700 }}>✕ {fails}</span>
          )}
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {checks.map((c, i) => <CheckRow key={i} check={c}/>)}
      </div>
    </div>
  );
}


function CheckRow({ check }) {
  const status = check.status || "UNKNOWN";
  const color = STATUS_COLOR[status] || "#888";
  const icon = status === "PASS" ? "✓" : status === "WARN" ? "⚠" : "✕";

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "6px 10px", borderRadius: 5,
      background: `${color}08`, border: `1px solid ${color}22`,
    }}>
      <span style={{
        color, fontSize: 14, fontWeight: 800, minWidth: 16,
      }}>
        {icon}
      </span>
      <span style={{ color: "#ddd", fontSize: 12, fontWeight: 600, minWidth: 220 }}>
        {check.name}
      </span>
      <span style={{
        color: "#999", fontSize: 11, flex: 1, fontFamily: "ui-monospace, monospace",
        wordBreak: "break-word",
      }}>
        {check.detail || "—"}
      </span>
    </div>
  );
}


function Stat({ label, value, color }) {
  return (
    <div style={{ textAlign: "right" }}>
      <div style={{ color: "#666", fontSize: 9, fontWeight: 700, letterSpacing: 0.5 }}>{label}</div>
      <div style={{ color, fontSize: 18, fontWeight: 800 }}>{value ?? 0}</div>
    </div>
  );
}
