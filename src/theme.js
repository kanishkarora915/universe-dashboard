// Universe Pro — Design Tokens
// Dark (default) + Light themes

export const DARK = {
  name: "dark",
  BG: "#0A0A0F",
  SURFACE: "#111118",
  SURFACE_HI: "#18181F",
  SURFACE_ACTIVE: "#1C1C25",
  BORDER: "#1E1E2E",
  BORDER_HI: "#2A2A3A",
  TEXT: "#FFFFFF",
  TEXT_MUTED: "#888888",
  TEXT_DIM: "#555555",
  TEXT_PLACEHOLDER: "#444444",

  ACCENT: "#0A84FF",
  ACCENT_DIM: "#0A84FF22",
  GREEN: "#30D158",
  GREEN_DIM: "#30D15822",
  RED: "#FF453A",
  RED_DIM: "#FF453A22",
  AMBER: "#FF9F0A",
  AMBER_DIM: "#FF9F0A22",
  YELLOW: "#FFD60A",
  PURPLE: "#BF5AF2",
  PURPLE_DIM: "#BF5AF222",
  CYAN: "#64D2FF",
  CYAN_DIM: "#64D2FF22",

  SHADOW: "0 4px 16px rgba(0,0,0,0.4)",
  SHADOW_HI: "0 8px 32px rgba(0,0,0,0.6)",
  GLOW_ACCENT: "0 0 20px rgba(10,132,255,0.15)",

  FLASH_GREEN: "rgba(48,209,88,0.18)",
  FLASH_RED: "rgba(255,69,58,0.18)",

  OVERLAY: "rgba(0,0,0,0.75)",
  SCRIM: "rgba(10,10,15,0.85)",
};

export const LIGHT = {
  name: "light",
  BG: "#FAFAFA",
  SURFACE: "#FFFFFF",
  SURFACE_HI: "#F4F4F6",
  SURFACE_ACTIVE: "#EEEEF0",
  BORDER: "#E5E5EA",
  BORDER_HI: "#C7C7CC",
  TEXT: "#1C1C1E",
  TEXT_MUTED: "#6C6C70",
  TEXT_DIM: "#AEAEB2",
  TEXT_PLACEHOLDER: "#C7C7CC",

  ACCENT: "#007AFF",
  ACCENT_DIM: "#007AFF14",
  GREEN: "#2CA048",
  GREEN_DIM: "#2CA04814",
  RED: "#D93025",
  RED_DIM: "#D9302514",
  AMBER: "#F29100",
  AMBER_DIM: "#F2910014",
  YELLOW: "#E8B800",
  PURPLE: "#9B51E0",
  PURPLE_DIM: "#9B51E014",
  CYAN: "#0098CE",
  CYAN_DIM: "#0098CE14",

  SHADOW: "0 2px 8px rgba(0,0,0,0.08)",
  SHADOW_HI: "0 4px 16px rgba(0,0,0,0.12)",
  GLOW_ACCENT: "0 0 16px rgba(0,122,255,0.10)",

  FLASH_GREEN: "rgba(44,160,72,0.12)",
  FLASH_RED: "rgba(217,48,37,0.12)",

  OVERLAY: "rgba(0,0,0,0.35)",
  SCRIM: "rgba(250,250,250,0.85)",
};

// Typography scale
export const FONT = {
  MONO: "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace",
  UI: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
};

export const TEXT_SIZE = {
  VERDICT: 28,
  H1: 18,
  H2: 14,
  BODY: 12,
  MICRO: 10,
  DATA: 16,
  DATA_LG: 22,
};

export const TEXT_WEIGHT = {
  BLACK: 900,
  BOLD: 700,
  MED: 500,
  REG: 400,
};

// Spacing scale (4px base)
export const SPACE = {
  XS: 4,
  SM: 8,
  MD: 12,
  LG: 16,
  XL: 20,
  XXL: 24,
  XXXL: 32,
};

// Radius (sharp — max 8px)
export const RADIUS = {
  XS: 2,
  SM: 4,
  MD: 6,
  LG: 8,
  PILL: 999,
};

// Transitions (fast, decisive)
export const TRANSITION = {
  FAST: "100ms cubic-bezier(0.4, 0, 0.2, 1)",
  BASE: "200ms cubic-bezier(0.4, 0, 0.2, 1)",
  SLOW: "300ms cubic-bezier(0.22, 1, 0.36, 1)",
};

// Z-index layers
export const Z = {
  BASE: 1,
  STICKY: 10,
  DROPDOWN: 100,
  MODAL: 1000,
  TOAST: 2000,
  TOOLTIP: 3000,
};

// Breakpoints
export const BP = {
  MOBILE: 640,
  TABLET: 1024,
  DESKTOP: 1400,
  ULTRAWIDE: 1920,
};

// Helper: apply theme to CSS variables on root
export function applyTheme(theme) {
  const root = document.documentElement;
  Object.entries(theme).forEach(([key, val]) => {
    if (key !== "name") root.style.setProperty(`--${key.toLowerCase()}`, val);
  });
  root.style.colorScheme = theme.name;
}
