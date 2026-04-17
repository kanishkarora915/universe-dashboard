import { useEffect, useMemo } from "react";

/**
 * Global keyboard shortcuts.
 * @param {Object} handlers - { [combo]: fn }
 *   combo examples: "cmd+k", "ctrl+k", "escape", "1", "?", "cmd+shift+l", "f"
 *
 * All keys in handler map are normalized to lowercase on registration,
 * so "Cmd+K" and "cmd+k" both work identically.
 */
export function useHotkeys(handlers, { enabled = true } = {}) {
  // Normalize all handler keys to lowercase ONCE — avoids case mismatch bugs
  const normalizedHandlers = useMemo(() => {
    if (!handlers) return {};
    const out = {};
    Object.entries(handlers).forEach(([k, v]) => {
      out[k.toLowerCase()] = v;
    });
    return out;
  }, [handlers]);

  useEffect(() => {
    if (!enabled) return;

    const onKey = (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      const typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;

      const key = e.key.toLowerCase();
      const mods = [];
      if (e.ctrlKey) mods.push("ctrl");
      if (e.metaKey) mods.push("cmd");
      if (e.shiftKey) mods.push("shift");
      if (e.altKey) mods.push("alt");

      // Prefer cmd over ctrl: if cmd held, don't register ctrl combo
      if (e.metaKey) {
        const idx = mods.indexOf("ctrl");
        if (idx >= 0) mods.splice(idx, 1);
      }

      const combo = [...mods, key].join("+");
      const comboAlt = [...mods.map((m) => (m === "cmd" ? "ctrl" : m)), key].join("+"); // cmd+k also matches ctrl+k

      // Find matching handler (case-insensitive via pre-normalized map)
      const handler = normalizedHandlers[combo] || normalizedHandlers[comboAlt];

      // For plain keys (no modifiers), skip if typing in input
      if (handler && mods.length === 0 && typing) {
        // Allow escape to work always (to close modals/search even from inputs)
        if (key !== "escape") return;
      }

      if (handler) {
        e.preventDefault();
        handler(e);
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [normalizedHandlers, enabled]);
}
