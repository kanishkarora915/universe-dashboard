import { useState, useEffect, useRef, useCallback } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION } from "../theme";

async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

// Convert 9:15 to min-of-day, 15:30 to min-of-day
function minOfDay(hh, mm) {
  return hh * 60 + mm;
}

const MARKET_OPEN = minOfDay(9, 15);
const MARKET_CLOSE = minOfDay(15, 30);
const MARKET_MINS = MARKET_CLOSE - MARKET_OPEN; // 375

function minsToLabel(mins) {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export default function ReplayMode({ index = "NIFTY", isOpen, onClose }) {
  const { theme } = useTheme();
  const [snapshots, setSnapshots] = useState([]);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4); // 1=realtime, 4=4x, 16=16x
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const timerRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return;
    fetchJSON(`/api/replay/snapshots?index=${index}&date=${date}`).then((data) => {
      setSnapshots(data?.snapshots || []);
      setCursor(0);
    });
  }, [isOpen, index, date]);

  useEffect(() => {
    if (!playing) {
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }
    timerRef.current = setInterval(() => {
      setCursor((c) => {
        if (c >= snapshots.length - 1) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, Math.max(100, 5000 / speed));
    return () => clearInterval(timerRef.current);
  }, [playing, speed, snapshots.length]);

  const current = snapshots[cursor];

  if (!isOpen) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: SPACE.MD }}>
      <div
        style={{
          background: theme.SURFACE,
          border: `1px solid ${theme.BORDER}`,
          borderRadius: RADIUS.LG,
          padding: SPACE.LG,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: SPACE.LG }}>
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
              Replay Mode
            </div>
            <div
              style={{
                color: theme.TEXT,
                fontSize: TEXT_SIZE.H1,
                fontWeight: TEXT_WEIGHT.BLACK,
                marginTop: 2,
              }}
            >
              {index} · {date}
            </div>
          </div>
          <div style={{ display: "flex", gap: SPACE.SM, alignItems: "center" }}>
            <input
              type="date"
              value={date}
              max={new Date().toISOString().slice(0, 10)}
              onChange={(e) => setDate(e.target.value)}
              style={{
                background: theme.SURFACE_HI,
                color: theme.TEXT,
                border: `1px solid ${theme.BORDER}`,
                borderRadius: RADIUS.SM,
                padding: "6px 10px",
                fontSize: TEXT_SIZE.BODY,
                fontFamily: FONT.UI,
              }}
            />
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
                }}
              >
                ×
              </button>
            )}
          </div>
        </div>

        {snapshots.length === 0 ? (
          <div
            style={{
              color: theme.TEXT_DIM,
              textAlign: "center",
              padding: SPACE.XXXL,
              fontSize: TEXT_SIZE.BODY,
            }}
          >
            No snapshots captured for {date}. Replay requires the engine to have captured snapshots during market hours.
          </div>
        ) : (
          <>
            {/* Timeline */}
            <div style={{ position: "relative", height: 48, marginBottom: SPACE.MD }}>
              <input
                type="range"
                min={0}
                max={snapshots.length - 1}
                value={cursor}
                onChange={(e) => setCursor(parseInt(e.target.value, 10))}
                style={{
                  width: "100%",
                  height: 6,
                  accentColor: theme.ACCENT,
                }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
                <span style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, fontFamily: FONT.MONO }}>
                  {minsToLabel(MARKET_OPEN)}
                </span>
                <span
                  style={{
                    color: theme.ACCENT,
                    fontSize: TEXT_SIZE.BODY,
                    fontWeight: TEXT_WEIGHT.BOLD,
                    fontFamily: FONT.MONO,
                  }}
                >
                  {current?.time || "—"}
                </span>
                <span style={{ color: theme.TEXT_DIM, fontSize: TEXT_SIZE.MICRO, fontFamily: FONT.MONO }}>
                  {minsToLabel(MARKET_CLOSE)}
                </span>
              </div>
            </div>

            {/* Controls */}
            <div style={{ display: "flex", alignItems: "center", gap: SPACE.MD, justifyContent: "center" }}>
              <button
                onClick={() => setCursor(0)}
                style={{
                  background: theme.SURFACE_HI,
                  color: theme.TEXT,
                  border: `1px solid ${theme.BORDER}`,
                  borderRadius: RADIUS.SM,
                  padding: "6px 14px",
                  cursor: "pointer",
                  fontSize: 14,
                }}
              >
                ⏮
              </button>
              <button
                onClick={() => setCursor((c) => Math.max(0, c - 1))}
                style={{
                  background: theme.SURFACE_HI,
                  color: theme.TEXT,
                  border: `1px solid ${theme.BORDER}`,
                  borderRadius: RADIUS.SM,
                  padding: "6px 14px",
                  cursor: "pointer",
                  fontSize: 14,
                }}
              >
                ⏪
              </button>
              <button
                onClick={() => setPlaying((p) => !p)}
                style={{
                  background: theme.ACCENT,
                  color: "#fff",
                  border: "none",
                  borderRadius: RADIUS.SM,
                  padding: "8px 20px",
                  cursor: "pointer",
                  fontSize: 14,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  minWidth: 80,
                }}
              >
                {playing ? "⏸ Pause" : "▶ Play"}
              </button>
              <button
                onClick={() => setCursor((c) => Math.min(snapshots.length - 1, c + 1))}
                style={{
                  background: theme.SURFACE_HI,
                  color: theme.TEXT,
                  border: `1px solid ${theme.BORDER}`,
                  borderRadius: RADIUS.SM,
                  padding: "6px 14px",
                  cursor: "pointer",
                  fontSize: 14,
                }}
              >
                ⏩
              </button>
              <div style={{ marginLeft: SPACE.LG, display: "flex", gap: 4 }}>
                {[1, 4, 16].map((s) => (
                  <button
                    key={s}
                    onClick={() => setSpeed(s)}
                    style={{
                      background: speed === s ? theme.PURPLE : "transparent",
                      color: speed === s ? "#fff" : theme.TEXT_MUTED,
                      border: `1px solid ${speed === s ? theme.PURPLE : theme.BORDER}`,
                      borderRadius: RADIUS.SM,
                      padding: "4px 10px",
                      cursor: "pointer",
                      fontSize: TEXT_SIZE.MICRO,
                      fontWeight: TEXT_WEIGHT.BOLD,
                      fontFamily: FONT.MONO,
                    }}
                  >
                    {s}x
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </div>

      {/* Current snapshot details */}
      {current && (
        <div
          style={{
            background: theme.SURFACE,
            border: `1px solid ${theme.BORDER}`,
            borderRadius: RADIUS.LG,
            padding: SPACE.LG,
          }}
        >
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
            Snapshot @ {current.time}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: SPACE.MD }}>
            <Stat label="Spot" value={current.spot?.toLocaleString("en-IN") || "—"} theme={theme} />
            <Stat label="PCR" value={current.pcr?.toFixed(2) || "—"} theme={theme} />
            <Stat label="Max Pain" value={current.maxPain || "—"} theme={theme} />
            <Stat label="CE Wall" value={current.ceWall || "—"} color={theme.RED} theme={theme} />
            <Stat label="PE Wall" value={current.peWall || "—"} color={theme.GREEN} theme={theme} />
            <Stat label="Signal" value={`${current.signalScore || 0}/9`} color={theme.ACCENT} theme={theme} />
          </div>
          {current.verdict && (
            <div
              style={{
                marginTop: SPACE.MD,
                padding: SPACE.MD,
                background: theme.SURFACE_HI,
                borderRadius: RADIUS.MD,
                color: theme.TEXT,
                fontSize: TEXT_SIZE.BODY,
                fontStyle: "italic",
              }}
            >
              "{current.verdict}"
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color, theme }) {
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
          fontFamily: FONT.MONO,
        }}
      >
        {value}
      </div>
    </div>
  );
}
