import { useState, useEffect, useCallback } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

/**
 * TRAINING DASHBOARD — beast mode ML training UI.
 *
 * Features exposed:
 * - "Train Now" manual trigger
 * - Training history viewer (accepted/rejected runs + why)
 * - Engine health report (auto-disable status)
 * - A/B testing UI
 * - Online learning status
 */

async function fetchJ(url, opts) {
  try {
    const r = await fetch(url, opts);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

function Section({ title, accent, children, theme }) {
  return (
    <div
      style={{
        background: theme.SURFACE,
        border: `1px solid ${theme.BORDER}`,
        borderLeft: `2px solid ${accent || theme.ACCENT}`,
        borderRadius: RADIUS.LG,
        padding: SPACE.LG,
        marginBottom: SPACE.MD,
      }}
    >
      <div
        style={{
          color: accent || theme.ACCENT,
          fontSize: TEXT_SIZE.MICRO,
          fontWeight: TEXT_WEIGHT.BOLD,
          letterSpacing: 2,
          textTransform: "uppercase",
          marginBottom: SPACE.MD,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  );
}

export default function TrainingDashboard({ isOpen, onClose }) {
  const { theme } = useTheme();
  const [training, setTraining] = useState(false);
  const [lastReport, setLastReport] = useState(null);
  const [history, setHistory] = useState([]);
  const [health, setHealth] = useState([]);
  const [abStatus, setAbStatus] = useState(null);
  const [onlineStatus, setOnlineStatus] = useState(null);

  const refresh = useCallback(async () => {
    const [h, eh, ab, os] = await Promise.all([
      fetchJ("/api/training/history"),
      fetchJ("/api/training/engine-health"),
      fetchJ("/api/training/ab-status"),
      fetchJ("/api/training/online-status"),
    ]);
    setHistory(h?.runs || []);
    setHealth(eh?.engines || []);
    setAbStatus(ab);
    setOnlineStatus(os);
  }, []);

  useEffect(() => {
    if (isOpen) refresh();
  }, [isOpen, refresh]);

  const runTrainNow = async () => {
    setTraining(true);
    const report = await fetchJ("/api/training/run-now", { method: "POST" });
    setLastReport(report);
    setTraining(false);
    refresh();
  };

  const finalizeAB = async () => {
    await fetchJ("/api/training/ab-finalize", { method: "POST" });
    refresh();
  };

  if (!isOpen) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: theme.OVERLAY,
        zIndex: Z.MODAL,
        display: "flex",
        justifyContent: "center",
        alignItems: "flex-start",
        paddingTop: "4vh",
        paddingBottom: "4vh",
        overflowY: "auto",
        backdropFilter: "blur(4px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(900px, 94vw)",
          background: theme.BG,
          border: `1px solid ${theme.BORDER_HI}`,
          borderRadius: RADIUS.LG,
          boxShadow: theme.SHADOW_HI,
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            padding: SPACE.LG,
            borderBottom: `1px solid ${theme.BORDER}`,
            background: theme.SURFACE,
          }}
        >
          <div>
            <div
              style={{
                color: theme.PURPLE,
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 2,
                textTransform: "uppercase",
              }}
            >
              🧠 Beast Mode Training
            </div>
            <div
              style={{
                color: theme.TEXT,
                fontSize: TEXT_SIZE.H1,
                fontWeight: TEXT_WEIGHT.BLACK,
                marginTop: 2,
              }}
            >
              ML Training Dashboard
            </div>
            <div
              style={{
                color: theme.TEXT_MUTED,
                fontSize: TEXT_SIZE.MICRO,
                marginTop: 4,
              }}
            >
              11 features: time-decay · R-multiples · regime-aware · validation-gated · auto-disable · A/B testing · online learning
            </div>
          </div>
          <div style={{ display: "flex", gap: SPACE.SM }}>
            <button
              onClick={runTrainNow}
              disabled={training}
              style={{
                background: theme.PURPLE,
                color: "#fff",
                border: "none",
                borderRadius: RADIUS.SM,
                padding: "6px 16px",
                cursor: training ? "not-allowed" : "pointer",
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 1,
                textTransform: "uppercase",
                opacity: training ? 0.6 : 1,
              }}
            >
              {training ? "Training..." : "🧪 Train Now"}
            </button>
            <button
              onClick={onClose}
              style={{
                background: "transparent",
                border: `1px solid ${theme.BORDER}`,
                color: theme.TEXT_MUTED,
                borderRadius: RADIUS.SM,
                padding: "4px 10px",
                cursor: "pointer",
                fontSize: 14,
              }}
            >
              ×
            </button>
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: SPACE.LG, maxHeight: "80vh", overflowY: "auto" }}>
          {/* Last training report */}
          {lastReport && (
            <Section
              title="Latest Training Run"
              accent={lastReport.applied ? theme.GREEN : theme.AMBER}
              theme={theme}
            >
              {lastReport.error && (
                <div style={{ color: theme.RED, fontSize: TEXT_SIZE.BODY }}>
                  {lastReport.error}
                </div>
              )}
              {lastReport.status === "skipped" && (
                <div style={{ color: theme.AMBER, fontSize: TEXT_SIZE.BODY }}>
                  ⚠ Skipped: {lastReport.reason}
                </div>
              )}
              {lastReport.applied != null && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: SPACE.MD }}>
                  <MiniStat label="Regime" value={lastReport.regime || "—"} color={theme.ACCENT} theme={theme} />
                  <MiniStat label="Data Points" value={lastReport.records_count || 0} theme={theme} />
                  <MiniStat label="Train Acc" value={`${lastReport.train_accuracy || 0}%`} color={theme.GREEN} theme={theme} />
                  <MiniStat label="Validate Acc" value={`${lastReport.validate_accuracy || 0}%`} color={theme.ACCENT} theme={theme} />
                  <MiniStat
                    label="Result"
                    value={lastReport.applied ? "✓ Applied" : "⚠ Rejected"}
                    color={lastReport.applied ? theme.GREEN : theme.RED}
                    theme={theme}
                  />
                </div>
              )}
              {lastReport.rejection_reason && (
                <div style={{ color: theme.AMBER, fontSize: TEXT_SIZE.MICRO, marginTop: SPACE.SM, fontStyle: "italic" }}>
                  Rejection: {lastReport.rejection_reason}
                </div>
              )}
              {lastReport.auto_disabled && lastReport.auto_disabled.length > 0 && (
                <div style={{ marginTop: SPACE.SM, padding: SPACE.SM, background: theme.RED_DIM, borderRadius: RADIUS.SM }}>
                  <strong style={{ color: theme.RED, fontSize: TEXT_SIZE.MICRO, letterSpacing: 1 }}>AUTO-DISABLED:</strong>
                  <span style={{ color: theme.TEXT, fontSize: TEXT_SIZE.BODY, marginLeft: 8 }}>
                    {lastReport.auto_disabled.join(", ")}
                  </span>
                </div>
              )}
            </Section>
          )}

          {/* Engine Health */}
          <Section title="🏥 Engine Health" theme={theme}>
            {health.length === 0 ? (
              <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
                No health data yet. Run training first.
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: TEXT_SIZE.MICRO }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${theme.BORDER}` }}>
                    <th style={thStyle(theme)}>Engine</th>
                    <th style={thStyle(theme)}>Recent</th>
                    <th style={thStyle(theme)}>Historical</th>
                    <th style={thStyle(theme)}>Trades</th>
                    <th style={thStyle(theme)}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {health.map((h) => {
                    const isHealthy = !h.auto_disabled && (h.recent_accuracy || 0) >= 50;
                    const color = h.auto_disabled ? theme.RED : isHealthy ? theme.GREEN : theme.AMBER;
                    return (
                      <tr key={h.engine_key} style={{ borderBottom: `1px solid ${theme.BORDER}44` }}>
                        <td style={tdStyle(theme)}>{h.engine_key}</td>
                        <td style={{ ...tdStyle(theme), color: color, fontFamily: FONT.MONO }}>
                          {h.recent_accuracy || 0}%
                        </td>
                        <td style={{ ...tdStyle(theme), fontFamily: FONT.MONO }}>
                          {h.historical_accuracy || 0}%
                        </td>
                        <td style={{ ...tdStyle(theme), color: theme.TEXT_MUTED }}>
                          {h.trades_evaluated || 0}
                        </td>
                        <td style={tdStyle(theme)}>
                          {h.auto_disabled ? (
                            <span style={{ color: theme.RED, fontWeight: TEXT_WEIGHT.BOLD }}>✗ DISABLED</span>
                          ) : isHealthy ? (
                            <span style={{ color: theme.GREEN, fontWeight: TEXT_WEIGHT.BOLD }}>✓ HEALTHY</span>
                          ) : (
                            <span style={{ color: theme.AMBER, fontWeight: TEXT_WEIGHT.BOLD }}>⚠ WATCH</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Section>

          {/* A/B Testing */}
          <Section title="🧪 A/B Testing" accent={theme.CYAN} theme={theme}>
            {abStatus?.running ? (
              <div>
                <div style={{ color: theme.TEXT, fontSize: TEXT_SIZE.BODY, marginBottom: SPACE.SM }}>
                  <strong>Running test ID: {abStatus.running.id}</strong>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD, marginBottom: SPACE.MD }}>
                  <div style={{ padding: SPACE.MD, background: theme.ACCENT_DIM, borderRadius: RADIUS.SM }}>
                    <div style={{ color: theme.ACCENT, fontSize: 9, letterSpacing: 1, fontWeight: TEXT_WEIGHT.BOLD }}>SET A</div>
                    <div style={{ color: theme.TEXT, fontSize: 14, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO, marginTop: 2 }}>
                      {abStatus.running.set_a_trades} trades · {abStatus.running.set_a_wins} wins
                    </div>
                    <div style={{ color: (abStatus.running.set_a_pnl || 0) >= 0 ? theme.GREEN : theme.RED, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>
                      P&L: ₹{Math.round(abStatus.running.set_a_pnl || 0).toLocaleString("en-IN")}
                    </div>
                  </div>
                  <div style={{ padding: SPACE.MD, background: theme.PURPLE_DIM, borderRadius: RADIUS.SM }}>
                    <div style={{ color: theme.PURPLE, fontSize: 9, letterSpacing: 1, fontWeight: TEXT_WEIGHT.BOLD }}>SET B</div>
                    <div style={{ color: theme.TEXT, fontSize: 14, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO, marginTop: 2 }}>
                      {abStatus.running.set_b_trades} trades · {abStatus.running.set_b_wins} wins
                    </div>
                    <div style={{ color: (abStatus.running.set_b_pnl || 0) >= 0 ? theme.GREEN : theme.RED, fontFamily: FONT.MONO, fontWeight: TEXT_WEIGHT.BOLD }}>
                      P&L: ₹{Math.round(abStatus.running.set_b_pnl || 0).toLocaleString("en-IN")}
                    </div>
                  </div>
                </div>
                <button
                  onClick={finalizeAB}
                  style={{
                    background: theme.CYAN,
                    color: "#000",
                    border: "none",
                    borderRadius: RADIUS.SM,
                    padding: "6px 14px",
                    cursor: "pointer",
                    fontSize: TEXT_SIZE.MICRO,
                    fontWeight: TEXT_WEIGHT.BOLD,
                    letterSpacing: 1,
                    textTransform: "uppercase",
                  }}
                >
                  Finalize & Promote Winner
                </button>
              </div>
            ) : (
              <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
                No A/B test running. Start one via API or Engine Control panel.
              </div>
            )}
            {abStatus?.recent && abStatus.recent.length > 0 && (
              <div style={{ marginTop: SPACE.MD }}>
                <div style={{ color: theme.TEXT_DIM, fontSize: 9, letterSpacing: 1, fontWeight: TEXT_WEIGHT.BOLD, marginBottom: 4 }}>
                  RECENT TESTS
                </div>
                {abStatus.recent.map((t) => (
                  <div key={t.id} style={{ fontSize: TEXT_SIZE.MICRO, color: theme.TEXT_MUTED, padding: "4px 0", borderBottom: `1px solid ${theme.BORDER}33` }}>
                    #{t.id} · Winner: <strong style={{ color: theme.GREEN }}>SET {t.winner?.toUpperCase()}</strong> · Ended {new Date(t.ended_at).toLocaleDateString("en-IN")}
                  </div>
                ))}
              </div>
            )}
          </Section>

          {/* Online Learning */}
          <Section title="⚡ Online Learning" accent={theme.AMBER} theme={theme}>
            {onlineStatus?.state ? (
              <div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: SPACE.MD }}>
                  <MiniStat label="Updates Count" value={onlineStatus.state.updates_count || 0} color={theme.AMBER} theme={theme} />
                  <MiniStat
                    label="Last Update"
                    value={onlineStatus.state.last_update ? new Date(onlineStatus.state.last_update).toLocaleTimeString("en-IN") : "Never"}
                    theme={theme}
                  />
                </div>
                <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, marginTop: SPACE.SM, fontStyle: "italic" }}>
                  Weights update after each completed trade with R-multiple weighted feedback.
                </div>
              </div>
            ) : (
              <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
                No online updates yet. Will start with first completed trade.
              </div>
            )}
          </Section>

          {/* Training History */}
          <Section title="📜 Training History" theme={theme}>
            {history.length === 0 ? (
              <div style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO }}>
                No training runs yet. Click "Train Now" to start.
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: TEXT_SIZE.MICRO }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${theme.BORDER}` }}>
                    <th style={thStyle(theme)}>When</th>
                    <th style={thStyle(theme)}>Regime</th>
                    <th style={thStyle(theme)}>Data</th>
                    <th style={thStyle(theme)}>Validate</th>
                    <th style={thStyle(theme)}>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h) => (
                    <tr key={h.id} style={{ borderBottom: `1px solid ${theme.BORDER}44` }}>
                      <td style={tdStyle(theme)}>
                        {new Date(h.timestamp).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
                      </td>
                      <td style={{ ...tdStyle(theme), color: theme.ACCENT }}>{h.regime}</td>
                      <td style={tdStyle(theme)}>{h.data_points}</td>
                      <td style={{ ...tdStyle(theme), fontFamily: FONT.MONO }}>
                        {(h.accuracy_validate || 0).toFixed(1)}%
                      </td>
                      <td style={tdStyle(theme)}>
                        {h.accepted ? (
                          <span style={{ color: theme.GREEN, fontWeight: TEXT_WEIGHT.BOLD }}>✓ Applied</span>
                        ) : (
                          <span style={{ color: theme.AMBER, fontWeight: TEXT_WEIGHT.BOLD }}>⚠ Rejected</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>
        </div>

        {/* Footer */}
        <div
          style={{
            padding: SPACE.MD,
            borderTop: `1px solid ${theme.BORDER}`,
            background: theme.SURFACE,
            fontSize: 10,
            color: theme.TEXT_DIM,
          }}
        >
          <strong style={{ color: theme.TEXT }}>Active features:</strong> R-multiple outcomes · exponential time decay (7-day half-life) ·
          regime classification (bull/bear/sideways) · time-of-day bucketing · correlation matrix · 80/20 train-validate split ·
          engine health monitoring · auto-disable broken engines · A/B testing · online incremental updates · feature engineering context.
        </div>
      </div>
    </div>
  );
}

function MiniStat({ label, value, color, theme }) {
  return (
    <div>
      <div style={{ color: theme.TEXT_DIM, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, textTransform: "uppercase", marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ color: color || theme.TEXT, fontSize: 15, fontWeight: TEXT_WEIGHT.BOLD, fontFamily: FONT.MONO }}>
        {value}
      </div>
    </div>
  );
}

function thStyle(theme) {
  return {
    padding: SPACE.SM,
    color: theme.TEXT_DIM,
    fontSize: 9,
    letterSpacing: 1,
    textTransform: "uppercase",
    textAlign: "left",
    fontWeight: TEXT_WEIGHT.BOLD,
  };
}

function tdStyle(theme) {
  return {
    padding: "6px 8px",
    color: theme.TEXT,
    fontSize: TEXT_SIZE.MICRO,
  };
}
