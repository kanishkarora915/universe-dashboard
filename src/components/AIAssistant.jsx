import { useState, useRef, useEffect, useCallback } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";
import { useAIChat } from "../hooks/useAIChat";

/**
 * AI ASSISTANT — Floating button + slide-in panel.
 *
 * Always visible. Click opens full-height right drawer with:
 * - Chat history (persisted in localStorage)
 * - 7 quick action presets
 * - Custom text input + voice input
 * - Context-aware (auto-includes active tab + pinned strikes)
 * - Copy response + clear history
 */

const QUICK_ACTIONS = [
  { action: "morning-brief", label: "☀ Morning Brief", color: "AMBER", desc: "Today's market setup" },
  { action: "psychology", label: "🧘 Psychology Check", color: "PURPLE", desc: "Am I overtrading?" },
  { action: "emergency", label: "🛑 Emergency", color: "RED", desc: "Trade going wrong" },
  { action: "trade-decision", label: "🎯 Should I Buy?", color: "GREEN", desc: "Decide a pinned strike" },
  { action: "risk-calc", label: "⚖ Risk Calc", color: "CYAN", desc: "Position sizing" },
  { action: "scenario", label: "📈 Scenario", color: "ACCENT", desc: "What if spot moves?" },
  { action: "pattern-explain", label: "🎓 Explain", color: "PURPLE", desc: "Teach a pattern" },
];

export default function AIAssistant({ activeTab, pinnedStrikes = [], openTrade }) {
  const { theme } = useTheme();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [listening, setListening] = useState(false);
  const inputRef = useRef(null);
  const scrollRef = useRef(null);

  const contextProvider = useCallback(() => ({
    activeTab,
    pinnedStrikes: pinnedStrikes.map(s => `${s.index} ${s.strike}${s.type || ""}`),
    openTrade: openTrade ? {
      idx: openTrade.idx,
      action: openTrade.action,
      strike: openTrade.strike,
      pnl: openTrade.pnl_rupees,
      status: openTrade.status,
    } : null,
    time: new Date().toLocaleTimeString("en-IN"),
  }), [activeTab, pinnedStrikes, openTrade]);

  const { messages, sending, send, sendQuickAction, clear } = useAIChat({ contextProvider });

  // Auto-scroll on new message
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 200);
  }, [open]);

  const handleSend = () => {
    if (!input.trim()) return;
    send(input);
    setInput("");
  };

  // Voice input via Web Speech API
  const startVoice = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      alert("Voice input not supported in this browser. Try Chrome.");
      return;
    }
    const rec = new SR();
    rec.lang = "en-IN";
    rec.continuous = false;
    rec.interimResults = false;
    setListening(true);
    rec.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      setInput(transcript);
      setListening(false);
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => setListening(false);
    rec.start();
  };

  const handleQuickAction = (action) => {
    // Some actions need extra payload
    let payload = {};
    if (action === "trade-decision" && pinnedStrikes[0]) {
      payload = { strike: pinnedStrikes[0] };
    } else if (action === "emergency" && openTrade) {
      payload = { trade: openTrade, concern: "My open trade is losing" };
    } else if (action === "risk-calc") {
      // Simple prompt for risk calc params
      const entry = parseFloat(prompt("Entry price (₹)?") || "0");
      const sl = parseFloat(prompt("Stop-loss price (₹)?") || "0");
      const capital = parseFloat(prompt("Your capital (₹)?", "500000") || "500000");
      if (entry && sl) payload = { entry, sl, capital, riskPct: 2, lotSize: 75 };
    } else if (action === "scenario" && pinnedStrikes[0]) {
      const delta = parseFloat(prompt("Expected spot move (%)? e.g. -1 for 1% drop", "-1") || "-1");
      payload = {
        spot: 24350, spotDeltaPct: delta,
        strikeLTP: 150, delta: 0.5, gamma: 0.018, theta: -3,
        hoursHeld: 1, lotSize: 75, lots: 1,
      };
    } else if (action === "pattern-explain") {
      const pattern = prompt("Describe the pattern you want explained:");
      if (pattern) payload = { pattern };
      else return;
    }
    sendQuickAction(action, payload);
  };

  return (
    <>
      <style>{`
        @keyframes ai-pulse {
          0%, 100% { transform: scale(1); box-shadow: 0 0 20px rgba(191,90,242,0.4); }
          50% { transform: scale(1.05); box-shadow: 0 0 32px rgba(191,90,242,0.7); }
        }
        @keyframes mic-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.3); opacity: 0.6; }
        }
        @keyframes slide-in-right {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>

      {/* Floating button — always visible */}
      <button
        onClick={() => setOpen(true)}
        aria-label="Open AI Assistant"
        title="AI Assistant (click or press /)"
        style={{
          position: "fixed",
          bottom: 20,
          right: 20,
          width: 56,
          height: 56,
          borderRadius: "50%",
          background: `linear-gradient(135deg, ${theme.PURPLE}, ${theme.ACCENT})`,
          color: "#fff",
          border: "none",
          cursor: "pointer",
          fontSize: 24,
          fontWeight: TEXT_WEIGHT.BOLD,
          boxShadow: theme.SHADOW_HI,
          zIndex: Z.STICKY + 5,
          animation: !open ? "ai-pulse 3s ease-in-out infinite" : "none",
          display: open ? "none" : "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        🧠
      </button>

      {/* Panel — slide in from right */}
      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            style={{
              position: "fixed", inset: 0,
              background: theme.OVERLAY,
              zIndex: Z.MODAL,
            }}
          />
          <aside
            style={{
              position: "fixed",
              top: 0, right: 0,
              width: "min(460px, 96vw)",
              height: "100vh",
              background: theme.SURFACE,
              borderLeft: `1px solid ${theme.BORDER_HI}`,
              boxShadow: theme.SHADOW_HI,
              zIndex: Z.MODAL + 1,
              display: "flex",
              flexDirection: "column",
              animation: "slide-in-right 260ms cubic-bezier(0.22,1,0.36,1)",
            }}
          >
            {/* Header */}
            <div
              style={{
                padding: SPACE.MD,
                borderBottom: `1px solid ${theme.BORDER}`,
                background: `linear-gradient(135deg, ${theme.PURPLE}15, ${theme.ACCENT}10)`,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <div style={{ color: theme.PURPLE, fontSize: 9, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 2, textTransform: "uppercase" }}>
                    🧠 Universe AI
                  </div>
                  <div style={{ color: theme.TEXT, fontSize: 18, fontWeight: TEXT_WEIGHT.BOLD, marginTop: 2 }}>
                    Trading Assistant
                  </div>
                </div>
                <div style={{ display: "flex", gap: SPACE.XS }}>
                  {messages.length > 0 && (
                    <button
                      onClick={clear}
                      title="Clear chat history"
                      style={{
                        background: "transparent",
                        border: `1px solid ${theme.BORDER}`,
                        color: theme.TEXT_DIM,
                        borderRadius: RADIUS.SM,
                        padding: "3px 8px",
                        fontSize: 10,
                        cursor: "pointer",
                      }}
                    >
                      Clear
                    </button>
                  )}
                  <button
                    onClick={() => setOpen(false)}
                    style={{
                      background: "transparent",
                      border: `1px solid ${theme.BORDER}`,
                      color: theme.TEXT_MUTED,
                      borderRadius: RADIUS.SM,
                      padding: "3px 8px",
                      cursor: "pointer",
                      fontSize: 14,
                    }}
                  >
                    ×
                  </button>
                </div>
              </div>
              {/* Context indicator */}
              <div
                style={{
                  marginTop: SPACE.SM,
                  fontSize: 9,
                  color: theme.TEXT_DIM,
                  fontFamily: FONT.MONO,
                  letterSpacing: 0.5,
                }}
              >
                Context: {activeTab || "dashboard"}
                {pinnedStrikes.length > 0 && ` · ${pinnedStrikes.length} strike${pinnedStrikes.length > 1 ? 's' : ''} pinned`}
                {openTrade && ` · open trade ${openTrade.idx} ${openTrade.action} ${openTrade.strike}`}
              </div>
            </div>

            {/* Chat area */}
            <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: SPACE.MD }}>
              {messages.length === 0 && (
                <div style={{ textAlign: "center", color: theme.TEXT_DIM, padding: SPACE.XL }}>
                  <div style={{ fontSize: 40, marginBottom: SPACE.SM }}>🧠</div>
                  <div style={{ fontSize: TEXT_SIZE.BODY, marginBottom: SPACE.SM, color: theme.TEXT }}>
                    Hi! Main Universe AI hoon.
                  </div>
                  <div style={{ fontSize: TEXT_SIZE.MICRO, lineHeight: 1.5 }}>
                    Quick actions try karo OR mujhe seedha poocho. Voice bhi available hai.
                  </div>
                </div>
              )}

              {messages.map((m, i) => (
                <div
                  key={i}
                  style={{
                    marginBottom: SPACE.SM,
                    padding: SPACE.SM,
                    background: m.role === "user" ? theme.ACCENT_DIM : theme.SURFACE_HI,
                    borderLeft: m.role === "user" ? `2px solid ${theme.ACCENT}` : `2px solid ${theme.PURPLE}`,
                    borderRadius: RADIUS.SM,
                    color: m.error ? theme.RED : theme.TEXT,
                    fontSize: TEXT_SIZE.BODY,
                    fontFamily: FONT.UI,
                    lineHeight: 1.5,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  <div
                    style={{
                      fontSize: 8,
                      color: m.role === "user" ? theme.ACCENT : theme.PURPLE,
                      fontWeight: TEXT_WEIGHT.BOLD,
                      letterSpacing: 1,
                      textTransform: "uppercase",
                      marginBottom: 4,
                    }}
                  >
                    {m.role === "user" ? "You" : "Universe AI"}
                    {m.isQuickAction && " · Quick Action"}
                  </div>
                  {m.content}
                  {m.role === "assistant" && !m.error && (
                    <div style={{ marginTop: SPACE.XS, display: "flex", gap: 8 }}>
                      <button
                        onClick={() => navigator.clipboard?.writeText(m.content)}
                        style={{
                          background: "transparent",
                          border: `1px solid ${theme.BORDER}`,
                          color: theme.TEXT_DIM,
                          borderRadius: 3,
                          padding: "1px 6px",
                          cursor: "pointer",
                          fontSize: 9,
                        }}
                      >
                        📋 Copy
                      </button>
                    </div>
                  )}
                </div>
              ))}

              {sending && (
                <div
                  style={{
                    padding: SPACE.SM,
                    color: theme.PURPLE,
                    fontSize: TEXT_SIZE.MICRO,
                    fontStyle: "italic",
                  }}
                >
                  🧠 Thinking...
                </div>
              )}
            </div>

            {/* Quick actions */}
            <div
              style={{
                padding: `${SPACE.SM}px ${SPACE.MD}px`,
                borderTop: `1px solid ${theme.BORDER}`,
                background: theme.SURFACE_HI,
              }}
            >
              <div style={{ fontSize: 9, color: theme.TEXT_DIM, fontWeight: TEXT_WEIGHT.BOLD, letterSpacing: 1, marginBottom: 4 }}>
                QUICK ACTIONS
              </div>
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {QUICK_ACTIONS.map((qa) => (
                  <button
                    key={qa.action}
                    onClick={() => handleQuickAction(qa.action)}
                    disabled={sending}
                    title={qa.desc}
                    style={{
                      background: theme[qa.color] + "22",
                      color: theme[qa.color],
                      border: `1px solid ${theme[qa.color]}44`,
                      borderRadius: RADIUS.SM,
                      padding: "4px 8px",
                      cursor: sending ? "not-allowed" : "pointer",
                      fontSize: 10,
                      fontWeight: TEXT_WEIGHT.BOLD,
                      fontFamily: FONT.UI,
                      whiteSpace: "nowrap",
                      opacity: sending ? 0.5 : 1,
                    }}
                  >
                    {qa.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Input */}
            <div
              style={{
                padding: SPACE.MD,
                borderTop: `1px solid ${theme.BORDER}`,
                display: "flex",
                gap: SPACE.SM,
                alignItems: "center",
                background: theme.SURFACE,
              }}
            >
              <button
                onClick={startVoice}
                disabled={listening}
                title={listening ? "Listening..." : "Voice input"}
                style={{
                  background: listening ? theme.RED : theme.SURFACE_HI,
                  color: listening ? "#fff" : theme.TEXT_MUTED,
                  border: `1px solid ${listening ? theme.RED : theme.BORDER}`,
                  borderRadius: "50%",
                  width: 36,
                  height: 36,
                  cursor: listening ? "wait" : "pointer",
                  fontSize: 14,
                  flexShrink: 0,
                  animation: listening ? "mic-pulse 1.2s ease-in-out infinite" : "none",
                }}
              >
                🎤
              </button>
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !sending) handleSend();
                }}
                placeholder="Ask anything..."
                disabled={sending}
                style={{
                  flex: 1,
                  background: theme.SURFACE_HI,
                  border: `1px solid ${theme.BORDER}`,
                  borderRadius: RADIUS.SM,
                  padding: "8px 12px",
                  color: theme.TEXT,
                  fontSize: TEXT_SIZE.BODY,
                  fontFamily: FONT.UI,
                  outline: "none",
                }}
              />
              <button
                onClick={handleSend}
                disabled={sending || !input.trim()}
                style={{
                  background: theme.PURPLE,
                  color: "#fff",
                  border: "none",
                  borderRadius: RADIUS.SM,
                  padding: "8px 14px",
                  cursor: sending || !input.trim() ? "not-allowed" : "pointer",
                  fontSize: TEXT_SIZE.MICRO,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  letterSpacing: 1,
                  textTransform: "uppercase",
                  opacity: sending || !input.trim() ? 0.5 : 1,
                  flexShrink: 0,
                }}
              >
                Send
              </button>
            </div>
          </aside>
        </>
      )}
    </>
  );
}
