import { useState, useEffect, useCallback } from "react";

const RECENT_KEY = "universe_recent_strikes";
const PINNED_KEY = "universe_pinned_strikes";
const MAX_RECENT = 10;
const MAX_PINNED = 12;

function read(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "[]");
  } catch {
    return [];
  }
}

function write(key, v) {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    // ignore quota errors
  }
}

function strikeKey(s) {
  return `${s.index}-${s.strike}-${s.type || "BOTH"}`;
}

export function useWatchlist() {
  const [recent, setRecent] = useState(() => read(RECENT_KEY));
  const [pinned, setPinned] = useState(() => read(PINNED_KEY));

  useEffect(() => write(RECENT_KEY, recent), [recent]);
  useEffect(() => write(PINNED_KEY, pinned), [pinned]);

  const addRecent = useCallback((strike) => {
    if (!strike || !strike.index || !strike.strike) return;
    setRecent((prev) => {
      const k = strikeKey(strike);
      const filtered = prev.filter((s) => strikeKey(s) !== k);
      const entry = { ...strike, visitedAt: Date.now() };
      return [entry, ...filtered].slice(0, MAX_RECENT);
    });
  }, []);

  const clearRecent = useCallback(() => setRecent([]), []);

  const isPinned = useCallback(
    (strike) => {
      const k = strikeKey(strike);
      return pinned.some((s) => strikeKey(s) === k);
    },
    [pinned]
  );

  const togglePin = useCallback(
    (strike) => {
      setPinned((prev) => {
        const k = strikeKey(strike);
        const exists = prev.some((s) => strikeKey(s) === k);
        if (exists) return prev.filter((s) => strikeKey(s) !== k);
        if (prev.length >= MAX_PINNED) return prev;
        return [...prev, { ...strike, pinnedAt: Date.now() }];
      });
    },
    []
  );

  const removePinned = useCallback((strike) => {
    setPinned((prev) => prev.filter((s) => strikeKey(s) !== strikeKey(strike)));
  }, []);

  return { recent, pinned, addRecent, clearRecent, isPinned, togglePin, removePinned };
}
