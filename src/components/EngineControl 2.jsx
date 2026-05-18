import { useEffect, useState } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

/**
 * ENGINE CONTROL — User can toggle each engine ON/OFF.
 * OFF engines still RUN + display data in their tab, but don't
 * contribute to trade verdict. This keeps all tabs useful while
 * letting user filter noisy signals for cleaner decisions.
 *
 * Recommendations are labeled per option-buyer perspective.
 */

// Each engine: what it does + option-buyer recommendation (ON/OFF/OPTIONAL)
const ENGINE_META = {
  seller_positioning: {
    label: "Seller Flow",
    icon: "🦑",
    desc: "Who's writing options — CE writing = resistance, PE writing = support. Direct institutional direction.",
    recommend: "ON",
    reason: "Best primary signal for option buyers. Always keep ON.",
  },
  oi_flow: {
    label: "OI Change",
    icon: "📈",
    desc: "Fresh OI addition vs unwinding. Tells if conviction is building or exiting.",
    recommend: "ON",
    reason: "Core signal. Confirms institutional commitment. Always ON.",
  },
  multi_timeframe: {
    label: "Multi-Timeframe",
    icon: "⏱",
    desc: "5m / 15m / 1h / Daily trend alignment. When all agree, high probability move.",
    recommend: "ON",
    reason: "Highest-accuracy confluence signal. Always ON for buyers.",
  },
  fii_dii: {
    label: "FII/DII Flow",
    icon: "💰",
    desc: "Institutional net buy/sell for the day. Strong directional bias.",
    recommend: "ON",
    reason: "Reliable for directional bias. Keep ON.",
  },
  global_cues: {
    label: "Global Cues",
    icon: "🌍",
    desc: "SGX Nifty, Dow futures, Asian markets. Sets morning tone.",
    recommend: "ON",
    reason: "Critical for opening-hour trades. Keep ON.",
  },
  market_context: {
    label: "Market Context",
    icon: "📊",
    desc: "Overall VIX, breadth, momentum. Macro health indicator.",
    recommend: "ON",
    reason: "Keeps verdict grounded in current regime. Keep ON.",
  },
  vwap: {
    label: "VWAP",
    icon: "📉",
    desc: "Volume-Weighted Average Price. Above VWAP = bullish, below = bearish.",
    recommend: "OPTIONAL",
    reason: "Good for intraday scalps, less useful for swings. Toggle based on style.",
  },
  trap_fingerprints: {
    label: "Trap Finder",
    icon: "🧨",
    desc: "Far OTM institutional trap detection. Where big money is pulling retail.",
    recommend: "OFF",
    reason: "Designed for SELLERS to avoid. Option BUYERS rarely trade far OTM. Adds noise — turn OFF.",
  },
  price_action: {
    label: "Price Action",
    icon: "💥",
    desc: "EMA / RSI / MACD chart signals on spot price.",
    recommend: "OFF",
    reason: "Subjective, slower than OI signals. Options premium doesn't track spot chart patterns cleanly. Turn OFF.",
  },
};

// Quick profiles — user can apply instantly
const PROFILES = {
  "optionBuyerClean": {
    label: "Option Buyer (clean)",
    desc: "5 core signals. Recommended for daily trading.",
    config: {
      seller_positioning: true, oi_flow: true, multi_timeframe: true,
      fii_dii: true, global_cues: true, market_context: true,
      vwap: false, trap_fingerprints: false, price_action: false,
    },
  },
  "allOn": {
    label: "All Signals (default)",
    desc: "Use all 9 engines. May produce conflicts.",
    config: Object.fromEntries(Object.keys(ENGINE_META).map((k) => [k, true])),
  },
  "minimalist": {
    label: "Minimalist (3 core)",
    desc: "Only Seller + OI + Multi-TF. Maximum clarity.",
    config: {
      seller_positioning: true, oi_flow: true, multi_timeframe: true,
      fii_dii: false, global_cues: false, market_context: false,
      vwap: false, trap_fingerprints: false, price_action: false,
    },
  },
  "morningSession": {
    label: "Morning Session",
    desc: "Emphasizes global cues + multi-TF for 9:15-11:00.",
    config: {
      seller_positioning: true, oi_flow: true, multi_timeframe: true,
      fii_dii: true, global_cues: true, market_context: true,
      vwap: true, trap_fingerprints: false, price_action: false,
    },
  },
};

export default function EngineControl({ isOpen, onClose }) {
  const { theme } = useTheme();
  const [engines, setEngines] = useState([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    fetch("/api/engine-toggles")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setEngines(d?.engines || []));
  }, [isOpen]);

  const save = async (toggles) => {
    setSaving(true);
    try {
      await fetch("/api/engine-toggles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ toggles }),
      });
      // Re-fetch
      const r = await fetch("/api/engine-toggles");
      if (r.ok) {
        const d = await r.json();
        setEngines(d.engines || []);
      }
    } finally {
      setSaving(false);
    }
  };

  const toggle = (key) => {
    const updated = engines.map((e) =>
      e.key === key ? { ...e, active: !e.active } : e
    );
    setEngines(updated);
    save(Object.fromEntries(updated.map((e) => [e.key, e.active])));
  };

  const applyProfile = (profileKey) => {
    const cfg = PROFILES[profileKey].config;
    const updated = engines.map((e) => ({ ...e, active: !!cfg[e.key] }));
    setEngines(updated);
    save(cfg);
  };

  const activeCount = engines.filter((e) => e.active).length;
  const totalWeight = engines.filter((e) => e.active).reduce((s, e) => s + e.weight, 0);

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
          width: "min(720px, 94vw)",
          background: theme.SURFACE,
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
          }}
        >
          <div>
            <div
              style={{
                color: theme.ACCENT,
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 2,
                textTransform: "uppercase",
              }}
            >
              ⚙ Engine Control
            </div>
            <div
              style={{
                color: theme.TEXT,
                fontSize: TEXT_SIZE.H1,
                fontWeight: TEXT_WEIGHT.BLACK,
                marginTop: 2,
              }}
            >
              Which engines decide your trades?
            </div>
            <div
              style={{
                color: theme.TEXT_MUTED,
                fontSize: TEXT_SIZE.MICRO,
                marginTop: 4,
              }}
            >
              {activeCount} of {engines.length} active · Total weight: {totalWeight}
              {saving && <span style={{ color: theme.AMBER, marginLeft: 8 }}>Saving...</span>}
            </div>
          </div>
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

        {/* Quick profiles */}
        <div
          style={{
            padding: SPACE.MD,
            borderBottom: `1px solid ${theme.BORDER}`,
            background: theme.SURFACE_HI,
          }}
        >
          <div
            style={{
              color: theme.PURPLE,
              fontSize: TEXT_SIZE.MICRO,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 1.5,
              textTransform: "uppercase",
              marginBottom: SPACE.SM,
            }}
          >
            Quick Profiles (one-click apply)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: SPACE.SM }}>
            {Object.entries(PROFILES).map(([key, p]) => (
              <button
                key={key}
                onClick={() => applyProfile(key)}
                style={{
                  background: key === "optionBuyerClean" ? theme.ACCENT_DIM : theme.SURFACE,
                  color: key === "optionBuyerClean" ? theme.ACCENT : theme.TEXT,
                  border: `1px solid ${key === "optionBuyerClean" ? theme.ACCENT : theme.BORDER}`,
                  borderRadius: RADIUS.SM,
                  padding: SPACE.SM,
                  cursor: "pointer",
                  textAlign: "left",
                  fontFamily: FONT.UI,
                  transition: TRANSITION.FAST,
                }}
              >
                <div style={{ fontSize: TEXT_SIZE.BODY, fontWeight: TEXT_WEIGHT.BOLD, marginBottom: 2 }}>
                  {key === "optionBuyerClean" && "⭐ "}{p.label}
                </div>
                <div style={{ color: theme.TEXT_MUTED, fontSize: TEXT_SIZE.MICRO, lineHeight: 1.3 }}>
                  {p.desc}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Engine rows */}
        <div style={{ padding: SPACE.MD, maxHeight: "55vh", overflowY: "auto" }}>
          {engines.map((e) => {
            const meta = ENGINE_META[e.key] || { label: e.key, desc: "", recommend: "ON" };
            const recColor = meta.recommend === "ON" ? theme.GREEN : meta.recommend === "OFF" ? theme.RED : theme.AMBER;
            return (
              <div
                key={e.key}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: SPACE.MD,
                  padding: SPACE.MD,
                  borderBottom: `1px solid ${theme.BORDER}44`,
                  background: e.active ? "transparent" : theme.SURFACE_HI + "40",
                  opacity: e.active ? 1 : 0.6,
                  transition: TRANSITION.FAST,
                }}
              >
                <div style={{ fontSize: 22, marginTop: 2 }}>{meta.icon || "•"}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: SPACE.SM, marginBottom: 4 }}>
                    <span
                      style={{
                        color: theme.TEXT,
                        fontSize: TEXT_SIZE.BODY,
                        fontWeight: TEXT_WEIGHT.BOLD,
                      }}
                    >
                      {meta.label}
                    </span>
                    <span
                      style={{
                        color: recColor,
                        fontSize: 9,
                        fontWeight: TEXT_WEIGHT.BOLD,
                        letterSpacing: 1.5,
                        padding: "1px 6px",
                        border: `1px solid ${recColor}44`,
                        borderRadius: RADIUS.XS,
                        background: recColor + "15",
                      }}
                    >
                      RECOMMEND: {meta.recommend}
                    </span>
                    <span
                      style={{
                        color: theme.TEXT_DIM,
                        fontSize: 9,
                        fontFamily: FONT.MONO,
                        marginLeft: "auto",
                      }}
                    >
                      Weight: {e.maxWeight}
                    </span>
                  </div>
                  <div
                    style={{
                      color: theme.TEXT_MUTED,
                      fontSize: TEXT_SIZE.MICRO,
                      lineHeight: 1.5,
                      marginBottom: 4,
                    }}
                  >
                    {meta.desc}
                  </div>
                  <div
                    style={{
                      color: recColor,
                      fontSize: TEXT_SIZE.MICRO,
                      fontStyle: "italic",
                      lineHeight: 1.4,
                    }}
                  >
                    {meta.reason}
                  </div>
                </div>
                {/* Toggle switch */}
                <button
                  onClick={() => toggle(e.key)}
                  aria-label={`${e.active ? "Disable" : "Enable"} ${meta.label} for verdict`}
                  style={{
                    width: 48,
                    height: 24,
                    borderRadius: 12,
                    background: e.active ? theme.GREEN : theme.TEXT_DIM,
                    border: "none",
                    cursor: "pointer",
                    position: "relative",
                    transition: TRANSITION.FAST,
                    flexShrink: 0,
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      top: 2,
                      left: e.active ? 26 : 2,
                      width: 20,
                      height: 20,
                      borderRadius: "50%",
                      background: "#fff",
                      transition: TRANSITION.BASE,
                      boxShadow: "0 2px 4px rgba(0,0,0,0.3)",
                    }}
                  />
                </button>
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: SPACE.MD,
            borderTop: `1px solid ${theme.BORDER}`,
            background: theme.BG,
            fontSize: TEXT_SIZE.MICRO,
            color: theme.TEXT_DIM,
            lineHeight: 1.5,
          }}
        >
          <strong style={{ color: theme.TEXT }}>How this works:</strong> Toggled OFF engines still run + display data
          in their tabs. But they don't contribute to the trade verdict. This lets you experiment with different
          signal combinations without losing data.
          <br />
          <strong style={{ color: theme.ACCENT }}>Option Buyer tip:</strong> Start with "Option Buyer (clean)" profile —
          Trap + Price Action are OFF because they add noise for buyers. Monitor results 1 week, then adjust.
        </div>
      </div>
    </div>
  );
}
