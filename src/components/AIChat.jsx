/**
 * AIChat — Floating chat widget powered by Claude Haiku.
 *
 * User asks anything: "what is data saying", "what to buy", "is this trap"
 * AI fetches ALL dashboard data + responds with deep analysis.
 */

import React, { useState, useEffect, useRef } from "react";

const PURPLE = "#a855f7";
const GREEN = "#26a69a";
const RED = "#ef5350";
const BLUE = "#2962ff";
const FG = "#d4d4d8";
const FG_DIM = "#71717a";
const BG = "#0a0a0a";
const CARD = "#0f0f10";
const BORDER = "#1f1f24";

async function postJSON(url, body) {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await r.json();
  } catch (e) {
    return { error: e.message };
  }
}

async function getJSON(url) {
  try {
    const r = await fetch(url);
    return await r.json();
  } catch (e) {
    return { error: e.message };
  }
}

const QUICK_QUESTIONS = [
  "what is data saying right now?",
  "what should I buy?",
  "is this a trap?",
  "where are sellers trapping retail?",
  "tomorrow ka kya scene hai?",
  "explain current OI structure",
];

export default function AIChat() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const sessionIdRef = useRef(`session-${Date.now()}`);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (open && messages.length === 0) {
      // Load chat history on first open
      getJSON(`/api/ai/chat-history?session_id=${sessionIdRef.current}&limit=10`).then(d => {
        if (d?.messages) setMessages(d.messages);
      });
    }
  }, [open]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const send = async (q) => {
    const question = (q || input).trim();
    if (!question || loading) return;

    setInput("");
    setMessages(prev => [...prev, { role: "user", content: question, ts: new Date().toISOString() }]);
    setLoading(true);

    const result = await postJSON("/api/ai/ask", {
      question,
      session_id: sessionIdRef.current,
    });

    if (result.error) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: `Error: ${result.error}\n\nMake sure CLAUDE_API_KEY is set in environment.`,
        ts: new Date().toISOString(),
      }]);
    } else {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: result.answer,
        ts: result.ts,
        tokens: { input: result.input_tokens, output: result.output_tokens },
      }]);
    }
    setLoading(false);
  };

  return (
    <>
      {/* Floating button */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            width: 56,
            height: 56,
            borderRadius: "50%",
            background: PURPLE,
            border: "none",
            color: "#fff",
            fontSize: 24,
            cursor: "pointer",
            boxShadow: "0 4px 20px rgba(168, 85, 247, 0.4)",
            zIndex: 9999,
          }}
        >
          🤖
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div style={{
          position: "fixed",
          bottom: 24,
          right: 24,
          width: 420,
          height: 600,
          background: CARD,
          border: `1px solid ${BORDER}`,
          borderRadius: 12,
          display: "flex",
          flexDirection: "column",
          zIndex: 9999,
          boxShadow: "0 10px 40px rgba(0,0,0,0.5)",
          fontFamily: "-apple-system, 'Segoe UI', system-ui, sans-serif",
        }}>
          {/* Header */}
          <div style={{
            padding: "12px 16px",
            borderBottom: `1px solid ${BORDER}`,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, color: PURPLE }}>
                🤖 AI BRAIN — Claude Haiku
              </div>
              <div style={{ fontSize: 10, color: FG_DIM, marginTop: 2 }}>
                Reads full dashboard data live
              </div>
            </div>
            <button onClick={() => setOpen(false)} style={{
              background: "transparent",
              border: "none",
              color: FG_DIM,
              fontSize: 18,
              cursor: "pointer",
            }}>×</button>
          </div>

          {/* Messages */}
          <div ref={scrollRef} style={{
            flex: 1,
            overflowY: "auto",
            padding: 12,
            background: BG,
          }}>
            {messages.length === 0 && (
              <div style={{ color: FG_DIM, fontSize: 12, textAlign: "center", padding: 20 }}>
                Ask anything about the live market data...
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} style={{
                marginBottom: 12,
                display: "flex",
                flexDirection: m.role === "user" ? "row-reverse" : "row",
              }}>
                <div style={{
                  maxWidth: "85%",
                  padding: "8px 12px",
                  borderRadius: 8,
                  background: m.role === "user" ? BLUE : CARD,
                  border: m.role === "user" ? "none" : `1px solid ${BORDER}`,
                  color: m.role === "user" ? "#fff" : FG,
                  fontSize: 12,
                  lineHeight: 1.5,
                  whiteSpace: "pre-wrap",
                  wordWrap: "break-word",
                }}>
                  {m.content}
                  {m.tokens && (
                    <div style={{ fontSize: 9, color: FG_DIM, marginTop: 6 }}>
                      {m.tokens.input + m.tokens.output} tokens · ~₹{((m.tokens.input * 0.000001) + (m.tokens.output * 0.000005)).toFixed(4)}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <div style={{ color: FG_DIM, fontSize: 11, padding: 8, textAlign: "center" }}>
                AI thinking... fetching dashboard data + analyzing
              </div>
            )}
          </div>

          {/* Quick questions */}
          {messages.length === 0 && (
            <div style={{ padding: "8px 12px", borderTop: `1px solid ${BORDER}` }}>
              <div style={{ fontSize: 9, color: FG_DIM, fontWeight: 700, marginBottom: 6 }}>
                QUICK QUESTIONS
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {QUICK_QUESTIONS.slice(0, 4).map((q, i) => (
                  <button key={i} onClick={() => send(q)} style={{
                    background: "transparent",
                    color: FG_DIM,
                    border: `1px solid ${BORDER}`,
                    padding: "4px 8px",
                    borderRadius: 4,
                    fontSize: 10,
                    cursor: "pointer",
                  }}>{q}</button>
                ))}
              </div>
            </div>
          )}

          {/* Input */}
          <form onSubmit={(e) => { e.preventDefault(); send(); }} style={{
            padding: 10,
            borderTop: `1px solid ${BORDER}`,
            display: "flex",
            gap: 6,
          }}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything about live data..."
              disabled={loading}
              style={{
                flex: 1,
                background: BG,
                border: `1px solid ${BORDER}`,
                color: FG,
                padding: "8px 12px",
                borderRadius: 6,
                fontSize: 12,
                outline: "none",
              }}
            />
            <button type="submit" disabled={loading || !input.trim()} style={{
              background: loading || !input.trim() ? "#222" : PURPLE,
              color: "#fff",
              border: "none",
              padding: "8px 16px",
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 700,
              cursor: loading || !input.trim() ? "not-allowed" : "pointer",
            }}>
              {loading ? "..." : "Ask"}
            </button>
          </form>
        </div>
      )}
    </>
  );
}
