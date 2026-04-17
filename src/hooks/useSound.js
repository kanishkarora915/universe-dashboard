// Web Audio API — generated sounds, no file downloads needed
// Works offline, respects browser autoplay policy

import { useRef, useCallback, useEffect } from "react";

const VOLUME_KEY = "universe_sound_volume";
const ENABLED_KEY = "universe_sound_enabled";
const PER_TYPE_KEY = "universe_sound_per_type";

function loadSettings() {
  try {
    const vol = parseFloat(localStorage.getItem(VOLUME_KEY));
    const enabled = localStorage.getItem(ENABLED_KEY);
    const perType = JSON.parse(localStorage.getItem(PER_TYPE_KEY) || "{}");
    return {
      volume: isNaN(vol) ? 0.6 : vol,
      enabled: enabled === null ? true : enabled === "true",
      perType,
    };
  } catch {
    return { volume: 0.6, enabled: true, perType: {} };
  }
}

function saveSettings(s) {
  localStorage.setItem(VOLUME_KEY, String(s.volume));
  localStorage.setItem(ENABLED_KEY, String(s.enabled));
  localStorage.setItem(PER_TYPE_KEY, JSON.stringify(s.perType));
}

// Sound generators (frequency in Hz, duration in seconds)
const PATTERNS = {
  TRADE_ENTRY: [
    { f: 523.25, d: 0.10, t: "sine" },   // C5
    { f: 659.25, d: 0.10, t: "sine", delay: 0.08 },  // E5
    { f: 783.99, d: 0.18, t: "sine", delay: 0.16 },  // G5
  ],
  TRADE_EXIT_W: [
    { f: 880, d: 0.08, t: "sine" },
    { f: 1108.73, d: 0.20, t: "sine", delay: 0.06 },
  ],
  TRADE_EXIT_L: [
    { f: 440, d: 0.12, t: "triangle" },
    { f: 329.63, d: 0.30, t: "triangle", delay: 0.12 },
  ],
  SIGNAL_NEW: [{ f: 880, d: 0.10, t: "sine" }],
  WARNING: [
    { f: 660, d: 0.08, t: "square" },
    { f: 660, d: 0.08, t: "square", delay: 0.12 },
  ],
  CRITICAL: [
    { f: 880, d: 0.08, t: "square" },
    { f: 660, d: 0.08, t: "square", delay: 0.10 },
    { f: 440, d: 0.12, t: "square", delay: 0.20 },
  ],
  GENTLE: [{ f: 1200, d: 0.04, t: "sine" }],
};

// Map alert_type -> sound pattern
const ALERT_SOUND = {
  TRADE_ENTRY: "TRADE_ENTRY",
  TRADE_EXIT_SL: "TRADE_EXIT_L",
  TRADE_EXIT_T1: "TRADE_EXIT_W",
  TRADE_EXIT_T2: "TRADE_EXIT_W",
  TRADE_EXIT_EOD: "GENTLE",
  MANUAL_EXIT_REQ: "WARNING",
  STALE_TICKER: "CRITICAL",
  KITE_DISCONNECT: "CRITICAL",
  SL_APPROACHING: "WARNING",
  PROFIT_PROTECT: "WARNING",
  NEW_SIGNAL_HIGH: "SIGNAL_NEW",
  GAP_PREDICTION_HIGH: "GENTLE",
  UNUSUAL_OI_SPIKE: "WARNING",
  TRAP_FINGERPRINT: "WARNING",
  SIGNAL_CHANGE: "GENTLE",
  PCR_EXTREME: "GENTLE",
  VIX_SPIKE: "WARNING",
  EXPIRY_WARNING: "GENTLE",
  AI_INSIGHT: "GENTLE",
  AUTOPSY_INSIGHT: null,   // silent
  WEEKLY_TRAINING_DONE: null,
  REPORT_READY: null,
};

export function useSound() {
  const ctxRef = useRef(null);
  const settingsRef = useRef(loadSettings());

  // Unlock AudioContext on first user interaction
  useEffect(() => {
    const unlock = () => {
      if (!ctxRef.current) {
        try {
          ctxRef.current = new (window.AudioContext || window.webkitAudioContext)();
        } catch {
          // ignore
        }
      }
      if (ctxRef.current && ctxRef.current.state === "suspended") {
        ctxRef.current.resume();
      }
    };
    const events = ["click", "keydown", "touchstart"];
    events.forEach((e) => document.addEventListener(e, unlock, { once: true }));
    return () => events.forEach((e) => document.removeEventListener(e, unlock));
  }, []);

  const playPattern = useCallback((patternName) => {
    const s = settingsRef.current;
    if (!s.enabled || !patternName) return;
    const ctx = ctxRef.current;
    if (!ctx) return;

    const pattern = PATTERNS[patternName];
    if (!pattern) return;

    const now = ctx.currentTime;

    pattern.forEach((note) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = note.t || "sine";
      osc.frequency.value = note.f;

      const start = now + (note.delay || 0);
      const dur = note.d || 0.1;

      // Envelope: quick attack, short release
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(s.volume * 0.3, start + 0.005);
      gain.gain.exponentialRampToValueAtTime(0.001, start + dur);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(start);
      osc.stop(start + dur + 0.05);
    });
  }, []);

  const playAlert = useCallback(
    (alertType) => {
      const s = settingsRef.current;
      if (!s.enabled) return;
      if (s.perType[alertType] === false) return;
      const pattern = ALERT_SOUND[alertType];
      if (pattern) playPattern(pattern);
    },
    [playPattern]
  );

  const setVolume = useCallback((v) => {
    settingsRef.current = { ...settingsRef.current, volume: Math.max(0, Math.min(1, v)) };
    saveSettings(settingsRef.current);
  }, []);

  const setEnabled = useCallback((enabled) => {
    settingsRef.current = { ...settingsRef.current, enabled };
    saveSettings(settingsRef.current);
  }, []);

  const setPerType = useCallback((alertType, enabled) => {
    settingsRef.current = {
      ...settingsRef.current,
      perType: { ...settingsRef.current.perType, [alertType]: enabled },
    };
    saveSettings(settingsRef.current);
  }, []);

  const getSettings = useCallback(() => settingsRef.current, []);

  return { playPattern, playAlert, setVolume, setEnabled, setPerType, getSettings, PATTERNS };
}
