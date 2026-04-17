import { createContext, useContext, useState, useEffect, useCallback, useMemo } from "react";
import { DARK, LIGHT, applyTheme } from "./theme";

const ThemeContext = createContext(null);

const MODES = ["dark", "light", "auto-time", "auto-system"];
const STORAGE_KEY = "universe_theme_mode";

function getAutoTimeTheme() {
  // Dark during market hours (9:00 AM - 4:00 PM IST), light otherwise
  const now = new Date();
  const hours = now.getHours();
  const isMarket = hours >= 9 && hours < 16;
  return isMarket ? "dark" : "light";
}

function getSystemTheme() {
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

function resolveMode(mode) {
  if (mode === "auto-time") return getAutoTimeTheme();
  if (mode === "auto-system") return getSystemTheme();
  return mode;
}

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    return MODES.includes(stored) ? stored : "dark";
  });

  const resolved = useMemo(() => resolveMode(mode), [mode]);
  const theme = resolved === "dark" ? DARK : LIGHT;

  useEffect(() => {
    applyTheme(theme);
    document.body.style.background = theme.BG;
    document.body.style.color = theme.TEXT;
    localStorage.setItem(STORAGE_KEY, mode);
  }, [theme, mode]);

  // Listen for system theme changes if in auto-system mode
  useEffect(() => {
    if (mode !== "auto-system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => applyTheme(getSystemTheme() === "dark" ? DARK : LIGHT);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [mode]);

  // Check time-based theme every minute if in auto-time
  useEffect(() => {
    if (mode !== "auto-time") return;
    const interval = setInterval(() => {
      applyTheme(getAutoTimeTheme() === "dark" ? DARK : LIGHT);
    }, 60000);
    return () => clearInterval(interval);
  }, [mode]);

  const toggle = useCallback(() => {
    setMode((m) => (resolveMode(m) === "dark" ? "light" : "dark"));
  }, []);

  const value = useMemo(
    () => ({ mode, setMode, theme, resolved, toggle, isDark: resolved === "dark" }),
    [mode, theme, resolved, toggle]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be inside ThemeProvider");
  return ctx;
}
