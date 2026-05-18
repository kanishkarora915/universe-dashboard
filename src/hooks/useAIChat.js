// useAIChat — manages chat state + API calls for floating AI assistant
import { useState, useEffect, useCallback, useRef } from "react";

const HISTORY_KEY = "universe_ai_chat_history";
const MAX_HISTORY = 50;

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveHistory(history) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-MAX_HISTORY)));
  } catch {
    // quota exceeded — drop oldest half
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-Math.floor(MAX_HISTORY / 2))));
    } catch {}
  }
}

export function useAIChat({ contextProvider } = {}) {
  const [messages, setMessages] = useState(() => loadHistory());
  const [sending, setSending] = useState(false);
  const contextRef = useRef(contextProvider);
  contextRef.current = contextProvider;

  useEffect(() => {
    saveHistory(messages);
  }, [messages]);

  const send = useCallback(async (userText) => {
    if (!userText || !userText.trim()) return;
    const now = Date.now();
    const userMsg = { role: "user", content: userText, ts: now };
    setMessages((m) => [...m, userMsg]);
    setSending(true);

    try {
      const context = contextRef.current ? contextRef.current() : {};
      const history = messages.slice(-6);
      const res = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userText, context, history }),
      });
      const data = await res.json();
      const replyText = data.reply || data.error || "No response";
      const reply = {
        role: "assistant",
        content: replyText,
        ts: Date.now(),
        error: !!data.error,
        tokensUsed: data.tokensUsed,
      };
      setMessages((m) => [...m, reply]);
    } catch (e) {
      setMessages((m) => [...m, {
        role: "assistant",
        content: `Connection error: ${e.message}`,
        ts: Date.now(),
        error: true,
      }]);
    } finally {
      setSending(false);
    }
  }, [messages]);

  const sendQuickAction = useCallback(async (action, payload = {}) => {
    setSending(true);
    const label = {
      "morning-brief": "Morning Brief requested",
      "psychology": "Psychology check",
      "emergency": "Emergency help requested",
      "trade-decision": "Trade decision requested",
      "pattern-explain": "Pattern explanation requested",
      "risk-calc": "Risk calculation",
      "scenario": "Scenario analysis",
    }[action] || action;
    setMessages((m) => [...m, { role: "user", content: `[${label}]`, ts: Date.now(), isQuickAction: true }]);

    try {
      const res = await fetch(`/api/ai/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      const content = data.reply || data.brief || data.decision || data.advice || data.analysis || data.explanation ||
        (data.advice === undefined && !data.error ? JSON.stringify(data, null, 2) : data.error || "No response");
      setMessages((m) => [...m, {
        role: "assistant",
        content,
        ts: Date.now(),
        error: !!data.error,
        data: data,
      }]);
    } catch (e) {
      setMessages((m) => [...m, {
        role: "assistant",
        content: `Error: ${e.message}`,
        ts: Date.now(),
        error: true,
      }]);
    } finally {
      setSending(false);
    }
  }, []);

  const clear = useCallback(() => {
    setMessages([]);
    saveHistory([]);
  }, []);

  return { messages, sending, send, sendQuickAction, clear };
}
