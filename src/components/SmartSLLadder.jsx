/**
 * SmartSLLadder — Live ladder visualization for open scalper trades.
 * Shows 7-stage profit ladder with done/active/pending status + spot anchor.
 *
 * Polls /api/scalper/trades/{id}/ladder every 2s for fresh data.
 */

import React, { useEffect, useState } from "react";

const GREEN = "#30D158";
const RED = "#FF453A";
const YELLOW = "#FFD60A";
const ORANGE = "#FF9F0A";
const ACCENT = "#0A84FF";
const GRAY = "#6b7280";
const BG = "#0A0A0F";
const CARD = "#111118";
const BORDER = "#1E1E2E";

async function safeFetch(url, fb) {
  try { const r = await fetch(url); if (!r.ok) return fb; return await r.json(); } catch { return fb; }
}

export default function SmartSLLadder({ tradeId, entry, action, currentLtp, entrySpot, currentSpot }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    const fetchLadder = async () => {
      const r = await safeFetch(`/api/scalper/trades/${tradeId}/ladder`, null);
      if (r && !r.error) setData(r);
    };
    fetchLadder();
    const iv = setInterval(fetchLadder, 2000);
    return () => clearInterval(iv);
  }, [tradeId]);

  if (!data) {
    return <div style={{ padding: 12, color: GRAY, fontSize: 11 }}>Loading SL ladder…</div>;
  }

  const ladder = data.ladder || [];
  const profitPct = data.entry_price > 0 ? ((data.current_ltp || data.entry_price) - data.entry_price) / data.entry_price * 100 : 0;
  const activeSL = data.active_sl;
  const spotAnchorPct = data.spot_anchor_pct || 0.4;
  const enabled = data.smart_sl_enabled;
  const isCE = (action || "").includes("CE");
  const spotChangePct = data.entry_spot && currentSpot
    ? ((currentSpot - data.entry_spot) / data.entry_spot) * 100
    : 0;
  const spotExitTrigger = isCE
    ? data.entry_spot * (1 - spotAnchorPct / 100)
    : data.entry_spot * (1 + spotAnchorPct / 100);

  return (
    <div style={{ background: BG, border: `1px solid ${BORDER}`, borderRadius: 8, padding: 14 }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <div style={{ color: ACCENT, fontSize: 12, fontWeight: 800 }}>📊 SMART SL LADDER</div>
          <div style={{ color: GRAY, fontSize: 10, marginTop: 2 }}>
            7-stage profit-based SL ratchet
          </div>
        </div>
        {!enabled && (
          <span style={{
            background: GRAY + "22", color: GRAY,
            padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700,
          }}>
            DISABLED (using static SL)
          </span>
        )}
        {enabled && (
          <span style={{
            background: GREEN + "22", color: GREEN,
            padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700,
          }}>
            ● ACTIVE
          </span>
        )}
      </div>

      {/* Profit progress bar */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: GRAY, marginBottom: 4 }}>
          <span>Profit: {profitPct >= 0 ? "+" : ""}{profitPct.toFixed(1)}%</span>
          <span>Stage {data.current_stage} of 6</span>
        </div>
        <div style={{ height: 6, background: "#1a1a1a", borderRadius: 3, overflow: "hidden" }}>
          <div style={{
            width: `${Math.min(Math.max((profitPct / 60) * 100, 0), 100)}%`,
            height: "100%",
            background: profitPct >= 25 ? GREEN : profitPct >= 10 ? ACCENT : profitPct >= 0 ? YELLOW : RED,
            transition: "width 0.5s",
          }} />
        </div>
      </div>

      {/* Ladder table */}
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${BORDER}` }}>
              <th style={{ padding: "5px", color: GRAY, textAlign: "left", fontSize: 9 }}>STAGE</th>
              <th style={{ padding: "5px", color: GRAY, textAlign: "left", fontSize: 9 }}>TRIGGER</th>
              <th style={{ padding: "5px", color: GRAY, textAlign: "right", fontSize: 9 }}>SL @</th>
              <th style={{ padding: "5px", color: GRAY, textAlign: "left", fontSize: 9 }}>LABEL</th>
              <th style={{ padding: "5px", color: GRAY, textAlign: "center", fontSize: 9 }}>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {ladder.map(s => {
              const isActive = s.status === "ACTIVE";
              const isDone = s.status === "DONE";
              const c = isDone ? GREEN : isActive ? ORANGE : "#444";
              return (
                <tr key={s.stage} style={{
                  borderBottom: `1px solid ${BORDER}33`,
                  background: isActive ? ORANGE + "15" : "transparent",
                }}>
                  <td style={{ padding: "5px", color: c, fontWeight: 700 }}>{s.stage}</td>
                  <td style={{ padding: "5px", color: c }}>
                    {s.trigger_pct > 0 ? `+${s.trigger_pct}%` : "Entry"}
                  </td>
                  <td style={{ padding: "5px", textAlign: "right", color: c, fontWeight: 700 }}>
                    ₹{s.sl_at?.toFixed(2)}
                  </td>
                  <td style={{ padding: "5px", color: c }}>
                    {s.sl_offset_pct > 0 ? `+${s.sl_offset_pct}%` :
                     s.sl_offset_pct === 0 ? "BE" : `${s.sl_offset_pct}%`}
                  </td>
                  <td style={{ padding: "5px", textAlign: "center" }}>
                    {isDone && <span style={{ color: GREEN, fontWeight: 700 }}>✓</span>}
                    {isActive && <span style={{ color: ORANGE, fontWeight: 700 }}>◉</span>}
                    {s.status === "PENDING" && <span style={{ color: "#444" }}>⏳</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Active SL highlight */}
      <div style={{
        marginTop: 12, padding: 10,
        background: enabled ? ORANGE + "15" : CARD,
        border: `1px solid ${enabled ? ORANGE + "44" : BORDER}`,
        borderRadius: 6,
      }}>
        <div style={{ fontSize: 9, color: GRAY, fontWeight: 700, textTransform: "uppercase" }}>
          {enabled ? "▼ Active Smart SL" : "Static SL (Smart SL disabled)"}
        </div>
        <div style={{ color: enabled ? ORANGE : "#888", fontSize: 16, fontWeight: 800, marginTop: 2 }}>
          ₹{activeSL?.toFixed(2)}
        </div>
        <div style={{ fontSize: 9, color: GRAY, marginTop: 2 }}>
          Distance from current ₹{(data.current_ltp || 0).toFixed(2)}:
          <b style={{ color: data.current_ltp - activeSL > 0 ? GREEN : RED, marginLeft: 4 }}>
            {((data.current_ltp - activeSL) / data.current_ltp * 100).toFixed(2)}%
          </b>
        </div>
      </div>

      {/* Spot anchor */}
      {enabled && data.entry_spot && (
        <div style={{
          marginTop: 8, padding: 10,
          background: ACCENT + "10",
          border: `1px solid ${ACCENT}33`,
          borderRadius: 6,
        }}>
          <div style={{ fontSize: 9, color: GRAY, fontWeight: 700, textTransform: "uppercase" }}>
            📍 Spot Anchor ({spotAnchorPct}% threshold)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 6 }}>
            <div>
              <div style={{ fontSize: 9, color: "#666" }}>Entry Spot</div>
              <div style={{ fontSize: 12, color: "#fff", fontWeight: 700 }}>{data.entry_spot.toFixed(1)}</div>
            </div>
            <div>
              <div style={{ fontSize: 9, color: "#666" }}>Current</div>
              <div style={{
                fontSize: 12, fontWeight: 700,
                color: Math.abs(spotChangePct) >= spotAnchorPct ? RED : GREEN,
              }}>
                {currentSpot ? currentSpot.toFixed(1) : "—"}
                <span style={{ fontSize: 9, marginLeft: 4, color: GRAY }}>
                  ({spotChangePct >= 0 ? "+" : ""}{spotChangePct.toFixed(2)}%)
                </span>
              </div>
            </div>
            <div>
              <div style={{ fontSize: 9, color: "#666" }}>Exit If</div>
              <div style={{ fontSize: 11, color: RED, fontWeight: 700 }}>
                {isCE ? "<" : ">"} {spotExitTrigger?.toFixed(1)}
              </div>
            </div>
          </div>
        </div>
      )}

      {!enabled && (
        <div style={{
          marginTop: 8, padding: 8,
          background: YELLOW + "15", border: `1px solid ${YELLOW}33`,
          borderRadius: 6, fontSize: 10, color: YELLOW,
        }}>
          ℹ️ Smart SL is OFF. Toggle ON in Scalper config to activate ladder protection.
        </div>
      )}
    </div>
  );
}
