// Securely-local credential storage for Kite API key + secret.
// Uses localStorage (origin-scoped, per-browser). Never transmits to any server
// except the user's own backend during OAuth flow.

import { useState, useEffect, useCallback } from "react";

const KEY = "universe_kite_api_key";
const SECRET = "universe_kite_api_secret";
const SAVED_AT = "universe_kite_saved_at";
const REMEMBER = "universe_kite_remember";

function read(k) {
  try {
    return localStorage.getItem(k) || "";
  } catch {
    return "";
  }
}

function write(k, v) {
  try {
    if (v == null || v === "") localStorage.removeItem(k);
    else localStorage.setItem(k, v);
  } catch {
    // ignore quota errors
  }
}

function readBool(k, defaultVal) {
  try {
    const v = localStorage.getItem(k);
    if (v === null) return defaultVal;
    return v === "true";
  } catch {
    return defaultVal;
  }
}

export function useKiteCredentials() {
  const [apiKey, setApiKey] = useState(() => read(KEY));
  const [apiSecret, setApiSecret] = useState(() => read(SECRET));
  const [remember, setRemember] = useState(() => readBool(REMEMBER, true));
  const [savedAt, setSavedAt] = useState(() => {
    const v = read(SAVED_AT);
    return v ? parseInt(v, 10) : null;
  });

  const hasSaved = Boolean(read(KEY) && read(SECRET));

  const save = useCallback((key, secret) => {
    if (!remember) return;
    write(KEY, key);
    write(SECRET, secret);
    const t = Date.now();
    write(SAVED_AT, String(t));
    write(REMEMBER, "true");
    setSavedAt(t);
  }, [remember]);

  const clear = useCallback((which = "all") => {
    if (which === "key" || which === "all") {
      write(KEY, "");
      setApiKey("");
    }
    if (which === "secret" || which === "all") {
      write(SECRET, "");
      setApiSecret("");
    }
    if (which === "all") {
      write(SAVED_AT, "");
      setSavedAt(null);
    }
  }, []);

  const toggleRemember = useCallback((enabled) => {
    setRemember(enabled);
    write(REMEMBER, String(enabled));
    // If user unchecks, clear saved credentials immediately for safety
    if (!enabled) {
      clear("all");
    }
  }, [clear]);

  // Friendly relative time
  const savedAgo = (() => {
    if (!savedAt) return null;
    const diff = Date.now() - savedAt;
    const min = Math.floor(diff / 60000);
    const hr = Math.floor(diff / 3600000);
    const day = Math.floor(diff / 86400000);
    if (day > 0) return `${day}d ago`;
    if (hr > 0) return `${hr}h ago`;
    if (min > 0) return `${min}m ago`;
    return "just now";
  })();

  // Mask helpers for preview
  const maskKey = (k) => {
    if (!k || k.length < 6) return "•".repeat(6);
    return `${k.slice(0, 3)}${"•".repeat(Math.max(3, k.length - 6))}${k.slice(-3)}`;
  };
  const maskSecret = (s) => {
    if (!s) return "";
    return "•".repeat(Math.min(s.length, 16));
  };

  return {
    apiKey, setApiKey,
    apiSecret, setApiSecret,
    remember, toggleRemember,
    savedAt, savedAgo,
    hasSaved,
    save, clear,
    maskKey, maskSecret,
  };
}
